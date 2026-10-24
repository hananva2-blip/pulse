import asyncio
import json
import logging
import os
import signal

import nats
from nats.js.api import RetentionPolicy, StreamConfig
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from prometheus_client import Counter, start_http_server
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
METRICS_PORT = int(os.getenv("METRICS_PORT", 9100))
TICK_SECONDS = 1

# Claims due monitors and moves their next run forward in one statement.
# SKIP LOCKED lets several schedulers run side by side without double-scheduling.
CLAIM_DUE = """
    UPDATE monitors SET next_run_at = now() + make_interval(secs => interval_seconds)
    WHERE id IN (
        SELECT id FROM monitors
        WHERE enabled AND next_run_at <= now()
        ORDER BY next_run_at
        LIMIT 500
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, kind, url, steps, timeout_seconds, expected_status
"""

JOBS = Counter("pulse_jobs_scheduled_total", "Check jobs published to the queue")
ERRORS = Counter("pulse_scheduler_errors_total", "Scheduler loop failures")

# Adds otelTraceID to log records, so a log line links to its trace in Grafana
LoggingInstrumentor().instrument(inject_trace_context=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s trace_id=%(otelTraceID)s")
tracer = trace.get_tracer("pulse.scheduler")


def setup_tracing():
    """Send traces to Tempo when OTEL_EXPORTER_OTLP_ENDPOINT is set (in Kubernetes); a no-op otherwise.
    The service name comes from OTEL_SERVICE_NAME."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


async def main():
    # Kubernetes sends SIGTERM on shutdown; finish the current batch, then exit
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)

    setup_tracing()
    start_http_server(METRICS_PORT)
    nc = await nats.connect(NATS_URL, max_reconnect_attempts=-1)
    js = nc.jetstream()
    # Work-queue retention: a job is deleted once a runner acks it
    await js.add_stream(StreamConfig(name="CHECKS", subjects=["checks.>"], retention=RetentionPolicy.WORK_QUEUE))

    async with AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        logging.info("Scheduling every %ss", TICK_SECONDS)
        while not stop.is_set():
            try:
                async with pool.connection() as conn:
                    rows = await (await conn.execute(CLAIM_DUE)).fetchall()
                for monitor_id, kind, url, steps, timeout, expected_status in rows:
                    job = {"monitor_id": monitor_id, "url": url, "steps": steps,
                           "timeout_seconds": timeout, "expected_status": expected_status}
                    # One trace per check; its context travels to the runner in the message headers
                    with tracer.start_as_current_span("schedule check", kind=SpanKind.PRODUCER,
                                                      attributes={"pulse.monitor_id": monitor_id, "url.full": url}):
                        headers = {}
                        propagate.inject(headers)
                        # checks.http or checks.browser: each kind has its own runners
                        await js.publish(f"checks.{kind}", json.dumps(job).encode(), headers=headers)
                if rows:
                    JOBS.inc(len(rows))
                    logging.info("Published %d jobs", len(rows))
            except Exception as exc:
                # A missed tick only delays checks; keep the loop alive
                ERRORS.inc()
                logging.error("Tick failed: %s", exc)
            await asyncio.sleep(TICK_SECONDS)

    await nc.drain()
    # Flush spans that are still buffered
    getattr(trace.get_tracer_provider(), "shutdown", lambda: None)()
    logging.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
