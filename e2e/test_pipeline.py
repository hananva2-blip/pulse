"""End-to-end tests of the pipeline: API -> scheduler -> NATS -> runner -> Postgres."""
from conftest import first_check, open_incident


def test_healthy_site_is_up(api, monitor):
    created = monitor("/status/200")

    check = first_check(api, created["id"])

    assert check["ok"] is True
    assert check["status_code"] == 200
    summary = api.get(f"/api/monitors/{created['id']}").json()
    assert summary["up"] is True
    assert float(summary["uptime_24h"]) == 100.0


def test_failing_site_is_down_with_the_reason(api, monitor):
    created = monitor("/status/503")

    check = first_check(api, created["id"])

    assert check["ok"] is False
    assert check["status_code"] == 503
    assert check["error"] == "Expected 200, got 503"


def test_expected_status_can_be_changed(api, monitor):
    created = monitor("/status/404", expected_status=404)

    assert first_check(api, created["id"])["ok"] is True


def test_slow_site_times_out(api, monitor):
    created = monitor("/delay/5", timeout_seconds=1)

    check = first_check(api, created["id"])

    assert check["ok"] is False
    assert check["error"] == "ReadTimeout"


def test_deleted_monitor_is_gone(api, monitor):
    created = monitor("/status/200")

    assert api.delete(f"/api/monitors/{created['id']}").status == 204
    assert api.get(f"/api/monitors/{created['id']}").status == 404


def test_browser_monitor_finds_the_text(api, monitor):
    created = monitor("/html", kind="browser", steps=[{"action": "expect_text", "value": "Moby-Dick"}])

    check = first_check(api, created["id"], timeout=90)

    assert check["ok"] is True, check["error"]
    assert check["has_screenshot"] is False


def test_browser_monitor_failure_keeps_a_screenshot(api, monitor):
    steps = [{"action": "expect_text", "value": "Not on this page"}]
    created = monitor("/html", kind="browser", steps=steps, timeout_seconds=3)

    check = first_check(api, created["id"], timeout=90)

    assert check["ok"] is False
    assert check["error"].startswith("Step 1 (expect_text) failed")
    assert check["has_screenshot"] is True
    screenshot = api.get(f"/api/checks/{check['id']}/screenshot")
    assert screenshot.status == 200
    assert screenshot.headers["content-type"] == "image/jpeg"
    assert screenshot.body()[:2] == b"\xff\xd8"


def test_two_failures_in_a_row_open_an_incident(api, monitor):
    created = monitor("/status/500", failures_to_alert=2)

    incident = open_incident(api, created["id"])

    assert incident["cause"] == "Expected 200, got 500"
    assert incident["resolved_at"] is None
    checks = api.get(f"/api/monitors/{created['id']}/checks").json()
    assert len(checks) >= 2 and not any(check["ok"] for check in checks)
