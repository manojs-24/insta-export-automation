"""Run a validated workflow on an open browser page and record it in the database.

Shared by the command line and the web UI, so both behave identically. Only the way the
person is asked questions (OTP, "press Enter", yes/no) differs, and that is passed in.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from playwright.sync_api import Page

from web_automation.config.schema import AutomationConfig
from web_automation.db import RunRecorder
from web_automation.engine.context import ExecutionContext
from web_automation.engine.executor import ExecutionResult
from web_automation.engine.report import build_facts
from web_automation.engine.runner import run_config
from web_automation.settings import Settings

EXIT_CODES = {"success": 0, "failed": 2, "stopped": 3}


@dataclass(frozen=True)
class Interaction:
    """How a run talks to the person. Anything left as None keeps the console behaviour."""

    notify: Callable[[str], None] | None = None
    warn: Callable[[str], None] | None = None
    wait_for_user: Callable[[str], None] | None = None
    confirm_with_user: Callable[[str], bool] | None = None
    ask_secret: Callable[[str], str] | None = None


@dataclass
class RunOutcome:
    result: ExecutionResult
    facts: dict[str, bool | str]
    exit_code: int
    run_id: int | None

    @property
    def report(self) -> dict[str, Any]:
        data = self.result.to_dict()
        data.update(self.facts)
        data["run_id"] = self.run_id
        return data


def settings_for(config: AutomationConfig, settings: Settings) -> Settings:
    """A config that asks for headless mode wins over the .env default."""
    return replace(settings, headless=True) if config.browser.headless else settings


def execute_on_page(
    config: AutomationConfig,
    settings: Settings,
    page: Page,
    interaction: Interaction | None = None,
    trigger: str = "manual",
) -> RunOutcome:
    interaction = interaction or Interaction()
    warn = interaction.warn or (lambda message: print(message, file=sys.stderr))

    recorder = (
        RunRecorder(settings.database_url, config.name, trigger).start()
        if settings.database_url
        else None
    )
    if recorder is None:
        warn("Note: WA_DATABASE_URL is not set; history and followers are not stored.")
    elif not recorder.available:
        warn(f"Warning: database unavailable, continuing without it ({recorder.last_error})")

    callbacks = {
        name: value
        for name, value in {
            "notify": interaction.notify,
            "wait_for_user": interaction.wait_for_user,
            "confirm_with_user": interaction.confirm_with_user,
            "ask_secret": interaction.ask_secret,
        }.items()
        if value is not None
    }
    ctx = ExecutionContext(
        page=page,
        timeout_ms=settings.timeout_ms,
        export_dir=settings.export_dir,
        database_url=settings.database_url,
        run_id=recorder.run_id if recorder and recorder.available else None,
        **callbacks,
    )
    try:
        result = run_config(config, ctx)
    except BaseException as exc:  # record the crash, then let it propagate
        if recorder:
            recorder.mark_crashed(type(exc).__name__)
        raise

    exit_code = EXIT_CODES[result.status]
    facts = build_facts(result)
    if recorder:
        recorder.finish(result, exit_code, facts)
    return RunOutcome(result, facts, exit_code, ctx.run_id)
