"""End-to-end tests of the dashboard and the status page, in a real browser."""
import time

from conftest import PULSE_URL, TARGET, first_check, open_incident
from playwright.sync_api import expect

# The pages refresh every 10-15 seconds, and a check runs every 10
SLOW = 60_000


def row(page, text):
    return page.get_by_role("row").filter(has_text=text)


def test_dashboard_shows_a_monitor_and_its_status(page, monitor):
    created = monitor("/status/200", name="e2e dashboard up")

    page.goto(PULSE_URL)

    expect(row(page, created["name"])).to_contain_text("Up", timeout=SLOW)
    expect(row(page, created["name"])).to_contain_text("100.00%")


def test_add_a_monitor_from_the_form(page, api):
    name = f"e2e form {time.time_ns()}"
    page.goto(PULSE_URL)

    page.get_by_label("Name").fill(name)
    page.get_by_label("URL").fill(f"{TARGET}/status/200")
    page.get_by_label("Check every").select_option("10")
    page.get_by_role("button", name="Add monitor").click()

    expect(row(page, name)).to_be_visible()
    created = next(m for m in api.get("/api/monitors").json() if m["name"] == name)
    assert created["interval_seconds"] == 10
    api.delete(f"/api/monitors/{created['id']}")


def test_the_form_shows_what_the_api_rejected(page):
    page.goto(PULSE_URL)

    page.get_by_label("Name").fill("e2e rejected")
    page.get_by_label("URL").fill("https://example.com")
    page.get_by_label("Expected status").fill("700")
    # The browser's own check would stop 700 first; skip it to see the API's answer
    page.locator("#add").evaluate("form => form.noValidate = true")
    page.get_by_role("button", name="Add monitor").click()

    expect(page.get_by_role("alert").filter(has_text="expected_status")).to_be_visible()


def test_delete_asks_before_it_deletes(page, monitor):
    created = monitor("/status/200", name="e2e delete me")
    page.goto(PULSE_URL)
    delete = page.get_by_role("button", name=f"Delete {created['name']}")

    delete.click()
    expect(delete).to_have_text("Confirm delete")
    expect(row(page, created["name"])).to_be_visible()

    delete.click()
    expect(row(page, created["name"])).to_have_count(0)


def test_failed_browser_check_shows_its_screenshot(page, api, monitor):
    steps = [{"action": "expect_text", "value": "Not on this page"}]
    created = monitor("/html", kind="browser", steps=steps, timeout_seconds=3)
    first_check(api, created["id"], timeout=90)

    page.goto(f"{PULSE_URL}/monitor?id={created['id']}")

    link = page.get_by_role("link", name="Screenshot").first
    expect(link).to_be_visible()
    screenshot = page.request.get(PULSE_URL + link.get_attribute("href"))
    assert screenshot.headers["content-type"] == "image/jpeg"


def test_status_page_shows_a_service_with_an_open_incident_as_down(page, api, monitor):
    created = monitor("/status/500", name="e2e status down", failures_to_alert=1)
    open_incident(api, created["id"])

    page.goto(f"{PULSE_URL}/status")

    service = page.locator("#services li").filter(has_text=created["name"])
    expect(service).to_contain_text("Down", timeout=SLOW)
    expect(page.locator("#overall")).to_contain_text("down")
    # The public page never shows the monitored URLs
    expect(page.get_by_text(created["url"])).to_have_count(0)


def test_names_are_shown_as_text_not_run_as_html(page, monitor):
    name = '<img src=x onerror="window.hacked = true">'
    monitor("/status/200", name=name)

    page.goto(PULSE_URL)

    expect(row(page, name)).to_be_visible()
    assert page.evaluate("window.hacked") is None
