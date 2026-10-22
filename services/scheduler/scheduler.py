import asyncio
import json
import logging
import os

import nats
from nats.js.api import RetentionPolicy, StreamConfig
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
    RETURNING id, url, timeout_seconds, expected_status
"""

JOBS = Counter("pulse_jobs_scheduled_total", "Check jobs published to the queue")
ERRORS = Counter("pulse_scheduler_errors_total", "Scheduler loop failures")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")


async def main():
    start_http_server(METRICS_PORT)
    nc = await nats.connect(NATS_URL, max_reconnect_attempts=-1)
    js = nc.jetstream()
    # Work-queue retention: a job is deleted once a runner acks it
    await js.add_stream(StreamConfig(name="CHECKS", subjects=["checks.>"], retention=RetentionPolicy.WORK_QUEUE))

    async with AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        logging.info("Scheduling every %ss", TICK_SECONDS)
        while True:
            try:
                async with pool.connection() as conn:
                    rows = await (await conn.execute(CLAIM_DUE)).fetchall()
                for monitor_id, url, timeout, expected_status in rows:
                    job = {"monitor_id": monitor_id, "url": url,
                           "timeout_seconds": timeout, "expected_status": expected_status}
                    await js.publish("checks.http", json.dumps(job).encode())
                if rows:
                    JOBS.inc(len(rows))
                    logging.info("Published %d jobs", len(rows))
            except Exception as exc:
                # A missed tick only delays checks; keep the loop alive
                ERRORS.inc()
                logging.error("Tick failed: %s", exc)
            await asyncio.sleep(TICK_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
