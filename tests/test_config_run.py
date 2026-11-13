import json
from pathlib import Path

import pytest

from web_automation.config import parse_config
from web_automation.engine import ExecutionContext
from web_automation.engine.runner import run_config

pytestmark = pytest.mark.browser


def make_config(site: str, extra_steps: list[dict], **execution: object) -> dict:
    return {
        "name": "local_run",
        "steps": [
            {"id": "open", "action": "navigate", "url": site},
            {"id": "go", "action": "click", "selector": {"type": "role", "role": "button", "name": "Go"}},
            {"id": "read", "action": "extract_text", "selector": {"type": "css", "value": "#greeting"}, "save_as": "g"},
            *extra_steps,
        ],
        "execution": execution,
    }


def test_json_config_runs_end_to_end(ctx: ExecutionContext, test_site: str) -> None:
    shot = {"id": "shot", "action": "wait", "ms": 10}
    result = run_config(parse_config(make_config(test_site, [shot], timeout=5000)), ctx)
    assert result.status == "success" and result.steps_completed == 4
    assert ctx.variables["g"] == "Clicked"
    assert ctx.timeout_ms == 5000  # execution.timeout reached the context


def test_on_failure_policy_comes_from_json(ctx: ExecutionContext, test_site: str) -> None:
    bad = {"id": "bad", "action": "click", "selector": {"type": "css", "value": "#nope"}, "timeout": 300}
    last = {"id": "last", "action": "wait", "ms": 10}

    stopped = run_config(parse_config(make_config(test_site, [bad, last], on_failure="stop")), ctx)
    assert [s.status for s in stopped.steps][-2:] == ["failed", "skipped"]

    carried_on = run_config(parse_config(make_config(test_site, [bad, last], on_failure="continue")), ctx)
    assert [s.status for s in carried_on.steps][-2:] == ["failed", "ok"]


def test_cli_runs_config_and_reports_failure_exit_code(
    browser_config, test_site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from web_automation.main import main

    monkeypatch.setenv("WA_PROFILE_ROOT", str(browser_config.profile_root))
    monkeypatch.setenv("WA_PROFILE", "cli")
    monkeypatch.setenv("WA_HEADLESS", "true")
    monkeypatch.chdir(tmp_path)  # relative folders (exports) stay out of the project

    good = tmp_path / "good.json"
    good.write_text(json.dumps(make_config(test_site, [])), encoding="utf-8")
    assert main(["--config", str(good)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"

    bad_step = {"id": "bad", "action": "click", "selector": {"type": "css", "value": "#nope"}, "timeout": 300}
    failing = tmp_path / "failing.json"
    failing.write_text(json.dumps(make_config(test_site, [bad_step])), encoding="utf-8")
    assert main(["--config", str(failing)]) == 2
    assert json.loads(capsys.readouterr().out)["steps_failed"] == 1
