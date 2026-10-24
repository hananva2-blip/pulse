"""End-to-end tests against a deployed Pulse: API -> scheduler -> NATS -> runner -> Postgres.

Run against a cluster with the chart installed (testTarget enabled) and the API reachable at PULSE_URL.
"""
import os
import time

import pytest

PULSE_URL = os.getenv("PULSE_URL", "http://localhost:8080")
# The go-httpbin test server inside the cluster, as the runners see it
TARGET = os.getenv("TEST_TARGET", "http://pulse-httpbin.pulse")


@pytest.fixture(scope="session")
def api(playwright):
    context = playwright.request.new_context(base_url=PULSE_URL)
    yield context
    context.dispose()


@pytest.fixture
def monitor(api):
    """Create a monitor for a path on the test server; delete it after the test."""
    created = []

    def create(path, **fields):
        body = {"name": f"e2e {path}", "url": f"{TARGET}{path}", "interval_seconds": 10, **fields}
        response = api.post("/api/monitors", data=body)
        assert response.status == 201, response.text()
        created.append(response.json()["id"])
        return response.json()

    yield create
    for monitor_id in created:
        api.delete(f"/api/monitors/{monitor_id}")


def first_check(api, monitor_id, timeout=60):
    """Wait until the pipeline has stored a result for the monitor, and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checks = api.get(f"/api/monitors/{monitor_id}/checks").json()
        if checks:
            return checks[0]
        time.sleep(1)
    pytest.fail(f"No check stored for monitor {monitor_id} within {timeout}s")


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
