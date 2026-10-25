"""Sends a Telegram message when an incident opens and when it is resolved.

The incidents table doubles as the outbox: a notification that was not sent yet has a NULL
*_notified_at column, so a failed send is retried on the next poll and a restart loses nothing.
"""
import logging
import os
import signal
import time

import httpx
from prometheus_client import Counter, start_http_server
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
METRICS_PORT = int(os.getenv("METRICS_PORT", 9100))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", 5))
# Without a token the notifier only logs the messages, so it runs in CI and kind without secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "https://api.telegram.org")

NOTIFICATIONS = Counter("pulse_notifications_total", "Incident notifications", ["event", "result"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s [notifier] %(message)s")
# httpx logs each request URL at INFO, and the Telegram URL contains the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)

# Opened messages go first: an incident opened and resolved between two polls sends both, in order.
# The row lock keeps a second notifier replica from sending the same message.
PENDING = """
    SELECT i.id, i.opened_at, i.resolved_at, i.cause, m.name, m.url,
           CASE WHEN i.opened_notified_at IS NULL THEN 'opened' ELSE 'resolved' END AS event
    FROM incidents i JOIN monitors m ON m.id = i.monitor_id
    WHERE i.opened_notified_at IS NULL OR (i.resolved_at IS NOT NULL AND i.resolved_notified_at IS NULL)
    ORDER BY i.id
    LIMIT 20
    FOR UPDATE OF i SKIP LOCKED
"""
MARK_SENT = {
    "opened": "UPDATE incidents SET opened_notified_at = now() WHERE id = %s",
    "resolved": "UPDATE incidents SET resolved_notified_at = now() WHERE id = %s",
}


def message(incident):
    if incident["event"] == "opened":
        return f"🔴 {incident['name']} is down\n{incident['url']}\n{incident['cause'] or 'No details'}"
    minutes = max(1, round((incident["resolved_at"] - incident["opened_at"]).total_seconds() / 60))
    return f"✅ {incident['name']} is back up after {minutes} min\n{incident['url']}"


def send(client, text):
    if not TELEGRAM_BOT_TOKEN:
        logging.info("Telegram is not set up; message: %s", text.replace("\n", " | "))
        return
    response = client.post(
        f"{TELEGRAM_API_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
    )
    response.raise_for_status()


def notify_pending(pool, client):
    """Send every pending notification once. Returns how many were sent."""
    sent = 0
    with pool.connection() as conn, conn.transaction():
        for incident in conn.execute(PENDING).fetchall():
            event = incident["event"]
            try:
                send(client, message(incident))
            except httpx.HTTPError as exc:
                # Log the kind of error, never str(exc): it contains the request URL, and so the token
                status = getattr(getattr(exc, "response", None), "status_code", None)
                logging.error("Could not send the %s message of incident %s: %s %s",
                              event, incident["id"], type(exc).__name__, status or "")
                NOTIFICATIONS.labels(event=event, result="error").inc()
                continue
            conn.execute(MARK_SENT[event], (incident["id"],))
            NOTIFICATIONS.labels(event=event, result="sent").inc()
            sent += 1
    return sent


def main():
    # Kubernetes sends SIGTERM on shutdown; finish the current poll, then exit
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    start_http_server(METRICS_PORT)
    logging.info("Sending incident notifications to %s", "Telegram" if TELEGRAM_BOT_TOKEN else "the log only")
    with (ConnectionPool(DATABASE_URL, min_size=1, max_size=1, kwargs={"row_factory": dict_row}) as pool,
          httpx.Client(timeout=10) as client):
        while not stopping:
            try:
                if sent := notify_pending(pool, client):
                    logging.info("Sent %d notifications", sent)
            except Exception as exc:
                # The database may be down or the schema not created yet; try again on the next poll
                logging.error("Poll failed: %s", exc)
            time.sleep(POLL_SECONDS)
    logging.info("Stopped")


if __name__ == "__main__":
    main()
