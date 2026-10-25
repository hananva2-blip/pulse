"""The incident trigger in Postgres, and the incidents API."""


def check(db, monitor_id, ok, error=None):
    db.execute("INSERT INTO checks (monitor_id, ok, error) VALUES (%s, %s, %s)", (monitor_id, ok, error))


def incidents(db, monitor_id):
    return db.execute(
        "SELECT cause, resolved_at IS NOT NULL FROM incidents WHERE monitor_id = %s ORDER BY id", (monitor_id,)
    ).fetchall()


def test_one_failure_opens_no_incident(db, monitor):
    check(db, monitor["id"], False, "Expected 200, got 503")

    assert incidents(db, monitor["id"]) == []


def test_second_failure_in_a_row_opens_an_incident(db, monitor):
    check(db, monitor["id"], False, "ConnectError")
    check(db, monitor["id"], False, "Expected 200, got 503")

    assert incidents(db, monitor["id"]) == [("Expected 200, got 503", False)]


def test_good_check_in_between_starts_the_count_again(db, monitor):
    for ok in (False, True, False):
        check(db, monitor["id"], ok, None if ok else "ReadTimeout")

    assert incidents(db, monitor["id"]) == []


def test_a_long_outage_keeps_one_incident_open(db, monitor):
    for _ in range(4):
        check(db, monitor["id"], False, "ReadTimeout")

    assert incidents(db, monitor["id"]) == [("ReadTimeout", False)]


def test_good_check_resolves_and_the_next_outage_opens_a_new_incident(db, monitor):
    for ok in (False, False, True, False, False):
        check(db, monitor["id"], ok, None if ok else "ReadTimeout")

    assert incidents(db, monitor["id"]) == [("ReadTimeout", True), ("ReadTimeout", False)]


def test_failures_to_alert_of_one_opens_at_once(client, db):
    created = client.post("/api/monitors", json={"name": "Shop", "url": "https://shop.example", "failures_to_alert": 1})

    check(db, created.json()["id"], False, "ConnectError")

    assert created.json()["failures_to_alert"] == 1
    assert incidents(db, created.json()["id"]) == [("ConnectError", False)]


def test_failures_to_alert_is_validated(client):
    for value in (0, 11):
        body = {"name": "Shop", "url": "https://shop.example", "failures_to_alert": value}
        assert client.post("/api/monitors", json=body).status_code == 422


def test_incidents_api_filters_open_and_resolved(client, db):
    down, recovered = (client.post("/api/monitors", json={"name": name, "url": "https://example.com"}).json()
                       for name in ("Down", "Recovered"))
    for ok in (False, False):
        check(db, down["id"], ok, "Expected 200, got 500")
    for ok in (False, False, True):
        check(db, recovered["id"], ok, None if ok else "ReadTimeout")

    everything = client.get("/api/incidents").json()
    open_now = client.get("/api/incidents", params={"status": "open"}).json()
    resolved = client.get("/api/incidents", params={"status": "resolved"}).json()

    assert len(everything) == 2
    assert [(i["monitor_name"], i["cause"], i["resolved_at"]) for i in open_now] == [
        ("Down", "Expected 200, got 500", None)]
    assert [i["monitor_name"] for i in resolved] == ["Recovered"]
    assert isinstance(resolved[0]["duration_seconds"], int)
    assert client.get("/api/incidents", params={"status": "maybe"}).status_code == 422


def test_deleting_a_monitor_deletes_its_incidents(client, db, monitor):
    check(db, monitor["id"], False, "ReadTimeout")
    check(db, monitor["id"], False, "ReadTimeout")

    client.delete(f"/api/monitors/{monitor['id']}")

    assert client.get("/api/incidents").json() == []
