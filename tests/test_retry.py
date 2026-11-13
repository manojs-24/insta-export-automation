import time
from pathlib import Path

import pytest

from web_automation.actions import Action, Step, register_action, registered_actions
from web_automation.actions.base import (
    _REGISTRY,
    RETRY_ANY,
    RETRY_NEVER,
    RETRY_PRE_ACTION,
    get_action,
    retry_settings,
    validate_retry,
)
from web_automation.config import load_config, parse_config
from web_automation.engine import ExecutionContext, Executor
from web_automation.utils.exceptions import (
    ActionError,
    ConfigError,
    ElementNotFoundError,
    UserDeclinedError,
    WorkflowStopped,
)

pytestmark = pytest.mark.browser

RETRY = {"max_attempts": 3, "delay_ms": 0}
LATE_BUTTON = {"type": "role", "role": "button", "name": "Load late"}


# ---------------------------------------------------------------- the policy table
def test_every_action_declares_a_policy_and_dangerous_ones_never_retry() -> None:
    policies = {name: get_action(name).retry_policy for name in registered_actions()}
    assert all(p in (RETRY_NEVER, RETRY_PRE_ACTION, RETRY_ANY) for p in policies.values())
    # Anything that submits, enters a secret, downloads, or writes to the database is never repeated.
    for name in ("enter_password", "ensure_login", "download_latest_export", "extract_from_zip",
                 "sync_followers", "confirm", "pause_for_user", "abort_if_visible", "click_if_visible"):
        assert policies[name] == RETRY_NEVER, name
    # A click may only be repeated when the element was never found (nothing clicked yet).
    assert policies["click"] == policies["fill"] == policies["select"] == RETRY_PRE_ACTION
    assert policies["navigate"] == policies["wait"] == policies["verify"] == RETRY_ANY


def test_new_actions_default_to_never_retry() -> None:
    class Fresh(Action):
        name = "fresh_test"

        def run(self, ctx, params) -> None: ...

    assert Fresh.retry_policy == RETRY_NEVER


@pytest.mark.parametrize(
    "bad",
    [{}, {"max_attempts": 1}, {"max_attempts": 6}, {"max_attempts": "3"}, {"max_attempts": True},
     {"max_attempts": 3, "delay_ms": -1}, {"max_attempts": 3, "typo": 1}, "x", 3],
)
def test_invalid_retry_settings_rejected(bad) -> None:
    with pytest.raises(ActionError, match="invalid 'retry'"):
        validate_retry("s", "wait", bad)


def test_retry_on_a_never_action_is_rejected_in_both_layers() -> None:
    with pytest.raises(ActionError, match="cannot be retried"):
        validate_retry("s", "enter_password", RETRY)
    config = {"name": "x", "steps": [{"id": "pw", "action": "enter_password", "value_env": "WA_X", "retry": RETRY}]}
    with pytest.raises(ConfigError, match="cannot be retried"):
        parse_config(config)


def test_retry_settings_defaults() -> None:
    assert retry_settings(None) == (1, 0)
    assert retry_settings({"max_attempts": 3}) == (3, 1000)
    assert retry_settings(RETRY) == (3, 0)


# ------------------------------------------------------------------- in the engine
def test_pre_action_click_is_retried_until_the_element_appears(ctx: ExecutionContext) -> None:
    ctx.timeout_ms = 400
    steps = [
        {"id": "start", "action": "click", "selector": LATE_BUTTON},  # makes #late appear after ~600 ms
        {"id": "late", "action": "click", "selector": {"type": "css", "value": "#late"},
         "retry": {"max_attempts": 4, "delay_ms": 300}},
    ]
    result = Executor(ctx).run(steps)
    assert result.status == "success", result.steps
    assert result.steps[1].attempts >= 2  # first attempt found nothing; a later one succeeded


def test_gives_up_after_max_attempts(ctx: ExecutionContext) -> None:
    ctx.timeout_ms = 200
    step = {"id": "never", "action": "click", "selector": {"type": "css", "value": "#nope"},
            "retry": {"max_attempts": 3, "delay_ms": 0}}
    result = Executor(ctx).run([step])
    assert result.status == "failed"
    assert result.steps[0].attempts == 3 and "not visible" in result.steps[0].message


def test_without_retry_a_failing_step_runs_exactly_once(ctx: ExecutionContext) -> None:
    ctx.timeout_ms = 200
    result = Executor(ctx).run([{"id": "x", "action": "click", "selector": {"type": "css", "value": "#nope"}}])
    assert result.steps[0].status == "failed" and result.steps[0].attempts == 1


def test_a_click_that_found_its_element_is_never_repeated(ctx: ExecutionContext) -> None:
    """The element is disabled: it WAS found, so a retry could not be 'before the action'."""
    calls: list[int] = []

    @register_action
    class CountedClick(Action):
        name = "counted_click_test"
        retry_policy = RETRY_PRE_ACTION

        def run(self, ctx, params) -> None:
            calls.append(1)
            raise ActionError("Element is visible but disabled; not clicking")

    try:
        result = Executor(ctx).run([{"id": "c", "action": "counted_click_test", "retry": RETRY}])
        assert result.status == "failed" and len(calls) == 1  # plain ActionError: not retried
    finally:
        _REGISTRY.pop("counted_click_test")


@pytest.mark.parametrize(
    ("policy", "error", "expected_calls"),
    [
        (RETRY_PRE_ACTION, ElementNotFoundError("missing"), 3),  # retried
        (RETRY_PRE_ACTION, ActionError("bad parameter"), 1),
        (RETRY_PRE_ACTION, ZeroDivisionError("bug"), 1),  # not even caught: a real bug
        (RETRY_ANY, ElementNotFoundError("missing"), 3),
        (RETRY_ANY, ActionError("bad parameter"), 1),  # parameter errors never get better
        (RETRY_NEVER, ElementNotFoundError("missing"), 1),
        (RETRY_ANY, WorkflowStopped("guard"), 1),  # a deliberate stop is final
        (RETRY_ANY, UserDeclinedError("no"), 1),
    ],
)
def test_which_failures_are_retried(ctx: ExecutionContext, policy: str, error: Exception, expected_calls: int) -> None:
    calls: list[int] = []

    @register_action
    class Probe(Action):
        name = "probe_test"
        retry_policy = policy

        def run(self, ctx, params) -> None:
            calls.append(1)
            raise error

    try:
        if policy == RETRY_NEVER:  # the engine refuses the retry setting up front
            with pytest.raises(ActionError, match="cannot be retried"):
                Executor(ctx).run([{"id": "p", "action": "probe_test", "retry": RETRY}])
            assert calls == []
            return
        if isinstance(error, ZeroDivisionError):
            with pytest.raises(ZeroDivisionError):
                Executor(ctx).run([{"id": "p", "action": "probe_test", "retry": RETRY}])
        else:
            Executor(ctx).run([{"id": "p", "action": "probe_test", "retry": RETRY}])
        assert len(calls) == expected_calls
    finally:
        _REGISTRY.pop("probe_test")


def test_a_step_that_succeeds_first_time_reports_one_attempt(ctx: ExecutionContext) -> None:
    step = {"id": "ok", "action": "wait", "ms": 50, "retry": RETRY}
    result = Executor(ctx).run([step])
    assert result.steps[0].status == "ok" and result.steps[0].attempts == 1


def test_attempts_are_in_the_report(ctx: ExecutionContext) -> None:
    ctx.timeout_ms = 200
    step = {"id": "never", "action": "click", "selector": {"type": "css", "value": "#nope"}, "retry": RETRY}
    data = Executor(ctx).run([step]).to_dict()
    assert data["steps"][0]["attempts"] == 3


def test_delay_is_applied_between_attempts(ctx: ExecutionContext) -> None:
    ctx.timeout_ms = 100
    step = {"id": "never", "action": "click", "selector": {"type": "css", "value": "#nope"},
            "retry": {"max_attempts": 3, "delay_ms": 400}}
    began = time.monotonic()
    Executor(ctx).run([step])
    assert time.monotonic() - began >= 0.8  # two waits of 400 ms


# ----------------------------------------------------------------- the real workflow
def test_workflow_retries_navigation_but_never_submission_or_secrets() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    by_id = {s.id: s for s in config.steps}
    for step_id in ("open_instagram", "open_more", "open_export", "create_export", "open_format"):
        assert by_id[step_id].retry is not None, step_id
    for step_id in ("start_export", "enter_password", "download_latest_export", "sync_followers",
                    "login_if_needed", "save_customise", "save_date_range", "save_format"):
        assert by_id[step_id].retry is None, f"{step_id} must not be retried"
