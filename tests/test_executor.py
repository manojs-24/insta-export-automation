import pytest

from web_automation.actions import Action, Step, register_action
from web_automation.actions.base import _REGISTRY
from web_automation.engine import ExecutionContext, Executor
from web_automation.utils.exceptions import ActionError, UnknownActionError

pytestmark = pytest.mark.browser

CLICK_GO = {"id": "go", "action": "click", "selector": {"type": "css", "value": "#go"}}
MISSING = {"id": "bad", "action": "click", "selector": {"type": "css", "value": "#nope"}, "timeout": 300}
READ = {"id": "read", "action": "extract_text", "selector": {"type": "css", "value": "#greeting"}, "save_as": "g"}


def test_runs_steps_in_order(ctx: ExecutionContext) -> None:
    result = Executor(ctx).run([CLICK_GO, READ], name="demo")
    assert result.status == "success"
    assert [s.id for s in result.steps] == ["go", "read"]
    assert result.steps_completed == 2 and result.steps_failed == 0
    assert ctx.variables["g"] == "Clicked"  # click ran before the read


def test_stop_on_failure_skips_the_rest(ctx: ExecutionContext) -> None:
    result = Executor(ctx).run([MISSING, CLICK_GO])
    assert result.status == "failed"
    assert [s.status for s in result.steps] == ["failed", "skipped"]
    assert "not visible" in result.steps[0].message
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"  # CLICK_GO never ran


def test_continue_on_failure(ctx: ExecutionContext) -> None:
    result = Executor(ctx).run([MISSING, CLICK_GO], on_failure="continue")
    assert [s.status for s in result.steps] == ["failed", "ok"]
    assert result.status == "failed"


def test_declined_confirm_always_stops(ctx: ExecutionContext) -> None:
    ctx.confirm_with_user = lambda message: False
    confirm = {"id": "ok", "action": "confirm", "message": "Submit?"}
    result = Executor(ctx).run([confirm, CLICK_GO], on_failure="continue")
    assert [s.status for s in result.steps] == ["failed", "skipped"]


def test_validation_happens_before_any_step_runs(ctx: ExecutionContext) -> None:
    with pytest.raises(UnknownActionError):
        Executor(ctx).run([CLICK_GO, {"id": "x", "action": "teleport"}])
    assert ctx.page.locator("#greeting").inner_text() == "Hello automation"
    with pytest.raises(ActionError, match="Duplicate"):
        Executor(ctx).run([CLICK_GO, CLICK_GO])
    with pytest.raises(ActionError, match="id"):
        Executor(ctx).run([{"action": "click"}])


def test_result_is_serialisable_and_hides_values(ctx: ExecutionContext) -> None:
    data = Executor(ctx).run([CLICK_GO, READ], name="demo").to_dict()
    assert data["automation"] == "demo" and data["steps_completed"] == 2
    assert "Clicked" not in str(data)  # extracted values are never put in the report


def test_new_action_can_be_registered(ctx: ExecutionContext) -> None:
    @register_action
    class Hello(Action):
        name = "hello_test"

        def run(self, ctx, params) -> None:
            ctx.variables["hello"] = params["who"]

    try:
        result = Executor(ctx).run([Step("h", "hello_test", {"who": "world"})])
        assert result.status == "success" and ctx.variables["hello"] == "world"
    finally:
        _REGISTRY.pop("hello_test")


def test_unexpected_bug_in_action_is_not_swallowed(ctx: ExecutionContext) -> None:
    @register_action
    class Buggy(Action):
        name = "buggy_test"

        def run(self, ctx, params) -> None:
            raise ZeroDivisionError("bug")

    try:
        with pytest.raises(ZeroDivisionError):
            Executor(ctx).run([Step("b", "buggy_test")])
    finally:
        _REGISTRY.pop("buggy_test")
