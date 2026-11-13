"""Store data in the database. Nothing here writes extracted data to disk or to the log."""

import json
import os
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from web_automation.actions.base import RETRY_NEVER, Action, register_action, require
from web_automation.actions.downloads import _find_member
from web_automation.db.followers import parse_followers, sync_followers
from web_automation.utils.exceptions import ActionError

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext

_DEFAULT_MAX_MB = 50


@register_action
class SyncFollowers(Action):
    """sync_followers: {source, member, account_env, max_mb?}.

    Reads the followers JSON straight out of the zip whose path is in variable `source`
    (nothing is written to disk) and makes the `wa_followers` table match it for the account
    named by environment variable `account_env`: new followers are added with the current
    date and time, followers missing from the list (they unfollowed) are deleted.
    Usernames are never logged or reported; only the counts are.
    """

    name = "sync_followers"
    retry_policy = RETRY_NEVER

    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        source = require(params, "source")
        member = require(params, "member")
        account_env = require(params, "account_env")
        max_mb = params.get("max_mb", _DEFAULT_MAX_MB)
        if not isinstance(max_mb, int) or isinstance(max_mb, bool) or max_mb <= 0:
            raise ActionError("'max_mb' must be a positive integer")

        account = os.environ.get(account_env, "").strip()
        if not account:
            raise ActionError(f"Environment variable '{account_env}' is not set")
        if not ctx.database_url:
            raise ActionError("WA_DATABASE_URL is not set; cannot store the followers")
        if source not in ctx.variables:
            raise ActionError(f"Variable '{source}' is not set; run download_latest_export first")

        max_bytes = max_mb * 1_048_576
        try:
            with zipfile.ZipFile(Path(ctx.variables[source])) as archive:
                info = _find_member(archive, member)
                if info.file_size > max_bytes:
                    raise ActionError(f"'{member}' is larger than {max_mb} MB; refusing to read it")
                with archive.open(info) as handle:
                    raw = handle.read(max_bytes + 1)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ActionError(f"Cannot open the zip file: {type(exc).__name__}") from None
        if len(raw) > max_bytes:
            raise ActionError(f"'{member}' is larger than {max_mb} MB; stopped")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ActionError(f"'{member}' is not valid JSON; the stored followers were not changed") from None

        followers = parse_followers(data)
        result = sync_followers(ctx.database_url, account, followers, ctx.run_id)
        ctx.variables["followers_total"] = str(result.total)
        ctx.variables["followers_added"] = str(result.added)
        ctx.variables["followers_removed"] = str(result.removed)
        ctx.notify(
            f"Followers synced: {result.total} total, {result.added} new, "
            f"{result.removed} unfollowed, {result.unchanged} unchanged"
        )
