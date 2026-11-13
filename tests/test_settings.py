from pathlib import Path

import pytest

from web_automation.settings import Settings, SettingsError

_VARS = [
    "WA_URL", "WA_PROFILE", "WA_PROFILE_ROOT", "WA_HEADLESS",
    "WA_CHROME_CHANNEL", "WA_TIMEOUT_MS",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_nothing_set(tmp_path: Path) -> None:
    s = Settings.load(tmp_path / "missing.env")
    assert s.url == "https://example.com/"
    assert s.headless is False
    assert s.channel == "chrome"
    assert s.timeout_ms == 30_000


def test_values_read_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("WA_URL=https://www.instagram.com/\nWA_PROFILE=instagram\nWA_TIMEOUT_MS=5000\n")
    s = Settings.load(env)
    monkeypatch.undo()  # load_dotenv wrote into os.environ; keep tests isolated
    assert s.url == "https://www.instagram.com/"
    assert s.profile == "instagram"
    assert s.timeout_ms == 5000
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_real_env_overrides_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("WA_PROFILE=from_file\n")
    monkeypatch.setenv("WA_PROFILE", "from_env")
    assert Settings.load(env).profile == "from_env"


def test_invalid_values_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_HEADLESS", "maybe")
    with pytest.raises(SettingsError):
        Settings.load(tmp_path / "missing.env")
    monkeypatch.setenv("WA_HEADLESS", "true")
    monkeypatch.setenv("WA_TIMEOUT_MS", "abc")
    with pytest.raises(SettingsError):
        Settings.load(tmp_path / "missing.env")


def test_browser_config_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_PROFILE", "p1")
    monkeypatch.setenv("WA_HEADLESS", "yes")
    config = Settings.load(tmp_path / "missing.env").browser_config()
    assert config.profile_name == "p1"
    assert config.headless is True
