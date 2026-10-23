import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from opentelemetry.sdk.trace import TracerProvider
from psycopg_pool import AsyncConnectionPool

from runner import LOG_FORMAT, MAX_DELIVERIES, give_up_or_retry, handle, run_check

JOB = {"monitor_id": 1, "url": "https://example.com", "timeout_seconds": 5, "expected_status": 200}


def fake_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_expected_status_is_up():
    async with fake_client(lambda request: httpx.Response(200)) as client:
        result = await run_check(client, JOB)

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert isinstance(result["latency_ms"], int)
    assert result["error"] is None


async def test_unexpected_status_is_down():
    async with fake_client(lambda request: httpx.Response(503)) as client:
        result = await run_check(client, JOB)

    assert result["ok"] is False
    assert result["error"] == "Expected 200, got 503"


async def test_connection_error_is_down():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    async with fake_client(refuse) as client:
        result = await run_check(client, JOB)

    assert result == {"ok": False, "status_code": None, "latency_ms": None, "error": "ConnectError"}


async def test_handle_stores_result_then_acks(db, monitor):
    msg = SimpleNamespace(data=json.dumps({**JOB, "monitor_id": monitor["id"]}).encode(), ack=AsyncMock())

    async with (AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=1) as pool,
                fake_client(lambda request: httpx.Response(200)) as client):
        await handle(msg, client, pool)

    stored = db.execute("SELECT ok, status_code FROM checks WHERE monitor_id = %s", (monitor["id"],)).fetchall()
    assert stored == [(True, 200)]
    msg.ack.assert_awaited_once()


def test_log_lines_carry_the_trace_id(caplog):
    # The log format references otelTraceID; a record without it fails to format
    tracer = TracerProvider().get_tracer("test")
    with caplog.at_level(logging.INFO), tracer.start_as_current_span("check") as span:
        logging.info("Checked %s", "https://example.com")
    expected = format(span.get_span_context().trace_id, "032x")
    assert logging.Formatter(LOG_FORMAT).format(caplog.records[-1]).endswith(f"trace_id={expected}")


async def test_job_of_a_deleted_monitor_is_dropped(db):
    # monitor_id 999999 does not exist: the monitor was deleted while the job was queued
    msg = SimpleNamespace(data=json.dumps({**JOB, "monitor_id": 999999}).encode(), ack=AsyncMock())

    async with (AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=1) as pool,
                fake_client(lambda request: httpx.Response(200)) as client):
        await handle(msg, client, pool)

    msg.ack.assert_awaited_once()  # acked, so it is not redelivered forever
    assert db.execute("SELECT count(*) FROM checks").fetchone()[0] == 0


async def test_failing_job_is_retried_then_dropped():
    retry = SimpleNamespace(metadata=SimpleNamespace(num_delivered=1), term=AsyncMock())
    await give_up_or_retry(retry, RuntimeError("database down"))
    retry.term.assert_not_awaited()

    poison = SimpleNamespace(metadata=SimpleNamespace(num_delivered=MAX_DELIVERIES), term=AsyncMock())
    await give_up_or_retry(poison, RuntimeError("database down"))
    poison.term.assert_awaited_once()
