import os

from psycopg import AsyncConnection

from scheduler import CLAIM_DUE


async def claim():
    async with await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        return await (await conn.execute(CLAIM_DUE)).fetchall()


async def test_due_monitor_is_claimed_once(monitor):
    first = await claim()
    second = await claim()

    assert [row[0] for row in first] == [monitor["id"]]
    assert second == []  # next_run_at moved forward, so it is not due again


async def test_claim_schedules_next_run_one_interval_later(db, monitor):
    await claim()
    seconds_ahead = db.execute(
        "SELECT extract(epoch FROM next_run_at - now()) FROM monitors WHERE id = %s", (monitor["id"],)
    ).fetchone()[0]
    assert 55 < seconds_ahead <= 60


async def test_disabled_monitor_is_not_claimed(db, monitor):
    db.execute("UPDATE monitors SET enabled = false WHERE id = %s", (monitor["id"],))
    assert await claim() == []
