"""Download the newest ready export and pull one file out of its zip.

Both actions only touch paths inside the export directory (`ctx.export_dir`), so a path
written in the JSON can never point elsewhere on disk. Downloaded archives hold personal
data: nothing here logs file contents, download URLs (they carry secrets) or extracted values.
"""

import json
import os
import re
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Download, Locator, Page
from playwright.sync_api import Error as PlaywrightError

from web_automation.actions.base import RETRY_NEVER, Action, get_action, register_action, require, step_timeout
from web_automation.actions.interaction import find_password_field
from web_automation.actions.locators import build_locator, find
from web_automation.utils.exceptions import ActionError, WorkflowStopped
from web_automation.utils.logger import get_logger

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

log = get_logger("downloads")

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(text: str) -> str:
    """A file name with nothing in it that could escape the folder it is saved in."""
    return _UNSAFE_NAME.sub("_", Path(text).name).strip("._") or "download"

_EXPIRES = re.compile(r"Expires on (\d{1,2})/(\d{1,2})/(\d{4})")  # day/month/year (English UK)
_DEFAULT_DOWNLOAD_TIMEOUT_MS = 300_000
_DEFAULT_MAX_MB = 200


def resolve_inside(base: Path, relative: str) -> Path:
    """Resolve `relative` under `base`; reject absolute paths, drive letters and '..'."""
    text = relative.replace("\\", "/")
    parts = PurePosixPath(text).parts
    if not parts or text.startswith("/") or ":" in text or any(p in ("", ".", "..") for p in parts):
        raise ActionError(f"Path must be relative and stay inside the export folder: {relative!r}")
    root = base.resolve()
    target = (root / Path(*parts)).resolve()
    if root not in target.parents:
        raise ActionError(f"Path escapes the export folder: {relative!r}")
    return target


def _expiry(text: str) -> date:
    match = _EXPIRES.search(text)
    if not match:
        return date.min
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return date.min


def _download_control(source: Page | Locator) -> Locator:
    """The 'Download' control of an export row (Meta shows it as a button or a link)."""
    return source.get_by_role("button", name="Download", exact=True).or_(
        source.get_by_role("link", name="Download", exact=True)
    )


def _pick_latest(rows: Locator) -> Locator:
    """Newest row by 'Expires on' date (exports live 4 days, so later expiry = newer).

    Ties keep the row listed first.
    """
    best: Locator | None = None
    best_key = date.min
    for index in range(rows.count()):
        row = rows.nth(index)
        try:
            key = _expiry(row.inner_text())
        except PlaywrightError:
            continue
        if best is None or key > best_key:  # strict '>' keeps the first row on ties
            best, best_key = row, key
    if best is None:
        raise ActionError("The export list changed while it was being read; try again")
    return best


class _DownloadWatcher:
    """Collects `download` events from every tab of the browser while active."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._context = page.context
        self._watched: list[Page] = []
        self.downloads: list[Download] = []

    def _on_download(self, download: Download) -> None:
        # a real method: Playwright cannot wrap built-ins such as list.append as listeners
        self.downloads.append(download)

    def _watch(self, page: Page) -> None:
        page.on("download", self._on_download)
        self._watched.append(page)

    def __enter__(self) -> "_DownloadWatcher":
        for page in list(self._context.pages):
            self._watch(page)
        self._context.on("page", self._watch)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        for emitter, event, handler in [(self._context, "page", self._watch)] + [
            (page, "download", self._on_download) for page in self._watched
        ]:
            try:
                emitter.remove_listener(event, handler)
            except PlaywrightError:
                pass

    def wait(self, seconds: float) -> Download | None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.downloads:
                return self.downloads[0]
            self._page.wait_for_timeout(200)  # lets Playwright deliver queued events
        return self.downloads[0] if self.downloads else None


def _save_download(download: Download, save_dir: Path, extension: str) -> Path:
    name = safe_filename(download.suggested_filename or "")
    if not name.lower().endswith(extension.lower()):
        raise ActionError(f"The downloaded file is not a {extension} file")
    save_dir.mkdir(parents=True, exist_ok=True)
    final, partial = save_dir / name, save_dir / f".partial-{name}"
    try:
        download.save_as(str(partial))
        error = download.failure()
        if error:
            raise ActionError(f"The download failed: {error}")
        if partial.stat().st_size == 0:
            raise ActionError("The downloaded file is empty")
        if extension.lower() == ".zip" and not zipfile.is_zipfile(partial):
            raise ActionError("The downloaded file is not a valid zip archive")
        os.replace(partial, final)
    finally:
        partial.unlink(missing_ok=True)
    return final


def _delete_older_downloads(save_dir: Path, keep: Path, extension: str) -> tuple[int, int]:
    """Delete every other `extension` file directly inside `save_dir`; returns (deleted, failed).

    Only regular files at the top level are considered (no sub-folders, no symlinks, no
    other extensions), and `keep` is never touched. A file that cannot be deleted (for
    example because it is open in another program) is counted, not fatal.
    """
    deleted = failed = 0
    for entry in save_dir.iterdir():
        if entry == keep or entry.is_symlink() or not entry.is_file():
            continue
        if not entry.name.lower().endswith(extension.lower()):
            continue
        try:
            entry.unlink()
            deleted += 1
        except OSError:
            failed += 1
    return deleted, failed


@register_action
class DownloadLatestExport(Action):
    """download_latest_export: {selector, row_contains, save_dir, save_as, extension?,
    password_env?, popup_title?, delete_old?, timeout?, download_timeout?}.

    Looks in `selector` (the export dialog) for finished exports: list rows that have a
    Download control and whose text contains every `row_contains` text. Downloads the newest
    (latest 'Expires on' date; ties keep the first listed) into `save_dir` under the export
    folder, and stores the saved path in the variable `save_as`. If Meta asks for the password
    first, it is entered from `password_env`. Stops the run cleanly (status "stopped") when
    no matching export is ready yet.

    With `delete_old`, once the new file is saved and verified, every other file with the
    same extension in `save_dir` is deleted, so only the latest download is kept.
    """

    name = "download_latest_export"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        contains = params.get("row_contains")
        if not (isinstance(contains, list) and contains and all(isinstance(t, str) and t for t in contains)):
            raise ActionError("'row_contains' must be a non-empty list of texts")
        variable = require(params, "save_as")
        save_dir = resolve_inside(ctx.export_dir, require(params, "save_dir"))
        extension = params.get("extension", ".zip")
        if not isinstance(extension, str) or not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", extension):
            raise ActionError("'extension' must look like '.zip'")
        password_env = params.get("password_env")
        if password_env is not None and not isinstance(password_env, str):
            raise ActionError("'password_env' must be an environment variable name")
        download_ms = params.get("download_timeout", _DEFAULT_DOWNLOAD_TIMEOUT_MS)
        if not isinstance(download_ms, int) or isinstance(download_ms, bool) or download_ms <= 0:
            raise ActionError("'download_timeout' must be a positive integer (milliseconds)")
        popup_title = params.get("popup_title", "Download your files")
        if not isinstance(popup_title, str) or not popup_title:
            raise ActionError("'popup_title' must be a non-empty string")
        delete_old = params.get("delete_old", False)
        if not isinstance(delete_old, bool):
            raise ActionError("'delete_old' must be true or false")
        locate_ms = step_timeout(ctx, params)

        scope = find(ctx, params)
        rows = scope.get_by_role("listitem").filter(has=_download_control(ctx.page))
        for text in contains:
            rows = rows.filter(has_text=re.compile(re.escape(text), re.IGNORECASE))

        end = time.monotonic() + locate_ms / 1000  # the list fills in after the dialog opens
        while rows.count() == 0 and time.monotonic() < end:
            ctx.page.wait_for_timeout(300)
        if rows.count() == 0:
            raise WorkflowStopped(
                f"No finished export matching {', '.join(contains)} is ready to download yet. "
                "A recent request may still be preparing; run again once Instagram says it is ready."
            )

        row = _pick_latest(rows)
        control = _download_control(row).first
        with _DownloadWatcher(ctx.page) as watcher:
            control.click(timeout=locate_ms)
            download = self._drive_to_download(
                ctx, watcher, popup_title, password_env, download_ms / 1000
            )

        saved = _save_download(download, save_dir, extension)
        ctx.variables[variable] = str(saved)
        ctx.notify(f"Downloaded {saved.name} ({saved.stat().st_size / 1_048_576:.1f} MB)")
        if delete_old:
            deleted, failed = _delete_older_downloads(save_dir, saved, extension)
            if deleted:
                ctx.notify(f"Deleted {deleted} older download(s); kept only {saved.name}")
            if failed:
                ctx.notify(f"Could not delete {failed} older download(s) (in use?); left in place")

    @staticmethod
    def _drive_to_download(
        ctx: "ExecutionContext",
        watcher: "_DownloadWatcher",
        popup_title: str,
        password_env: str | None,
        seconds: float,
    ) -> Download:
        """Step through what Meta shows after the row's Download click, until a file arrives.

        Observed sequence: a "Download your files" popup with its own Download button, then a
        "re-enter your password" prompt, then the browser download starts. The stages are
        handled in whatever order they appear, each at most once, and a download that starts
        immediately is accepted too.
        """
        popup_button = build_locator(
            ctx.page,
            {"type": "role", "role": "button", "name": "Download", "exact": True, "within_text": popup_title},
        )
        popup_done = password_done = False
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if watcher.downloads:
                return watcher.downloads[0]

            if not popup_done:
                try:
                    count = popup_button.count()
                    shown = count > 0 and popup_button.first.is_visible()
                except PlaywrightError:
                    count, shown = 0, False  # page is mid-navigation
                if shown:
                    if count > 1:
                        raise ActionError(
                            f"The '{popup_title}' popup lists {count} files; downloading several "
                            "files is not supported"
                        )
                    popup_button.first.click(timeout=10_000)
                    popup_done = True
                    continue

            if not password_done and find_password_field(ctx, "Password", 250) is not None:
                if not password_env:
                    raise ActionError(
                        "Meta asked for the password but no 'password_env' was configured"
                    )
                # The file arriving is the success signal, so don't wait for the prompt to close.
                # Marked done first: the password is submitted at most once, never retried.
                password_done = True
                get_action("enter_password").run(
                    ctx, {"value_env": password_env, "timeout": 8000, "wait_for_close": False}
                )
                continue

            ctx.page.wait_for_timeout(200)  # lets Playwright deliver queued events

        if watcher.downloads:
            return watcher.downloads[0]
        raise ActionError(
            "No download started "
            f"(popup button clicked: {popup_done}, password entered: {password_done})"
        )


def _find_member(archive: zipfile.ZipFile, member: str) -> zipfile.ZipInfo:
    wanted = member.replace("\\", "/").strip("/")
    entries = {i.filename.replace("\\", "/"): i for i in archive.infolist() if not i.is_dir()}
    if wanted in entries:
        return entries[wanted]
    matches = [name for name in entries if name.endswith("/" + wanted)]  # zip wrapped in a folder
    if len(matches) == 1:
        return entries[matches[0]]
    if len(matches) > 1:
        raise ActionError(f"'{member}' matches more than one file in the zip")
    stem = PurePosixPath(wanted).stem.split("_")[0]
    similar = [name for name in entries if stem and stem in name][:5]
    raise ActionError(f"'{member}' is not in the zip. Similar entries: {similar or 'none'}")


@register_action
class ExtractFromZip(Action):
    """extract_from_zip: {source, member, dest, validate_json?, max_mb?}.

    Copies one file out of the zip whose path is in variable `source` to `dest` (relative to
    the export folder), replacing any existing file atomically. With `validate_json` the new
    file must parse as JSON first, so a bad download never overwrites the previous good file.
    """

    name = "extract_from_zip"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        source = require(params, "source")
        member = require(params, "member")
        dest = resolve_inside(ctx.export_dir, require(params, "dest"))
        validate_json = params.get("validate_json", False)
        max_mb = params.get("max_mb", _DEFAULT_MAX_MB)
        if not isinstance(validate_json, bool):
            raise ActionError("'validate_json' must be true or false")
        if not isinstance(max_mb, int) or isinstance(max_mb, bool) or max_mb <= 0:
            raise ActionError("'max_mb' must be a positive integer")
        if source not in ctx.variables:
            raise ActionError(f"Variable '{source}' is not set; run download_latest_export first")
        zip_path = Path(ctx.variables[source])
        max_bytes = max_mb * 1_048_576

        try:
            archive = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ActionError(f"Cannot open the zip file: {type(exc).__name__}") from None
        with archive:
            info = _find_member(archive, member)
            if info.file_size > max_bytes:
                raise ActionError(f"'{member}' is larger than {max_mb} MB; refusing to extract it")
            dest.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-", suffix=".part")
            try:
                copied = 0
                with os.fdopen(handle, "wb") as out, archive.open(info) as src:
                    while chunk := src.read(1 << 20):
                        copied += len(chunk)
                        if copied > max_bytes:  # the zip header can lie about the size
                            raise ActionError(f"'{member}' is larger than {max_mb} MB; stopped")
                        out.write(chunk)
                if validate_json:
                    try:
                        with open(tmp_name, "rb") as check:
                            json.load(check)
                    except ValueError:
                        raise ActionError(
                            f"'{member}' is not valid JSON; the previous file was kept"
                        ) from None
                os.replace(tmp_name, dest)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        ctx.notify(f"Saved {dest.name} ({copied / 1024:.0f} KB) in {dest.parent.name}/")
