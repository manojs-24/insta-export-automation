"""Execution history and log storage.

A database problem must never stop or hide an export, so every method here swallows
DatabaseError after remembering it: the run carries on and the console still has
everything. Log lines are buffered in memory and written in batches.
"""

import logging
import threading
from dataclasses import dataclass, field

from web_automation.db.connection import connect, describe, ensure_schema
from web_automation.utils.exceptions import DatabaseError
from web_automation.utils.logger import get_logger

log = get_logger("history")

_FLUSH_EVERY = 25
_MAX_MESSAGE = 4000


def _clip(text: str | None) -> str | None:
    return None if text is None else text[:_MAX_MESSAGE]


@dataclass
class RunRecorder:
    """Creates a wa_runs row at the start, buffers logs, and finalises the row at the end."""

    url: str
    automation: str
    trigger: str = "manual"  # 'manual' or 'scheduled'
    run_id: int | None = None
    available: bool = False
    last_error: str | None = None
    _buffer: list[tuple] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _handler: logging.Handler | None = None

    # ------------------------------------------------------------------ start
    def start(self) -> "RunRecorder":
        try:
            ensure_schema(self.url)
            with connect(self.url) as conn:
                row = conn.execute(
                    "INSERT INTO wa_runs (automation, trigger) VALUES (%s, %s) RETURNING id",
                    (self.automation, self.trigger),
                ).fetchone()
            self.run_id = row[0]
            self.available = True
            self._attach_log_handler()
            log.info("Run %s recorded in the database (%s)", self.run_id, describe(self.url))
        except DatabaseError as exc:
            self._remember(exc)
        return self

    # ------------------------------------------------------------------- logs
    def _attach_log_handler(self) -> None:
        recorder = self

        class _DbHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                recorder.add_log(record.levelname, record.name, record.getMessage())

        self._handler = _DbHandler(level=logging.INFO)
        logging.getLogger("web_automation").addHandler(self._handler)

    def add_log(self, level: str, logger: str, message: str) -> None:
        if not self.available:
            return
        with self._lock:
            self._buffer.append((self.run_id, level, logger, _clip(message)))
            full = len(self._buffer) >= _FLUSH_EVERY
        if full:
            self.flush()

    def flush(self) -> None:
        if not self.available:
            return
        with self._lock:
            rows, self._buffer = self._buffer, []
        if not rows:
            return
        try:
            with connect(self.url) as conn, conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO wa_logs (run_id, level, logger, message) VALUES (%s, %s, %s, %s)",
                    rows,
                )
        except DatabaseError as exc:
            self._remember(exc)

    # ----------------------------------------------------------------- finish
    def finish(self, result, exit_code: int, facts: dict[str, bool]) -> None:
        """Store the final run row and its steps. `result` is an engine ExecutionResult."""
        if self._handler is not None:
            logging.getLogger("web_automation").removeHandler(self._handler)
            self._handler = None
        self.flush()
        if not self.available:
            return
        try:
            with connect(self.url) as conn:
                conn.execute(
                    "UPDATE wa_runs SET status=%s, completed_at=now(), exit_code=%s, stop_reason=%s,"
                    " steps_completed=%s, steps_failed=%s, execution_completed=%s,"
                    " export_request_submitted=%s, export_request_acknowledged=%s,"
                    " export_file_downloaded=%s, message=%s WHERE id=%s",
                    (
                        result.status, exit_code, _clip(result.stop_reason),
                        result.steps_completed, result.steps_failed,
                        facts["execution_completed"], facts["export_request_submitted"],
                        facts["export_request_acknowledged"], facts["export_file_downloaded"],
                        _clip(facts.get("message")), self.run_id,
                    ),
                )
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO wa_run_steps (run_id, position, step_id, action, status,"
                        " duration_ms, message, attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        [
                            (self.run_id, i, s.id, s.action, s.status, s.duration_ms,
                             _clip(s.message) or None, s.attempts)
                            for i, s in enumerate(result.steps, start=1)
                        ],
                    )
        except DatabaseError as exc:
            self._remember(exc)

    def mark_crashed(self, reason: str) -> None:
        """The run ended with an unexpected error before a result existed."""
        if self._handler is not None:
            logging.getLogger("web_automation").removeHandler(self._handler)
            self._handler = None
        self.flush()
        if not self.available:
            return
        try:
            with connect(self.url) as conn:
                conn.execute(
                    "UPDATE wa_runs SET status='failed', completed_at=now(), exit_code=1, message=%s"
                    " WHERE id=%s",
                    (_clip(reason), self.run_id),
                )
        except DatabaseError as exc:
            self._remember(exc)

    # ---------------------------------------------------------------- helpers
    def _remember(self, exc: DatabaseError) -> None:
        # Stop recording after the first failure: retrying would add a connect timeout to
        # every later write, and the console already has everything.
        self.last_error = str(exc)
        self.available = False
        log.warning("Database recording stopped (the run continues): %s", exc)
