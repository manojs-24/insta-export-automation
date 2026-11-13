"""Starts the configured workflow every WA_SCHEDULE_MINUTES minutes, using APScheduler.

The schedule lives in the web UI process: when the UI is not running, nothing runs. It
never starts a second run on top of one that is still in progress; that tick is skipped.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from web_automation.utils.exceptions import WebAutomationError
from web_automation.utils.logger import get_logger
from web_automation.web.runner import WebRunner

log = get_logger("scheduler")

JOB_ID = "scheduled-run"


class RunScheduler:
    def __init__(
        self,
        runner: WebRunner,
        configs_dir: Path,
        config_name: str,
        minutes: int,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self._runner = runner
        self._config_path = configs_dir / config_name
        self._config_name = config_name
        self.minutes = minutes
        # Quieten APScheduler's own chatter; our log lines say what matters.
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        self._scheduler = scheduler or BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
        )
        self.last_tick: datetime | None = None
        self.last_tick_result: str = ""

    # ----------------------------------------------------------------- lifecycle
    @property
    def enabled(self) -> bool:
        return self.minutes > 0

    def start(self) -> None:
        if not self.enabled:
            log.info("Automatic runs are off (WA_SCHEDULE_MINUTES is 0 or not set)")
            return
        if not self._config_path.is_file():
            raise WebAutomationError(
                f"WA_SCHEDULE_CONFIG '{self._config_name}' was not found in {self._config_path.parent}"
            )
        # The first automatic run happens one full interval after start-up, not immediately,
        # so opening the UI never triggers an export by surprise.
        self._scheduler.add_job(
            self.tick,
            "interval",
            minutes=self.minutes,
            id=JOB_ID,
            next_run_time=datetime.now().astimezone() + timedelta(minutes=self.minutes),
            replace_existing=True,
        )
        self._scheduler.start()
        log.info(
            "Automatic runs every %d minute(s) of '%s'; the first at %s",
            self.minutes, self._config_name, self.next_run_time().strftime("%H:%M:%S"),
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def next_run_time(self) -> datetime | None:
        if not self.enabled or not self._scheduler.running:
            return None
        job = self._scheduler.get_job(JOB_ID)
        return job.next_run_time if job else None

    # --------------------------------------------------------------------- a tick
    def tick(self) -> str:
        """One scheduled moment. Returns what happened (also kept for the status page)."""
        self.last_tick = datetime.now().astimezone()
        if self._runner.running:
            result = "skipped: a run was still in progress"
            log.warning("Scheduled run skipped: a run is still in progress")
        else:
            try:
                self._runner.start(self._config_path, trigger="scheduled")
                result = "started"
                log.info("Scheduled run started")
            except WebAutomationError as exc:  # e.g. the file became invalid
                result = f"could not start: {exc}"
                log.error("Scheduled run could not start: %s", exc)
        self.last_tick_result = result
        return result

    def status(self) -> dict[str, Any]:
        next_run = self.next_run_time()
        return {
            "enabled": self.enabled,
            "minutes": self.minutes,
            "config": self._config_name if self.enabled else None,
            "next_run": next_run.isoformat() if next_run else None,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
            "last_tick_result": self.last_tick_result,
        }
