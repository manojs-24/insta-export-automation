import pytest

from web_automation.actions import Step
from web_automation.actions.base import condition_met, validate_when
from web_automation.config import load_config, parse_config
from web_automation.engine import ExecutionContext, Executor
from web_automation.engine.runner import run_config
from web_automation.utils.exceptions import ActionError, ConfigError

from pathlib import Path

pytestmark = pytest.mark.browser

SET_RESULT = {"id": "who", "action": "extract_text", "selector": {"type": "css", "value": "#greeting"}, "save_as": "mode"}
CLICK_GO = {"id": "go", "action": "click", "selector": {"type": "css", "value": "#go"}}


def conditional(when: dict) -> dict:
    return {**CLICK_GO, "id": "go_if", "when": when}


def test_condition_logic_pure() -> None:
    assert condition_met({"variable": "v", "equals": "a"}, {"v": "a"}) is True
    assert condition_met({"variable": "v", "equals": "a"}, {"v": "b"}) is False
    assert condition_met({"variable": "v", "equals": "a"}, {}) is False  # unset equals nothing
    assert condition_met({"variable": "v", "not_equals": "a"}, {"v": "b"}) is True
    assert condition_met({"variable": "v", "not_equals": "a"}, {"v": "a"}) is False
    assert condition_met({"variable": "v", "not_equals": "a"}, {}) is True  # unset differs from all


@pytest.mark.parametrize(
    "bad",
    [None, {}, "x", {"variable": "v"}, {"variable": "v", "equals": "a", "not_equals": "b"},
     {"variable": "", "equals": "a"}, {"variable": "v", "equals": 1}, {"variable": "v", "equals": "a", "extra": 1}],
)
def test_invalid_conditions_rejected(bad) -> None:
    with pytest.raises(ActionError, match="invalid 'when'"):
        validate_when("s", bad)


def test_step_runs_when_condition_met(ctx: ExecutionContext) -> None:
    steps = [SET_RESULT, conditional({"variable": "mode", "equals": "Hello automation"})]
    result = Executor(ctx).run(steps)
    assert [s.status for s in result.steps] == ["ok", "ok"]
    assert ctx.page.locator("#greeting").inner_text() == "Clicked"


def test_step_skipped_when_condition_not_met_and_run_continues(ctx: ExecutionContext) -> None:
    steps = [SET_RESULT, conditional({"variable": "mode", "equals": "something else"}),
             {"id": "shot", "action": "wait", "ms": 10}]
    result = Executor(ctx).run(steps)
    assert result.status == "success"  # a skipped step is not a failure
    assert [s.status for s in result.steps] == ["ok", "skipped", "ok"]
    assert "condition on 'mode' not met" in result.steps[1].message
    assert "Hello automation" not in result.steps[1].message  # the value itself is never reported
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # the click never ran


def test_not_equals(ctx: ExecutionContext) -> None:
    result = Executor(ctx).run([SET_RESULT, conditional({"variable": "mode", "not_equals": "Hello automation"})])
    assert [s.status for s in result.steps] == ["ok", "skipped"]


def test_unknown_variable_rejected_before_anything_runs(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="no earlier step sets"):
        Executor(ctx).run([CLICK_GO, conditional({"variable": "never_set", "equals": "x"})])
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # step 1 never ran


def test_variable_must_be_set_by_an_earlier_step_not_a_later_one(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError, match="no earlier step sets"):
        Executor(ctx).run([conditional({"variable": "mode", "equals": "x"}), SET_RESULT])


def test_failure_inside_a_conditional_step_still_fails_the_run(ctx: ExecutionContext) -> None:
    bad = {"id": "bad", "action": "click", "selector": {"type": "css", "value": "#nope"}, "timeout": 300,
           "when": {"variable": "mode", "equals": "Hello automation"}}
    result = Executor(ctx).run([SET_RESULT, bad])
    assert result.status == "failed" and result.steps[1].status == "failed"


def test_schema_accepts_when_and_rejects_bad_ones() -> None:
    base = {"id": "a", "action": "extract_text", "selector": {"type": "css", "value": "#x"}, "save_as": "v"}
    ok = {"id": "b", "action": "wait", "ms": 10, "when": {"variable": "v", "equals": "x"}}
    parsed = parse_config({"name": "w", "steps": [base, ok]})
    assert parsed.to_steps()[1].when == {"variable": "v", "equals": "x"}
    assert "when" not in parsed.to_steps()[1].params  # the condition is not passed to the action
    for bad_when in ({"variable": "v"}, {"variable": "v", "equals": "x", "not_equals": "y"}, {"variable": "v", "typo": "x"}):
        with pytest.raises(ConfigError):
            parse_config({"name": "w", "steps": [base, {**ok, "when": bad_when}]})
    with pytest.raises(ConfigError, match="no earlier step sets"):
        parse_config({"name": "w", "steps": [ok, base]})  # uses 'v' before it exists


def test_ensure_login_stores_the_outcome(ctx: ExecutionContext) -> None:
    from web_automation.actions import get_action

    get_action("ensure_login").run(ctx, {"timeout_s": 5, "save_as": "login_result"})
    assert ctx.variables["login_result"] == "already_authenticated"  # the test page has no login form


def test_popup_steps_only_run_after_a_real_login() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    by_id = {s.id: s for s in config.steps}
    assert by_id["login_if_needed"].save_as == "login_result"
    for step_id in ("save_login_info", "login_popup_closed", "dismiss_notifications_popup", "notifications_popup_closed"):
        when = by_id[step_id].when
        assert when is not None and (when.variable, when.equals) == ("login_result", "logged_in"), step_id
    # steps that must ALWAYS run are not conditional
    for step_id in ("home_loaded", "open_more", "start_export", "no_pending_request"):
        assert by_id[step_id].when is None


def test_workflow_skips_popup_steps_when_already_logged_in(ctx: ExecutionContext, test_site: str) -> None:
    steps = [
        {"id": "open", "action": "navigate", "url": test_site},
        {"id": "login", "action": "ensure_login", "timeout_s": 5, "save_as": "login_result"},
        {"id": "popup", "action": "click_if_visible", "when": {"variable": "login_result", "equals": "logged_in"},
         "selector": {"type": "css", "value": "#go"}, "timeout": 30000},
        {"id": "after", "action": "wait", "ms": 10},
    ]
    import time

    began = time.monotonic()
    result = run_config(parse_config({"name": "skip_demo", "steps": steps}), ctx)
    assert result.status == "success"
    assert [s.status for s in result.steps] == ["ok", "ok", "skipped", "ok"]
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # popup step never clicked
    assert time.monotonic() - began < 20  # the skipped step did not wait out its 30 s timeout
