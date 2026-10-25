"""The notifier against a real Postgres and a fake Telegram API."""
import json
import logging
import os

import httpx
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import notifier
from notifier import notify_pending

TOKEN = "123456:not-a-real-token"


class FakeTelegram:
    """Records every request; answers with `status`."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.client = httpx.Client(transport=httpx.MockTransport(self.answer))

    def answer(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": self.status == 200})

    def texts(self):
        return [json.loads(request.content)["text"] for request in self.requests]


@pytest.fixture
def pool(client):
    with ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=1, kwargs={"row_factory": dict_row}) as p:
        yield p


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "42")
    fake = FakeTelegram()
    yield fake
    fake.client.close()


def checks(db, monitor_id, *results):
    for ok in results:
        db.execute("INSERT INTO checks (monitor_id, ok, error) VALUES (%s, %s, %s)",
                   (monitor_id, ok, None if ok else "Expected 200, got 503"))


def test_an_opened_incident_sends_one_message(db, monitor, pool, telegram):
    checks(db, monitor["id"], False, False)

    assert notify_pending(pool, telegram.client) == 1
    assert notify_pending(pool, telegram.client) == 0  # already sent

    request = telegram.requests[0]
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content)["chat_id"] == "42"
    assert telegram.texts() == ["🔴 Example is down\nhttps://example.com/\nExpected 200, got 503"]


def test_a_resolved_incident_sends_down_then_up(db, monitor, pool, telegram):
    checks(db, monitor["id"], False, False, True)

    assert notify_pending(pool, telegram.client) == 1
    assert notify_pending(pool, telegram.client) == 1

    down, up = telegram.texts()
    assert down.startswith("🔴 Example is down")
    assert up.startswith("✅ Example is back up after 1 min")


def test_a_failed_send_is_retried_and_the_token_is_not_logged(db, monitor, pool, telegram, caplog):
    checks(db, monitor["id"], False, False)
    telegram.status = 500

    assert notify_pending(pool, telegram.client) == 0
    assert "HTTPStatusError 500" in caplog.text
    assert TOKEN not in caplog.text

    telegram.status = 200
    assert notify_pending(pool, telegram.client) == 1
    assert len(telegram.requests) == 2


def test_without_a_token_the_message_is_only_logged(db, monitor, pool, caplog, monkeypatch):
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "")
    caplog.set_level(logging.INFO)
    checks(db, monitor["id"], False, False)
    unreachable = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("no request expected")))

    assert notify_pending(pool, unreachable) == 1
    assert "Example is down" in caplog.text
