from pathlib import Path

import pytest

from web_automation.browser.manager import BrowserManager, ensure_safe_profile_dir
from web_automation.browser.session import BrowserConfig
from web_automation.utils.exceptions import BrowserNotRunningError, UnsafeProfileError


def test_profile_dir_is_resolved_under_root(tmp_path: Path) -> None:
    config = BrowserConfig(profile_root=tmp_path, profile_name="instagram")
    assert config.profile_dir == (tmp_path / "instagram").resolve()


def test_default_profile_is_browser_data_chrome_profile() -> None:
    assert BrowserConfig().profile_dir == (Path("browser_data") / "chrome_profile").resolve()


def test_defaults_match_readme() -> None:
    config = BrowserConfig()
    assert config.headless is False
    assert config.channel == "chrome"


def test_dedicated_profile_is_allowed(tmp_path: Path) -> None:
    ensure_safe_profile_dir(tmp_path / "browser_data" / "default")


def test_everyday_chrome_profile_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    everyday = tmp_path / "Google" / "Chrome" / "User Data"
    with pytest.raises(UnsafeProfileError):
        ensure_safe_profile_dir(everyday)
    with pytest.raises(UnsafeProfileError):
        ensure_safe_profile_dir(everyday / "Default")


def test_session_before_start_raises() -> None:
    manager = BrowserManager()
    assert manager.is_running is False
    with pytest.raises(BrowserNotRunningError):
        _ = manager.session


def test_stop_without_start_is_noop() -> None:
    BrowserManager().stop()
