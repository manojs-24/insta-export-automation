import getpass
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page


def _wait_for_enter(message: str) -> None:
    input(message)


def _ask_secret(message: str) -> str:
    """Read a secret (e.g. an OTP) from the terminal without echoing it."""
    return getpass.getpass(message)


def _ask_yes_no(message: str) -> bool:
    return input(f"{message} [y/N]: ").strip().lower() in {"y", "yes"}


@dataclass
class ExecutionContext:
    """Everything an action may use while a run executes.

    The user-interaction callbacks are injectable so tests (and a future API backend)
    can replace the console prompts.
    """

    page: Page
    timeout_ms: int = 30_000
    export_dir: Path = Path("exports")  # downloads and extracted files stay inside this folder
    database_url: str = field(default="", repr=False)  # contains a password: never printed
    run_id: int | None = None  # the wa_runs row of this execution, when recording is on
    variables: dict[str, str] = field(default_factory=dict)  # values saved by extract_*
    current_step_id: str = ""
    notify: Callable[[str], None] = print
    wait_for_user: Callable[[str], None] = _wait_for_enter
    confirm_with_user: Callable[[str], bool] = _ask_yes_no
    ask_secret: Callable[[str], str] = _ask_secret
