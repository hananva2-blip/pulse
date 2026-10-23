import asyncio
import json
import logging
import os
import signal
import time

import httpx
import nats
from nats.js.api import RetentionPolicy, StreamConfig
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import Counter, Histogram, start_http_server
from psycopg.errors import ForeignKeyViolation
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
METRICS_PORT = int(os.getenv("METRICS_PORT", 9100))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 10))
# A job that keeps failing is dropped after this many deliveries, so it cannot block the queue
MAX_DELIVERIES = 5

CHECKS = Counter("pulse_checks_total", "Checks run", ["result"])
LATENCY = Histogram("pulse_check_latency_seconds", "Response time of checked URLs",
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10))

# Adds otelTraceID to log records, so a log line links to its trace in Grafana
LoggingInstrumentor().instrument(inject_trace_context=True)
LOG_FORMAT = "%(asctime)s [runner] %(message)s trace_id=%(otelTraceID)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
# httpx logs every request at INFO; the checks are already counted in metrics
logging.getLogger("httpx").setLevel(logging.WARNING)
tracer = trace.get_tracer("pulse.runner")


def setup_tracing():
    """Send traces to Tempo when OTEL_EXPORTER_OTLP_ENDPOINT is set (in Kubernetes); a no-op otherwise.
    The service name comes from OTEL_SERVICE_NAME."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # The check request and the result insert become child spans of "run check"
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument()


async def run_check(client, job):
    """Request the URL once and describe the outcome. Never raises."""
    started = time.perf_counter()
    try:
        response = await client.get(job["url"], timeout=job["timeout_seconds"], follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "latency_ms": None, "error": type(exc).__name__}

    latency_ms = int((time.perf_counter() - started) * 1000)
    ok = response.status_code == job["expected_status"]
    error = None if ok else f"Expected {job['expected_status']}, got {response.status_code}"
    return {"ok": ok, "status_code": response.status_code, "latency_ms": latency_ms, "error": error}


async def handle(msg, client, pool):
    job = json.loads(msg.data)
    # Continue the trace the scheduler started for this check
    parent = propagate.extract(getattr(msg, "headers", None) or {})
    attributes = {"pulse.monitor_id": job["monitor_id"], "url.full": job["url"]}
    span_options = {"context": parent, "kind": SpanKind.CONSUMER, "attributes": attributes}
    with tracer.start_as_current_span("run check", **span_options) as span:
        result = await run_check(client, job)
        if not result["ok"]:
            span.set_status(Status(StatusCode.ERROR, result["error"]))
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO checks (monitor_id, ok, status_code, latency_ms, error) VALUES (%s, %s, %s, %s, %s)",
                    (job["monitor_id"], result["ok"], result["status_code"], result["latency_ms"], result["error"]),
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
    if result["latency_ms"] is not None:
        LATENCY.observe(result["latency_ms"] / 1000)


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
    # All runners share one durable consumer, so each job goes to exactly one of them
    sub = await js.pull_subscribe("checks.http", durable="http-runners", stream="CHECKS")

    headers = {"User-Agent": "Pulse-Monitor/1.0"}
    async with (AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=BATCH_SIZE) as pool,
                httpx.AsyncClient(headers=headers) as client):
        logging.info("Waiting for jobs")
        while not stop.is_set():
            try:
                msgs = await sub.fetch(BATCH_SIZE, timeout=5)
            except TimeoutError:
                # No jobs. nats-py raises either its own TimeoutError or asyncio's; both are TimeoutError.
                continue
            results = await asyncio.gather(*(handle(m, client, pool) for m in msgs), return_exceptions=True)
            for msg, error in zip(msgs, results, strict=True):
                if isinstance(error, Exception):
                    await give_up_or_retry(msg, error)

    # close, not drain: draining a pull subscription waits forever. Jobs fetched but not
    # acked are redelivered to another runner, so nothing is lost.
    await nc.close()
    # Flush spans that are still buffered
    getattr(trace.get_tracer_provider(), "shutdown", lambda: None)()
    logging.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
