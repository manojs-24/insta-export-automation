from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page


@dataclass(frozen=True)
class BrowserConfig:
    """Settings for one browser session. Mirrors the README's `browser` block."""

    profile_name: str = "chrome_profile"
    profile_root: Path = Path("browser_data")
    headless: bool = False
    channel: str = "chrome"  # installed Google Chrome, not bundled Chromium
    default_timeout_ms: int = 30_000
    viewport: tuple[int, int] | None = None  # None = use the real window size

    @property
    def profile_dir(self) -> Path:
        return (self.profile_root / self.profile_name).resolve()


@dataclass
class BrowserSession:
    """A live Chrome persistent context plus its primary page."""

    config: BrowserConfig
    context: BrowserContext
    page: Page

    def new_page(self) -> Page:
        page = self.context.new_page()
        page.set_default_timeout(self.config.default_timeout_ms)
        return page
