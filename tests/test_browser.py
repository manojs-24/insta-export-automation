import threading
from dataclasses import replace

import pytest

from web_automation.browser import BrowserConfig, BrowserManager
from web_automation.utils.exceptions import BrowserLaunchError, ProfileInUseError

pytestmark = pytest.mark.browser


def test_launches_chrome_and_navigates(browser_config: BrowserConfig, test_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        assert session.page.title() == "Test Page"
        assert session.page.locator("#greeting").inner_text() == "Hello automation"
        session.page.get_by_role("button", name="Go").click()
        assert session.page.locator("#greeting").inner_text() == "Clicked"


def test_uses_installed_chrome_not_chromium(browser_config: BrowserConfig, test_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        user_agent = session.page.evaluate("navigator.userAgent")
        assert "Chrome/" in user_agent
        brands = session.page.evaluate("navigator.userAgentData.brands.map(b => b.brand)")
        assert "Google Chrome" in brands


def test_profile_persists_across_sessions(browser_config: BrowserConfig, test_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        session.page.evaluate("localStorage.setItem('token', 'abc')")

    assert browser_config.profile_dir.exists()

    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        assert session.page.evaluate("localStorage.getItem('token')") == "abc"


def test_separate_profiles_are_isolated(browser_config: BrowserConfig, test_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        session.page.evaluate("localStorage.setItem('token', 'abc')")

    other = replace(browser_config, profile_name="other")
    with BrowserManager(other) as session:
        session.page.goto(test_site)
        assert session.page.evaluate("localStorage.getItem('token')") is None


def test_stop_is_idempotent_and_closes_browser(browser_config: BrowserConfig) -> None:
    manager = BrowserManager(browser_config)
    session = manager.start()
    assert manager.is_running
    manager.stop()
    manager.stop()
    assert not manager.is_running
    assert session.context.pages == []


def test_browser_closed_when_body_raises(browser_config: BrowserConfig) -> None:
    manager = BrowserManager(browser_config)
    with pytest.raises(RuntimeError, match="boom"):
        with manager:
            raise RuntimeError("boom")
    assert not manager.is_running


def test_start_is_idempotent(browser_config: BrowserConfig) -> None:
    manager = BrowserManager(browser_config)
    try:
        assert manager.start() is manager.start()
    finally:
        manager.stop()


def test_missing_chrome_channel_gives_clear_error(browser_config: BrowserConfig) -> None:
    bad = replace(browser_config, channel="chrome-does-not-exist")
    manager = BrowserManager(bad)
    with pytest.raises(BrowserLaunchError):
        manager.start()
    assert not manager.is_running


def test_locked_profile_gives_clear_error(browser_config: BrowserConfig) -> None:
    with BrowserManager(browser_config):
        # Playwright's sync API cannot nest in one thread, so try from another.
        outcome: list[BaseException | None] = []

        def attempt() -> None:
            second = BrowserManager(browser_config)
            try:
                second.start()
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 - report to main thread
                outcome.append(exc)
            finally:
                second.stop()

        worker = threading.Thread(target=attempt)
        worker.start()
        worker.join(timeout=60)
        assert isinstance(outcome[0], ProfileInUseError), outcome
        assert "already open" in str(outcome[0])
    # The failed attempt must not have damaged the profile.
    assert browser_config.profile_dir.exists()


def test_profile_not_deleted_on_stop(browser_config: BrowserConfig) -> None:
    with BrowserManager(browser_config):
        pass
    assert browser_config.profile_dir.exists()
