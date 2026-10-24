import asyncio
import json
import logging
import os
import re
import signal
import time

import nats
from nats.js.api import RetentionPolicy, StreamConfig
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from playwright.async_api import async_playwright, expect
from prometheus_client import Counter, Histogram, start_http_server
from psycopg.errors import ForeignKeyViolation
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
METRICS_PORT = int(os.getenv("METRICS_PORT", 9100))
# Each check opens a page in Chromium, so a runner handles only a few at a time
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 2))
MAX_DELIVERIES = 5

CHECKS = Counter("pulse_browser_checks_total", "Browser checks run", ["result"])
DURATION = Histogram("pulse_browser_check_duration_seconds", "Time to load the page and run all steps",
                     buckets=(0.5, 1, 2, 5, 10, 20, 30))

# Adds otelTraceID to log records, so a log line links to its trace in Grafana
LoggingInstrumentor().instrument(inject_trace_context=True)
LOG_FORMAT = "%(asctime)s [browser-runner] %(message)s trace_id=%(otelTraceID)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
tracer = trace.get_tracer("pulse.browser-runner")


def setup_tracing():
    """Send traces to Tempo when OTEL_EXPORTER_OTLP_ENDPOINT is set (in Kubernetes); a no-op otherwise."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    PsycopgInstrumentor().instrument()


class StepFailed(Exception):
    pass


async def run_steps(page, job):
    """Open the monitor's URL, then run its steps in order. Returns the page's HTTP status."""
    timeout_ms = job["timeout_seconds"] * 1000
    page.set_default_timeout(timeout_ms)
    response = await page.goto(job["url"], wait_until="domcontentloaded")
    for number, step in enumerate(job.get("steps") or [], start=1):
        action = step["action"]
        try:
            if action == "click":
                await page.click(step["selector"])
            elif action == "fill":
                await page.fill(step["selector"], step["value"])
            elif action == "expect_text":
                await expect(page.get_by_text(step["value"]).first).to_be_visible(timeout=timeout_ms)
            elif action == "expect_url":
                await expect(page).to_have_url(re.compile(re.escape(step["value"])), timeout=timeout_ms)
        except Exception as exc:
            # Playwright errors run to many lines; the first one says what happened
            raise StepFailed(f"Step {number} ({action}) failed: {str(exc).splitlines()[0]}") from exc
    return response.status if response else None


async def run_check(browser, job):
    """Run one browser check in a fresh context. Never raises; on failure it keeps a screenshot."""
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    page = await context.new_page()
    started = time.perf_counter()
    try:
        status_code = await run_steps(page, job)
        ok, error, screenshot = True, None, None
    except Exception as exc:
        status_code, ok, error = None, False, str(exc).splitlines()[0][:500]
        screenshot = await page.screenshot(type="jpeg", quality=70)
    finally:
        await context.close()
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {"ok": ok, "status_code": status_code, "latency_ms": duration_ms, "error": error, "screenshot": screenshot}


async def handle(msg, browser, pool):
    job = json.loads(msg.data)
    parent = propagate.extract(getattr(msg, "headers", None) or {})
    attributes = {"pulse.monitor_id": job["monitor_id"], "url.full": job["url"]}
    span_options = {"context": parent, "kind": SpanKind.CONSUMER, "attributes": attributes}
    with tracer.start_as_current_span("run browser check", **span_options) as span:
        result = await run_check(browser, job)
        if not result["ok"]:
            span.set_status(Status(StatusCode.ERROR, result["error"]))
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO checks (monitor_id, ok, status_code, latency_ms, error, screenshot)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (job["monitor_id"], result["ok"], result["status_code"], result["latency_ms"],
                     result["error"], result["screenshot"]),
                )
        except ForeignKeyViolation:
            # The monitor was deleted while this job waited in the queue: nothing to record
            await msg.ack()
            logging.info("Monitor %s was deleted; dropped its queued job", job["monitor_id"])
            return
        # Ack only after the result is stored, so a crash means the job is redelivered
        await msg.ack()
        logging.info("Checked %s: %s", job["url"], "up" if result["ok"] else f"down ({result['error']})")

    CHECKS.labels(result="up" if result["ok"] else "down").inc()
    DURATION.observe(result["latency_ms"] / 1000)


async def give_up_or_retry(msg, error):
    """A failed job is redelivered, unless it already failed MAX_DELIVERIES times."""
    if msg.metadata.num_delivered >= MAX_DELIVERIES:
        await msg.term()
        logging.error("Job failed %d times, dropping it: %s", MAX_DELIVERIES, error)
    else:
        logging.error("Job failed, it will be redelivered: %s", error)


async def main():
    # Kubernetes sends SIGTERM on shutdown; finish the current batch, then exit
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)

    setup_tracing()
    start_http_server(METRICS_PORT)
    nc = await nats.connect(NATS_URL, max_reconnect_attempts=-1)
    js = nc.jetstream()
    await js.add_stream(StreamConfig(name="CHECKS", subjects=["checks.>"], retention=RetentionPolicy.WORK_QUEUE))
    # Its own consumer and subject: browser and HTTP runners scale independently
    sub = await js.pull_subscribe("checks.browser", durable="browser-runners", stream="CHECKS")

    async with (async_playwright() as playwright,
                AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=BATCH_SIZE) as pool):
        # One browser for the process; every check gets its own isolated context
        browser = await playwright.chromium.launch(args=["--disable-dev-shm-usage"])
        logging.info("Waiting for browser jobs")
        while not stop.is_set():
            try:
                msgs = await sub.fetch(BATCH_SIZE, timeout=5)
            except TimeoutError:
                continue
            results = await asyncio.gather(*(handle(m, browser, pool) for m in msgs), return_exceptions=True)
            for msg, error in zip(msgs, results, strict=True):
                if isinstance(error, Exception):
                    await give_up_or_retry(msg, error)
        await browser.close()

    await nc.close()
    getattr(trace.get_tracer_provider(), "shutdown", lambda: None)()
    logging.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
