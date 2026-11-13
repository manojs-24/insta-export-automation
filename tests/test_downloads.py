import json
from pathlib import Path

import pytest

from web_automation.config import parse_config
from web_automation.engine import ExecutionContext
from web_automation.engine.runner import run_config

pytestmark = pytest.mark.browser

DIALOG = {"type": "role", "role": "dialog", "name": "Export your information"}
MEMBER = "connections/followers_and_following/followers_1.json"


def workflow(site: str, page: str = "index.html", **download: object) -> dict:
    return {
        "name": "download_demo",
        "steps": [
            {"id": "open", "action": "navigate", "url": f"{site}/{page}"},
            {
                "id": "download", "action": "download_latest_export", "selector": DIALOG,
                "row_contains": ["Followers and following", "JSON"], "save_dir": "downloads",
                "save_as": "zip", "timeout": 3000, **download,
            },
            {
                "id": "extract", "action": "extract_from_zip", "source": "zip", "member": MEMBER,
                "dest": "followers/followers_1.json", "validate_json": True,
            },
        ],
    }


@pytest.fixture
def exports(ctx: ExecutionContext, tmp_path: Path) -> Path:
    ctx.export_dir = tmp_path / "exports"
    return ctx.export_dir


def test_downloads_newest_matching_export_and_extracts_followers(
    ctx: ExecutionContext, exports: Path, downloads_site: str
) -> None:
    result = run_config(parse_config(workflow(downloads_site)), ctx)
    assert result.status == "success", result.steps
    # new.zip is the JSON row with the latest 'Expires on'. old.zip (earlier) and html.zip
    # (later, but HTML) must not be chosen.
    assert (exports / "downloads" / "new.zip").exists()
    assert not (exports / "downloads" / "old.zip").exists()
    assert not (exports / "downloads" / "html.zip").exists()
    followers = exports / "followers" / "followers_1.json"
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "new"}]
    assert not list(exports.rglob(".partial-*")) and not list(exports.rglob(".tmp-*"))


def test_a_newer_download_replaces_the_followers_file(
    ctx: ExecutionContext, exports: Path, downloads_site: str
) -> None:
    followers = exports / "followers" / "followers_1.json"
    followers.parent.mkdir(parents=True)
    followers.write_text('[{"version": "from a previous run"}]', encoding="utf-8")
    assert run_config(parse_config(workflow(downloads_site)), ctx).status == "success"
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "new"}]
    assert run_config(parse_config(workflow(downloads_site)), ctx).status == "success"  # re-run
    assert len(list(followers.parent.iterdir())) == 1  # still a single file, no leftovers


def test_nothing_ready_stops_cleanly_without_touching_files(
    ctx: ExecutionContext, exports: Path, downloads_site: str
) -> None:
    followers = exports / "followers" / "followers_1.json"
    followers.parent.mkdir(parents=True)
    followers.write_text('[{"version": "keep me"}]', encoding="utf-8")
    result = run_config(parse_config(workflow(downloads_site, page="empty.html")), ctx)
    assert result.status == "stopped" and "No finished export" in result.stop_reason
    assert [s.status for s in result.steps] == ["ok", "stopped", "skipped"]
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "keep me"}]


def test_missing_member_fails_and_keeps_the_old_file(
    ctx: ExecutionContext, exports: Path, downloads_site: str
) -> None:
    followers = exports / "followers" / "followers_1.json"
    followers.parent.mkdir(parents=True)
    followers.write_text('[{"version": "keep me"}]', encoding="utf-8")
    data = workflow(downloads_site)
    data["steps"][2]["member"] = "connections/followers_and_following/missing.json"
    result = run_config(parse_config(data), ctx)
    assert result.status == "failed" and "not in the zip" in result.steps[2].message
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "keep me"}]


def test_full_instagram_sequence_popup_then_password_then_zip(
    ctx: ExecutionContext, exports: Path, downloads_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_TEST_PW", "right-pass")
    data = workflow(downloads_site, page="flow.html", password_env="WA_TEST_PW", popup_title="Download your files")
    result = run_config(parse_config(data), ctx)
    assert result.status == "success", result.steps
    assert (exports / "downloads" / "new.zip").exists()
    followers = exports / "followers" / "followers_1.json"
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "new"}]


def test_download_that_starts_immediately_needs_no_popup_or_password(
    ctx: ExecutionContext, exports: Path, downloads_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WA_TEST_PW", raising=False)  # never needed
    data = workflow(downloads_site, page="direct.html", password_env="WA_TEST_PW")
    assert run_config(parse_config(data), ctx).status == "success"
    assert (exports / "downloads" / "new.zip").exists()


def test_password_prompt_without_configured_env_fails_clearly(
    ctx: ExecutionContext, exports: Path, downloads_site: str
) -> None:
    result = run_config(parse_config(workflow(downloads_site, page="flow.html")), ctx)
    assert result.status == "failed" and "no 'password_env'" in result.steps[1].message
    assert not (exports / "followers").exists()


def test_wrong_password_downloads_nothing_and_keeps_old_file(
    ctx: ExecutionContext, exports: Path, downloads_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_TEST_PW", "wrong")
    ctx.wait_for_user = lambda message: None  # the user does not fix it
    followers = exports / "followers" / "followers_1.json"
    followers.parent.mkdir(parents=True)
    followers.write_text('[{"version": "keep me"}]', encoding="utf-8")
    data = workflow(downloads_site, page="flow.html", password_env="WA_TEST_PW", download_timeout=4000)
    result = run_config(parse_config(data), ctx)
    assert result.status == "failed"
    assert json.loads(followers.read_text(encoding="utf-8")) == [{"version": "keep me"}]
    assert not (exports / "downloads").exists() or not list((exports / "downloads").glob("*.zip"))


def test_popup_listing_several_files_is_refused(
    ctx: ExecutionContext, exports: Path, downloads_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WA_TEST_PW", "right-pass")
    data = workflow(downloads_site, page="twofiles.html", password_env="WA_TEST_PW")
    result = run_config(parse_config(data), ctx)
    assert result.status == "failed" and "several" in result.steps[1].message


def seed_old_downloads(exports: Path) -> dict[str, Path]:
    folder = exports / "downloads"
    (folder / "nested").mkdir(parents=True)
    files = {
        "old_zip": folder / "instagram-old-2026-11-01.zip",
        "other_zip": folder / "instagram-older-2026-10-20.zip",
        "note": folder / "notes.txt",  # different extension: must survive
        "nested": folder / "nested" / "keep.zip",  # sub-folder: must survive
    }
    for path in files.values():
        path.write_bytes(b"old")
    return files


def test_delete_old_keeps_only_the_latest_zip(ctx: ExecutionContext, exports: Path, downloads_site: str) -> None:
    old = seed_old_downloads(exports)
    result = run_config(parse_config(workflow(downloads_site, delete_old=True)), ctx)
    assert result.status == "success", result.steps
    zips = sorted(p.name for p in (exports / "downloads").glob("*.zip"))
    assert zips == ["new.zip"]  # the two old top-level zips are gone, the new one is kept
    assert old["note"].exists() and old["nested"].exists()  # nothing else was touched
    assert json.loads((exports / "followers" / "followers_1.json").read_text(encoding="utf-8")) == [{"version": "new"}]


def test_delete_old_off_by_default(ctx: ExecutionContext, exports: Path, downloads_site: str) -> None:
    old = seed_old_downloads(exports)
    assert run_config(parse_config(workflow(downloads_site)), ctx).status == "success"
    assert old["old_zip"].exists() and old["other_zip"].exists()


def test_redownloading_the_same_file_keeps_exactly_one(ctx: ExecutionContext, exports: Path, downloads_site: str) -> None:
    for _ in range(2):
        assert run_config(parse_config(workflow(downloads_site, delete_old=True)), ctx).status == "success"
    assert [p.name for p in (exports / "downloads").glob("*.zip")] == ["new.zip"]


def test_failed_download_deletes_nothing(ctx: ExecutionContext, exports: Path, downloads_site: str) -> None:
    old = seed_old_downloads(exports)
    result = run_config(parse_config(workflow(downloads_site, page="empty.html", delete_old=True)), ctx)
    assert result.status == "stopped"  # nothing ready to download
    assert old["old_zip"].exists() and old["other_zip"].exists()  # the previous downloads survive


def test_invalid_download_deletes_nothing(
    ctx: ExecutionContext, exports: Path, downloads_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = seed_old_downloads(exports)
    monkeypatch.setenv("WA_TEST_PW", "wrong")
    ctx.wait_for_user = lambda message: None
    data = workflow(downloads_site, page="flow.html", delete_old=True, password_env="WA_TEST_PW", download_timeout=3000)
    assert run_config(parse_config(data), ctx).status == "failed"
    assert old["old_zip"].exists() and old["other_zip"].exists()


def test_delete_old_schema_and_config() -> None:
    from pathlib import Path as P
    from web_automation.config import load_config
    from web_automation.utils.exceptions import ConfigError

    with pytest.raises(ConfigError):
        parse_config(workflow("http://x", delete_old="yes"))
    config = load_config(P(__file__).parent.parent / "configs" / "instagram_export.json")
    step = next(s for s in config.steps if s.id == "download_latest_export")
    assert step.delete_old is True
