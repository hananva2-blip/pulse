import asyncio
import json
import logging
import os
import time

import httpx
import nats
from nats.errors import TimeoutError as NatsTimeout
from nats.js.api import RetentionPolicy, StreamConfig
from prometheus_client import Counter, Histogram, start_http_server
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
METRICS_PORT = int(os.getenv("METRICS_PORT", 9100))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 10))

CHECKS = Counter("pulse_checks_total", "Checks run", ["result"])
LATENCY = Histogram("pulse_check_latency_seconds", "Response time of checked URLs",
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [runner] %(message)s")


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
    result = await run_check(client, job)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO checks (monitor_id, ok, status_code, latency_ms, error) VALUES (%s, %s, %s, %s, %s)",
            (job["monitor_id"], result["ok"], result["status_code"], result["latency_ms"], result["error"]),
        )
    # Ack only after the result is stored, so a crash means the job is redelivered
    await msg.ack()

    CHECKS.labels(result="up" if result["ok"] else "down").inc()
    if result["latency_ms"] is not None:
        LATENCY.observe(result["latency_ms"] / 1000)


async def main():
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
        while True:
            try:
                msgs = await sub.fetch(BATCH_SIZE, timeout=5)
            except NatsTimeout:
                continue
            results = await asyncio.gather(*(handle(m, client, pool) for m in msgs), return_exceptions=True)
            for error in (r for r in results if isinstance(r, Exception)):
                logging.error("Job failed, it will be redelivered: %s", error)


if __name__ == "__main__":
    asyncio.run(main())
