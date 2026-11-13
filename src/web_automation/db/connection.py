"""Postgres connection helpers. The connection URL contains a password: it is never printed,
logged, put in an exception message, or kept in a repr."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from urllib.parse import urlsplit

import psycopg

from web_automation.utils.exceptions import DatabaseError

_CONNECT_TIMEOUT_S = 10


def validate_url(url: str) -> str:
    """Reject anything that is not a postgres URL, without echoing the value."""
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
        raise DatabaseError(
            "WA_DATABASE_URL must look like postgresql://user:password@host:5432/dbname"
        )
    return url


def describe(url: str) -> str:
    """A log-safe description of the target (host and database only, never the credentials)."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path or ''}"


@contextmanager
def connect(url: str) -> Iterator[psycopg.Connection]:
    """Open a connection; commit on success, roll back on error, always close.

    Errors are re-raised as DatabaseError carrying only the error class and the SQLSTATE,
    because driver messages can include connection details.
    """
    validate_url(url)
    try:
        conn = psycopg.connect(url, connect_timeout=_CONNECT_TIMEOUT_S)
    except psycopg.Error as exc:
        raise DatabaseError(
            f"Could not connect to the database ({describe(url)}): {type(exc).__name__}"
        ) from None
    try:
        with conn:
            yield conn
    except psycopg.Error as exc:
        raise DatabaseError(
            f"Database operation failed: {type(exc).__name__} (SQLSTATE {exc.sqlstate or '?'})"
        ) from None
    finally:
        conn.close()


TABLES = ("wa_runs", "wa_run_steps", "wa_logs", "wa_followers", "wa_follower_events")


def check_database(url: str) -> dict[str, int]:
    """Connect, create any missing tables, and return the row count of each table."""
    ensure_schema(url)
    with connect(url) as conn:
        # TABLES is a fixed list in this file, never user input
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def ensure_schema(url: str) -> None:
    """Create the tables if they do not exist. Safe to run on every start."""
    sql = resources.files("web_automation.db").joinpath("schema.sql").read_text(encoding="utf-8")
    with connect(url) as conn:
        conn.execute(sql)
