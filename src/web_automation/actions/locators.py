"""Turn a JSON selector spec into a Playwright Locator.

Supported types follow the README's preference order: role, label, placeholder, text,
css, xpath. Specs are data only; nothing here evaluates JavaScript.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from web_automation.actions.base import step_timeout
from web_automation.utils.exceptions import ActionError, ElementNotFoundError, SelectorError

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

SELECTOR_TYPES = ("role", "label", "placeholder", "text", "css", "xpath")


def _need(spec: dict[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value:
        raise SelectorError(f"Selector of type '{spec.get('type')}' needs a non-empty '{key}'")
    return value


def _build(page: Page | Locator, spec: Any) -> Locator:
    if not isinstance(spec, dict) or "type" not in spec:
        raise SelectorError(f"Selector must be an object with a 'type': {spec!r}")
    kind, exact = spec["type"], bool(spec.get("exact", False))
    if kind == "role":
        role, name = _need(spec, "role"), spec.get("name")
        if name is None:
            return page.get_by_role(role)  # type: ignore[arg-type]
        if spec.get("starts_with"):
            if exact:
                raise SelectorError("'exact' and 'starts_with' cannot be combined")
            # escaped, so the JSON value is always literal text, never a regex
            pattern = re.compile("^" + re.escape(name), re.IGNORECASE)
            return page.get_by_role(role, name=pattern)  # type: ignore[arg-type]
        return page.get_by_role(role, name=name, exact=exact)  # type: ignore[arg-type]
    if kind == "label":
        return page.get_by_label(_need(spec, "value"), exact=exact)
    if kind == "placeholder":
        return page.get_by_placeholder(_need(spec, "value"), exact=exact)
    if kind == "text":
        return page.get_by_text(_need(spec, "value"), exact=exact)
    if kind == "css":
        return page.locator(_need(spec, "value"))
    if kind == "xpath":
        return page.locator("xpath=" + _need(spec, "value"))
    raise SelectorError(f"Unsupported selector type '{kind}'. Supported: {', '.join(SELECTOR_TYPES)}")


def build_locator(page: Page, spec: Any) -> Locator:
    """Locator for `spec`.

    With `within_text`, only the smallest <div> that contains both that text and the target
    is searched. A popup's "Save" button is then never confused with a "Save" button elsewhere
    on the page (e.g. on feed posts behind the popup).
    """
    if isinstance(spec, dict) and spec.get("within_text") is not None:
        text = _need(spec, "within_text")
        if spec.get("type") == "xpath":
            raise SelectorError("'within_text' is not supported with xpath selectors")
        inner = {key: value for key, value in spec.items() if key != "within_text"}
        container = (
            page.locator("div")
            .filter(has=page.get_by_text(text))
            .filter(has=_build(page, inner))
            .last  # document order lists ancestors first, so the last match is the innermost
        )
        return _build(container, inner)
    return _build(page, spec)


def find(
    ctx: "ExecutionContext",
    params: dict[str, Any],
    state: Literal["attached", "detached", "hidden", "visible"] = "visible",
) -> Locator:
    """Locate the step's element and wait for `state`. Never falls back to another element."""
    spec = params.get("selector")
    locator = build_locator(ctx.page, spec)
    timeout = step_timeout(ctx, params)
    try:
        locator.wait_for(state=state, timeout=timeout)
    except PlaywrightError as exc:
        description = json.dumps(spec, sort_keys=True)
        if "strict mode violation" in str(exc):
            raise ActionError(
                f"Selector matches more than one element; make it more specific: {description}"
            ) from exc
        raise ElementNotFoundError(f"Element not {state} within {timeout}ms: {description}") from exc
    return locator
