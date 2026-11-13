"""Local web UI: a FastAPI app that serves one static page plus a small JSON API.

Security model (the page shows your followers and can start a run that uses your password):
  * the server is meant to listen on 127.0.0.1 only (see `serve`);
  * every /api request must be authorised, in one of two modes:
      - login mode (WA_UI_USERNAME + WA_UI_PASSWORD set): the page shows a sign-in form; a
        correct login sets a random HttpOnly session cookie that expires after 12 hours, and
        repeated wrong attempts lock logins out for a while;
      - token mode (no login configured): the one-time link printed at start-up carries a
        random token that is sent in a header;
  * browsers' cross-site requests are refused (Origin/Host check), so another website you
    have open cannot drive the API;
  * responses are marked no-store, and the page escapes everything it shows.
"""

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from web_automation.db import ensure_schema, queries
from web_automation.settings import Settings
from web_automation.utils.exceptions import DatabaseError, WebAutomationError
from web_automation.web.auth import (
    COOKIE_NAME,
    LoginThrottle,
    SessionStore,
    credentials_match,
    tokens_equal,
    validate_login_settings,
)
from web_automation.web.runner import WebRunner
from web_automation.web.scheduler import RunScheduler

STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


class AnswerBody(BaseModel):
    question_id: int
    value: str | bool | None = None


class RunBody(BaseModel):
    config: str = "instagram_export.json"


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


def create_app(
    settings: Settings | None = None,
    token: str | None = None,
    runner: WebRunner | None = None,
    configs_dir: Path = Path("configs"),
    scheduler: RunScheduler | None = None,
    sessions: SessionStore | None = None,
    throttle: LoginThrottle | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    login_enabled = validate_login_settings(settings.ui_username, settings.ui_password)
    sessions = sessions or SessionStore()
    throttle = throttle or LoginThrottle()
    token = token or secrets.token_urlsafe(24)
    runner = runner or WebRunner()
    app = FastAPI(title="Web Automation Engine", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.sessions = sessions

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # Refuse requests from other websites (cross-site) and from non-local Host names
        # (DNS rebinding). Direct local use sends no Origin header and is allowed.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") else "[::1]"
        origin = request.headers.get("origin")
        origin_host = urlsplit(origin).hostname if origin else None
        if host not in LOOPBACK or (origin and origin_host not in LOOPBACK):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"
        )
        return response

    def require_token(
        request: Request, x_access_token: str | None = Header(default=None)
    ) -> None:
        if login_enabled:
            if not sessions.is_valid(request.cookies.get(COOKIE_NAME)):
                raise HTTPException(status_code=401, detail="Please sign in")
            return
        if not x_access_token or not tokens_equal(x_access_token, app.state.token):
            raise HTTPException(status_code=401, detail="Missing or wrong access token")

    def database() -> str:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="WA_DATABASE_URL is not set in .env")
        return settings.database_url

    def guarded(call):
        try:
            return call()
        except DatabaseError as exc:
            detail = str(exc)
            if "42703" in detail or "42P01" in detail:  # missing column / table
                detail += (
                    ". The database tables are out of date: restart the UI, or run "
                    "'uv run web-automation --db-check', to update them."
                )
            raise HTTPException(status_code=503, detail=detail) from None

    # ------------------------------------------------------------------- pages
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/static/{name}", include_in_schema=False)
    def static_file(name: str) -> FileResponse:
        if name not in {"app.js", "style.css"}:
            raise HTTPException(status_code=404)
        media = "text/javascript" if name.endswith(".js") else "text/css"
        return FileResponse(STATIC / name, media_type=media)

    api = Depends(require_token)

    # ------------------------------------------------------------------- login
    @app.get("/auth/mode", include_in_schema=False)
    def auth_mode(request: Request) -> dict[str, bool]:
        """Tells the page which sign-in to show. Reveals nothing but two yes/no values."""
        return {
            "login_required": login_enabled,
            "signed_in": login_enabled and sessions.is_valid(request.cookies.get(COOKIE_NAME)),
        }

    @app.post("/auth/login", include_in_schema=False)
    def login(body: LoginBody, response: Response) -> dict[str, str]:
        if not login_enabled:
            raise HTTPException(status_code=404)
        wait = throttle.retry_after()
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Too many wrong attempts. Try again in {wait} seconds.",
                headers={"Retry-After": str(wait)},
            )
        if not credentials_match(settings.ui_username, settings.ui_password, body.username, body.password):
            throttle.record_failure()
            raise HTTPException(status_code=401, detail="Wrong user name or password")
        throttle.record_success()
        response.set_cookie(
            COOKIE_NAME, sessions.create(), max_age=int(sessions.lifetime_s),
            httponly=True, samesite="strict", path="/",
        )
        return {"status": "signed in"}

    @app.post("/auth/logout", include_in_schema=False)
    def logout(request: Request, response: Response) -> dict[str, str]:
        sessions.revoke(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"status": "signed out"}

    # ------------------------------------------------------------------ status
    @app.get("/api/info", dependencies=[api])
    def info() -> dict[str, Any]:
        configs = sorted(p.name for p in configs_dir.glob("*.json"))
        return {
            "database_configured": bool(settings.database_url),
            "account": __import__("os").environ.get("WA_EXPECTED_ACCOUNT", ""),
            "configs": configs,
            "schedule": scheduler.status() if scheduler else {"enabled": False},
        }

    @app.get("/api/summary", dependencies=[api])
    def summary(account: str = Query(min_length=1, max_length=200)) -> dict[str, Any]:
        return guarded(lambda: queries.summary(database(), account))

    @app.get("/api/accounts", dependencies=[api])
    def accounts() -> list[str]:
        return guarded(lambda: queries.accounts(database()))

    # ------------------------------------------------------------------- data
    @app.get("/api/followers", dependencies=[api])
    def followers(
        account: str = Query(min_length=1, max_length=200),
        search: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=queries.MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return guarded(lambda: queries.followers(database(), account, search, limit, offset))

    @app.get("/api/follower-events", dependencies=[api])
    def follower_events(
        account: str = Query(min_length=1, max_length=200),
        event: str = Query(pattern="^(added|removed)$"),
        since: datetime | None = None,
        limit: int = Query(default=50, ge=1, le=queries.MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return guarded(lambda: queries.follower_events(database(), account, event, since, limit, offset))

    @app.get("/api/runs", dependencies=[api])
    def runs(
        limit: int = Query(default=25, ge=1, le=queries.MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return guarded(lambda: queries.runs(database(), limit, offset))

    @app.get("/api/runs/{run_id}/steps", dependencies=[api])
    def run_steps(run_id: int) -> list[dict[str, Any]]:
        return guarded(lambda: queries.run_steps(database(), run_id))

    @app.get("/api/logs", dependencies=[api])
    def logs(
        run_id: int | None = Query(default=None, ge=1),
        level: str | None = Query(default=None, pattern="^(?i)(info|warning|error|debug|critical)$"),
        search: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=100, ge=1, le=queries.MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return guarded(lambda: queries.logs(database(), run_id, level, search, limit, offset))

    # --------------------------------------------------------------- the runner
    @app.get("/api/run", dependencies=[api])
    def run_status() -> dict[str, Any]:
        return runner.status()

    @app.get("/api/schedule", dependencies=[api])
    def schedule_status() -> dict[str, Any]:
        return scheduler.status() if scheduler else {"enabled": False}

    @app.post("/api/run", dependencies=[api])
    def start_run(body: RunBody) -> dict[str, str]:
        name = Path(body.config).name  # a bare file name only: no folders, no '..'
        path = configs_dir / name
        if name != body.config or path.suffix != ".json" or not path.is_file():
            raise HTTPException(status_code=400, detail="Unknown configuration file")
        try:
            runner.start(path, trigger="manual")  # the button always means "manual"
        except WebAutomationError as exc:
            raise HTTPException(status_code=409 if runner.running else 400, detail=str(exc)) from None
        return {"status": "started"}

    @app.post("/api/run/answer", dependencies=[api])
    def answer(body: AnswerBody) -> dict[str, str]:
        try:
            runner.answer(body.question_id, body.value)
        except WebAutomationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"status": "ok"}  # the code is never echoed back

    @app.post("/api/run/cancel", dependencies=[api])
    def cancel() -> dict[str, str]:
        runner.cancel()
        return {"status": "cancel requested"}

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the UI. Refuses any host that is not this machine."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise WebAutomationError(
            "The UI shows your followers and can start runs; it only listens on this computer "
            "(127.0.0.1)."
        )
    token = secrets.token_urlsafe(24)
    settings = Settings.load()
    login_enabled = validate_login_settings(settings.ui_username, settings.ui_password)
    runner = WebRunner()
    scheduler = RunScheduler(
        runner, Path("configs"), settings.schedule_config, settings.schedule_minutes
    )
    app = create_app(settings=settings, token=token, runner=runner, scheduler=scheduler)
    if settings.database_url:
        # Bring older tables up to date (new columns) before any page asks for them.
        try:
            ensure_schema(settings.database_url)
        except DatabaseError as exc:
            print(f"Warning: could not update the database tables: {exc}\n")
    scheduler.start()
    if login_enabled:
        print(f"\nWeb UI:  http://{host}:{port}/\n"
              "Sign in with WA_UI_USERNAME and WA_UI_PASSWORD from your .env file.")
    else:
        print(f"\nWeb UI:  http://{host}:{port}/#token={token}\n"
              "Open exactly this link (the token after # is your access key; keep it private).\n"
              "Tip: set WA_UI_USERNAME and WA_UI_PASSWORD in .env to sign in with a password instead.")
    if scheduler.enabled:
        print(f"Automatic runs: every {scheduler.minutes} minute(s); the first at "
              f"{scheduler.next_run_time().strftime('%H:%M')} (only while this window stays open).\n")
    else:
        print("Automatic runs: off (set WA_SCHEDULE_MINUTES in .env to turn them on).\n")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        scheduler.stop()
