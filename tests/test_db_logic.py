"""Database logic that needs no Postgres: parsing, the sync plan, safety checks, the run
report, and the rule that the password in the URL never reaches an error message."""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from web_automation.actions import get_action
from web_automation.config import parse_config
from web_automation.db import RunRecorder, parse_followers, validate_url
from web_automation.db.connection import connect, describe
from web_automation.db.followers import Follower, check_drop_is_plausible, plan_sync, sync_followers
from web_automation.engine import ExecutionContext
from web_automation.engine.executor import ExecutionResult, StepResult
from web_automation.engine.report import build_facts
from web_automation.utils.exceptions import ActionError, ConfigError, DatabaseError

SECRET = "hunter2-db-password"
UNREACHABLE = f"postgresql://wa_user:{SECRET}@127.0.0.1:1/wa_db"  # port 1: refused at once
MEMBER = "connections/followers_and_following/followers_1.json"


def entry(name: str | None, href: str | None = None, ts: int | None = 1_700_000_000) -> dict:
    data: dict = {"href": f"https://www.instagram.com/{name}" if href is None else href, "value": name or ""}
    if ts is not None:
        data["timestamp"] = ts
    return {"title": "", "media_list_data": [], "string_list_data": [data]}


# ------------------------------------------------------------------ parsing
def test_parse_followers_reads_the_instagram_structure() -> None:
    result = parse_followers([entry("alice"), entry("bob", ts=1_700_000_500)])
    assert [f.username for f in result] == ["alice", "bob"]
    assert result[0].profile_url == "https://www.instagram.com/alice"
    assert result[1].followed_at == datetime.fromtimestamp(1_700_000_500, tz=timezone.utc)


def test_parse_followers_username_falls_back_to_the_profile_url() -> None:
    (follower,) = parse_followers([entry(None, href="https://www.instagram.com/_u/carol/")])
    assert follower.username == "carol"


def test_parse_followers_dedupes_and_tolerates_missing_timestamp() -> None:
    result = parse_followers([entry("alice"), entry("alice"), entry("dave", ts=None)])
    assert [f.username for f in result] == ["alice", "dave"]
    assert result[1].followed_at is None


@pytest.mark.parametrize(
    "bad",
    [{}, "x", [1], [{}], [{"string_list_data": []}], [{"string_list_data": ["x"]}], [entry(None, href="")]],
)
def test_parse_followers_rejects_unexpected_shapes(bad) -> None:
    with pytest.raises(DatabaseError):
        parse_followers(bad)


# ------------------------------------------------------------ sync planning
def test_plan_sync_adds_new_and_removes_unfollowed_only() -> None:
    add, remove = plan_sync(existing={"a", "b", "c"}, latest={"b", "c", "d", "e"})
    assert add == ["d", "e"] and remove == ["a"]


def test_plan_sync_first_run_adds_everyone_and_identical_run_changes_nothing() -> None:
    assert plan_sync(set(), {"a", "b"}) == (["a", "b"], [])
    assert plan_sync({"a", "b"}, {"a", "b"}) == ([], [])


def test_a_suspicious_drop_is_refused() -> None:
    check_drop_is_plausible(existing_count=100, remove_count=10)  # normal churn
    check_drop_is_plausible(existing_count=10, remove_count=10)  # tiny table: not checked
    with pytest.raises(DatabaseError, match="looks incomplete"):
        check_drop_is_plausible(existing_count=100, remove_count=60)


def test_empty_list_never_wipes_the_table() -> None:
    with pytest.raises(DatabaseError, match="empty"):
        sync_followers(UNREACHABLE, "acct", [])  # refused before any connection is tried


# ------------------------------------------------------ password never leaks
@pytest.mark.parametrize("url", ["mysql://u:secretpw@h/db", "not a url secretpw", "postgresql://", ""])
def test_invalid_urls_are_rejected_without_echoing_them(url: str) -> None:
    with pytest.raises(DatabaseError) as info:
        validate_url(url)
    assert "secretpw" not in str(info.value)  # whatever was typed is never repeated back


def test_describe_hides_credentials() -> None:
    text = describe(UNREACHABLE)
    assert SECRET not in text and "wa_user" not in text and text == "127.0.0.1:1/wa_db"


def test_connection_error_does_not_contain_the_password() -> None:
    with pytest.raises(DatabaseError) as info:
        with connect(UNREACHABLE):
            pass
    assert SECRET not in str(info.value) and "wa_user" not in str(info.value)


def test_settings_repr_hides_the_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from web_automation.settings import Settings

    monkeypatch.setenv("WA_DATABASE_URL", UNREACHABLE)
    settings = Settings.load(tmp_path / "missing.env")
    assert settings.database_url == UNREACHABLE and SECRET not in repr(settings)
    monkeypatch.setenv("WA_DATABASE_URL", "mysql://u:p@h/db")
    with pytest.raises(DatabaseError):
        Settings.load(tmp_path / "missing.env")


# -------------------------------------------------- recorder survives outages
def test_recorder_continues_when_the_database_is_down() -> None:
    recorder = RunRecorder(UNREACHABLE, "demo").start()
    assert recorder.available is False and recorder.run_id is None
    assert SECRET not in (recorder.last_error or "")
    result = ExecutionResult("demo", "success", "t0", "t1", [StepResult("s", "click", "ok")])
    recorder.add_log("INFO", "x", "message")  # all of these are quiet no-ops
    recorder.finish(result, 0, build_facts(result))
    recorder.mark_crashed("boom")


# ------------------------------------------------------------------- report
def steps(*pairs: tuple[str, str]) -> ExecutionResult:
    items = [StepResult(step_id, "click", status) for step_id, status in pairs]
    failed = any(s.status == "failed" for s in items)
    stopped = any(s.status == "stopped" for s in items)
    return ExecutionResult("w", "failed" if failed else "stopped" if stopped else "success", "t0", "t1", items)


def test_report_distinguishes_clicked_from_acknowledged() -> None:
    clicked_only = build_facts(steps(("start_export", "ok"), ("request_acknowledged", "failed")))
    assert clicked_only["export_request_submitted"] is True
    assert clicked_only["export_request_acknowledged"] is False
    assert clicked_only["execution_completed"] is False
    assert "NOT acknowledged" in clicked_only["message"]

    full = build_facts(steps(("start_export", "ok"), ("request_acknowledged", "ok"), ("download_latest_export", "ok")))
    assert full["export_request_submitted"] and full["export_request_acknowledged"]
    assert full["export_file_downloaded"] and full["execution_completed"]


def test_report_when_nothing_was_submitted() -> None:
    stopped = steps(("no_pending_request", "stopped"), ("start_export", "skipped"))
    stopped.steps[0].message = "An export request is already pending"
    facts = build_facts(stopped)
    assert facts["export_request_submitted"] is False and facts["export_request_acknowledged"] is False
    assert "already pending" in facts["message"]
    assert build_facts(steps(("open", "failed")))["message"] == "No export request was submitted"


def test_acknowledgement_requires_the_submit_to_have_happened() -> None:
    odd = build_facts(steps(("start_export", "skipped"), ("request_acknowledged", "ok")))
    assert odd["export_request_acknowledged"] is False  # an old pending request must not count


# ------------------------------------------------- the sync_followers action
def make_zip(path: Path, text: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"instagram-x/{MEMBER}", text)
    return path


@pytest.fixture
def zctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExecutionContext:
    monkeypatch.setenv("WA_TEST_ACCOUNT", "im_test")
    ctx = ExecutionContext(page=None, export_dir=tmp_path, notify=lambda m: None, database_url=UNREACHABLE)  # type: ignore[arg-type]
    ctx.variables["zip"] = str(make_zip(tmp_path / "a.zip", json.dumps([entry("alice")])))
    return ctx


def run_sync(ctx: ExecutionContext, **override) -> None:
    params = {"source": "zip", "member": MEMBER, "account_env": "WA_TEST_ACCOUNT", **override}
    get_action("sync_followers").run(ctx, params)


def test_sync_action_precondition_errors(zctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ActionError, match="not set"):
        run_sync(zctx, account_env="WA_NOT_SET_ANYWHERE")
    zctx.database_url = ""
    with pytest.raises(ActionError, match="WA_DATABASE_URL"):
        run_sync(zctx)
    zctx.database_url = UNREACHABLE
    with pytest.raises(ActionError, match="Variable 'nope'"):
        run_sync(zctx, source="nope")


def test_sync_action_bad_json_is_rejected_before_any_database_work(zctx: ExecutionContext, tmp_path: Path) -> None:
    zctx.variables["zip"] = str(make_zip(tmp_path / "bad.zip", "{not json"))
    with pytest.raises(ActionError, match="not valid JSON"):
        run_sync(zctx)  # the database is unreachable, yet the error is about the JSON
    zctx.variables["zip"] = str(make_zip(tmp_path / "wrong.zip", json.dumps({"unexpected": True})))
    with pytest.raises(DatabaseError, match="must contain a JSON list"):
        run_sync(zctx)


def test_sync_action_database_outage_fails_cleanly_without_the_password(zctx: ExecutionContext) -> None:
    with pytest.raises(DatabaseError) as info:
        run_sync(zctx)
    assert SECRET not in str(info.value)


def test_sync_action_size_limit(zctx: ExecutionContext, tmp_path: Path) -> None:
    big = make_zip(tmp_path / "big.zip", "[" + "0," * 800_000 + "0]")  # about 1.6 MB
    zctx.variables["zip"] = str(big)
    with pytest.raises(ActionError, match="larger than 1 MB"):
        run_sync(zctx, max_mb=1)


def test_sync_action_nothing_is_written_to_disk(zctx: ExecutionContext, tmp_path: Path) -> None:
    before = {p.name for p in tmp_path.rglob("*")}
    with pytest.raises(DatabaseError):
        run_sync(zctx)
    assert {p.name for p in tmp_path.rglob("*")} == before


def test_workflow_schema_for_the_new_step() -> None:
    step = {"id": "s", "action": "sync_followers", "source": "zip", "member": MEMBER, "account_env": "WA_EXPECTED_ACCOUNT"}
    parse_config({"name": "n", "steps": [{"id": "d", "action": "download_latest_export", "selector": {"type": "css", "value": "x"},
                                           "row_contains": ["a"], "save_dir": "downloads", "save_as": "zip"}, step]})
    for bad in ({"member": "../x"}, {"account_env": "bad name"}, {"max_mb": 0}):
        with pytest.raises(ConfigError):
            parse_config({"name": "n", "steps": [{**step, **bad}]})


def test_instagram_workflow_syncs_followers_after_the_download() -> None:
    from web_automation.config import load_config

    config = load_config(Path(__file__).parent.parent / "configs" / "instagram_export.json")
    ids = [s.id for s in config.steps]
    assert "extract_followers" not in ids  # no followers file is written to the exports folder
    assert ids.index("download_latest_export") < ids.index("sync_followers")
    sync = config.steps[ids.index("sync_followers")]
    assert sync.source == "export_zip" and sync.account_env == "WA_EXPECTED_ACCOUNT"
    assert sync.member == "connections/followers_and_following/followers_1.json"
