import json
from pathlib import Path

import pytest

from web_automation.actions import get_action
from web_automation.config import load_config, parse_config
from web_automation.engine import ExecutionContext
from web_automation.engine.runner import run_config
from web_automation.utils.exceptions import ActionError, WorkflowStopped

pytestmark = pytest.mark.browser

REQUESTED = {"type": "role", "role": "heading", "name": "Requested", "exact": True}
MESSAGE = "An export request is already pending."


def workflow(site: str, query: str = "") -> dict:
    return {
        "name": "guard_demo",
        "steps": [
            {"id": "open", "action": "navigate", "url": site + query},
            {"id": "guard", "action": "abort_if_visible", "selector": REQUESTED, "timeout": 3000, "message": MESSAGE},
            {"id": "create", "action": "click", "selector": {"type": "role", "role": "button", "name": "Create export"}},
            {"id": "done", "action": "wait", "ms": 10},
        ],
    }


def test_pending_request_stops_before_anything_is_created(ctx: ExecutionContext, pending_site: str) -> None:
    result = run_config(parse_config(workflow(pending_site, "?pending")), ctx)
    assert result.status == "stopped" and result.stop_reason == MESSAGE
    assert [s.status for s in result.steps] == ["ok", "stopped", "skipped", "skipped"]
    assert ctx.page.locator("#created").inner_text() == ""  # Create export was never clicked
    assert result.to_dict()["stop_reason"] == MESSAGE


def test_no_pending_request_lets_the_flow_continue(ctx: ExecutionContext, pending_site: str) -> None:
    result = run_config(parse_config(workflow(pending_site)), ctx)
    assert result.status == "success" and result.stop_reason is None
    assert ctx.page.locator("#created").inner_text() == "created"


def test_stop_is_not_overridden_by_continue_policy(ctx: ExecutionContext, pending_site: str) -> None:
    data = workflow(pending_site, "?pending")
    data["execution"] = {"on_failure": "continue"}
    result = run_config(parse_config(data), ctx)
    assert result.status == "stopped"
    assert [s.status for s in result.steps][2:] == ["skipped", "skipped"]


def test_guard_waits_for_a_late_loading_list(ctx: ExecutionContext, pending_site: str) -> None:
    ctx.page.goto(pending_site + "?pending")  # heading appears ~700 ms after load
    with pytest.raises(WorkflowStopped, match="already pending"):
        get_action("abort_if_visible").run(ctx, {"selector": REQUESTED, "timeout": 3000, "message": MESSAGE})


def test_guard_requires_message(ctx: ExecutionContext) -> None:
    with pytest.raises(ActionError):
        get_action("abort_if_visible").run(ctx, {"selector": REQUESTED, "timeout": 300})


def test_cli_exit_code_3_when_stopped(browser_config, pending_site: str, tmp_path: Path, monkeypatch, capsys) -> None:
    from web_automation.main import main

    monkeypatch.setenv("WA_PROFILE_ROOT", str(browser_config.profile_root))
    monkeypatch.setenv("WA_PROFILE", "cli")
    monkeypatch.setenv("WA_HEADLESS", "true")
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "pending.json"
    path.write_text(json.dumps(workflow(pending_site, "?pending")), encoding="utf-8")
    assert main(["--config", str(path)]) == 3
    assert "STOPPED (nothing was submitted)" in capsys.readouterr().out


def test_instagram_guard_runs_before_anything_is_created() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert config.steps[ids.index("no_pending_request")].action == "abort_if_visible"
    assert ids.index("no_pending_request") < ids.index("create_export") < ids.index("start_export")
