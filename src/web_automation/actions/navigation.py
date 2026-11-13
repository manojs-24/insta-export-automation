from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from web_automation.actions.base import RETRY_ANY, Action, register_action, require, step_timeout
from web_automation.utils.exceptions import ActionError

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

_WAIT_UNTIL = ("commit", "domcontentloaded", "load", "networkidle")


@register_action
class Navigate(Action):
    """navigate: {url, wait_until?, timeout?}. Only http(s) URLs are allowed."""

    name = "navigate"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        url = require(params, "url")
        if urlsplit(url).scheme not in ("http", "https"):
            raise ActionError("navigate only supports http:// and https:// URLs")
        wait_until = params.get("wait_until", "load")
        if wait_until not in _WAIT_UNTIL:
            raise ActionError(f"'wait_until' must be one of {', '.join(_WAIT_UNTIL)}")
        ctx.page.goto(url, wait_until=wait_until, timeout=step_timeout(ctx, params))
