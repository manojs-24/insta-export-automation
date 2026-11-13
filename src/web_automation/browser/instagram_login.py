"""Milestone 1 login helper: handle Instagram's login page if it appears.

Interim code: from Milestone 4 this becomes JSON-driven steps (`fill`, `pause_for_user`).
Rules: never log or print credentials; never submit the password twice (avoids account
lockouts). The OTP / 2FA code is typed by the user, either at the terminal prompt (hidden
input, typed into Chrome for them, never stored or logged) or directly in Chrome.
"""

import re
import time
from collections.abc import Callable
from enum import Enum

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from web_automation.utils.exceptions import LoginTimeoutError
from web_automation.utils.logger import get_logger

log = get_logger("login")

# Selectors are NOT yet verified against the live Instagram UI (Milestone 4 does that).
_PASSWORD = 'input[type="password"]'
_USERNAME = (
    'input[name="username"], input[name="email"], '
    'input[autocomplete="username"], input[autocomplete="username webauthn"]'
)
_LOGIN_URL_MARKERS = ("/accounts/login", "/challenge", "two_factor")
# Likely markers of a verification-code field (not verified against the live page).
_CODE_FIELD = (
    'input[autocomplete="one-time-code"], input[name="verificationCode"], '
    'input[name="security_code"], input[name="confirmationCode"], input[name="code"]'
)
_MAX_CODE_PROMPTS = 2  # a wrong code may be retried once by the user, never more


class LoginResult(Enum):
    ALREADY_AUTHENTICATED = "already_authenticated"
    LOGGED_IN = "logged_in"


def _visible(locator: Locator) -> bool:
    try:
        return locator.first.is_visible()
    except PlaywrightError:
        return False  # page is mid-navigation


def _needs_login(page: Page) -> bool:
    try:
        url = page.url
    except PlaywrightError:
        return True
    return _visible(page.locator(_PASSWORD)) or any(m in url for m in _LOGIN_URL_MARKERS)


def _find_code_field(page: Page) -> Locator | None:
    """A visible verification-code input, or None. Never matches the password field."""
    candidates = [
        page.locator(_CODE_FIELD).first,
        page.get_by_role("textbox", name=re.compile(r"code", re.IGNORECASE)).first,
    ]
    for field in candidates:
        if _visible(field):
            return field
    return None


def _wait_until_gone(page: Page, field: Locator, seconds: float) -> None:
    """After submitting a code, wait for the field to disappear (accepted) or time out (rejected)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _visible(field) or not _needs_login(page):
            return
        try:
            if field.input_value() == "":  # the page cleared the box: it rejected the code
                return
        except PlaywrightError:
            return
        time.sleep(0.25)


def ensure_logged_in(
    page: Page,
    username: str = "",
    password: str = "",
    timeout_s: int = 300,
    detect_timeout_ms: int = 5000,
    notify: Callable[[str], None] = print,
    ask_code: Callable[[str], str] | None = None,
) -> LoginResult:
    """If a login page is showing, log in; otherwise do nothing.

    With credentials: fill both fields and press Enter once. Without: wait for the user.
    Either way, an OTP / verification step is completed by the user in the browser, and
    this returns once the login page is gone. Raises LoginTimeoutError otherwise.
    """
    # Decide as soon as EITHER a login form or the logged-in sidebar shows. Waiting for a
    # login form that never appears used to cost the full detect timeout on every run.
    logged_in_marker = page.get_by_role("link", name="Home", exact=True).first
    deadline = time.monotonic() + detect_timeout_ms / 1000
    while time.monotonic() < deadline:
        if _visible(page.locator(_PASSWORD)) or _visible(logged_in_marker):
            break
        time.sleep(0.1)
    if not _needs_login(page):
        log.info("No login page; session already authenticated")
        return LoginResult.ALREADY_AUTHENTICATED

    user_field, pass_field = page.locator(_USERNAME).first, page.locator(_PASSWORD).first
    if username and password and _visible(user_field) and _visible(pass_field):
        log.info("Login page detected; filling credentials from environment")
        user_field.fill(username)
        pass_field.fill(password)
        pass_field.press("Enter")  # single submit, never retried
    else:
        notify("Login page detected. Please log in manually in the Chrome window.")

    deadline = time.monotonic() + timeout_s
    prompted = False
    code_prompts = 0
    started = time.monotonic()
    while time.monotonic() < deadline:
        if not _needs_login(page):
            log.info("Login completed")
            return LoginResult.LOGGED_IN
        if ask_code is not None and code_prompts < _MAX_CODE_PROMPTS:
            code_field = _find_code_field(page)
            if code_field is not None:
                code_prompts += 1
                log.info("Verification code requested (prompt %d)", code_prompts)
                code = ask_code(
                    "Enter the verification code Instagram sent (typing is hidden). "
                    "Leave it empty and press Enter to type it in Chrome yourself: "
                ).strip()
                if code:
                    try:
                        code_field.fill(code)
                        code_field.press("Enter")  # one submission per prompt
                    except PlaywrightError:
                        notify("Could not type the code into the page; please enter it in Chrome.")
                    else:
                        _wait_until_gone(page, code_field, 10)
                        if _needs_login(page) and _visible(code_field):
                            notify("Instagram did not accept the code.")
                continue
        if not prompted and time.monotonic() - started > 8:
            notify(
                "Waiting for verification: if Instagram asks you to choose where to get a code, "
                "choose it in the Chrome window. "
                + (
                    "The code prompt appears here once Instagram shows the code field."
                    if ask_code is not None
                    else "Enter the code in the Chrome window; the script continues automatically."
                )
            )
            prompted = True
        time.sleep(0.5)
    raise LoginTimeoutError(
        f"Login did not complete within {timeout_s}s (wrong credentials, or the OTP step "
        "was not finished). The script did not retry, to avoid locking the account."
    )
