import os
import time
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from web_automation.actions.base import RETRY_ANY, RETRY_NEVER, RETRY_PRE_ACTION, Action, register_action, require, step_timeout
from web_automation.actions.locators import find
from web_automation.utils.exceptions import ActionError, ElementNotFoundError, UserDeclinedError

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext


@register_action
class Click(Action):
    """click: {selector, timeout?}. Requires the element to be visible and enabled."""

    name = "click"
    retry_policy = RETRY_PRE_ACTION

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        locator = find(ctx, params)
        if not locator.is_enabled():
            raise ActionError("Element is visible but disabled; not clicking")
        locator.click(timeout=step_timeout(ctx, params))


@register_action
class ClickIfVisible(Action):
    """click_if_visible: {selector, timeout?}.

    Clicks the element if it shows up within `timeout`; otherwise the step passes without
    doing anything. For popups that only appear sometimes (e.g. "Save your login info?").
    An ambiguous selector (several matches) is still an error, never a guess.
    """

    name = "click_if_visible"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        try:
            locator = find(ctx, params)
        except ElementNotFoundError:
            ctx.notify("Optional element not shown; skipping.")
            return
        if not locator.is_enabled():
            raise ActionError("Element is visible but disabled; not clicking")
        locator.click(timeout=step_timeout(ctx, params))


@register_action
class Fill(Action):
    """fill: {selector, value | value_env, timeout?}.

    `value_env` names an environment variable, so secrets never appear in the JSON.
    The filled text is never logged or returned.
    """

    name = "fill"
    retry_policy = RETRY_PRE_ACTION

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        has_value, has_env = "value" in params, "value_env" in params
        if has_value == has_env:
            raise ActionError("fill needs exactly one of 'value' or 'value_env'")
        try:
            locator = find(ctx, params)
        except ElementNotFoundError:
            if params.get("optional"):
                ctx.notify("Optional field not shown; skipping.")
                return
            raise
        if has_env:
            env_name = require(params, "value_env")
            value = os.environ.get(env_name)
            if value is None:
                raise ActionError(f"Environment variable '{env_name}' is not set")
        else:
            value = require(params, "value")
        timeout = step_timeout(ctx, params)
        locator.fill(value, timeout=timeout)
        if params.get("submit"):
            locator.press("Enter", timeout=timeout)


@register_action
class Select(Action):
    """select: {selector, value | label, timeout?}. Picks an option in a native <select>."""

    name = "select"
    retry_policy = RETRY_PRE_ACTION

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        has_value, has_label = "value" in params, "label" in params
        if has_value == has_label:
            raise ActionError("select needs exactly one of 'value' or 'label'")
        key = "value" if has_value else "label"
        option = {key: require(params, key)}
        selected = find(ctx, params).select_option(**option, timeout=step_timeout(ctx, params))
        if not selected:
            raise ActionError("No option was selected")


@register_action
class PauseForUser(Action):
    """pause_for_user: {message?}. Blocks until the user finishes in the browser window."""

    name = "pause_for_user"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        message = params.get("message", "Complete the required step in the Chrome window.")
        ctx.notify(message)
        ctx.wait_for_user("Press Enter here when you are done... ")


@register_action
class Confirm(Action):
    """confirm: {message}. A 'no' answer fails the step and always stops the run."""

    name = "confirm"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        if not ctx.confirm_with_user(require(params, "message")):
            raise UserDeclinedError("User declined to continue")


@register_action
class Verify(Action):
    """verify: {selector, contains | contains_env, timeout?}.

    Fails unless the element's visible text contains the expected text (case-insensitive).
    Used to check the intended account / chosen options before anything is submitted.
    """

    name = "verify"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        has_text, has_env = "contains" in params, "contains_env" in params
        if has_text == has_env:
            raise ActionError("verify needs exactly one of 'contains' or 'contains_env'")
        if has_env:
            env_name = require(params, "contains_env")
            expected = os.environ.get(env_name, "")
            if not expected:
                raise ActionError(f"Environment variable '{env_name}' is not set")
        else:
            expected = require(params, "contains")
        # Poll instead of reading once: right after a dialog closes the page is still
        # animating / re-rendering, so the first read can be stale.
        deadline = time.monotonic() + step_timeout(ctx, params) / 1000
        locator = find(ctx, params)
        actual = ""
        while True:
            try:
                actual = locator.inner_text()
                if expected.casefold() in actual.casefold():
                    return
            except PlaywrightError:
                pass  # element re-rendered between polls; look it up again
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        seen = " ".join(actual.split())[:80]
        raise ActionError(f"Verification failed: expected text not found. Element text was: {seen!r}")


@register_action
class EnsureLogin(Action):
    """ensure_login: {timeout_s?, save_as?}. If a login page shows, log in (WA_IG_* from the
    environment, else manually); an OTP is entered by the user. With `save_as`, the outcome
    ("logged_in" or "already_authenticated") is stored in that variable so later steps can
    run conditionally with `when`."""

    name = "ensure_login"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        from web_automation.browser.instagram_login import ensure_logged_in

        timeout_s = params.get("timeout_s", int(os.environ.get("WA_LOGIN_TIMEOUT_S", "300")))
        if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
            raise ActionError("'timeout_s' must be a positive integer")
        save_as = params.get("save_as")
        if save_as is not None and (not isinstance(save_as, str) or not save_as):
            raise ActionError("'save_as' must be a variable name")
        outcome = ensure_logged_in(
            ctx.page,
            os.environ.get("WA_IG_USERNAME", ""),
            os.environ.get("WA_IG_PASSWORD", ""),
            timeout_s=timeout_s,
            notify=ctx.notify,
            ask_code=ctx.ask_secret,
        )
        if save_as:
            ctx.variables[save_as] = outcome.value


def find_password_field(ctx: "ExecutionContext", field_name: str, timeout_ms: int) -> tuple[Page, Locator] | None:
    """Poll every open tab/popup window for a visible password field; None if none appears."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for page in list(ctx.page.context.pages):
            if page.is_closed():
                continue
            for frame in list(page.frames):  # the prompt may live in an iframe
                field = frame.get_by_role("textbox", name=field_name, exact=True).first
                try:
                    if field.is_visible():
                        return page, field
                except PlaywrightError:
                    pass  # page is mid-navigation
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def _still_visible(page: Page, field: Locator) -> bool:
    try:
        return not page.is_closed() and field.is_visible()
    except PlaywrightError:
        return False


@register_action
class EnterPassword(Action):
    """enter_password: {value_env, timeout?, field_name?, button_name?}.

    Handles a "re-enter your password" prompt if one appears, in the current tab or in a
    popup window. If no prompt shows up within `timeout`, the step is skipped. Otherwise it
    types the password from the environment variable, clicks the prompt's Continue button,
    and waits for the prompt to close. It never retries: if the prompt stays open (wrong
    password, extra verification) it asks the user to finish it in Chrome, then re-checks.
    The password is never logged or reported.
    """

    name = "enter_password"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        env_name = require(params, "value_env")
        field_name = params.get("field_name", "Password")
        button_name = params.get("button_name", "Continue")
        timeout = step_timeout(ctx, params)

        found = find_password_field(ctx, field_name, timeout)
        if found is None:
            ctx.notify("No password prompt appeared; continuing.")
            return
        page, field = found

        password = os.environ.get(env_name)
        if not password:
            raise ActionError(
                f"A password prompt is showing but '{env_name}' is not set; "
                "set it in .env or enter the password in the Chrome window."
            )

        # Click Continue inside the prompt's own dialog, not elsewhere on the page.
        field.fill(password, timeout=timeout)
        try:
            button = field.locator(
                "xpath=ancestor::*[@role='dialog'][1]"
            ).get_by_role("button", name=button_name, exact=True)
            if not button.count():
                raise ActionError(f"No '{button_name}' button found inside the password prompt")
            button.click(timeout=timeout)
        except PlaywrightError:
            if not page.is_closed():  # a popup that closes itself on success is fine
                raise

        if params.get("wait_for_close") is False:
            return  # the caller has its own success signal (e.g. a download starting)
        try:
            field.wait_for(state="hidden", timeout=timeout)
        except PlaywrightError:
            if page.is_closed():
                return
            ctx.notify(
                "The password prompt is still open (wrong password or another check). "
                "Finish it in the Chrome window; the script will not retry."
            )
            ctx.wait_for_user("Press Enter here once the prompt is gone... ")
            if _still_visible(page, field):
                raise ActionError("Password prompt is still open; stopping without retrying") from None


@register_action
class SetCheckboxes(Action):
    """set_checkboxes: {selector (container), checked: [names], only?, timeout?}.

    Makes the named checkboxes ticked. With `only` (the default) every other checkbox in the
    container is first cleared, so the end state is exactly the listed set no matter what the
    page started with. Safe to re-run: boxes already in the right state are never flipped
    back. Names must match the checkbox's accessible name exactly.
    """

    name = "set_checkboxes"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        wanted = params.get("checked")
        if (
            not isinstance(wanted, list)
            or not wanted
            or not all(isinstance(n, str) and n for n in wanted)
            or len(set(wanted)) != len(wanted)
        ):
            raise ActionError("'checked' must be a non-empty list of unique checkbox names")
        only = params.get("only", True)
        if not isinstance(only, bool):
            raise ActionError("'only' must be true or false")
        timeout = step_timeout(ctx, params)
        scope = find(ctx, params)

        if only:
            ticked = scope.get_by_role("checkbox", checked=True)
            for _ in range(200):  # re-query each time: the matching set shrinks as we clear
                if ticked.count() == 0:
                    break
                ticked.first.set_checked(False, timeout=timeout)
            else:
                raise ActionError("Could not clear the existing checkboxes")

        for name in wanted:
            box = scope.get_by_role("checkbox", name=name, exact=True)
            try:
                box.wait_for(state="attached", timeout=timeout)
                box.set_checked(True, timeout=timeout)
            except PlaywrightError as exc:
                if "strict mode violation" in str(exc):
                    raise ActionError(f"More than one checkbox is named '{name}'") from exc
                raise ActionError(f"Could not tick checkbox '{name}'") from exc

        for name in wanted:
            if not scope.get_by_role("checkbox", name=name, exact=True).is_checked():
                raise ActionError(f"Checkbox '{name}' is not ticked after setting it")
        if only:
            extra = scope.get_by_role("checkbox", checked=True).count() - len(wanted)
            if extra:
                raise ActionError(f"{extra} unexpected checkbox(es) are still ticked")
        ctx.notify(f"Checkboxes set: {', '.join(wanted)}")
