"""Tests against a REAL Postgres. Skipped unless WA_TEST_DATABASE_URL is set.

Every test runs inside its own throwaway schema (created and dropped by the fixture), so
the application's real tables in that database are never touched. Point
WA_TEST_DATABASE_URL at your hosted database if you like: only the temporary schema is used.
"""

import os
import uuid

import psycopg
import pytest

from web_automation.db import RunRecorder, ensure_schema, sync_followers
from web_automation.db.connection import check_database, connect
from web_automation.db.followers import Follower
from web_automation.engine.executor import ExecutionResult, StepResult
from web_automation.engine.report import build_facts
from web_automation.utils.exceptions import DatabaseError

BASE_URL = os.environ.get("WA_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="set WA_TEST_DATABASE_URL to test against Postgres")


@pytest.fixture
def db_url():
    schema = f"wa_test_{uuid.uuid4().hex[:12]}"  # generated here, so safe to interpolate
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in BASE_URL else "?"
    try:
        yield f"{BASE_URL}{separator}options=-csearch_path%3D{schema}"
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def people(*names: str) -> list[Follower]:
    return [Follower(n, f"https://www.instagram.com/{n}", None) for n in names]


def rows(url: str, sql: str, *params) -> list[tuple]:
    with connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def test_schema_is_idempotent(db_url: str) -> None:
    ensure_schema(db_url)
    ensure_schema(db_url)
    assert set(check_database(db_url).values()) == {0}


def test_first_sync_adds_everyone_with_a_timestamp(db_url: str) -> None:
    ensure_schema(db_url)
    result = sync_followers(db_url, "acct", people("alice", "bob"))
    assert (result.total, result.added, result.removed, result.unchanged) == (2, 2, 0, 0)
    stored = rows(db_url, "SELECT username, added_at IS NOT NULL FROM wa_followers ORDER BY username")
    assert stored == [("alice", True), ("bob", True)]


def test_later_sync_adds_only_new_and_removes_unfollowed(db_url: str) -> None:
    ensure_schema(db_url)
    sync_followers(db_url, "acct", people("alice", "bob", "carol"))
    first_added = dict(rows(db_url, "SELECT username, added_at FROM wa_followers"))
    result = sync_followers(db_url, "acct", people("bob", "carol", "dave"))
    assert (result.added, result.removed, result.unchanged) == (1, 1, 2)
    assert [r[0] for r in rows(db_url, "SELECT username FROM wa_followers ORDER BY username")] == ["bob", "carol", "dave"]
    now = dict(rows(db_url, "SELECT username, added_at FROM wa_followers"))
    assert now["bob"] == first_added["bob"] and now["carol"] == first_added["carol"]  # untouched
    events = rows(db_url, "SELECT username, event FROM wa_follower_events ORDER BY id")
    assert ("alice", "removed") in events and ("dave", "added") in events


def test_identical_sync_changes_nothing(db_url: str) -> None:
    ensure_schema(db_url)
    sync_followers(db_url, "acct", people("alice", "bob"))
    before = rows(db_url, "SELECT * FROM wa_followers ORDER BY username")
    result = sync_followers(db_url, "acct", people("alice", "bob"))
    assert (result.added, result.removed) == (0, 0)
    assert rows(db_url, "SELECT * FROM wa_followers ORDER BY username") == before


def test_accounts_are_kept_separate(db_url: str) -> None:
    ensure_schema(db_url)
    sync_followers(db_url, "one", people("alice"))
    sync_followers(db_url, "two", people("bob"))
    assert rows(db_url, "SELECT account, username FROM wa_followers ORDER BY account") == [("one", "alice"), ("two", "bob")]


def test_a_suspicious_drop_changes_nothing(db_url: str) -> None:
    ensure_schema(db_url)
    names = [f"user{i:03d}" for i in range(100)]
    sync_followers(db_url, "acct", people(*names))
    with pytest.raises(DatabaseError, match="looks incomplete"):
        sync_followers(db_url, "acct", people(*names[:20]))
    assert rows(db_url, "SELECT count(*) FROM wa_followers")[0][0] == 100


def test_empty_list_changes_nothing(db_url: str) -> None:
    ensure_schema(db_url)
    sync_followers(db_url, "acct", people("alice"))
    with pytest.raises(DatabaseError):
        sync_followers(db_url, "acct", [])
    assert rows(db_url, "SELECT count(*) FROM wa_followers")[0][0] == 1


def test_run_history_steps_and_logs_are_stored(db_url: str) -> None:
    import logging

    recorder = RunRecorder(db_url, "demo").start()
    assert recorder.available and recorder.run_id
    logging.getLogger("web_automation.test").warning("hello from the run")
    result = ExecutionResult(
        "demo", "success", "t0", "t1",
        [StepResult("start_export", "click", "ok", 120), StepResult("request_acknowledged", "wait", "ok", 900)],
    )
    recorder.finish(result, 0, build_facts(result))
    run = rows(db_url, "SELECT status, exit_code, export_request_submitted, export_request_acknowledged, completed_at IS NOT NULL FROM wa_runs")
    assert run == [("success", 0, True, True, True)]
    assert rows(db_url, "SELECT step_id, status, duration_ms FROM wa_run_steps ORDER BY position") == [
        ("start_export", "ok", 120), ("request_acknowledged", "ok", 900),
    ]
    assert any("hello from the run" in r[0] for r in rows(db_url, "SELECT message FROM wa_logs"))


def test_crashed_run_is_marked_failed(db_url: str) -> None:
    recorder = RunRecorder(db_url, "demo").start()
    recorder.mark_crashed("ZeroDivisionError")
    assert rows(db_url, "SELECT status, exit_code, message FROM wa_runs") == [("failed", 1, "ZeroDivisionError")]
