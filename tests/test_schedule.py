"""Scheduling: settings validation, the tick logic, the manual/automatic label, and the
OTP waiting time taken from .env. Nothing here waits for a real interval."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from web_automation.settings import Settings, SettingsError
from web_automation.utils.exceptions import WebAutomationError
from web_automation.web.app import create_app
from web_automation.web.runner import WebRunner
from web_automation.web.scheduler import RunScheduler

TOKEN = "schedule-token"
HEAD = {"X-Access-Token": TOKEN}
ENV_NAMES = ("WA_SCHEDULE_MINUTES", "WA_SCHEDULE_CONFIG", "WA_ANSWER_WAIT_MINUTES")
OTP_CONFIG = {"name": "otp", "steps": [{"id": "login", "action": "ensure_login", "timeout_s": 5}]}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings.load(tmp_path / "missing.env")


# --------------------------------------------------------------------- settings
def test_defaults_are_off_and_ten_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(tmp_path, monkeypatch)
    assert (s.schedule_minutes, s.answer_wait_minutes, s.schedule_config) == (0, 10, "instagram_export.json")


def test_values_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = load(tmp_path, monkeypatch, WA_SCHEDULE_MINUTES="60", WA_ANSWER_WAIT_MINUTES="25", WA_SCHEDULE_CONFIG="smoke_test.json")
    assert (s.schedule_minutes, s.answer_wait_minutes, s.schedule_config) == (60, 25, "smoke_test.json")


@pytest.mark.parametrize("value", ["1", "4", "-5", "10081", "abc", "1.5"])
def test_unsafe_schedule_values_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    with pytest.raises(SettingsError):
        load(tmp_path, monkeypatch, WA_SCHEDULE_MINUTES=value)


@pytest.mark.parametrize(("value", "expected"), [("0", 0), ("5", 5), ("60", 60), ("10080", 10080), ("", 0)])
def test_valid_schedule_values_are_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, expected: int) -> None:
    assert load(tmp_path, monkeypatch, WA_SCHEDULE_MINUTES=value).schedule_minutes == expected


@pytest.mark.parametrize("value", ["0", "-1", "241", "x"])
def test_answer_wait_must_be_between_1_and_240(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    with pytest.raises(SettingsError):
        load(tmp_path, monkeypatch, WA_ANSWER_WAIT_MINUTES=value)


@pytest.mark.parametrize("value", ["../x.json", "sub/x.json", "x.txt", ".hidden.json", "C:\\x.json"])
def test_schedule_config_must_be_a_bare_json_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    with pytest.raises(SettingsError):
        load(tmp_path, monkeypatch, WA_SCHEDULE_CONFIG=value)


# ----------------------------------------------------------------------- the tick
class FakeRunner:
    def __init__(self, busy: bool = False) -> None:
        self.busy, self.started = busy, []

    @property
    def running(self) -> bool:
        return self.busy

    def start(self, path: Path, trigger: str = "manual") -> None:
        self.started.append((path.name, trigger))


@pytest.fixture
def configs(tmp_path: Path) -> Path:
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "demo.json").write_text(json.dumps({"name": "demo", "steps": [{"id": "s", "action": "wait", "ms": 10}]}))
    (folder / "otp.json").write_text(json.dumps(OTP_CONFIG))
    return folder


def test_a_tick_starts_a_scheduled_run(configs: Path) -> None:
    runner = FakeRunner()
    sched = RunScheduler(runner, configs, "demo.json", 60)
    assert sched.tick() == "started"
    assert runner.started == [("demo.json", "scheduled")]
    assert sched.status()["last_tick_result"] == "started" and sched.last_tick is not None


def test_a_tick_never_starts_a_second_run_on_top_of_a_running_one(configs: Path) -> None:
    runner = FakeRunner(busy=True)
    sched = RunScheduler(runner, configs, "demo.json", 60)
    assert "skipped" in sched.tick()
    assert runner.started == []


def test_a_tick_survives_a_run_that_cannot_start(configs: Path) -> None:
    class Failing(FakeRunner):
        def start(self, path, trigger="manual") -> None:
            raise WebAutomationError("config became invalid")

    sched = RunScheduler(Failing(), configs, "demo.json", 60)
    assert "could not start" in sched.tick()  # logged, not raised: the schedule keeps going


def test_schedule_off_means_no_jobs(configs: Path) -> None:
    sched = RunScheduler(FakeRunner(), configs, "demo.json", 0)
    sched.start()
    status = sched.status()
    assert sched.enabled is False and sched.next_run_time() is None
    assert status["enabled"] is False and status["next_run"] is None
    sched.stop()


def test_an_unknown_schedule_config_fails_at_start_up(configs: Path) -> None:
    with pytest.raises(WebAutomationError, match="was not found"):
        RunScheduler(FakeRunner(), configs, "nope.json", 60).start()


def test_the_first_run_is_one_interval_after_start_not_immediately(configs: Path) -> None:
    sched = RunScheduler(FakeRunner(), configs, "demo.json", 60)
    sched.start()
    try:
        status = sched.status()
        assert status["enabled"] and status["minutes"] == 60
        remaining = sched.next_run_time().timestamp() - time.time()
        assert 55 * 60 < remaining <= 60 * 60 + 5
        assert sched.last_tick is None  # nothing has run yet
    finally:
        sched.stop()


def test_a_real_short_interval_fires_the_job(configs: Path) -> None:
    """Uses APScheduler itself with a 1-second interval, to prove the wiring really fires."""
    from apscheduler.schedulers.background import BackgroundScheduler

    runner = FakeRunner()
    sched = RunScheduler(runner, configs, "demo.json", 1, scheduler=BackgroundScheduler())
    sched._scheduler.add_job(sched.tick, "interval", seconds=1, id="fast")
    sched._scheduler.start()
    try:
        deadline = time.monotonic() + 5
        while not runner.started and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        sched.stop()
    assert runner.started and runner.started[0][1] == "scheduled"


# ------------------------------------------------- manual vs automatic in the app
def make_settings(tmp_path: Path, **extra) -> Settings:
    return Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome", timeout_ms=1000,
                    export_dir=tmp_path, **extra)


class FakeBrowser:
    def __init__(self, config) -> None:
        self.page = SimpleNamespace(url="http://x/", title=lambda: "t", wait_for_timeout=lambda ms: None)

    def __enter__(self):
        return SimpleNamespace(page=self.page)

    def __exit__(self, *exc) -> None:
        return None


def test_the_button_is_recorded_as_manual_and_the_scheduler_as_automatic(tmp_path: Path, configs: Path) -> None:
    settings = make_settings(tmp_path)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser)
    sched = RunScheduler(runner, configs, "demo.json", 60)
    app = create_app(settings=settings, token=TOKEN, runner=runner, configs_dir=configs, scheduler=sched)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    assert client.get("/api/run", headers=HEAD).json()["trigger"] == "manual"  # idle default
    assert client.post("/api/run", headers=HEAD, json={"config": "demo.json"}).status_code == 200
    runner.join(10)
    first = client.get("/api/run", headers=HEAD).json()
    assert first["trigger"] == "manual" and first["status"] == "success"
    assert "manual run" in " ".join(e["text"] for e in first["events"])

    assert sched.tick() == "started"
    runner.join(10)
    second = client.get("/api/run", headers=HEAD).json()
    assert second["trigger"] == "scheduled" and second["status"] == "success"
    assert "automatic run" in " ".join(e["text"] for e in second["events"])


def test_the_api_cannot_forge_an_automatic_run(tmp_path: Path, configs: Path) -> None:
    settings = make_settings(tmp_path)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser)
    client = TestClient(create_app(settings=settings, token=TOKEN, runner=runner, configs_dir=configs),
                        base_url="http://127.0.0.1:8000")
    client.post("/api/run", headers=HEAD, json={"config": "demo.json", "trigger": "scheduled"})
    runner.join(10)
    assert client.get("/api/run", headers=HEAD).json()["trigger"] == "manual"  # the extra field is ignored


def test_schedule_status_is_exposed_to_the_page(tmp_path: Path, configs: Path) -> None:
    settings = make_settings(tmp_path)
    sched = RunScheduler(FakeRunner(), configs, "demo.json", 60)
    sched.start()
    try:
        client = TestClient(create_app(settings=settings, token=TOKEN, configs_dir=configs, scheduler=sched),
                            base_url="http://127.0.0.1:8000")
        info = client.get("/api/info", headers=HEAD).json()["schedule"]
        assert info["enabled"] and info["minutes"] == 60 and info["next_run"]
        assert client.get("/api/schedule", headers=HEAD).json()["minutes"] == 60
        assert client.get("/api/schedule").status_code == 401  # needs the token like everything else
    finally:
        sched.stop()
    off = TestClient(create_app(settings=settings, token=TOKEN, configs_dir=configs), base_url="http://127.0.0.1:8000")
    assert off.get("/api/info", headers=HEAD).json()["schedule"] == {"enabled": False}


# ----------------------------------------------- the OTP waiting time from .env
def test_unanswered_question_waits_for_the_env_minutes(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path, answer_wait_minutes=7)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser)
    asked = {}

    def fake_login(page, *a, ask_code=None, **k):
        asked["timeout"] = runner._wait_timeout_s  # read while the question is being asked
        runner.cancel()                            # do not really wait 7 minutes
        ask_code("code?")

    monkeypatch.setattr("web_automation.browser.instagram_login.ensure_logged_in", fake_login)
    runner.start(configs / "otp.json")
    runner.join(10)
    assert asked["timeout"] == 7 * 60  # taken from WA_ANSWER_WAIT_MINUTES, not the old fixed 10


def test_the_timeout_message_names_the_wait_and_mentions_the_next_attempt(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser, wait_timeout_s=0.3)
    monkeypatch.setattr("web_automation.browser.instagram_login.ensure_logged_in",
                        lambda page, *a, ask_code=None, **k: ask_code("code?"))
    runner.start(configs / "otp.json", trigger="scheduled")
    runner.join(10)
    text = json.dumps(runner.status(), default=str)
    assert "Nobody answered" in text and "next scheduled run will try again" in text
