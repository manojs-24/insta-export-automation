"""Signing in to the web UI: the password check, sessions, the brute-force lockout, cookie
flags, that nothing is reachable without a session, and that token mode still works."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web_automation.settings import Settings, SettingsError
from web_automation.web.app import create_app
from web_automation.web.auth import (
    COOKIE_NAME,
    LoginThrottle,
    SessionStore,
    credentials_match,
    tokens_equal,
    validate_login_settings,
)

USER, PASSWORD = "manoj", "correct horse battery"
TOKEN = "link-token-123"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_settings(tmp_path: Path, user: str = USER, password: str = PASSWORD) -> Settings:
    return Settings(url="x", profile="p", profile_root=tmp_path, headless=True, channel="chrome",
                    timeout_ms=1000, export_dir=tmp_path, ui_username=user, ui_password=password)


def make_client(tmp_path: Path, **kwargs) -> TestClient:
    app = create_app(settings=make_settings(tmp_path, **kwargs.pop("creds", {})), token=TOKEN, configs_dir=tmp_path, **kwargs)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def sign_in(client: TestClient, user: str = USER, password: str = PASSWORD):
    return client.post("/auth/login", json={"username": user, "password": password})


# ------------------------------------------------------------- pure password logic
def test_credentials_must_both_match() -> None:
    assert credentials_match("a", "pw12345678", "a", "pw12345678")
    assert not credentials_match("a", "pw12345678", "b", "pw12345678")
    assert not credentials_match("a", "pw12345678", "a", "wrong")
    assert not credentials_match("a", "pw12345678", "", "")
    assert credentials_match("é用户", "пароль-1234", "é用户", "пароль-1234")  # non-ASCII is fine


def test_token_comparison_accepts_any_text_without_crashing() -> None:
    assert tokens_equal("abc", "abc") and not tokens_equal("abc", "abd")
    assert not tokens_equal("é", "e")  # plain compare_digest would raise TypeError on this


@pytest.mark.parametrize(("user", "password", "expected"), [("", "", False), ("a", "12345678", True)])
def test_login_setting_modes(user: str, password: str, expected: bool) -> None:
    assert validate_login_settings(user, password) is expected


@pytest.mark.parametrize(
    ("user", "password"),
    [("a", ""), ("", "12345678"), ("a", "short"), ("u" * 101, "12345678"), ("a", "p" * 201)],
)
def test_bad_login_settings_are_rejected_without_echoing_them(user: str, password: str) -> None:
    with pytest.raises(SettingsError) as info:
        validate_login_settings(user, password)
    assert password not in str(info.value) or password == ""


def test_settings_repr_hides_the_ui_password(tmp_path: Path) -> None:
    text = repr(make_settings(tmp_path))
    assert PASSWORD not in text and USER not in text


# ------------------------------------------------------------------------ sessions
def test_sessions_expire_and_can_be_revoked() -> None:
    clock = Clock()
    store = SessionStore(lifetime_s=100, clock=clock)
    token = store.create()
    assert store.is_valid(token) and not store.is_valid("nope") and not store.is_valid(None)
    clock.now += 99
    assert store.is_valid(token)
    clock.now += 2
    assert not store.is_valid(token)  # expired
    again = store.create()
    store.revoke(again)
    assert not store.is_valid(again)


def test_tokens_are_random_and_only_hashes_are_kept() -> None:
    store = SessionStore()
    first, second = store.create(), store.create()
    assert first != second and len(first) >= 40
    assert first not in repr(store._expires) and first.encode() not in b"".join(store._expires)


def test_only_the_newest_sessions_are_kept() -> None:
    store = SessionStore()
    tokens = [store.create() for _ in range(25)]
    assert not store.is_valid(tokens[0]) and store.is_valid(tokens[-1])


# ----------------------------------------------------------------------- the lockout
def test_five_wrong_attempts_lock_logins_out_even_for_the_right_password() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=5, window_s=300, lockout_s=300, clock=clock)
    for _ in range(4):
        throttle.record_failure()
    assert throttle.retry_after() == 0
    throttle.record_failure()
    assert throttle.retry_after() == 300
    clock.now += 100
    assert throttle.retry_after() == 200
    clock.now += 201
    assert throttle.retry_after() == 0  # allowed again


def test_old_failures_do_not_add_up() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_s=60, lockout_s=60, clock=clock)
    throttle.record_failure(); throttle.record_failure()
    clock.now += 61
    throttle.record_failure()
    assert throttle.retry_after() == 0


def test_a_success_clears_the_count() -> None:
    throttle = LoginThrottle(max_failures=3)
    throttle.record_failure(); throttle.record_failure()
    throttle.record_success()
    throttle.record_failure()
    assert throttle.retry_after() == 0


# ---------------------------------------------------------------- the web app: login
def test_login_mode_is_announced_to_the_page(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.get("/auth/mode").json() == {"login_required": True, "signed_in": False}
    sign_in(client)
    assert client.get("/auth/mode").json() == {"login_required": True, "signed_in": True}


def test_nothing_is_reachable_without_signing_in(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    for method, path in [("get", "/api/info"), ("get", "/api/run"), ("get", "/api/runs"), ("get", "/api/logs"),
                         ("get", "/api/followers?account=a"), ("post", "/api/run"), ("post", "/api/run/cancel"),
                         ("post", "/api/run/answer"), ("get", "/api/schedule")]:
        assert getattr(client, method)(path).status_code == 401, path
    # the link token must NOT work as a back door while login is on
    assert client.get("/api/info", headers={"X-Access-Token": TOKEN}).status_code == 401


def test_correct_login_opens_the_api(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert sign_in(client).status_code == 200
    assert client.get("/api/info").status_code == 200


@pytest.mark.parametrize(("user", "password"), [("wrong", PASSWORD), (USER, "wrong"), ("", ""), (USER, PASSWORD + " ")])
def test_wrong_credentials_are_refused_with_one_generic_message(tmp_path: Path, user: str, password: str) -> None:
    client = make_client(tmp_path)
    response = sign_in(client, user, password)
    assert response.status_code == 401 and response.json()["detail"] == "Wrong user name or password"
    assert COOKIE_NAME not in response.cookies  # same message for a wrong name or a wrong password
    assert client.get("/api/info").status_code == 401


def test_the_session_cookie_is_locked_down(tmp_path: Path) -> None:
    header = sign_in(make_client(tmp_path)).headers["set-cookie"].lower()
    assert header.startswith(COOKIE_NAME) and "httponly" in header
    assert "samesite=strict" in header and "path=/" in header and "max-age=43200" in header
    assert PASSWORD not in header and USER not in header


def test_lockout_blocks_the_right_password_too(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    for _ in range(5):
        assert sign_in(client, password="wrong").status_code == 401
    locked = sign_in(client)  # correct password, but locked out
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert "Try again in" in locked.json()["detail"]
    assert client.get("/api/info").status_code == 401


def test_the_lockout_ends_and_a_success_resets_it(tmp_path: Path) -> None:
    clock = Clock()
    client = make_client(tmp_path, throttle=LoginThrottle(clock=clock))
    for _ in range(5):
        sign_in(client, password="wrong")
    assert sign_in(client).status_code == 429
    clock.now += 301
    assert sign_in(client).status_code == 200


def test_logout_ends_the_session(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    sign_in(client)
    assert client.get("/api/info").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/api/info").status_code == 401
    assert client.get("/auth/mode").json()["signed_in"] is False


def test_an_expired_session_sends_the_user_back_to_sign_in(tmp_path: Path) -> None:
    clock = Clock()
    client = make_client(tmp_path, sessions=SessionStore(lifetime_s=60, clock=clock))
    sign_in(client)
    clock.now += 61
    assert client.get("/api/info").status_code == 401


def test_a_forged_or_old_cookie_is_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.cookies.set(COOKIE_NAME, "not-a-real-session")
    assert client.get("/api/info").status_code == 401


def test_other_websites_cannot_sign_in_or_use_the_session(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    evil = {"Origin": "https://evil.example"}
    assert client.post("/auth/login", json={"username": USER, "password": PASSWORD}, headers=evil).status_code == 403
    sign_in(client)
    assert client.get("/api/info", headers=evil).status_code == 403  # cookie alone is not enough


def test_login_endpoint_input_is_validated(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.post("/auth/login", json={"username": 123, "password": []}).status_code == 422
    assert client.post("/auth/login", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert sign_in(client, "x" * 100_000, "y" * 100_000).status_code == 401  # huge input: refused, no crash


def test_the_password_never_appears_in_any_response(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    texts = [sign_in(client, password="wrong-guess-123").text, sign_in(client).text, client.get("/auth/mode").text,
             client.get("/api/info").text]
    assert all(PASSWORD not in t and "wrong-guess-123" not in t for t in texts)


# ---------------------------------------------------------------------- token mode
def test_without_a_login_configured_the_token_link_still_works(tmp_path: Path) -> None:
    client = make_client(tmp_path, creds={"user": "", "password": ""})
    assert client.get("/auth/mode").json() == {"login_required": False, "signed_in": False}
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/info", headers={"X-Access-Token": TOKEN}).status_code == 200
    assert client.post("/auth/login", json={"username": "a", "password": "b"}).status_code == 404  # no login to attempt


def test_a_half_configured_login_is_refused_at_start_up(tmp_path: Path) -> None:
    with pytest.raises(SettingsError):
        make_client(tmp_path, creds={"user": "manoj", "password": ""})
    with pytest.raises(SettingsError):
        make_client(tmp_path, creds={"user": "manoj", "password": "short"})


def test_non_ascii_token_header_is_a_401_not_a_server_error(tmp_path: Path) -> None:
    client = make_client(tmp_path, creds={"user": "", "password": ""})
    response = client.get("/api/info", headers={"X-Access-Token": "tökén".encode("utf-8")})
    assert response.status_code == 401
