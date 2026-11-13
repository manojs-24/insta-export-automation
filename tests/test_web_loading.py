"""The UI's loading indicators, checked in a real (headless) Chrome against a slow fake database."""

import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright

import web_automation.db.queries as queries
from web_automation.settings import Settings
from web_automation.web.app import create_app
from web_automation.web.runner import WebRunner

pytestmark = pytest.mark.browser

DELAY = {"seconds": 0.0}


def until(predicate: Callable[[], bool], seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 - the page may be mid-update
            pass
        time.sleep(0.05)
    return False


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Page, str]]:
    def slow(fn):
        def wrapper(*args, **kwargs):
            time.sleep(DELAY["seconds"])
            return fn(*args, **kwargs)
        return wrapper

    stamp = "2026-11-12T10:00:00"
    monkeypatch.setattr(queries, "summary", slow(lambda url, account: {
        "account": account, "followers": 346, "added_last_7_days": 4, "unfollowed_last_7_days": 2,
        "unfollowed_total": 9, "last_run": {"id": 7, "status": "success", "started_at": stamp, "completed_at": None}}))
    monkeypatch.setattr(queries, "followers", slow(lambda url, account, search, limit, offset: {"total": 2, "items": [
        {"username": f"{search or 'user'}_a", "profile_url": None, "followed_at": None, "added_at": stamp},
        {"username": "<img src=x onerror=alert(1)>", "profile_url": None, "followed_at": None, "added_at": stamp}]}))
    for name in ("follower_events", "runs", "logs"):
        monkeypatch.setattr(queries, name, slow(lambda *a, **k: {"total": 0, "items": []}))
    monkeypatch.setenv("WA_EXPECTED_ACCOUNT", "demo_account")

    settings = Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome", timeout_ms=1000,
                        database_url="postgresql://u:p@127.0.0.1:1/db")
    token = secrets.token_urlsafe(12)
    app = create_app(settings=settings, token=token, runner=WebRunner(settings_loader=lambda: settings))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert until(lambda: server.started, 10)
    port = server.servers[0].sockets[0].getsockname()[1]
    DELAY["seconds"] = 0.0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
        page.on("dialog", lambda d: (page.errors.append("dialog: " + d.message), d.dismiss()))  # type: ignore[attr-defined]
        yield page, f"http://127.0.0.1:{port}/#token={token}"
        browser.close()
    server.should_exit = True
    thread.join(5)


def test_dashboard_shows_loading_then_numbers(ui) -> None:
    page, url = ui
    DELAY["seconds"] = 1.5
    page.goto(url)
    assert until(lambda: page.inner_text("#s-followers") == "Loading…")
    assert page.locator("#loadbar").is_visible()
    assert until(lambda: page.inner_text("#s-followers") == "346")
    assert until(lambda: page.locator("#loadbar").is_hidden(), 3)
    assert "token" not in page.url  # the access token is removed from the address bar
    assert not page.errors


def test_table_tab_shows_spinner_row_and_disables_pager(ui) -> None:
    page, url = ui
    page.goto(url)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    DELAY["seconds"] = 1.5
    page.click('nav button[data-tab="followers"]')
    assert until(lambda: page.locator("#followers-body .spinner").count() == 1)
    assert "Loading…" in page.inner_text("#followers-body")
    assert all(b.is_disabled() for b in page.locator('[data-pager="followers"]').all())
    assert until(lambda: "user_a" in page.inner_text("#followers-body"))
    assert page.locator("#followers-body .spinner").count() == 0


def test_hostile_username_is_text_not_markup(ui) -> None:
    page, url = ui
    page.goto(url)
    page.click('nav button[data-tab="followers"]')
    assert until(lambda: "user_a" in page.inner_text("#followers-body"))
    assert "<img src=x" in page.inner_text("#followers-body")
    assert page.locator("#followers-body img").count() == 0 and not page.errors


def test_empty_table_says_so_instead_of_spinning_forever(ui) -> None:
    page, url = ui
    page.goto(url)
    page.click('nav button[data-tab="runs"]')
    assert until(lambda: "Nothing to show yet." in page.inner_text("#runs-body"))
    assert page.locator("#runs-body .spinner").count() == 0


def test_background_polling_does_not_flash_the_progress_bar(ui) -> None:
    page, url = ui
    page.goto(url)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    assert until(lambda: page.locator("#loadbar").is_hidden(), 5)
    visible = 0
    for _ in range(35):  # about 3.5 s, covering two status polls (one every 1.5 s)
        visible += page.locator("#loadbar").is_visible()
        time.sleep(0.1)
    assert visible == 0


def test_a_late_old_response_cannot_overwrite_newer_rows(ui) -> None:
    page, url = ui
    page.goto(url)
    page.click('nav button[data-tab="followers"]')
    assert until(lambda: "user_a" in page.inner_text("#followers-body"))
    DELAY["seconds"] = 2.0
    page.fill("#followers-search", "first")
    time.sleep(0.6)  # the debounced slow request is now in flight
    DELAY["seconds"] = 0.0
    page.fill("#followers-search", "second")
    assert until(lambda: "second_a" in page.inner_text("#followers-body"))
    time.sleep(2.6)  # the slow 'first' answer arrives late
    text = page.inner_text("#followers-body")
    assert "second_a" in text and "first_a" not in text
