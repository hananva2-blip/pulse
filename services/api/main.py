import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, HttpUrl

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row})

# Last status plus 24h uptime and p95 latency, computed in Postgres
MONITOR_SUMMARY = """
    SELECT m.id, m.name, m.url, m.interval_seconds, m.expected_status, m.enabled,
           last.ok AS up, last.checked_at AS last_checked_at, last.latency_ms AS last_latency_ms,
           round(100.0 * avg(c.ok::int), 2) AS uptime_24h,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY c.latency_ms) AS p95_latency_ms_24h
    FROM monitors m
    LEFT JOIN LATERAL (
        SELECT ok, checked_at, latency_ms FROM checks
        WHERE monitor_id = m.id ORDER BY checked_at DESC LIMIT 1
    ) last ON TRUE
    LEFT JOIN checks c ON c.monitor_id = m.id AND c.checked_at > now() - interval '24 hours'
    {where}
    GROUP BY m.id, last.ok, last.checked_at, last.latency_ms
    ORDER BY m.id
"""


@asynccontextmanager
async def lifespan(app):
    pool.open()
    with pool.connection() as conn:
        conn.execute(SCHEMA)
    yield
    pool.close()


app = FastAPI(title="Pulse API", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, include_in_schema=False)


class MonitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    interval_seconds: int = Field(default=60, ge=10, le=3600)
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    expected_status: int = Field(default=200, ge=100, le=599)


@app.get("/healthz")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/monitors", status_code=201)
def create_monitor(monitor: MonitorIn):
    with pool.connection() as conn:
        return conn.execute(
            """INSERT INTO monitors (name, url, interval_seconds, timeout_seconds, expected_status)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, name, url, interval_seconds, timeout_seconds, expected_status, enabled""",
            (monitor.name, str(monitor.url), monitor.interval_seconds,
             monitor.timeout_seconds, monitor.expected_status),
        ).fetchone()


@app.get("/api/monitors")
def list_monitors():
    with pool.connection() as conn:
        return conn.execute(MONITOR_SUMMARY.format(where="")).fetchall()


@app.get("/api/monitors/{monitor_id}")
def get_monitor(monitor_id: int):
    with pool.connection() as conn:
        row = conn.execute(MONITOR_SUMMARY.format(where="WHERE m.id = %s"), (monitor_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return row


@app.get("/api/monitors/{monitor_id}/checks")
def list_checks(monitor_id: int, limit: int = 50):
    with pool.connection() as conn:
        return conn.execute(
            """SELECT checked_at, ok, status_code, latency_ms, error FROM checks
               WHERE monitor_id = %s ORDER BY checked_at DESC LIMIT %s""",
            (monitor_id, min(limit, 500)),
        ).fetchall()


@app.delete("/api/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int):
    with pool.connection() as conn:
        deleted = conn.execute("DELETE FROM monitors WHERE id = %s", (monitor_id,)).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="Monitor not found")
