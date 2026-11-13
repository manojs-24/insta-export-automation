"""Read-only queries behind the web UI. Every value from outside is passed as a parameter,
never pasted into the SQL text."""

from datetime import datetime
from typing import Any

from web_automation.db.connection import connect

MAX_PAGE_SIZE = 500


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE_SIZE)), max(0, int(offset))


def _like(search: str | None) -> str | None:
    """A LIKE pattern for a plain-text search; % and _ typed by the user are matched literally."""
    if not search:
        return None
    escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if escaped else None


def _rows(url: str, sql: str, params: tuple) -> list[dict[str, Any]]:
    with connect(url) as conn:
        cur = conn.execute(sql, params)
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _one(url: str, sql: str, params: tuple = ()) -> Any:
    with connect(url) as conn:
        return conn.execute(sql, params).fetchone()[0]


def accounts(url: str) -> list[str]:
    return [r["account"] for r in _rows(
        url, "SELECT DISTINCT account FROM wa_followers ORDER BY account", ()
    )]


def summary(url: str, account: str) -> dict[str, Any]:
    """Headline numbers for the dashboard."""
    with connect(url) as conn:
        total = conn.execute("SELECT count(*) FROM wa_followers WHERE account=%s", (account,)).fetchone()[0]
        added_7 = conn.execute(
            "SELECT count(*) FROM wa_follower_events WHERE account=%s AND event='added'"
            " AND happened_at > now() - interval '7 days'", (account,)).fetchone()[0]
        removed_7 = conn.execute(
            "SELECT count(*) FROM wa_follower_events WHERE account=%s AND event='removed'"
            " AND happened_at > now() - interval '7 days'", (account,)).fetchone()[0]
        removed_all = conn.execute(
            "SELECT count(*) FROM wa_follower_events WHERE account=%s AND event='removed'", (account,)
        ).fetchone()[0]
        last = conn.execute(
            "SELECT id, status, started_at, completed_at, trigger FROM wa_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "account": account,
        "followers": total,
        "added_last_7_days": added_7,
        "unfollowed_last_7_days": removed_7,
        "unfollowed_total": removed_all,
        "last_run": None if last is None else {
            "id": last[0], "status": last[1], "started_at": last[2], "completed_at": last[3],
            "trigger": last[4],
        },
    }


def followers(url: str, account: str, search: str | None, limit: int, offset: int) -> dict[str, Any]:
    limit, offset = _page(limit, offset)
    pattern = _like(search)
    where, params = "account = %s", [account]
    if pattern:
        where += " AND username ILIKE %s ESCAPE '\\'"
        params.append(pattern)
    total = _one(url, f"SELECT count(*) FROM wa_followers WHERE {where}", tuple(params))  # noqa: S608 - fixed text
    items = _rows(
        url,
        f"SELECT username, profile_url, followed_at, added_at FROM wa_followers WHERE {where}"  # noqa: S608
        " ORDER BY added_at DESC, username LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return {"total": total, "items": items}


def follower_events(
    url: str, account: str, event: str, since: datetime | None, limit: int, offset: int
) -> dict[str, Any]:
    """Recent additions or unfollows. `event` is 'added' or 'removed'."""
    if event not in ("added", "removed"):
        raise ValueError("event must be 'added' or 'removed'")
    limit, offset = _page(limit, offset)
    where, params = "account = %s AND event = %s", [account, event]
    if since is not None:
        where += " AND happened_at >= %s"
        params.append(since)
    total = _one(url, f"SELECT count(*) FROM wa_follower_events WHERE {where}", tuple(params))  # noqa: S608
    items = _rows(
        url,
        f"SELECT username, event, happened_at, run_id FROM wa_follower_events WHERE {where}"  # noqa: S608
        " ORDER BY happened_at DESC, id DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return {"total": total, "items": items}


def runs(url: str, limit: int, offset: int) -> dict[str, Any]:
    limit, offset = _page(limit, offset)
    total = _one(url, "SELECT count(*) FROM wa_runs")
    items = _rows(
        url,
        "SELECT id, automation, trigger, status, started_at, completed_at, exit_code, stop_reason,"
        " steps_completed, steps_failed, execution_completed, export_request_submitted,"
        " export_request_acknowledged, export_file_downloaded, message"
        " FROM wa_runs ORDER BY id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
    return {"total": total, "items": items}


def run_steps(url: str, run_id: int) -> list[dict[str, Any]]:
    return _rows(
        url,
        "SELECT position, step_id, action, status, duration_ms, attempts, message"
        " FROM wa_run_steps WHERE run_id = %s ORDER BY position",
        (run_id,),
    )


def logs(
    url: str, run_id: int | None, level: str | None, search: str | None, limit: int, offset: int
) -> dict[str, Any]:
    limit, offset = _page(limit, offset)
    clauses, params = [], []
    if run_id is not None:
        clauses.append("run_id = %s")
        params.append(run_id)
    if level:
        clauses.append("level = %s")
        params.append(level.upper())
    pattern = _like(search)
    if pattern:
        clauses.append("message ILIKE %s ESCAPE '\\'")
        params.append(pattern)
    where = " AND ".join(clauses) or "true"
    total = _one(url, f"SELECT count(*) FROM wa_logs WHERE {where}", tuple(params))  # noqa: S608
    items = _rows(
        url,
        f"SELECT id, run_id, logged_at, level, logger, message FROM wa_logs WHERE {where}"  # noqa: S608
        " ORDER BY id DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return {"total": total, "items": items}
