"""Runs one workflow at a time on a background thread, and lets the web page answer the
questions a run asks (the OTP code, "press Enter", yes/no).

The OTP code only ever lives in memory for the moment it is handed to the run: it is never
written to the database, the logs, the status the page polls, or any file.
"""

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from web_automation.browser import BrowserManager
from web_automation.config import load_config
from web_automation.engine.service import Interaction, execute_on_page, settings_for
from web_automation.settings import Settings
from web_automation.utils.exceptions import WebAutomationError
from web_automation.utils.logger import configure_logging

WAIT_TIMEOUT_S = 600  # default; WA_ANSWER_WAIT_MINUTES in .env overrides it
MAX_CODE_LENGTH = 32


class RunAbandoned(WebAutomationError):
    """The person did not answer a question in time (or the run was cancelled)."""


@dataclass
class Question:
    kind: str  # "secret" (OTP), "continue" (press Enter), "confirm" (yes/no)
    prompt: str
    id: int = 0


@dataclass
class _State:
    status: str = "idle"  # idle | running | success | failed | stopped | error
    config: str | None = None
    trigger: str = "manual"  # who started it: 'manual' (button/CLI) or 'scheduled'
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""
    report: dict[str, Any] | None = None
    question: Question | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=200))


class WebRunner:
    def __init__(
        self,
        settings_loader: Callable[[], Settings] = Settings.load,
        browser_factory: Callable[..., BrowserManager] = BrowserManager,
        wait_timeout_s: float | None = None,  # None = use WA_ANSWER_WAIT_MINUTES
    ) -> None:
        self._settings_loader = settings_loader
        self._browser_factory = browser_factory
        self._fixed_wait_timeout_s = wait_timeout_s
        self._wait_timeout_s = wait_timeout_s if wait_timeout_s is not None else WAIT_TIMEOUT_S
        self._lock = threading.RLock()
        self._answered = threading.Event()
        self._answer: Any = None
        self._question_counter = 0
        self._cancelled = False
        self._state = _State()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        """What the page polls. Never contains an answer the person typed."""
        with self._lock:
            s = self._state
            q = s.question
            return {
                "status": s.status,
                "config": s.config,
                "trigger": s.trigger,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "message": s.message,
                "report": s.report,
                "question": None if q is None else {"id": q.id, "kind": q.kind, "prompt": q.prompt},
                "events": list(s.events),
            }

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    def _event(self, text: str) -> None:
        with self._lock:
            self._state.events.append({"at": datetime.now().isoformat(timespec="seconds"), "text": text})

    # ------------------------------------------------------------------- start
    def start(self, config_path: Path, trigger: str = "manual") -> None:
        if trigger not in ("manual", "scheduled"):
            raise ValueError("trigger must be 'manual' or 'scheduled'")
        with self._lock:
            if self.running:
                raise WebAutomationError("A run is already in progress")
            # Validate before anything starts, so a bad file fails right here with a clear error.
            config = load_config(config_path)
            self._cancelled = False
            self._state = _State(
                status="running", config=config.name, trigger=trigger, started_at=datetime.now()
            )
            self._event(f"Started '{config.name}' ({'automatic' if trigger == 'scheduled' else 'manual'} run)")
            self._thread = threading.Thread(
                target=self._run, args=(config, trigger), name="workflow-run", daemon=True
            )
            self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, config, trigger: str) -> None:
        final = {"status": "error", "message": "", "report": None}
        try:
            settings = settings_for(config, self._settings_loader())
            configure_logging()
            if self._fixed_wait_timeout_s is None:  # an explicit value (tests) wins over .env
                self._wait_timeout_s = settings.answer_wait_minutes * 60
            with self._browser_factory(settings.browser_config()) as session:
                outcome = execute_on_page(
                    config,
                    settings,
                    session.page,
                    Interaction(
                        notify=self._event,
                        warn=self._event,
                        wait_for_user=lambda prompt: self._ask("continue", prompt),
                        confirm_with_user=lambda prompt: bool(self._ask("confirm", prompt)),
                        ask_secret=lambda prompt: str(self._ask("secret", prompt)),
                    ),
                    trigger=trigger,
                )
            final = {
                "status": outcome.result.status,
                "message": str(outcome.facts.get("message", "")),
                "report": outcome.report,
            }
        except RunAbandoned as exc:
            final = {"status": "failed", "message": str(exc), "report": None}
        except WebAutomationError as exc:
            final = {"status": "error", "message": str(exc), "report": None}
        except Exception as exc:  # never let the thread die silently
            final = {"status": "error", "message": f"Unexpected error: {type(exc).__name__}", "report": None}
        finally:
            with self._lock:
                self._state.status = final["status"]
                self._state.message = final["message"]
                self._state.report = final["report"]
                self._state.finished_at = datetime.now()
                self._state.question = None
                self._event(f"Finished: {final['status']}")

    # --------------------------------------------------------------- questions
    def _ask(self, kind: str, prompt: str) -> Any:
        """Called on the run thread: show a question on the page and wait for the answer."""
        with self._lock:
            self._question_counter += 1
            self._answer = None
            self._answered.clear()
            self._state.question = Question(kind=kind, prompt=prompt, id=self._question_counter)
            self._event("Waiting for you: " + ("enter the code in the page" if kind == "secret" else prompt))
        answered = self._answered.wait(self._wait_timeout_s)
        with self._lock:
            self._state.question = None
            answer, self._answer = self._answer, None
            if self._cancelled:
                raise RunAbandoned("The run was cancelled")
        if not answered:
            minutes = max(1, round(self._wait_timeout_s / 60))
            raise RunAbandoned(
                f"Nobody answered within {minutes} minute(s); the run was stopped"
                + (" (the next scheduled run will try again)" if self._state.trigger == "scheduled" else "")
            )
        return answer

    def answer(self, question_id: int, value: Any) -> None:
        with self._lock:
            q = self._state.question
            if q is None or q.id != question_id:
                raise WebAutomationError("There is no question waiting for that answer")
            if q.kind == "secret":
                if not isinstance(value, str) or len(value) > MAX_CODE_LENGTH:
                    raise WebAutomationError("The code is too long or not text")
                value = value.strip()
            elif q.kind == "confirm":
                value = bool(value)
            else:
                value = None
            self._answer = value
            self._answered.set()

    def cancel(self) -> None:
        """Stop waiting on a question. A step that is mid-click finishes first."""
        with self._lock:
            self._cancelled = True
            self._answered.set()
            self._event("Cancel requested")
