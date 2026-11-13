import argparse
import getpass
import json
import sys
from dataclasses import replace
from pathlib import Path

from web_automation.browser import BrowserManager
from web_automation.browser.inspect import inspect_pages
from web_automation.browser.instagram_login import ensure_logged_in
from web_automation.config import load_config
from web_automation.db.connection import check_database, describe
from web_automation.engine.service import execute_on_page, settings_for
from web_automation.settings import Settings
from web_automation.utils.exceptions import WebAutomationError
from web_automation.utils.logger import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="web-automation",
        description="Open installed Chrome with a persistent profile, optionally running a "
        "JSON automation. Defaults come from .env / environment variables; flags override them.",
    )
    parser.add_argument("--config", type=Path, help="JSON automation file to validate and run")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="with --config: check the file and exit without launching Chrome",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="selector discovery: save accessibility snapshots of pages you navigate to",
    )
    parser.add_argument(
        "--db-check",
        action="store_true",
        help="connect to WA_DATABASE_URL, create the tables if missing, show row counts, and exit",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="start the web UI on http://127.0.0.1:8000 (followers, runs, logs, OTP box)",
    )
    parser.add_argument("--port", type=int, default=8000, help="port for --ui (default 8000)")
    parser.add_argument("--url", help="page to open when no --config is given (env: WA_URL)")
    parser.add_argument("--profile", help="persistent profile name (env: WA_PROFILE)")
    parser.add_argument(
        "--headless", action="store_true", default=None, help="run without a visible window"
    )
    parser.add_argument(
        "--keep-open", action="store_true", help="wait for Enter before closing the browser"
    )
    args = parser.parse_args(argv)
    if args.validate_only and not args.config:
        parser.error("--validate-only requires --config")

    try:
        if args.ui:
            from web_automation.web.app import serve

            serve(port=args.port)
            return 0
        if args.db_check:
            url = Settings.load().database_url
            if not url:
                print("error: WA_DATABASE_URL is not set in .env", file=sys.stderr)
                return 1
            counts = check_database(url)
            print(f"Connected to {describe(url)}. Tables are ready (rows):")
            for table, count in counts.items():
                print(f"  {table}: {count}")
            return 0
        config = load_config(args.config) if args.config else None
        if config and args.validate_only:
            print(f"OK: {args.config} is valid ({config.name}, {len(config.steps)} steps)")
            return 0

        settings = Settings.load()
        overrides = {
            k: v
            for k, v in {"url": args.url, "profile": args.profile, "headless": args.headless}.items()
            if v is not None
        }
        settings = replace(settings, **overrides)
        if config:
            settings = settings_for(config, settings)

        configure_logging()
        with BrowserManager(settings.browser_config()) as session:
            exit_code = 0
            if config:
                outcome = execute_on_page(config, settings, session.page)
                print(json.dumps(outcome.report, indent=2))
                exit_code = outcome.exit_code
                facts = outcome.facts
                if outcome.result.status == "stopped":
                    print(f"\nSTOPPED (nothing was submitted): {outcome.result.stop_reason}")
                elif facts["export_request_submitted"] and not facts["export_request_acknowledged"]:
                    print("\nWARNING: " + str(facts["message"]), file=sys.stderr)
            else:
                session.page.goto(settings.url)
                if "instagram.com" in settings.url:
                    login = ensure_logged_in(
                        session.page,
                        settings.ig_username,
                        settings.ig_password,
                        timeout_s=settings.login_timeout_s,
                        ask_code=getpass.getpass,
                    )
                    print(f"Login: {login.value}")
                print(f"Opened: {session.page.url}  title: {session.page.title()!r}")
                if args.inspect:
                    inspect_pages(session.page, Path("logs") / "inspect")
            if args.keep_open:
                input("Press Enter to close the browser...")
            return exit_code
    except WebAutomationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
