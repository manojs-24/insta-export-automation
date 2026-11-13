# Run commands

Run everything from the project folder. `uv run` uses the project's `.venv` itself, so there is
nothing to activate. Commands work in PowerShell and in Git Bash unless marked.

## 1. Run the Instagram export (the main workflow)

```
uv run web-automation --config configs/instagram_export.json --keep-open
```

What it does: log in if needed, check no export is pending, create the export (customised fields,
All time, JSON), enter the password, wait for Instagram to accept it, download the newest
finished export, and update the followers table in the database.

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Configuration, browser or database setup error (nothing was run) |
| 2 | A step failed (open the run in the UI: Runs, then Details) |
| 3 | A guard stopped the run on purpose, e.g. an export is already pending (nothing submitted) |

Without `--keep-open`, Chrome closes as soon as the run ends instead of waiting for Enter.

## 1b. Web UI (followers, recent/unfollowed, runs, logs, OTP box)

```
uv run web-automation --ui          # or double-click start_ui.bat
```

Two ways in:
- **Sign-in page (recommended).** Put `WA_UI_USERNAME` and `WA_UI_PASSWORD` (8+ characters) in `.env`.
  Then just open `http://127.0.0.1:8000/` and sign in. The login lasts 12 hours; five wrong
  attempts lock sign-in for five minutes; restarting the UI signs everyone out.
- **One-time link.** If you leave both empty, open the exact link printed in the terminal (it ends
  in `#token=...`; that token is your access key, keep it private). It changes at every start. The UI only works on this computer (127.0.0.1). From the page you can
press **Run now**; when Instagram needs a verification code, a box appears at the top of the
page: type the code there and press Submit. The code is not stored or logged.

### Automatic runs

Set `WA_SCHEDULE_MINUTES` in `.env` (0 = off, minimum 5) and start the UI. Runs happen only while
the UI window stays open; the first is one full interval after start-up. `WA_ANSWER_WAIT_MINUTES`
sets how long a run waits for you to type the OTP code. The Runs tab has a **Started by** column
(Manual / Automatic).

## 2. Check things without running the workflow

```
uv run web-automation --config configs/instagram_export.json --validate-only   # config is valid? (no Chrome)
uv run web-automation --db-check                                               # database reachable? creates tables
uv run web-automation --help                                                   # every option
```

## 3. Simple browser runs

```
uv run web-automation --keep-open                                   # open WA_URL in Chrome (smoke test)
start.bat                                                           # same, by double-click
uv run web-automation --url https://example.com/ --keep-open        # open a specific page
uv run web-automation --profile other --keep-open                   # use a different saved profile
uv run web-automation --url https://example.com/ --headless         # no visible window
```

Do not use `--headless` for the Instagram workflow: the OTP and password prompts need the window.

## 4. Find selectors for a new page

```
uv run web-automation --inspect --keep-open --url https://www.instagram.com/
```

Click to a page, type a label in the terminal, press Enter. A snapshot is saved to
`logs/inspect/`. Press Enter on a blank label to finish. Snapshots can contain account names:
look before sharing.

## 5. Tests

```
uv run pytest                              # everything (about 5 minutes, opens Chrome headless)
uv run pytest -m "not browser"             # fast tests only, no Chrome (about 10 seconds)
uv run pytest tests/test_db_logic.py       # one file
uv run pytest -k followers                 # tests whose name contains "followers"
uv run pytest tests/test_actions.py::test_click_missing_element_fails_clearly   # one test
```

PowerShell only, to watch Chrome during the browser tests:
```
$env:WA_TEST_HEADED="1"; uv run pytest
```

Database tests against a real Postgres (they use a temporary schema and delete it afterwards):
```
$env:WA_TEST_DATABASE_URL="postgresql://user:password@host:5432/dbname"; uv run pytest tests/test_db_integration.py
```
In Git Bash: `WA_TEST_DATABASE_URL="postgresql://..." uv run pytest tests/test_db_integration.py`

## 6. Dependencies

```
uv sync                                    # install what pyproject.toml / uv.lock say
uv add <package>                           # add a package
uv remove <package>                        # remove one
uv export --format requirements-txt --no-hashes --no-emit-project -o requirements.txt   # refresh requirements.txt
```

## 7. Settings (`.env`)

| Variable | Used for |
|---|---|
| `WA_PROFILE` | Browser profile folder under `browser_data/` (holds the Instagram login) |
| `WA_IG_USERNAME`, `WA_IG_PASSWORD` | Login and the "re-enter your password" prompts |
| `WA_EXPECTED_ACCOUNT` | The account the export must belong to (also names the followers' owner in the database) |
| `WA_DATABASE_URL` | `postgresql://user:password@host:5432/dbname` |
| `WA_EXPORT_DIR` | Where downloaded zips are saved (`exports/`) |
| `WA_URL`, `WA_HEADLESS`, `WA_TIMEOUT_MS`, `WA_LOGIN_TIMEOUT_S`, `WA_CHROME_CHANNEL`, `WA_PROFILE_ROOT` | Other options, see `.env.example` |

Command-line flags (`--url`, `--profile`, `--headless`) override `.env` for one run.

## 8. Where things end up

| What | Where |
|---|---|
| Downloaded export (latest only) | `exports/downloads/` |
| Followers, run history, logs | The database (`wa_followers`, `wa_runs`, `wa_run_steps`, `wa_logs`) |
| Run logs | The database (`wa_logs`), shown in the UI's Logs tab. Nothing is written to a log file |
| Selector snapshots | `logs/inspect/` |
| Saved login | `browser_data/<profile>/` (keep it; deleting it means logging in again) |

## 9. Common problems

| Message | Fix |
|---|---|
| `Profile 'x' is already open in another Chrome window` | Close the automation Chrome window that is still open, then run again |
| `STOPPED ... export request is already pending` | Wait for Instagram to finish it, or cancel it there. Nothing was submitted |
| `Environment variable ... is not set` | Fill that variable in `.env` |
| `Could not connect to the database` | Check `WA_DATABASE_URL`; hosted databases often need `?sslmode=require` at the end |
| A step failed | Open the run in the UI (Runs tab, Details shows the failing step and its message; Logs tab has the lines) and send me the error |
