import os
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright, sync_playwright

from web_automation.browser.session import BrowserConfig, BrowserSession
from web_automation.utils.exceptions import (
    BrowserLaunchError,
    BrowserNotRunningError,
    ProfileInUseError,
    UnsafeProfileError,
)
from web_automation.utils.logger import get_logger

log = get_logger("browser")


def _everyday_chrome_dirs() -> list[Path]:
    """Locations of the user's normal Chrome profile, which we must never touch."""
    candidates = [
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\User Data",
        str(Path.home() / "Library/Application Support/Google/Chrome"),
        str(Path.home() / ".config/google-chrome"),
    ]
    return [Path(c).resolve() for c in candidates if c and not c.startswith(r"\Google")]


def ensure_safe_profile_dir(profile_dir: Path) -> None:
    resolved = profile_dir.resolve()
    for forbidden in _everyday_chrome_dirs():
        if resolved == forbidden or forbidden in resolved.parents:
            raise UnsafeProfileError(
                f"Refusing to use {resolved}: it is inside the everyday Chrome profile. "
                "Use a dedicated automation profile directory."
            )


def is_profile_in_use(profile_dir: Path) -> bool:
    """True if a running Chrome currently holds this profile.

    Chrome keeps `lockfile` open on Windows and creates a `SingletonLock` symlink on
    POSIX. Only a *held* Windows lock counts; a leftover file from a crash does not.
    """
    if os.name == "nt":
        lock = profile_dir / "lockfile"
        if not lock.exists():
            return False
        try:
            with open(lock, "ab"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False
    return (profile_dir / "SingletonLock").is_symlink()


class BrowserManager:
    """Owns the Playwright driver and one Chrome persistent context.

    Use as a context manager so Chrome is always closed, even on errors:

        with BrowserManager(BrowserConfig(profile_name="instagram")) as session:
            session.page.goto("https://example.com")
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._playwright: Playwright | None = None
        self._session: BrowserSession | None = None

    @property
    def session(self) -> BrowserSession:
        if self._session is None:
            raise BrowserNotRunningError("Browser is not running; call start() first.")
        return self._session

    @property
    def is_running(self) -> bool:
        return self._session is not None

    def start(self) -> BrowserSession:
        if self._session is not None:
            return self._session

        profile_dir = self.config.profile_dir
        ensure_safe_profile_dir(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        if is_profile_in_use(profile_dir):
            raise ProfileInUseError(
                f"Profile '{profile_dir.name}' is already open in another Chrome window. "
                "Close that window (or the other automation run) and try again. "
                "The profile has not been modified."
            )

        log.info("Launching Chrome (headless=%s, profile=%s)", self.config.headless, profile_dir.name)
        self._playwright = sync_playwright().start()
        try:
            viewport = (
                {"width": self.config.viewport[0], "height": self.config.viewport[1]}
                if self.config.viewport
                else None
            )
            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=self.config.channel,
                headless=self.config.headless,
                no_viewport=viewport is None,
                viewport=viewport,
            )
        except PlaywrightError as exc:
            self._stop_playwright()
            raise BrowserLaunchError(
                f"Could not launch Chrome (channel={self.config.channel!r}). Is Google Chrome "
                f"installed, and is the profile '{profile_dir.name}' free of another running "
                f"instance? Details: {str(exc).splitlines()[0]}"
            ) from exc

        context.set_default_timeout(self.config.default_timeout_ms)
        # A persistent context opens with one blank page; reuse it.
        page = context.pages[0] if context.pages else context.new_page()
        self._session = BrowserSession(config=self.config, context=context, page=page)
        return self._session

    def stop(self) -> None:
        """Close Chrome and the Playwright driver. Idempotent."""
        session, self._session = self._session, None
        if session is not None:
            try:
                session.context.close()
            except PlaywrightError as exc:
                log.warning("Error while closing browser context: %s", str(exc).splitlines()[0])
        self._stop_playwright()
        if session is not None:
            log.info("Browser closed")

    def _stop_playwright(self) -> None:
        playwright, self._playwright = self._playwright, None
        if playwright is not None:
            try:
                playwright.stop()
            except PlaywrightError as exc:
                log.warning("Error while stopping Playwright: %s", str(exc).splitlines()[0])

    def __enter__(self) -> BrowserSession:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
