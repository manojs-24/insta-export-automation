import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from web_automation.actions import Step, get_action
from web_automation.actions.base import (
    RETRY_ANY,
    RETRY_PRE_ACTION,
    condition_met,
    retry_settings,
    validate_retry,
    validate_when,
)
from web_automation.engine.context import ExecutionContext
from web_automation.utils.exceptions import (
    ActionError,
    ElementNotFoundError,
    UserDeclinedError,
    WebAutomationError,
    WorkflowStopped,
)
from web_automation.utils.logger import get_logger

log = get_logger("executor")


@dataclass
class StepResult:
    id: str
    action: str
    status: str  # "ok" | "failed" | "stopped" (a guard halted the run on purpose) | "skipped"
    duration_ms: int = 0
    message: str = ""
    attempts: int = 1  # more than 1 only for steps that were retried


@dataclass
class ExecutionResult:
    """Structured outcome of a run. Extracted values are deliberately not included."""

    automation: str
    status: str  # "success" | "failed" | "stopped"
    started_at: str
    completed_at: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def stop_reason(self) -> str | None:
        """Why a guard stopped the run, if one did."""
        return next((s.message for s in self.steps if s.status == "stopped"), None)

    @property
    def steps_completed(self) -> int:
        return sum(s.status == "ok" for s in self.steps)

    @property
    def steps_failed(self) -> int:
        return sum(s.status == "failed" for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps_completed"], data["steps_failed"] = self.steps_completed, self.steps_failed
        data["stop_reason"] = self.stop_reason
        return data


def _safe_url(url: str) -> str:
    """URL without query/fragment, since those can carry tokens."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class Executor:
    """Runs steps one after another against an ExecutionContext."""

    def __init__(self, ctx: ExecutionContext) -> None:
        self.ctx = ctx
        self.attempts_used: dict[str, int] = {}  # step id -> attempts, only for retried steps

    def run(
        self, steps: Sequence[Step | dict[str, Any]], name: str = "adhoc", on_failure: str = "stop"
    ) -> ExecutionResult:
        if on_failure not in ("stop", "continue"):
            raise ValueError("on_failure must be 'stop' or 'continue'")
        parsed = [s if isinstance(s, Step) else Step.from_dict(s) for s in steps]

        # Validate everything up front so a typo never leaves a half-executed workflow.
        seen: set[str] = set()
        defined = set(self.ctx.variables)  # variables a `when` may rely on
        for step in parsed:
            if step.id in seen:
                raise ActionError(f"Duplicate step id '{step.id}'")
            seen.add(step.id)
            get_action(step.action)
            validate_retry(step.id, step.action, step.retry)
            if step.when is not None:
                validate_when(step.id, step.when)
                if step.when["variable"] not in defined:
                    raise ActionError(
                        f"Step '{step.id}' depends on variable '{step.when['variable']}', "
                        "which no earlier step sets (save_as)"
                    )
            saved = step.params.get("save_as")
            if isinstance(saved, str):
                defined.add(saved)

        started = datetime.now().isoformat(timespec="seconds")
        results: list[StepResult] = []
        halted = False
        log.info("Run '%s' started (%d steps, on_failure=%s)", name, len(parsed), on_failure)
        for step in parsed:
            if halted:
                results.append(StepResult(step.id, step.action, "skipped"))
                continue
            result, fatal = self._run_step(step)
            results.append(result)
            if result.status == "stopped" or (
                result.status == "failed" and (on_failure == "stop" or fatal)
            ):
                halted = True

        if any(r.status == "failed" for r in results):
            status = "failed"
        elif any(r.status == "stopped" for r in results):
            status = "stopped"
        else:
            status = "success"
        log.info("Run '%s' finished: %s", name, status)
        return ExecutionResult(
            automation=name,
            status=status,
            started_at=started,
            completed_at=datetime.now().isoformat(timespec="seconds"),
            steps=results,
        )

    @staticmethod
    def _may_retry(policy: str, exc: Exception) -> bool:
        """Is repeating the action safe after this particular failure?

        Only errors that happen before the action had any effect qualify. A guard stop, a
        declined confirmation, bad parameters and anything unexpected never do.
        """
        if isinstance(exc, (WorkflowStopped, UserDeclinedError)):
            return False
        if policy == RETRY_ANY:
            return isinstance(exc, (ElementNotFoundError, PlaywrightError))
        if policy == RETRY_PRE_ACTION:
            # The element was not found, so nothing has been clicked, typed or selected yet.
            return isinstance(exc, ElementNotFoundError)
        return False

    def _run_step(self, step: Step) -> tuple[StepResult, bool]:
        self.ctx.current_step_id = step.id
        if step.when is not None and not condition_met(step.when, self.ctx.variables):
            # Not an error and not a halt: later steps carry on. The variable's value is
            # deliberately left out of the message and the log.
            message = f"condition on '{step.when['variable']}' not met"
            log.info("Step '%s' skipped: %s", step.id, message)
            return StepResult(step.id, step.action, "skipped", 0, message), False
        began = time.monotonic()
        log.info("Step '%s' (%s) started", step.id, step.action)
        action = get_action(step.action)
        attempts, delay_ms = retry_settings(step.retry)
        try:
            for attempt in range(1, attempts + 1):
                try:
                    action.run(self.ctx, step.params)
                    break
                except (WebAutomationError, PlaywrightError) as exc:
                    if attempt == attempts or not self._may_retry(action.retry_policy, exc):
                        raise
                    log.warning(
                        "Step '%s' attempt %d/%d failed (%s); trying again in %d ms",
                        step.id, attempt, attempts, type(exc).__name__, delay_ms,
                    )
                    self.attempts_used[step.id] = attempt + 1
                    self.ctx.page.wait_for_timeout(delay_ms)
        except WorkflowStopped as exc:
            log.warning("Step '%s' stopped the run: %s | url=%s", step.id, exc, self._current_url())
            return StepResult(step.id, step.action, "stopped", self._ms(began), str(exc)), True
        except (WebAutomationError, PlaywrightError) as exc:
            message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            log.error("Step '%s' failed: %s | url=%s", step.id, message, self._current_url())
            return (
                StepResult(
                    step.id, step.action, "failed", self._ms(began), message,
                    attempts=self.attempts_used.get(step.id, 1),
                ),
                isinstance(exc, UserDeclinedError),
            )
        used = self.attempts_used.get(step.id, 1)
        log.info("Step '%s' ok%s", step.id, f" (after {used} attempts)" if used > 1 else "")
        return StepResult(step.id, step.action, "ok", self._ms(began), attempts=used), False

    @staticmethod
    def _ms(began: float) -> int:
        return int((time.monotonic() - began) * 1000)

    def _current_url(self) -> str:
        try:
            return _safe_url(self.ctx.page.url)
        except PlaywrightError:
            return "<unavailable>"
