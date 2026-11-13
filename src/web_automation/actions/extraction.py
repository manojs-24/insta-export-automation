from typing import TYPE_CHECKING, Any

from web_automation.actions.base import RETRY_ANY, Action, register_action, require
from web_automation.actions.locators import find
from web_automation.utils.exceptions import ActionError
from web_automation.utils.logger import get_logger

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

log = get_logger("extraction")


@register_action
class ExtractText(Action):
    """extract_text: {selector, save_as}. Stores the element's visible text in variables."""

    name = "extract_text"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        save_as = require(params, "save_as")
        ctx.variables[save_as] = find(ctx, params).inner_text().strip()
        log.info("Extracted text into variable '%s'", save_as)  # value deliberately not logged


@register_action
class ExtractAttribute(Action):
    """extract_attribute: {selector, attribute, save_as}. Fails if the attribute is absent."""

    name = "extract_attribute"
    retry_policy = RETRY_ANY

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        attribute, save_as = require(params, "attribute"), require(params, "save_as")
        value = find(ctx, params).get_attribute(attribute)
        if value is None:
            raise ActionError(f"Element has no attribute '{attribute}'")
        ctx.variables[save_as] = value
        log.info("Extracted attribute '%s' into variable '%s'", attribute, save_as)
