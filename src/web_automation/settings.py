import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from web_automation.browser.session import BrowserConfig
from web_automation.db.connection import validate_url
from web_automation.utils.exceptions import WebAutomationError

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


MIN_SCHEDULE_MINUTES = 5       # a typo must not turn into a flood of runs against Instagram
MAX_SCHEDULE_MINUTES = 10_080  # one week
MIN_ANSWER_WAIT_MINUTES = 1
MAX_ANSWER_WAIT_MINUTES = 240


class SettingsError(WebAutomationError):
    """An environment variable has an invalid value."""


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError(f"{name} must be true/false, got {raw!r}")


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from None


def _clean_config_name(value: str) -> str:
    """A bare *.json file name only: no folders, no '..' (it is looked up in configs/)."""
    name = value.strip() or "instagram_export.json"
    if name != Path(name).name or not name.endswith(".json") or name.startswith("."):
        raise SettingsError("WA_SCHEDULE_CONFIG must be the name of a .json file in configs/")
    return name


_HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def _clean_hosts(value: str) -> tuple[str, ...]:
    """Comma-separated bare host names the web page may be reached as (no scheme, port or path)."""
    hosts: list[str] = []
    for part in value.split(","):
        host = part.strip().lower()
        if not host:
            continue
        if not _HOST_NAME.fullmatch(host):
            raise SettingsError(
                "WA_UI_ALLOWED_HOSTS must be a comma-separated list of plain host names such as "
                "'automation.example.com' (no http://, no port, no path, no wildcard)"
            )
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > 20:
        raise SettingsError("WA_UI_ALLOWED_HOSTS may list at most 20 host names")
    return tuple(hosts)


def _get_minutes(name: str, default: int, low: int, high: int, allow_zero: bool = False) -> int:
    value = _get_int(name, default)
    if allow_zero and value == 0:
        return 0
    if not low <= value <= high:
        zero = " (or 0 to turn it off)" if allow_zero else ""
        raise SettingsError(f"{name} must be between {low} and {high} minutes{zero}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from environment variables / the .env file."""

    url: str
    profile: str
    profile_root: Path
    headless: bool
    channel: str
    timeout_ms: int
    ig_username: str = field(default="", repr=False)
    ig_password: str = field(default="", repr=False)  # never printed or logged
    login_timeout_s: int = 300
    export_dir: Path = Path("exports")
    database_url: str = field(default="", repr=False)  # contains a password: never printed
    schedule_minutes: int = 0  # 0 = no automatic runs
    answer_wait_minutes: int = 10  # how long a run waits for the OTP code / an answer
    schedule_config: str = "instagram_export.json"
    # Optional login for the web UI. Both empty = the UI uses the one-time token link instead.
    ui_username: str = field(default="", repr=False)
    ui_password: str = field(default="", repr=False)  # never printed or logged
    # Where the web page listens. Anything but this computer (for example 0.0.0.0 inside a
    # container) is only accepted together with a sign-in; see web/app.py:serve.
    ui_host: str = "127.0.0.1"
    # Extra host names the page may be reached as (for example your domain behind a proxy).
    # localhost always works; every other name is refused, which stops DNS rebinding.
    ui_allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        # Real environment variables win over .env values (override=False).
        load_dotenv(env_file, override=False)
        database_url = os.environ.get("WA_DATABASE_URL", "").strip()
        if database_url:
            validate_url(database_url)  # raises without echoing the value
        return cls(
            url=os.environ.get("WA_URL", "https://example.com/"),
            profile=os.environ.get("WA_PROFILE", "chrome_profile"),
            profile_root=Path(os.environ.get("WA_PROFILE_ROOT", "browser_data")),
            headless=_get_bool("WA_HEADLESS", False),
            channel=os.environ.get("WA_CHROME_CHANNEL", "chrome"),
            timeout_ms=_get_int("WA_TIMEOUT_MS", 30_000),
            ig_username=os.environ.get("WA_IG_USERNAME", ""),
            ig_password=os.environ.get("WA_IG_PASSWORD", ""),
            login_timeout_s=_get_int("WA_LOGIN_TIMEOUT_S", 300),
            export_dir=Path(os.environ.get("WA_EXPORT_DIR", "exports")),
            database_url=database_url,
            schedule_minutes=_get_minutes(
                "WA_SCHEDULE_MINUTES", 0, MIN_SCHEDULE_MINUTES, MAX_SCHEDULE_MINUTES, allow_zero=True
            ),
            answer_wait_minutes=_get_minutes(
                "WA_ANSWER_WAIT_MINUTES", 10, MIN_ANSWER_WAIT_MINUTES, MAX_ANSWER_WAIT_MINUTES
            ),
            schedule_config=_clean_config_name(os.environ.get("WA_SCHEDULE_CONFIG", "instagram_export.json")),
            ui_username=os.environ.get("WA_UI_USERNAME", "").strip(),
            ui_password=os.environ.get("WA_UI_PASSWORD", ""),
            ui_host=os.environ.get("WA_UI_HOST", "").strip() or "127.0.0.1",
            ui_allowed_hosts=_clean_hosts(os.environ.get("WA_UI_ALLOWED_HOSTS", "")),
        )

    def browser_config(self) -> BrowserConfig:
        return BrowserConfig(
            profile_name=self.profile,
            profile_root=self.profile_root,
            headless=self.headless,
            channel=self.channel,
            default_timeout_ms=self.timeout_ms,
        )
