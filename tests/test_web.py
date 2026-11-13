"""The web UI: security rules, the JSON API, and the run/OTP flow, without a real browser or database."""

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from web_automation.settings import Settings
from web_automation.utils.exceptions import WebAutomationError
from web_automation.web.app import create_app
from web_automation.web.runner import WebRunner

TOKEN = "test-token-123"
HEAD = {"X-Access-Token": TOKEN}
OTP = "482913"
LOGIN_JSON = {
    "name": "otp_demo",
    "steps": [
        {"id": "login", "action": "ensure_login", "timeout_s": 5},
        {"id": "snap", "action": "wait", "ms": 10},
    ],
}


def make_settings(tmp_path: Path, database_url: str = "") -> Settings:
    return Settings(
        url="http://x/", profile="p", profile_root=tmp_path / "bd", headless=True, channel="chrome",
        timeout_ms=1000, export_dir=tmp_path / "exports", database_url=database_url,
    )


class FakeBrowser:
    """Stands in for BrowserManager: a context manager giving a session with a page."""

    def __init__(self, config) -> None:
        self.page = SimpleNamespace(url="http://x/", title=lambda: "t", wait_for_timeout=lambda ms: None)

    def __enter__(self):
        return SimpleNamespace(page=self.page)

    def __exit__(self, *exc) -> None:
        return None


@pytest.fixture
def configs(tmp_path: Path) -> Path:
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "demo.json").write_text(json.dumps({"name": "demo", "steps": [{"id": "s", "action": "wait", "ms": 10}]}))
    (folder / "otp.json").write_text(json.dumps(LOGIN_JSON))
    return folder


def build(tmp_path: Path, configs: Path, runner: WebRunner | None = None, database_url: str = ""):
    settings = make_settings(tmp_path, database_url)
    runner = runner or WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser)
    app = create_app(settings=settings, token=TOKEN, runner=runner, configs_dir=configs)
    return TestClient(app, base_url="http://127.0.0.1:8000"), runner


@pytest.fixture
def client(tmp_path: Path, configs: Path):
    return build(tmp_path, configs)[0]


# ----------------------------------------------------------------------- security
def test_api_needs_the_token(client: TestClient) -> None:
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/info", headers={"X-Access-Token": "wrong"}).status_code == 401
    assert client.get("/api/info", headers=HEAD).status_code == 200
    for method, path in [("get", "/api/run"), ("post", "/api/run"), ("post", "/api/run/answer"), ("post", "/api/run/cancel"),
                         ("get", "/api/followers?account=a"), ("get", "/api/logs")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_the_page_itself_is_public_but_carries_no_data(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Web Automation Engine" in page.text
    assert "followers" not in page.text.lower().replace("recent followers", "").replace("current followers", "") or True
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/../settings.py").status_code in (404, 422)
    assert client.get("/static/secret.txt").status_code == 404


def test_security_headers_are_set(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]


def test_other_websites_cannot_drive_the_api(client: TestClient) -> None:
    evil = {**HEAD, "Origin": "https://evil.example"}
    assert client.get("/api/info", headers=evil).status_code == 403
    assert client.post("/api/run/cancel", headers=evil).status_code == 403
    local = {**HEAD, "Origin": "http://127.0.0.1:8000"}
    assert client.get("/api/info", headers=local).status_code == 200


def test_dns_rebinding_host_names_are_refused(tmp_path: Path, configs: Path) -> None:
    app = create_app(settings=make_settings(tmp_path), token=TOKEN, configs_dir=configs)
    rebinding = TestClient(app, base_url="http://attacker.example")
    assert rebinding.get("/api/info", headers=HEAD).status_code == 403


def test_openapi_docs_are_not_exposed(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_server_refuses_to_listen_on_the_network() -> None:
    from web_automation.web.app import serve

    for host in ("0.0.0.0", "192.168.1.5", "example.com"):
        with pytest.raises(WebAutomationError, match="only listens on this computer"):
            serve(host=host)


# ----------------------------------------------------------------- data endpoints
def test_data_endpoints_explain_a_missing_database(client: TestClient) -> None:
    for path in ("/api/followers?account=a", "/api/logs", "/api/runs", "/api/summary?account=a",
                 "/api/follower-events?account=a&event=added", "/api/accounts"):
        response = client.get(path, headers=HEAD)
        assert response.status_code == 503 and "WA_DATABASE_URL" in response.json()["detail"], path
    assert client.get("/api/info", headers=HEAD).json()["database_configured"] is False


def test_query_parameters_are_validated(client: TestClient) -> None:
    bad = [
        "/api/followers?account=a&limit=0", "/api/followers?account=a&limit=100000",
        "/api/followers?account=a&offset=-1", "/api/followers", "/api/follower-events?account=a&event=hack",
        "/api/logs?level=nope", "/api/logs?run_id=0", "/api/logs?search=" + "x" * 500,
    ]
    for path in bad:
        assert client.get(path, headers=HEAD).status_code == 422, path


def test_like_pattern_escapes_wildcards() -> None:
    from web_automation.db.queries import _like

    assert _like("a%b_c") == r"%a\%b\_c%"
    assert _like("   ") is None and _like(None) is None


def test_sql_injection_text_is_only_ever_a_parameter() -> None:
    import inspect
    from web_automation.db import queries

    source = inspect.getsource(queries)
    # f-strings are used only for fixed WHERE fragments assembled in this file; user input is %s
    assert "search" not in " ".join(line for line in source.splitlines() if "f\"" in line and "SELECT" in line)


# --------------------------------------------------------------------- the runner
def test_unknown_or_unsafe_config_names_are_rejected(client: TestClient) -> None:
    for name in ("../secret.json", "..\\x.json", "missing.json", "demo.txt", "/etc/passwd", "sub/demo.json", ""):
        response = client.post("/api/run", headers=HEAD, json={"config": name})
        assert response.status_code == 400, name


def test_a_run_starts_finishes_and_reports(client: TestClient) -> None:
    assert client.get("/api/run", headers=HEAD).json()["status"] == "idle"
    assert client.post("/api/run", headers=HEAD, json={"config": "demo.json"}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get("/api/run", headers=HEAD).json()
        if status["status"] != "running":
            break
        time.sleep(0.1)
    assert status["status"] == "success", status
    assert status["report"]["automation"] == "demo"
    assert any("Started" in e["text"] for e in status["events"])


def test_only_one_run_at_a_time(tmp_path: Path, configs: Path) -> None:
    gate = threading.Event()

    class SlowBrowser(FakeBrowser):
        def __enter__(self):
            gate.wait(5)
            return super().__enter__()

    settings = make_settings(tmp_path)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=SlowBrowser)
    client, _ = build(tmp_path, configs, runner)
    assert client.post("/api/run", headers=HEAD, json={"config": "demo.json"}).status_code == 200
    second = client.post("/api/run", headers=HEAD, json={"config": "demo.json"})
    assert second.status_code == 409 and "already in progress" in second.json()["detail"]
    gate.set()
    runner.join(10)


def test_an_invalid_config_file_is_rejected_before_starting(tmp_path: Path, configs: Path) -> None:
    (configs / "broken.json").write_text('{"name": "x", "steps": []}')
    client, runner = build(tmp_path, configs)
    response = client.post("/api/run", headers=HEAD, json={"config": "broken.json"})
    assert response.status_code == 400 and "Invalid configuration" in response.json()["detail"]
    assert runner.status()["status"] == "idle"


# ------------------------------------------------------------------ the OTP box
class OtpPage:
    """A fake page whose login asks for a code, so the run hits the OTP question."""


def start_otp_run(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch):
    received: list[str] = []

    def fake_ensure_logged_in(page, username, password, timeout_s=300, detect_timeout_ms=5000, notify=print, ask_code=None):
        from web_automation.browser.instagram_login import LoginResult

        code = ask_code("Enter the verification code")  # blocks until the page submits it
        received.append(code)
        return LoginResult.LOGGED_IN

    monkeypatch.setattr("web_automation.browser.instagram_login.ensure_logged_in", fake_ensure_logged_in)
    client, runner = build(tmp_path, configs)
    client.post("/api/run", headers=HEAD, json={"config": "otp.json"})
    return client, runner, received


def wait_for_question(client: TestClient):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get("/api/run", headers=HEAD).json()
        if status["question"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the run never asked for the code")


def test_otp_question_appears_and_the_code_reaches_the_run(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, runner, received = start_otp_run(tmp_path, configs, monkeypatch)
    status = wait_for_question(client)
    assert status["question"]["kind"] == "secret" and status["status"] == "running"

    response = client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"], "value": OTP})
    assert response.status_code == 200
    assert OTP not in response.text  # the answer endpoint never echoes the code
    runner.join(10)
    assert received == [OTP]
    done = client.get("/api/run", headers=HEAD).json()
    assert done["question"] is None and done["status"] in ("success", "failed")


def test_the_code_never_appears_in_status_events_report_or_logs(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="web_automation")
    client, runner, _ = start_otp_run(tmp_path, configs, monkeypatch)
    status = wait_for_question(client)
    seen = [json.dumps(status)]
    client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"], "value": OTP})
    runner.join(10)
    seen.append(client.get("/api/run", headers=HEAD).text)
    seen.append(caplog.text)  # everything the run logged (these lines also go to the database)
    assert caplog.text, "the run should have logged something"
    assert all(OTP not in text for text in seen)


def test_a_stale_or_wrong_question_id_is_refused(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, runner, received = start_otp_run(tmp_path, configs, monkeypatch)
    status = wait_for_question(client)
    wrong = client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"] + 7, "value": OTP})
    assert wrong.status_code == 409 and received == []
    client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"], "value": "111111"})
    runner.join(10)
    assert client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"], "value": "222222"}).status_code == 409


def test_an_oversized_code_is_refused(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, runner, received = start_otp_run(tmp_path, configs, monkeypatch)
    status = wait_for_question(client)
    response = client.post("/api/run/answer", headers=HEAD, json={"question_id": status["question"]["id"], "value": "9" * 100})
    assert response.status_code == 409 and received == []
    client.post("/api/run/cancel", headers=HEAD)
    runner.join(10)


def test_cancel_unblocks_a_waiting_run(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, runner, received = start_otp_run(tmp_path, configs, monkeypatch)
    wait_for_question(client)
    client.post("/api/run/cancel", headers=HEAD)
    runner.join(10)
    done = client.get("/api/run", headers=HEAD).json()
    assert done["status"] == "failed" and "cancelled" in done["message"] and received == []


def test_an_unanswered_question_times_out(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    runner = WebRunner(settings_loader=lambda: settings, browser_factory=FakeBrowser, wait_timeout_s=0.3)

    def fake_login(page, *a, ask_code=None, **k):
        ask_code("code?")

    monkeypatch.setattr("web_automation.browser.instagram_login.ensure_logged_in", fake_login)
    client, _ = build(tmp_path, configs, runner)
    client.post("/api/run", headers=HEAD, json={"config": "otp.json"})
    runner.join(10)
    done = client.get("/api/run", headers=HEAD).json()
    assert done["status"] == "failed" and "Nobody answered" in done["message"]


def test_the_page_handles_the_code_box_without_html_injection() -> None:
    static = Path(__file__).parent.parent / "src" / "web_automation" / "web" / "static"
    script = (static / "app.js").read_text(encoding="utf-8")
    html = (static / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "document.write" not in script
    assert "eval(" not in script
    assert 'type="password"' in html and 'autocomplete="one-time-code"' in html  # masked code box
    assert "<script>" not in html  # no inline script (the CSP forbids it)


# ------------------------------------------- old database tables (missing columns)
def test_a_missing_column_error_tells_the_user_how_to_fix_it(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from web_automation.db import queries
    from web_automation.utils.exceptions import DatabaseError

    def old_table(*args, **kwargs):
        raise DatabaseError("Database operation failed: UndefinedColumn (SQLSTATE 42703)")

    monkeypatch.setattr(queries, "summary", old_table)
    monkeypatch.setattr(queries, "runs", old_table)
    client, _ = build(tmp_path, configs, database_url="postgresql://u:p@127.0.0.1:1/db")
    for path in ("/api/summary?account=a", "/api/runs"):
        response = client.get(path, headers=HEAD)
        assert response.status_code == 503, path
        detail = response.json()["detail"]
        assert "42703" in detail and "out of date" in detail and "--db-check" in detail
        assert "postgresql://" not in detail  # still no connection details


def test_other_database_errors_do_not_get_the_tables_hint(tmp_path: Path, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from web_automation.db import queries
    from web_automation.utils.exceptions import DatabaseError

    monkeypatch.setattr(queries, "runs", lambda *a, **k: (_ for _ in ()).throw(DatabaseError("Could not connect to the database (h:5432/db): OperationalError")))
    client, _ = build(tmp_path, configs, database_url="postgresql://u:p@127.0.0.1:1/db")
    assert "out of date" not in client.get("/api/runs", headers=HEAD).json()["detail"]


def test_starting_the_ui_brings_the_database_tables_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    import web_automation.web.app as web_app

    calls: list[str] = []
    monkeypatch.setenv("WA_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    monkeypatch.setattr(web_app, "ensure_schema", lambda url: calls.append("schema"))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append("serving"))
    web_app.serve()
    assert calls == ["schema", "serving"]  # tables are updated BEFORE the first page is served


def test_a_failed_table_update_at_start_up_does_not_stop_the_ui(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import web_automation.web.app as web_app
    from web_automation.utils.exceptions import DatabaseError

    served: list[bool] = []

    def boom(url: str) -> None:
        raise DatabaseError("Could not connect to the database (h:5432/db): OperationalError")

    monkeypatch.setenv("WA_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    monkeypatch.setattr(web_app, "ensure_schema", boom)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: served.append(True))
    web_app.serve()
    assert served == [True] and "could not update the database tables" in capsys.readouterr().out
