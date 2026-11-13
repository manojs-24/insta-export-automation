"""Keep the `wa_followers` table equal to the latest export's follower list.

Per sync: followers not yet in the table are inserted (added_at = now); followers in the
table but missing from the latest list are deleted (they unfollowed); followers present in
both are left alone, so `added_at` keeps meaning "when we first saw them". Everything happens
in one transaction, and the table is never touched when the list looks wrong.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from web_automation.db.connection import connect
from web_automation.utils.exceptions import DatabaseError

# A real export always lists many followers. Syncing an empty or tiny list over a populated
# table would wipe the followers, so a drop of more than this share is refused as suspicious.
_MAX_DROP_FRACTION = 0.5
_MIN_EXISTING_FOR_DROP_CHECK = 20


@dataclass(frozen=True)
class Follower:
    username: str
    profile_url: str | None
    followed_at: datetime | None


@dataclass(frozen=True)
class SyncResult:
    account: str
    total: int
    added: int
    removed: int
    unchanged: int


def parse_followers(data: Any) -> list[Follower]:
    """Read Instagram's followers_1.json structure. Raises DatabaseError on an unexpected shape."""
    if not isinstance(data, list):
        raise DatabaseError("followers file must contain a JSON list")
    found: dict[str, Follower] = {}
    for item in data:
        entries = item.get("string_list_data") if isinstance(item, dict) else None
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            raise DatabaseError("unexpected followers file structure (no string_list_data)")
        entry = entries[0]
        href = entry.get("href") if isinstance(entry.get("href"), str) else None
        username = entry.get("value") if isinstance(entry.get("value"), str) else None
        if not username and href:  # some exports leave `value` empty; the profile URL ends in it
            username = urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1]
        if not username:
            raise DatabaseError("a follower entry has no username")
        stamp = entry.get("timestamp")
        followed_at = (
            datetime.fromtimestamp(stamp, tz=timezone.utc)
            if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > 0
            else None
        )
        found[username] = Follower(username, href, followed_at)
    return list(found.values())


def plan_sync(existing: set[str], latest: set[str]) -> tuple[list[str], list[str]]:
    """What a sync changes: (usernames to add, usernames to remove). Pure; no database."""
    return sorted(latest - existing), sorted(existing - latest)


def check_drop_is_plausible(existing_count: int, remove_count: int) -> None:
    """Refuse a sync that would delete a large share of stored followers (incomplete export)."""
    if (
        existing_count >= _MIN_EXISTING_FOR_DROP_CHECK
        and remove_count > existing_count * _MAX_DROP_FRACTION
    ):
        raise DatabaseError(
            f"refusing to remove {remove_count} of {existing_count} stored followers; "
            "the new list looks incomplete. Nothing was changed."
        )


def sync_followers(
    url: str, account: str, followers: list[Follower], run_id: int | None = None
) -> SyncResult:
    """Make the table match `followers` for `account`. Returns what changed."""
    if not account:
        raise DatabaseError("an account name is required to store followers")
    if not followers:
        raise DatabaseError("the followers list is empty; refusing to wipe the stored followers")
    latest = {f.username: f for f in followers}

    with connect(url) as conn:
        with conn.transaction():
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT username FROM wa_followers WHERE account = %s FOR UPDATE", (account,)
                )
            }
            add_names, to_remove = plan_sync(existing, set(latest))
            to_add = [latest[name] for name in add_names]
            check_drop_is_plausible(len(existing), len(to_remove))

            if to_remove:
                conn.execute(
                    "DELETE FROM wa_followers WHERE account = %s AND username = ANY(%s)",
                    (account, to_remove),
                )
            if to_add:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO wa_followers (account, username, profile_url, followed_at) "
                        "VALUES (%s, %s, %s, %s)",
                        [(account, f.username, f.profile_url, f.followed_at) for f in to_add],
                    )
            events = [(run_id, account, name, "removed") for name in to_remove] + [
                (run_id, account, f.username, "added") for f in to_add
            ]
            if events:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO wa_follower_events (run_id, account, username, event) "
                        "VALUES (%s, %s, %s, %s)",
                        events,
                    )

    return SyncResult(
        account=account,
        total=len(latest),
        added=len(to_add),
        removed=len(to_remove),
        unchanged=len(existing & latest.keys()),
    )


def load_followers_file(path: Path) -> list[Follower]:
    try:
        return parse_followers(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise DatabaseError(f"cannot read the followers file: {type(exc).__name__}") from None


__all__ = ["Follower", "SyncResult", "load_followers_file", "parse_followers", "plan_sync", "sync_followers"]
