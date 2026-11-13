
import pytest

from web_automation.browser import BrowserConfig, BrowserManager
from web_automation.browser.instagram_login import LoginResult, ensure_logged_in
from web_automation.settings import Settings
from web_automation.utils.exceptions import LoginTimeoutError

pytestmark = pytest.mark.browser


def test_no_login_page_is_left_alone(browser_config: BrowserConfig, login_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(f"{login_site}/home.html")
        result = ensure_logged_in(session.page, "alice", "s3cret", detect_timeout_ms=500)
        assert result is LoginResult.ALREADY_AUTHENTICATED
        assert session.page.url.endswith("/home.html")


def test_login_page_is_filled_from_credentials(browser_config: BrowserConfig, login_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(f"{login_site}/accounts/login/")
        result = ensure_logged_in(session.page, "alice", "s3cret", timeout_s=20)
        assert result is LoginResult.LOGGED_IN
        assert session.page.url.endswith("/home.html")


def test_wrong_password_times_out_without_retry(browser_config: BrowserConfig, login_site: str) -> None:
    with BrowserManager(browser_config) as session:
        session.page.goto(f"{login_site}/accounts/login/")
        with pytest.raises(LoginTimeoutError):
            ensure_logged_in(session.page, "alice", "wrong", timeout_s=3)
        assert "/accounts/login" in session.page.url


def test_manual_login_waits_for_user(browser_config: BrowserConfig, login_site: str) -> None:
    """No credentials: the helper waits, then returns once 'the user' logs in (OTP path)."""
    messages: list[str] = []
    with BrowserManager(browser_config) as session:
        page = session.page
        page.goto(f"{login_site}/accounts/login/")
        # Simulate the user finishing login after the first poll; goto from the same
        # thread is not possible while ensure_logged_in blocks, so use a timer on the page.
        page.evaluate("setTimeout(() => location.href = '/home.html', 1500)")
        result = ensure_logged_in(page, timeout_s=20, notify=messages.append)
        assert result is LoginResult.LOGGED_IN
        assert any("manually" in m for m in messages)


def test_password_not_in_settings_repr(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("WA_IG_USERNAME", "alice")
    monkeypatch.setenv("WA_IG_PASSWORD", "s3cret")
    settings = Settings.load(tmp_path / "missing.env")
    assert settings.ig_password == "s3cret"
    assert "s3cret" not in repr(settings)
    assert "alice" not in repr(settings)


def test_otp_typed_in_terminal_is_entered_and_login_completes(browser_config: BrowserConfig, otp_site: str) -> None:
    asked: list[str] = []

    def terminal(prompt: str) -> str:
        asked.append(prompt)
        return "123456"

    with BrowserManager(browser_config) as session:
        session.page.goto(f"{otp_site}/accounts/login/")
        result = ensure_logged_in(
            session.page, "alice", "s3cret", timeout_s=30, notify=lambda m: None, ask_code=terminal
        )
        assert result is LoginResult.LOGGED_IN and session.page.url.endswith("/home.html")
    assert len(asked) == 1 and "123456" not in asked[0]  # the prompt never contains the code


def test_wrong_code_allows_exactly_one_retry_then_waits_for_user(browser_config: BrowserConfig, otp_site: str) -> None:
    answers = iter(["000000", "111111", "999999"])  # the third answer must never be requested
    asked: list[str] = []

    def terminal(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    with BrowserManager(browser_config) as session:
        session.page.goto(f"{otp_site}/accounts/login/")
        with pytest.raises(LoginTimeoutError):
            ensure_logged_in(
                session.page, "alice", "s3cret", timeout_s=15, notify=lambda m: None, ask_code=terminal
            )
    assert len(asked) == 2  # no more than two code submissions in total


def test_empty_answer_leaves_the_code_to_chrome(browser_config: BrowserConfig, otp_site: str) -> None:
    with BrowserManager(browser_config) as session:
        page = session.page
        page.goto(f"{otp_site}/accounts/login/")
        # user answers "" at the terminal, then types the code in the browser window instead
        def terminal(prompt: str) -> str:
            page.get_by_label("Security code").fill("123456")
            page.get_by_label("Security code").press("Enter")
            return ""

        result = ensure_logged_in(
            page, "alice", "s3cret", timeout_s=20, notify=lambda m: None, ask_code=terminal
        )
        assert result is LoginResult.LOGGED_IN


def test_without_ask_code_it_still_waits_for_chrome(browser_config: BrowserConfig, otp_site: str) -> None:
    messages: list[str] = []
    with BrowserManager(browser_config) as session:
        page = session.page
        page.goto(f"{otp_site}/accounts/login/")
        # no ask_code: behaves like before (Chrome-only), and finishes when the user does
        page.evaluate("setTimeout(() => location.href = '/home.html', 2500)")
        result = ensure_logged_in(page, timeout_s=20, notify=messages.append)
        assert result is LoginResult.LOGGED_IN
