import copy
import json
from pathlib import Path

import pytest

from web_automation.actions import registered_actions
from web_automation.config import SUPPORTED_ACTIONS, load_config, parse_config
from web_automation.config.schema import STEP_MODELS
from web_automation.main import main
from web_automation.utils.exceptions import ConfigError

CSS = {"type": "css", "value": "#x"}

VALID = {
    "name": "demo",
    "version": "1.0",
    "browser": {"type": "chrome", "headless": False, "persistent": True},
    "target": {"url": "https://example.com/"},
    "steps": [
        {"id": "open", "action": "navigate", "url": "https://example.com/"},
        {"id": "more", "action": "click", "selector": {"type": "role", "role": "button", "name": "More"}},
        {"id": "wait", "action": "wait", "url_contains": "example"},
    ],
    "execution": {"timeout": 30000, "on_failure": "stop"},
}


def with_step(step: dict) -> dict:
    data = copy.deepcopy(VALID)
    data["steps"] = [step]
    return data


def message_for(data: dict) -> str:
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    return str(info.value)


def test_readme_example_is_valid() -> None:
    config = parse_config(VALID)
    assert config.name == "demo" and len(config.steps) == 3
    assert config.execution.on_failure == "stop"


def test_defaults_applied() -> None:
    config = parse_config({"name": "d", "steps": [{"id": "s", "action": "wait", "ms": 10}]})
    assert config.browser.headless is False and config.execution.timeout == 30000


def test_every_registered_action_has_a_schema_and_vice_versa() -> None:
    assert set(SUPPORTED_ACTIONS) == set(registered_actions())
    assert set(STEP_MODELS) == set(SUPPORTED_ACTIONS)


def test_to_steps_drops_none_and_metadata() -> None:
    data = with_step({"id": "c", "action": "click", "selector": CSS, "description": "docs only"})
    (step,) = parse_config(data).to_steps()
    assert (step.id, step.action) == ("c", "click")
    assert step.params == {"selector": {"type": "css", "value": "#x"}}


def test_unsupported_action_rejected() -> None:
    message = message_for(with_step({"id": "x", "action": "run_python", "code": "1"}))
    assert "steps.0" in message and "navigate" in message


def test_missing_action_field() -> None:
    assert "action" in message_for(with_step({"id": "x"}))


@pytest.mark.parametrize(
    ("step", "fragment"),
    [
        ({"id": "a", "action": "navigate"}, "url"),
        ({"id": "a", "action": "navigate", "url": "javascript:alert(1)"}, "http"),
        ({"id": "a", "action": "navigate", "url": "file:///etc/passwd"}, "http"),
        ({"id": "a", "action": "click"}, "selector"),
        ({"id": "a", "action": "click", "selector": {"type": "magic", "value": "x"}}, "selector"),
        ({"id": "a", "action": "click", "selector": {"type": "css"}}, "value"),
        ({"id": "a", "action": "click", "selector": CSS, "slector_typo": 1}, "slector_typo"),
        ({"id": "a", "action": "click", "selector": CSS, "timeout": -5}, "timeout"),
        ({"id": "a", "action": "click", "selector": CSS, "timeout": "30"}, "timeout"),
        ({"id": "a", "action": "extract_text", "selector": CSS}, "save_as"),
        ({"id": "a", "action": "extract_text", "selector": CSS, "save_as": "9bad name"}, "save_as"),
        ({"id": "bad id!", "action": "wait", "ms": 10}, "id"),
        ({"id": "a", "action": "confirm"}, "message"),
        ({"id": "a", "action": "wait"}, "exactly one"),
        ({"id": "a", "action": "wait", "ms": 5, "url_contains": "x"}, "exactly one"),
        ({"id": "a", "action": "wait", "ms": 5, "state": "hidden"}, "selector"),
        ({"id": "a", "action": "wait", "ms": 10**9}, "ms"),
        ({"id": "a", "action": "select", "selector": CSS}, "exactly one"),
        ({"id": "a", "action": "fill", "selector": CSS}, "exactly one"),
        ({"id": "a", "action": "fill", "selector": CSS, "value": "v", "value_env": "E"}, "exactly one"),
    ],
)
def test_invalid_steps_rejected(step: dict, fragment: str) -> None:
    assert fragment in message_for(with_step(step))


def test_fill_requires_env_for_password_fields() -> None:
    selector = {"type": "label", "value": "Password"}
    assert "value_env" in message_for(with_step({"id": "p", "action": "fill", "selector": selector, "value": "hunter2"}))
    ok = with_step({"id": "p", "action": "fill", "selector": selector, "value_env": "WA_IG_PASSWORD"})
    assert parse_config(ok).steps[0].value_env == "WA_IG_PASSWORD"


def test_validation_message_never_echoes_secret_values() -> None:
    data = with_step({"id": "p", "action": "fill", "selector": {"type": "label", "value": "Password"}, "value": "hunter2"})
    assert "hunter2" not in message_for(data)


def test_duplicate_step_ids_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["steps"].append(copy.deepcopy(data["steps"][0]))
    assert "duplicate step id 'open'" in message_for(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(steps=[]),
        lambda d: d.pop("steps"),
        lambda d: d.pop("name"),
        lambda d: d.update(name="has spaces"),
        lambda d: d.update(unknown_top_level=1),
        lambda d: d["browser"].update(type="firefox"),
        lambda d: d["browser"].update(persistent=False),
        lambda d: d["execution"].update(on_failure="ignore"),
        lambda d: d["execution"].update(timeout=0),
        lambda d: d["target"].update(url="ftp://x"),
    ],
)
def test_invalid_top_level_rejected(mutate) -> None:
    data = copy.deepcopy(VALID)
    mutate(data)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_all_problems_are_reported_together() -> None:
    data = copy.deepcopy(VALID)
    data["steps"] = [{"id": "a", "action": "click"}, {"id": "b", "action": "navigate"}]
    message = message_for(data)
    assert "steps.0" in message and "steps.1" in message


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_file(tmp_path: Path) -> None:
    assert load_config(write(tmp_path, json.dumps(VALID))).name == "demo"


def test_load_errors_are_clear(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(write(tmp_path, '{"name": '))
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(write(tmp_path, "[1, 2]"))
    with pytest.raises(ConfigError, match="larger than"):
        load_config(write(tmp_path, " " * 1_100_000))


def test_shipped_configs_are_valid() -> None:
    configs = sorted(Path(__file__).parent.parent.joinpath("configs").glob("*.json"))
    assert configs, "expected at least one shipped config"
    for path in configs:
        load_config(path)


def test_cli_validate_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(write(tmp_path, json.dumps(VALID))), "--validate-only"]) == 0
    assert "is valid" in capsys.readouterr().out
    assert main(["--config", str(write(tmp_path, '{"name": "x", "steps": []}')), "--validate-only"]) == 1
    assert "Invalid configuration" in capsys.readouterr().err


def test_instagram_workflow_config_is_valid_and_guards_submission() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert len(ids) == len(set(ids))
    # Clicking "Start export" (then the password popup) is what raises the request,
    # so every guard must come before it.
    for earlier in ("no_pending_request", "verify_account", "verify_all_time", "verify_json"):
        assert ids.index(earlier) < ids.index("start_export")
    assert "confirm_submit" not in ids  # submission is automatic by design (user decision)
    # Customise information comes first, then date range, then format (user-requested order).
    assert ids.index("pick_fields") < ids.index("save_customise") < ids.index("customise_closed")
    assert ids.index("customise_closed") < ids.index("open_date_range") < ids.index("open_format")
    pick = config.steps[ids.index("pick_fields")]
    assert pick.action == "set_checkboxes" and pick.only is True
    assert pick.checked == ["Contacts", "Followers and following"]
    assert ids.index("start_export") < ids.index("enter_password") < ids.index("request_acknowledged")
    start = config.steps[ids.index("start_export")]
    assert start.action == "click" and start.selector.name == "Start export"
    assert config.execution.on_failure == "stop"


def test_role_selector_starts_with_rules() -> None:
    base = {"type": "role", "role": "button", "name": "Format"}
    ok = parse_config(with_step({"id": "a", "action": "click", "selector": {**base, "starts_with": True}}))
    assert ok.steps[0].selector.starts_with is True
    both = with_step({"id": "a", "action": "click", "selector": {**base, "starts_with": True, "exact": True}})
    assert "cannot be combined" in message_for(both)
    nameless = with_step({"id": "a", "action": "click", "selector": {"type": "role", "role": "button", "starts_with": True}})
    assert "requires 'name'" in message_for(nameless)


def test_set_checkboxes_schema() -> None:
    dialog = {"type": "role", "role": "dialog", "name": "X"}
    good = with_step({"id": "c", "action": "set_checkboxes", "selector": dialog, "checked": ["A", "B"]})
    assert parse_config(good).steps[0].checked == ["A", "B"]
    for bad in (
        {"id": "c", "action": "set_checkboxes", "selector": dialog, "checked": []},
        {"id": "c", "action": "set_checkboxes", "selector": dialog, "checked": ["A", "A"]},
        {"id": "c", "action": "set_checkboxes", "selector": dialog},
        {"id": "c", "action": "set_checkboxes", "checked": ["A"]},
    ):
        with pytest.raises(ConfigError):
            parse_config(with_step(bad))


def test_login_popup_step_sits_between_login_and_navigation() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert ids.index("login_if_needed") < ids.index("save_login_info") < ids.index("home_loaded") < ids.index("open_more")
    step = config.steps[ids.index("save_login_info")]
    assert step.action == "click_if_visible" and step.selector.name == "Save"
    assert step.selector.exact is True and step.selector.within_text == "Save your login info to Instagram?"
    assert ids.index("save_login_info") < ids.index("login_popup_closed") < ids.index("home_loaded")


def test_within_text_schema() -> None:
    scoped = {"type": "role", "role": "button", "name": "Save", "within_text": "Popup title"}
    assert parse_config(with_step({"id": "c", "action": "click", "selector": scoped})).steps[0].selector.within_text == "Popup title"
    with pytest.raises(ConfigError):
        parse_config(with_step({"id": "c", "action": "click", "selector": {"type": "xpath", "value": "//a", "within_text": "x"}}))


def test_notifications_popup_step_dismisses_not_now_before_home() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert ids.index("login_popup_closed") < ids.index("dismiss_notifications_popup")
    assert ids.index("notifications_popup_closed") < ids.index("home_loaded") < ids.index("open_more")
    step = config.steps[ids.index("dismiss_notifications_popup")]
    assert step.action == "click_if_visible" and step.selector.name == "Not Now"
    assert step.selector.within_text == "Turn on notifications"


def test_download_steps_come_after_the_request_is_acknowledged() -> None:
    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert ids.index("request_acknowledged") < ids.index("download_latest_export") < ids.index("sync_followers")
    download = config.steps[ids.index("download_latest_export")]
    assert download.row_contains == ["Followers and following", "JSON"]
    assert download.save_dir == "downloads" and download.password_env == "WA_IG_PASSWORD"
    sync = config.steps[ids.index("sync_followers")]
    assert sync.source == download.save_as
    assert sync.member == "connections/followers_and_following/followers_1.json"
    assert "extract_followers" not in ids  # followers go to the database, not to a file