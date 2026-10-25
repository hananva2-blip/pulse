"""Shared fixtures for the end-to-end tests.

Run against a cluster with the chart installed (testTarget enabled) and Pulse reachable at PULSE_URL.
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


def open_incident(api, monitor_id, timeout=60):
    """Wait until the monitor has an open incident, and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        incidents = api.get("/api/incidents", params={"status": "open"}).json()
        mine = [incident for incident in incidents if incident["monitor_id"] == monitor_id]
        if mine:
            return mine[0]
        time.sleep(1)
    pytest.fail(f"No incident opened for monitor {monitor_id} within {timeout}s")
