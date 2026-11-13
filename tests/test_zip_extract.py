import json
import zipfile
from pathlib import Path

import pytest

from web_automation.actions import get_action
from web_automation.actions.downloads import resolve_inside
from web_automation.config import parse_config
from web_automation.engine import ExecutionContext
from web_automation.utils.exceptions import ActionError, ConfigError

MEMBER = "connections/followers_and_following/followers_1.json"


def make_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return path


@pytest.fixture
def zctx(tmp_path: Path) -> ExecutionContext:
    """Context without a browser: extraction never touches the page."""
    return ExecutionContext(page=None, export_dir=tmp_path / "exports", notify=lambda m: None)  # type: ignore[arg-type]


def extract(ctx: ExecutionContext, zip_path: Path, **params) -> None:
    ctx.variables["zip"] = str(zip_path)
    get_action("extract_from_zip").run(
        ctx, {"source": "zip", "member": MEMBER, "dest": "followers/followers_1.json", **params}
    )


def test_extracts_and_replaces(zctx: ExecutionContext, tmp_path: Path) -> None:
    dest = zctx.export_dir / "followers" / "followers_1.json"
    extract(zctx, make_zip(tmp_path / "a.zip", {MEMBER: '[{"v": 1}]'}))
    assert json.loads(dest.read_text()) == [{"v": 1}]
    extract(zctx, make_zip(tmp_path / "b.zip", {MEMBER: '[{"v": 2}]'}))
    assert json.loads(dest.read_text()) == [{"v": 2}]
    assert [p.name for p in dest.parent.iterdir()] == ["followers_1.json"]


def test_member_inside_a_wrapper_folder(zctx: ExecutionContext, tmp_path: Path) -> None:
    extract(zctx, make_zip(tmp_path / "w.zip", {f"instagram-user-2026/{MEMBER}": "[1]"}))
    assert (zctx.export_dir / "followers" / "followers_1.json").read_text() == "[1]"


def test_invalid_json_keeps_previous_file(zctx: ExecutionContext, tmp_path: Path) -> None:
    dest = zctx.export_dir / "followers" / "followers_1.json"
    dest.parent.mkdir(parents=True)
    dest.write_text("[0]")
    with pytest.raises(ActionError, match="not valid JSON"):
        extract(zctx, make_zip(tmp_path / "bad.zip", {MEMBER: "{not json"}), validate_json=True)
    assert dest.read_text() == "[0]"
    assert [p.name for p in dest.parent.iterdir()] == ["followers_1.json"]


def test_size_limit_enforced(zctx: ExecutionContext, tmp_path: Path) -> None:
    big = make_zip(tmp_path / "big.zip", {MEMBER: "[" + "0," * 2_000_000 + "0]"})  # about 4 MB
    with pytest.raises(ActionError, match="larger than 1 MB"):
        extract(zctx, big, max_mb=1)
    assert not (zctx.export_dir / "followers" / "followers_1.json").exists()


def test_error_cases(zctx: ExecutionContext, tmp_path: Path) -> None:
    with pytest.raises(ActionError, match="not set"):
        get_action("extract_from_zip").run(zctx, {"source": "nope", "member": MEMBER, "dest": "x.json"})
    notzip = tmp_path / "x.zip"
    notzip.write_text("not a zip")
    with pytest.raises(ActionError, match="Cannot open"):
        extract(zctx, notzip)
    with pytest.raises(ActionError, match="not in the zip"):
        extract(zctx, make_zip(tmp_path / "c.zip", {"other.json": "[]"}))


@pytest.mark.parametrize(
    "bad", ["../escape.json", "..\\escape.json", "/abs.json", "C:/abs.json", "a/../../b.json", ""]
)
def test_paths_cannot_leave_the_export_folder(zctx: ExecutionContext, tmp_path: Path, bad: str) -> None:
    with pytest.raises(ActionError):
        resolve_inside(zctx.export_dir, bad)
    with pytest.raises(ActionError):
        extract(zctx, make_zip(tmp_path / "d.zip", {MEMBER: "[]"}), dest=bad)
    assert not (tmp_path / "escape.json").exists()


def test_schema_rejects_unsafe_paths_and_bad_limits() -> None:
    def step(**extra):
        base = {"id": "e", "action": "extract_from_zip", "source": "zip", "member": MEMBER, "dest": "a.json"}
        return {"name": "z", "steps": [{**base, **extra}]}

    parse_config(step())
    for bad in ({"dest": "../x.json"}, {"dest": "C:/x.json"}, {"member": "/etc/passwd"}, {"max_mb": 0}):
        with pytest.raises(ConfigError):
            parse_config(step(**bad))
