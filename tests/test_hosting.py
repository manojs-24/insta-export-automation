"""Hosting the web page (Docker or a server): where it may listen, which host names it answers
to, the cookie flags behind a proxy, and that the container files are what they should be."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web_automation.settings import Settings, SettingsError
from web_automation.utils.exceptions import WebAutomationError
from web_automation.web.app import create_app, host_name, serve
from web_automation.web.auth import COOKIE_NAME

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "hosting-token"
HEAD = {"X-Access-Token": TOKEN}
USER, PASSWORD = "manoj", "correct horse battery"


def make_settings(tmp_path: Path, **extra) -> Settings:
    return Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome",
                    timeout_ms=1000, export_dir=tmp_path, **extra)


def client_for(tmp_path: Path, base: str = "http://127.0.0.1:8000", **extra) -> TestClient:
    app = create_app(settings=make_settings(tmp_path, **extra), token=TOKEN, configs_dir=tmp_path)
    return TestClient(app, base_url=base)


# ------------------------------------------------------------------- host name parsing
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1"), ("localhost", "localhost"), ("LocalHost:9", "localhost"),
        ("[::1]:8000", "::1"), ("http://evil.example", "evil.example"), ("https://a.b.c:8443/x", "a.b.c"),
        ("", None), (None, None), ("[bad", None),
    ],
)
def test_host_name_parsing(header: str | None, expected: str | None) -> None:
    assert host_name(header) == expected


# ------------------------------------------------------------- allowed host names (rebinding)
def test_localhost_names_always_work(tmp_path: Path) -> None:
    for base in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
        assert client_for(tmp_path, base).get("/auth/mode").status_code == 200, base


def test_an_unlisted_host_name_is_refused(tmp_path: Path) -> None:
    assert client_for(tmp_path, "http://automation.example.com").get("/auth/mode").status_code == 403
    assert client_for(tmp_path, "http://127.0.0.1.evil.example").get("/auth/mode").status_code == 403


def test_a_listed_host_name_is_accepted_and_others_still_refused(tmp_path: Path) -> None:
    listed = ("automation.example.com",)
    assert client_for(tmp_path, "http://automation.example.com", ui_allowed_hosts=listed).get("/auth/mode").status_code == 200
    assert client_for(tmp_path, "http://AUTOMATION.example.com:8443", ui_allowed_hosts=listed).get("/auth/mode").status_code == 200
    assert client_for(tmp_path, "http://other.example.com", ui_allowed_hosts=listed).get("/auth/mode").status_code == 403


def test_the_origin_header_is_checked_against_the_same_list(tmp_path: Path) -> None:
    client = client_for(tmp_path, ui_allowed_hosts=("automation.example.com",))
    assert client.get("/auth/mode", headers={"Origin": "https://automation.example.com"}).status_code == 200
    assert client.get("/auth/mode", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/login", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_the_container_health_check_request_is_allowed(tmp_path: Path) -> None:
    # The image's HEALTHCHECK calls http://127.0.0.1:8000/auth/mode with no Origin and no token.
    response = client_for(tmp_path).get("/auth/mode")
    assert response.status_code == 200 and response.json() == {"login_required": False, "signed_in": False}


# --------------------------------------------------------------------------- settings
def test_host_settings_default_to_this_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WA_UI_HOST", "WA_UI_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.load(tmp_path / "missing.env")
    assert settings.ui_host == "127.0.0.1" and settings.ui_allowed_hosts == ()


def test_allowed_hosts_are_cleaned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_UI_ALLOWED_HOSTS", " Automation.Example.com , 10.0.0.5,, automation.example.com ")
    assert Settings.load(tmp_path / "missing.env").ui_allowed_hosts == ("automation.example.com", "10.0.0.5")


@pytest.mark.parametrize("value", ["https://x.example", "x.example:8000", "x.example/path", "*.example.com", "a b", "-bad.example", "x" * 300])
def test_malformed_allowed_hosts_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("WA_UI_ALLOWED_HOSTS", value)
    with pytest.raises(SettingsError):
        Settings.load(tmp_path / "missing.env")


# ------------------------------------------------------- listening beyond this computer
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com"])
def test_listening_beyond_this_computer_needs_a_sign_in(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    started = []
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k.get("host")))
    with pytest.raises(WebAutomationError, match="unless a sign-in is set") as info:
        serve(host=host)
    assert started == [] and host in str(info.value)  # refused before anything started


def test_the_environment_can_request_a_wide_listen_address_only_with_a_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    with pytest.raises(WebAutomationError, match="unless a sign-in is set"):
        serve()  # WA_UI_HOST=0.0.0.0 alone is not enough
    monkeypatch.setenv("WA_UI_USERNAME", USER)
    monkeypatch.setenv("WA_UI_PASSWORD", PASSWORD)
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **k: seen.update(k))
    serve()
    assert seen["host"] == "0.0.0.0"  # now allowed, because a sign-in protects it


def test_this_computer_still_needs_nothing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **k: seen.update(k))
    serve()
    assert seen["host"] == "127.0.0.1"


def test_the_start_up_message_never_prints_a_wildcard_address(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("WA_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("WA_UI_USERNAME", USER)
    monkeypatch.setenv("WA_UI_PASSWORD", PASSWORD)
    monkeypatch.setenv("WA_SCHEDULE_MINUTES", "0")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    serve()
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8000/" in out and "http://0.0.0.0" not in out  # 0.0.0.0 is not a usable address
    assert PASSWORD not in out and USER not in out


# ----------------------------------------------------------------- the cookie behind a proxy
def test_the_cookie_is_secure_only_when_served_over_https(tmp_path: Path) -> None:
    client = client_for(tmp_path, ui_username=USER, ui_password=PASSWORD)
    plain = client.post("/auth/login", json={"username": USER, "password": PASSWORD})
    assert "secure" not in plain.headers["set-cookie"].lower()  # plain http on localhost must still work
    secure = client_for(tmp_path, ui_username=USER, ui_password=PASSWORD).post(
        "/auth/login", json={"username": USER, "password": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
    cookie = secure.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert COOKIE_NAME in cookie


# ------------------------------------------------------------------ the container files
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
ENTRYPOINT = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")


def test_the_image_runs_as_an_unprivileged_user() -> None:
    assert re.search(r"^USER app$", DOCKERFILE, re.M)
    assert DOCKERFILE.index("useradd") < DOCKERFILE.index("USER app")


def test_the_image_uses_the_exact_locked_dependencies() -> None:
    assert "uv sync --frozen --no-dev" in DOCKERFILE and "--no-editable" in DOCKERFILE
    assert re.search(r"uv==\d+\.\d+\.\d+", DOCKERFILE)  # a pinned installer, not "latest"
    assert "COPY pyproject.toml uv.lock" in DOCKERFILE


def test_secrets_and_personal_data_are_kept_out_of_the_build() -> None:
    for entry in (".env", ".env.*", "browser_data/", "exports/", ".git/", ".venv/"):
        assert entry in IGNORE, entry
    assert "COPY .env" not in DOCKERFILE and "COPY . " not in DOCKERFILE  # nothing copies the whole folder
    copied = re.findall(r"^COPY (?:--\S+ )*(\S+)", DOCKERFILE, re.M)
    assert set(copied) <= {"pyproject.toml", "src", "configs", "docker/entrypoint.sh", "/opt/venv"}, copied


def test_the_data_lives_in_volumes_and_the_chrome_memory_is_enlarged() -> None:
    assert "WA_PROFILE_ROOT=/data/browser_data" in DOCKERFILE and "WA_EXPORT_DIR=/data/exports" in DOCKERFILE
    assert "VOLUME" in DOCKERFILE
    assert re.search(r"shm_size:\s*\"?1gb", COMPOSE)  # Chrome crashes on Docker's 64 MB default
    assert "browser_data:/data/browser_data" in COMPOSE and "exports:/data/exports" in COMPOSE


def test_the_port_is_published_on_this_computer_only() -> None:
    assert '"127.0.0.1:8000:8000"' in COMPOSE
    assert "0.0.0.0:8000" not in COMPOSE and not re.search(r'^\s*- "8000:8000"', COMPOSE, re.M)


def test_the_image_is_amd64_because_chrome_for_linux_is() -> None:
    assert "platform: linux/amd64" in COMPOSE
    assert "amd64" in DOCKERFILE and "dpkg --print-architecture" in DOCKERFILE


def test_the_entrypoint_is_safe_and_has_unix_line_endings() -> None:
    raw = (ROOT / "docker" / "entrypoint.sh").read_bytes()
    assert raw.startswith(b"#!/bin/sh\n") and b"\r" not in raw
    assert "set -eu" in ENTRYPOINT and 'exec "$@"' in ENTRYPOINT
    # stale-lock cleanup must only happen for the long-running web page
    assert '*" --ui "*)' in ENTRYPOINT and ENTRYPOINT.index("--ui") < ENTRYPOINT.index("SingletonLock")
    assert "rm -rf" not in ENTRYPOINT  # it only removes the three named lock files
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attributes  # Windows checkouts must not add CRLF


def test_the_health_check_calls_a_path_that_needs_no_login() -> None:
    assert "/auth/mode" in DOCKERFILE and "HEALTHCHECK" in DOCKERFILE
    assert "WA_UI_HOST=0.0.0.0" in DOCKERFILE


def test_the_compose_file_never_embeds_a_secret() -> None:
    assert "env_file: .env" in COMPOSE
    for secret_key in ("WA_IG_PASSWORD", "WA_UI_PASSWORD", "WA_DATABASE_URL"):
        assert not re.search(rf"^\s*{secret_key}\s*:", COMPOSE, re.M), secret_key
