"""Browser checks against a real headless Chromium. Pages are data: URLs, so no network is needed."""
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

from playwright.async_api import async_playwright
from psycopg_pool import AsyncConnectionPool

from browser_runner import handle, run_check

JPEG = b"\xff\xd8"  # every JPEG file starts with these two bytes


def page(html):
    return "data:text/html," + quote(html)


def job(html, *steps, timeout=2):
    return {"monitor_id": 1, "url": page(html), "steps": list(steps), "timeout_seconds": timeout}


@asynccontextmanager
async def chromium():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        yield browser
        await browser.close()


async def test_expected_text_is_up():
    async with chromium() as browser:
        check = job("<h1>Welcome to Pulse</h1>", {"action": "expect_text", "value": "Welcome"})
        result = await run_check(browser, check)

    assert result["ok"] is True
    assert result["error"] is None
    assert result["screenshot"] is None  # kept only on failure
    assert isinstance(result["latency_ms"], int)


async def test_missing_text_is_down_with_the_step_and_a_screenshot():
    async with chromium() as browser:
        result = await run_check(browser, job("<h1>Home</h1>", {"action": "expect_text", "value": "Checkout"}))

    assert result["ok"] is False
    assert result["error"].startswith("Step 1 (expect_text) failed")
    assert result["screenshot"].startswith(JPEG)


async def test_fill_and_click_flow():
    html = """
      <input id="name">
      <button onclick="document.getElementById('out').textContent =
                       'Hello ' + document.getElementById('name').value">Greet</button>
      <p id="out"></p>
    """
    steps = [
        {"action": "fill", "selector": "#name", "value": "Hanan"},
        {"action": "click", "selector": "button"},
        {"action": "expect_text", "value": "Hello Hanan"},
    ]
    async with chromium() as browser:
        result = await run_check(browser, job(html, *steps))

    assert result["ok"] is True, result["error"]


async def test_failing_click_names_the_step():
    async with chromium() as browser:
        check = job("<p>No buttons here</p>", {"action": "click", "selector": "#buy"}, timeout=1)
        result = await run_check(browser, check)

    assert result["ok"] is False
    assert result["error"].startswith("Step 1 (click) failed")


async def test_expect_url_matches_part_of_the_url():
    async with chromium() as browser:
        found = await run_check(browser, job("<p>Hi</p>", {"action": "expect_url", "value": "text/html"}))
        missing = await run_check(browser, job("<p>Hi</p>", {"action": "expect_url", "value": "/checkout"}, timeout=1))

    assert found["ok"] is True, found["error"]
    assert missing["ok"] is False
    assert missing["error"].startswith("Step 1 (expect_url) failed")


async def test_handle_stores_the_screenshot_then_acks(db, monitor):
    failing = {**job("<h1>Home</h1>", {"action": "expect_text", "value": "Checkout"}), "monitor_id": monitor["id"]}
    msg = SimpleNamespace(data=json.dumps(failing).encode(), ack=AsyncMock())

    async with chromium() as browser, AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=1) as pool:
        await handle(msg, browser, pool)

    ok, screenshot = db.execute("SELECT ok, screenshot FROM checks WHERE monitor_id = %s", (monitor["id"],)).fetchone()
    assert ok is False
    assert bytes(screenshot).startswith(JPEG)
    msg.ack.assert_awaited_once()
