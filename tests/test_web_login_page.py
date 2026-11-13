"""The sign-in page itself, driven in a real (headless) Chrome."""

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
from web_automation.web.auth import SessionStore

pytestmark = pytest.mark.browser

USER, PASSWORD = "manoj", "correct horse battery"


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


def start_server(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert until(lambda: server.started, 10)
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


@pytest.fixture
def login_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Page, str, SessionStore]]:
    monkeypatch.setattr(queries, "summary", lambda url, account: {
        "account": account, "followers": 346, "added_last_7_days": 4, "unfollowed_last_7_days": 2,
        "unfollowed_total": 9, "last_run": None})
    monkeypatch.setenv("WA_EXPECTED_ACCOUNT", "demo_account")
    settings = Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome",
                        timeout_ms=1000, database_url="postgresql://u:p@127.0.0.1:1/db",
                        ui_username=USER, ui_password=PASSWORD)
    sessions = SessionStore()
    server, thread, port = start_server(create_app(settings=settings, token=secrets.token_urlsafe(8), sessions=sessions))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.errors = []  # type: ignore[attr-defined]
        page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
        yield page, f"http://127.0.0.1:{port}/", sessions
        browser.close()
    server.should_exit = True
    thread.join(5)


def sign_in(page: Page, user: str = USER, password: str = PASSWORD) -> None:
    page.fill("#login-user", user)
    page.fill("#login-password", password)
    page.click("#login-submit")


def test_the_page_shows_the_sign_in_form_and_hides_the_app(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    assert page.locator("#app").is_hidden() and page.locator("#logout").is_hidden()
    assert page.get_attribute("#login-password", "type") == "password"
    assert page.get_attribute("#login-password", "autocomplete") == "current-password"
    assert not page.errors


def test_a_correct_login_shows_the_dashboard_and_clears_the_password(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    sign_in(page)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    assert page.locator("#login").is_hidden() and page.locator("#logout").is_visible()
    assert page.input_value("#login-password") == ""  # not left in the form
    assert not page.errors


def test_a_wrong_password_shows_one_message_and_clears_the_field(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    sign_in(page, password="nope")
    assert until(lambda: "Wrong user name or password" in page.inner_text("#login-error"))
    assert page.input_value("#login-password") == "" and page.locator("#app").is_hidden()
    assert page.locator("#login-submit").is_enabled()


def test_the_lockout_message_reaches_the_page(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    for _ in range(5):
        sign_in(page, password="nope")
        assert until(lambda: page.locator("#login-submit").is_enabled())
    sign_in(page)  # the right password, but locked out
    assert until(lambda: "Try again in" in page.inner_text("#login-error"))
    assert page.locator("#app").is_hidden()


def test_reloading_keeps_you_signed_in_and_sign_out_ends_it(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    sign_in(page)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    page.reload()
    assert until(lambda: page.inner_text("#s-followers") == "346")  # cookie survives a reload
    page.click("#logout")
    assert until(lambda: page.locator("#login").is_visible() and page.locator("#app").is_hidden())
    page.reload()
    assert until(lambda: page.locator("#login").is_visible())  # and a reload does not sign back in


def test_an_expired_session_returns_to_the_sign_in_form(login_ui) -> None:
    page, url, sessions = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    sign_in(page)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    sessions._expires.clear()  # the server forgets the session, like an expiry or a restart
    assert until(lambda: page.locator("#login").is_visible(), 10)  # the 1.5 s status poll notices
    assert "session ended" in page.inner_text("#login-error")
    assert not page.errors


def test_the_session_cookie_cannot_be_read_by_page_scripts(login_ui) -> None:
    page, url, _ = login_ui
    page.goto(url)
    assert until(lambda: page.locator("#login").is_visible())
    sign_in(page)
    assert until(lambda: page.inner_text("#s-followers") == "346")
    assert page.evaluate("document.cookie") == ""  # HttpOnly: invisible to JavaScript
    cookies = page.context.cookies()
    assert [c["name"] for c in cookies] == ["wa_session"] and cookies[0]["httpOnly"] is True
    assert cookies[0]["sameSite"] == "Strict"
    assert page.evaluate("JSON.stringify(sessionStorage) + JSON.stringify(localStorage)") == "{}{}"  # nothing stored


def test_without_a_login_the_token_link_still_opens_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(queries, "summary", lambda url, account: {
        "account": account, "followers": 7, "added_last_7_days": 0, "unfollowed_last_7_days": 0,
        "unfollowed_total": 0, "last_run": None})
    monkeypatch.setenv("WA_EXPECTED_ACCOUNT", "demo_account")
    settings = Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome",
                        timeout_ms=1000, database_url="postgresql://u:p@127.0.0.1:1/db")
    token = secrets.token_urlsafe(8)
    server, thread, port = start_server(create_app(settings=settings, token=token))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/#token={token}")
            assert until(lambda: page.inner_text("#s-followers") == "7")
            assert page.locator("#login").is_hidden() and page.locator("#logout").is_hidden()
            assert "token" not in page.url
            browser.close()
    finally:
        server.should_exit = True
        thread.join(5)
