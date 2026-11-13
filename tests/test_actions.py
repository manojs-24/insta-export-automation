import pytest

from web_automation.actions import get_action, registered_actions
from web_automation.actions.locators import build_locator
from web_automation.actions.downloads import safe_filename
from web_automation.engine import ExecutionContext
from web_automation.utils.exceptions import (
    ActionError,
    SelectorError,
    UnknownActionError,
    UserDeclinedError,
)

pytestmark = pytest.mark.browser


def run(ctx: ExecutionContext, action: str, **params) -> None:
    get_action(action).run(ctx, params)


def test_all_readme_actions_registered() -> None:
    expected = {
        "navigate", "click", "fill", "select", "wait", "extract_text",
        "extract_attribute", "pause_for_user", "confirm",
    }
    assert expected <= set(registered_actions())


def test_unknown_action() -> None:
    with pytest.raises(UnknownActionError):
        get_action("hack")


def test_navigate(ctx: ExecutionContext, test_site: str) -> None:
    run(ctx, "navigate", url=test_site.replace("index", "other"))
    assert ctx.page.title() == "Other"


def test_navigate_rejects_non_http(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError):
        run(ctx, "navigate", url="file:///etc/passwd")
    with pytest.raises(ActionError):
        run(ctx, "navigate", url="javascript:alert(1)")


@pytest.mark.parametrize(
    "selector",
    [
        {"type": "role", "role": "button", "name": "Go"},
        {"type": "text", "value": "Go", "exact": True},
        {"type": "css", "value": "#go"},
        {"type": "xpath", "value": "//button[@id='go']"},
    ],
)
def test_click_with_each_selector_type(ctx: ExecutionContext, selector: dict) -> None:
    run(ctx, "click", selector=selector)
    assert ctx.page.locator("#greeting").inner_text() == "Clicked"


def test_click_missing_element_fails_clearly(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="not visible within"):
        run(ctx, "click", selector={"type": "css", "value": "#nope"}, timeout=300)


def test_click_disabled_element_refused(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="disabled"):
        run(ctx, "click", selector={"type": "role", "role": "button", "name": "Locked"})


def test_ambiguous_selector_does_not_pick_one(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="more than one"):
        run(ctx, "click", selector={"type": "css", "value": ".dup"})


def test_fill_by_label_and_placeholder(ctx: ExecutionContext) -> None:
    run(ctx, "fill", selector={"type": "label", "value": "Email"}, value="a@b.c")
    assert ctx.page.locator("#email").input_value() == "a@b.c"
    run(ctx, "fill", selector={"type": "placeholder", "value": "you@example.com"}, value="x@y.z")
    assert ctx.page.locator("#email").input_value() == "x@y.z"


def test_fill_value_from_env(ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_TEST_SECRET", "from-env")
    run(ctx, "fill", selector={"type": "css", "value": "#email"}, value_env="WA_TEST_SECRET")
    assert ctx.page.locator("#email").input_value() == "from-env"


def test_fill_requires_exactly_one_source(ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sel = {"type": "css", "value": "#email"}
    with pytest.raises(ActionError):
        run(ctx, "fill", selector=sel)
    with pytest.raises(ActionError):
        run(ctx, "fill", selector=sel, value="a", value_env="X")
    monkeypatch.delenv("WA_TEST_UNSET", raising=False)
    with pytest.raises(ActionError, match="not set"):
        run(ctx, "fill", selector=sel, value_env="WA_TEST_UNSET")


def test_select_option(ctx: ExecutionContext) -> None:
    sel = {"type": "css", "value": "#colour"}
    run(ctx, "select", selector=sel, label="Green")
    assert ctx.page.locator("#colour").input_value() == "g"
    run(ctx, "select", selector=sel, value="r")
    assert ctx.page.locator("#colour").input_value() == "r"
    with pytest.raises(ActionError):
        run(ctx, "select", selector=sel)


def test_extract_text_and_attribute(ctx: ExecutionContext) -> None:
    run(ctx, "extract_text", selector={"type": "css", "value": "#greeting"}, save_as="g")
    run(ctx, "extract_attribute", selector={"type": "css", "value": "#link"}, attribute="data-id", save_as="i")
    assert ctx.variables == {"g": "Hello automation", "i": "42"}
    with pytest.raises(ActionError, match="no attribute"):
        run(ctx, "extract_attribute", selector={"type": "css", "value": "#link"}, attribute="nope", save_as="z")


def test_wait_for_late_element(ctx: ExecutionContext) -> None:
    run(ctx, "click", selector={"type": "role", "role": "button", "name": "Load late"})
    run(ctx, "wait", selector={"type": "css", "value": "#late"})
    assert ctx.page.locator("#late").inner_text() == "Arrived"


def test_wait_variants(ctx: ExecutionContext) -> None:
    run(ctx, "wait", url_contains="index.html")
    run(ctx, "wait", load_state="load")
    run(ctx, "wait", ms=50)
    with pytest.raises(ActionError):
        run(ctx, "wait")  # no condition
    with pytest.raises(ActionError):
        run(ctx, "wait", ms=50, load_state="load")  # two conditions
    with pytest.raises(ActionError):
        run(ctx, "wait", ms=10**9)


def test_wait_times_out(ctx: ExecutionContext) -> None:
    with pytest.raises(Exception):
        run(ctx, "wait", url_contains="never-appears", timeout=300)


def test_safe_filename() -> None:
    assert safe_filename("..\\..\\evil name?.png") == "evil_name_.png"
    assert safe_filename("") == "download"


def test_pause_and_confirm(ctx: ExecutionContext) -> None:
    seen: list[str] = []
    ctx.notify = seen.append
    run(ctx, "pause_for_user", message="Enter the OTP")
    assert seen == ["Enter the OTP"]
    ctx.confirm_with_user = lambda message: False
    with pytest.raises(UserDeclinedError):
        run(ctx, "confirm", message="Submit?")


@pytest.mark.parametrize(
    "spec", [None, {}, {"type": "magic", "value": "x"}, {"type": "css"}, {"type": "role"}]
)
def test_bad_selectors_rejected(ctx: ExecutionContext, spec) -> None:
    with pytest.raises(SelectorError):
        build_locator(ctx.page, spec)


def test_verify_checks_text_case_insensitively(ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sel = {"type": "css", "value": "#greeting"}
    run(ctx, "verify", selector=sel, contains="hello AUTOMATION")
    with pytest.raises(ActionError, match="Verification failed"):
        run(ctx, "verify", selector=sel, contains="Goodbye", timeout=300)
    monkeypatch.setenv("WA_TEST_EXPECT", "automation")
    run(ctx, "verify", selector=sel, contains_env="WA_TEST_EXPECT")
    monkeypatch.delenv("WA_TEST_EXPECT")
    with pytest.raises(ActionError, match="not set"):
        run(ctx, "verify", selector=sel, contains_env="WA_TEST_EXPECT")
    with pytest.raises(ActionError):
        run(ctx, "verify", selector=sel)


def test_ensure_login_noop_when_no_login_page(ctx: ExecutionContext) -> None:
    run(ctx, "ensure_login", timeout_s=5)  # test page has no login form


def test_role_starts_with_avoids_substring_collisions(ctx: ExecutionContext) -> None:
    # "Customise information" contains "format", so a plain substring name matches two buttons.
    plain = {"type": "role", "role": "button", "name": "Format"}
    with pytest.raises(ActionError, match="more than one"):
        run(ctx, "click", selector=plain)
    run(ctx, "click", selector={**plain, "starts_with": True})
    assert ctx.page.locator("#greeting").inner_text() == "Format chosen"


def test_fill_submit_presses_enter(ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_TEST_SECRET", "abcdef")
    run(ctx, "fill", selector={"type": "label", "value": "Secret"}, value_env="WA_TEST_SECRET", submit=True)
    assert ctx.page.locator("#greeting").inner_text() == "Submitted:6"


def test_optional_fill_skips_when_field_absent(ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WA_TEST_UNSET", raising=False)  # not even needed when the field is absent
    seen: list[str] = []
    ctx.notify = seen.append
    run(ctx, "fill", selector={"type": "css", "value": "#nope"}, value_env="WA_TEST_UNSET", optional=True, timeout=300)
    assert seen and "skipping" in seen[0]
    with pytest.raises(ActionError):  # same step without optional still fails
        run(ctx, "fill", selector={"type": "css", "value": "#nope"}, value_env="WA_TEST_UNSET", timeout=300)


FIELDS = {"type": "role", "role": "group", "name": "Fields"}


def ticked(ctx: ExecutionContext) -> list[str]:
    boxes = ctx.page.get_by_role("group", name="Fields").get_by_role("checkbox")
    return [n for n in ("Alpha", "Beta", "Gamma", "Delta")
            if ctx.page.get_by_role("checkbox", name=n, exact=True).is_checked()] if boxes.count() == 4 else []


def test_set_checkboxes_ends_with_exactly_the_listed_set(ctx: ExecutionContext) -> None:
    assert ticked(ctx) == ["Beta", "Delta"]  # page starts with Beta + Delta ticked
    run(ctx, "set_checkboxes", selector=FIELDS, checked=["Alpha", "Beta"])
    assert ticked(ctx) == ["Alpha", "Beta"]  # Delta cleared, Alpha ticked, Beta untouched


def test_set_checkboxes_is_idempotent(ctx: ExecutionContext) -> None:
    for _ in range(2):  # a naive click would flip the boxes back on the second run
        run(ctx, "set_checkboxes", selector=FIELDS, checked=["Gamma"])
        assert ticked(ctx) == ["Gamma"]


def test_set_checkboxes_only_false_keeps_others(ctx: ExecutionContext) -> None:
    run(ctx, "set_checkboxes", selector=FIELDS, checked=["Alpha"], only=False)
    assert ticked(ctx) == ["Alpha", "Beta", "Delta"]


def test_set_checkboxes_rejects_bad_input(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="not found|Could not tick"):
        run(ctx, "set_checkboxes", selector=FIELDS, checked=["Nonexistent"], timeout=500)
    for bad in ([], ["Alpha", "Alpha"], "Alpha", [""]):
        with pytest.raises(ActionError, match="non-empty list"):
            run(ctx, "set_checkboxes", selector=FIELDS, checked=bad)
    with pytest.raises(ActionError, match="'only'"):
        run(ctx, "set_checkboxes", selector=FIELDS, checked=["Alpha"], only="yes")


def test_verify_waits_for_text_that_changes_late(ctx: ExecutionContext) -> None:
    sel = {"type": "css", "value": "#greeting"}
    run(ctx, "click", selector={"type": "role", "role": "button", "name": "Settle"})
    # the text only changes ~600 ms after the click; a single immediate read would fail
    run(ctx, "verify", selector=sel, contains="Settled", timeout=3000)


def test_verify_failure_message_shows_what_the_page_said(ctx: ExecutionContext) -> None:
    sel = {"type": "css", "value": "#greeting"}
    with pytest.raises(ActionError, match="Element text was: 'Hello automation'"):
        run(ctx, "verify", selector=sel, contains="Never appears", timeout=400)


POPUP_SAVE = {
    "type": "role", "role": "button", "name": "Save", "exact": True,
    "within_text": "Save your login info to Instagram?",
}


def test_click_if_visible_clicks_the_popup_button_not_the_feed_one(ctx: ExecutionContext) -> None:
    run(ctx, "click_if_visible", selector=POPUP_SAVE, timeout=2000)
    assert ctx.page.locator("#greeting").inner_text() == "Info saved"  # not 'Wrong save (feed post)'


def test_plain_save_selector_is_ambiguous_without_scoping(ctx: ExecutionContext) -> None:
    plain = {"type": "role", "role": "button", "name": "Save", "exact": True}
    with pytest.raises(ActionError, match="more than one"):
        run(ctx, "click", selector=plain, timeout=500)


def test_within_text_skips_when_popup_absent(ctx: ExecutionContext) -> None:
    ctx.notify = lambda message: None
    gone = {**POPUP_SAVE, "within_text": "A popup that is not on the page"}
    run(ctx, "click_if_visible", selector=gone, timeout=400)
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # nothing clicked


def test_within_text_rejects_xpath(ctx: ExecutionContext) -> None:
    with pytest.raises(SelectorError, match="xpath"):
        build_locator(ctx.page, {"type": "xpath", "value": "//button", "within_text": "x"})


def test_click_if_visible_skips_when_absent(ctx: ExecutionContext) -> None:
    seen: list[str] = []
    ctx.notify = seen.append
    run(ctx, "click_if_visible", selector={"type": "role", "role": "button", "name": "No such popup"}, timeout=300)
    assert seen == ["Optional element not shown; skipping."]
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # nothing else was clicked


def test_click_if_visible_still_rejects_ambiguous_and_disabled(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="more than one"):
        run(ctx, "click_if_visible", selector={"type": "css", "value": ".dup"}, timeout=500)
    with pytest.raises(ActionError, match="disabled"):
        run(ctx, "click_if_visible", selector={"type": "role", "role": "button", "name": "Locked"}, timeout=500)
