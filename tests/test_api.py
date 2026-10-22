def test_health(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_monitor_uses_defaults(client):
    response = client.post("/api/monitors", json={"name": "Docs", "url": "https://example.com/docs"})

    assert response.status_code == 201
    body = response.json()
    assert body["interval_seconds"] == 60
    assert body["expected_status"] == 200
    assert body["enabled"] is True


def test_new_monitor_has_no_stats_yet(client, monitor):
    body = client.get(f"/api/monitors/{monitor['id']}").json()
    assert body["up"] is None
    assert body["uptime_24h"] is None


def test_rejects_invalid_monitor(client):
    assert client.post("/api/monitors", json={"name": "Bad", "url": "not-a-url"}).status_code == 422
    too_often = {"name": "Fast", "url": "https://example.com", "interval_seconds": 5}
    assert client.post("/api/monitors", json=too_often).status_code == 422


def test_stats_from_checks(client, db, monitor):
    # Three successes then one failure, oldest first
    rows = [(True, 100, 4), (True, 200, 3), (True, 300, 2), (False, None, 1)]
    for ok, latency, minutes_ago in rows:
        db.execute(
            "INSERT INTO checks (monitor_id, ok, latency_ms, checked_at) "
            "VALUES (%s, %s, %s, now() - make_interval(mins => %s))",
            (monitor["id"], ok, latency, minutes_ago),
        )

    body = client.get(f"/api/monitors/{monitor['id']}").json()

    assert body["up"] is False                 # the newest check failed
    assert float(body["uptime_24h"]) == 75.0   # 3 of 4
    assert body["p95_latency_ms_24h"] == 290   # p95 of 100, 200, 300


def test_checks_are_newest_first(client, db, monitor):
    db.execute("INSERT INTO checks (monitor_id, ok, checked_at) VALUES (%s, true, now() - interval '1 minute')",
               (monitor["id"],))
    db.execute("INSERT INTO checks (monitor_id, ok, checked_at) VALUES (%s, false, now())", (monitor["id"],))

    checks = client.get(f"/api/monitors/{monitor['id']}/checks").json()
    assert [c["ok"] for c in checks] == [False, True]


def test_delete_monitor(client, db, monitor):
    db.execute("INSERT INTO checks (monitor_id, ok) VALUES (%s, true)", (monitor["id"],))

    assert client.delete(f"/api/monitors/{monitor['id']}").status_code == 204
    assert client.get(f"/api/monitors/{monitor['id']}").status_code == 404
    assert db.execute("SELECT count(*) FROM checks").fetchone()[0] == 0
    assert client.delete(f"/api/monitors/{monitor['id']}").status_code == 404
