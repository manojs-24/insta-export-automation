from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError

from web_automation.actions.base import RETRY_ANY, RETRY_NEVER, Action, register_action, require, step_timeout
from web_automation.actions.locators import build_locator, find
from web_automation.utils.exceptions import ActionError, WorkflowStopped

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

_CONDITIONS = ("selector", "url_contains", "load_state", "ms")
_ELEMENT_STATES = ("attached", "detached", "hidden", "visible")
_LOAD_STATES = ("domcontentloaded", "load", "networkidle")


@register_action
class Wait(Action):
    """wait: exactly one of
    {selector, state?}  element reaches state (default visible)
    {url_contains}      current URL contains the text
    {load_state}        domcontentloaded | load | networkidle
    {ms}                fixed pause (last resort)
    Every form accepts an optional `timeout`.
    """

    name = "wait"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        given = [key for key in _CONDITIONS if key in params]
        if len(given) != 1:
            raise ActionError(f"wait needs exactly one of: {', '.join(_CONDITIONS)}")
        timeout = step_timeout(ctx, params)
        kind = given[0]
        if kind == "selector":
            state = params.get("state", "visible")
            if state not in _ELEMENT_STATES:
                raise ActionError(f"'state' must be one of {', '.join(_ELEMENT_STATES)}")
            find(ctx, params, state=state)
        elif kind == "url_contains":
            text = params["url_contains"]
            if not isinstance(text, str) or not text:
                raise ActionError("'url_contains' must be a non-empty string")
            ctx.page.wait_for_url(lambda url: text in url, timeout=timeout)
        elif kind == "load_state":
            state = params["load_state"]
            if state not in _LOAD_STATES:
                raise ActionError(f"'load_state' must be one of {', '.join(_LOAD_STATES)}")
            ctx.page.wait_for_load_state(state, timeout=timeout)
        else:
            ms = params["ms"]
            if not isinstance(ms, int) or isinstance(ms, bool) or not 0 < ms <= 60_000:
                raise ActionError("'ms' must be an integer between 1 and 60000")
            ctx.page.wait_for_timeout(ms)


@register_action
class AbortIfVisible(Action):
    """abort_if_visible: {selector, message, timeout?}.

    Waits up to `timeout` for the element to appear. If it does, the workflow stops cleanly
    with `message` (status "stopped", not "failed"). If it never appears, the step passes.
    Use it as a guard, e.g. "an export request is already pending".
    """

    name = "abort_if_visible"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        message = require(params, "message")
        locator = build_locator(ctx.page, params.get("selector"))
        try:
            locator.first.wait_for(state="visible", timeout=step_timeout(ctx, params))
        except PlaywrightError:
            return  # not found within the timeout: safe to continue
        raise WorkflowStopped(message)
