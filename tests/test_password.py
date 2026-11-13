import pytest

from web_automation.actions import get_action
from web_automation.config import parse_config
from web_automation.engine import ExecutionContext
from web_automation.utils.exceptions import ActionError

pytestmark = pytest.mark.browser


def open_prompt(ctx: ExecutionContext, site: str) -> None:
    ctx.page.goto(site)
    ctx.page.get_by_role("button", name="Start").click()


def enter(ctx: ExecutionContext, **params) -> None:
    get_action("enter_password").run(ctx, {"value_env": "WA_TEST_PW", **params})


def test_password_entered_and_continue_clicked(ctx: ExecutionContext, password_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_TEST_PW", "right-pass")
    open_prompt(ctx, password_site)
    enter(ctx, timeout=3000)
    assert ctx.page.locator("#state").inner_text() == "accepted"
    assert not ctx.page.get_by_role("textbox", name="Password").is_visible()


def test_skipped_when_no_prompt(ctx: ExecutionContext, password_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WA_TEST_PW", raising=False)  # not needed when nothing is asked
    seen: list[str] = []
    ctx.notify = seen.append
    ctx.page.goto(password_site)  # prompt never opened
    enter(ctx, timeout=500)
    assert seen == ["No password prompt appeared; continuing."]


def test_missing_env_var_fails_when_prompt_is_showing(ctx: ExecutionContext, password_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WA_TEST_PW", raising=False)
    open_prompt(ctx, password_site)
    with pytest.raises(ActionError, match="not set"):
        enter(ctx, timeout=2000)


def test_wrong_password_is_not_retried(ctx: ExecutionContext, password_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_TEST_PW", "wrong")
    open_prompt(ctx, password_site)
    calls: list[str] = []
    ctx.notify = lambda m: None
    ctx.wait_for_user = calls.append  # user "presses Enter" without fixing anything
    with pytest.raises(ActionError, match="without retrying"):
        enter(ctx, timeout=1500)
    assert len(calls) == 1
    assert ctx.page.locator("#state").inner_text() == "rejected"  # submitted exactly once


def test_user_can_finish_after_wrong_password(ctx: ExecutionContext, password_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_TEST_PW", "wrong")
    open_prompt(ctx, password_site)
    ctx.notify = lambda m: None

    def user_fixes_it(message: str) -> None:
        ctx.page.get_by_role("textbox", name="Password").fill("right-pass")
        ctx.page.get_by_role("button", name="Continue").click()

    ctx.wait_for_user = user_fixes_it
    enter(ctx, timeout=1500)  # no error: the user completed the prompt


def test_schema_requires_env_and_has_action() -> None:
    base = {"name": "p", "steps": [{"id": "pw", "action": "enter_password", "value_env": "WA_IG_PASSWORD"}]}
    assert parse_config(base).steps[0].value_env == "WA_IG_PASSWORD"
    from web_automation.utils.exceptions import ConfigError

    for bad in ({"id": "pw", "action": "enter_password"}, {"id": "pw", "action": "enter_password", "value": "hunter2"}):
        with pytest.raises(ConfigError) as info:
            parse_config({"name": "p", "steps": [bad]})
        assert "hunter2" not in str(info.value)
