# Web Automation Engine

A rule-based browser automation engine built on Playwright and your installed Google Chrome. Workflows
are written as JSON files, not code. The included workflow requests an **Instagram data export**
(followers and following, as JSON), downloads the finished export, and keeps your **followers list** in a
PostgreSQL database, recording who followed and who unfollowed. A local web page shows the results and
lets you run the workflow, and it can also run on a schedule.

> Only use it on accounts you own or are authorised to manage. It never bypasses a login, a security
> check or a CAPTCHA: verification codes and password prompts are completed by you (or by your own
> credentials in `.env`).

## What it does

1. Opens Chrome with a dedicated, saved profile (your everyday Chrome profile is never used).
2. Logs in to Instagram if needed. The verification code (for example sent to WhatsApp) is typed by you,
   in the terminal or in the web page.
3. Checks that no export request is already pending, so it never creates a duplicate.
4. Creates an export: your chosen fields (default: contacts, followers and following), date range
   **All time**, format **JSON**, and checks the account, range and format before submitting.
5. Clicks **Start export**, enters your password when Instagram asks for it again, and waits for
   Instagram to acknowledge the request.
6. Downloads the newest finished export into `exports/downloads/` (older downloads are removed) and
   reads the followers list straight from the zip.
7. Updates the database: new followers are added with the date and time they were seen, and people who
   no longer follow you are removed. Every run, step and log line is stored too.

A new export takes Instagram some time to prepare. The download step takes the newest export that is
already finished, so a request you just made is normally picked up by a later run.

## Requirements

- Windows (the project was built and tested on Windows 11), with **Google Chrome** installed. Or Docker on
  a 64-bit Intel/AMD machine, which brings its own Chrome: see [Running with Docker](#running-with-docker).
- [uv](https://docs.astral.sh/uv/), which also installs the right Python (3.14) for the project.
- A **PostgreSQL** database you can reach (a hosted one is fine) and its connection URL.
- An Instagram account you own.

## Setup

1. Get the project folder and open a terminal in it.
2. Install the dependencies:
   ```
   uv sync
   ```
3. Create your settings file and fill it in (see [Settings](#settings)):
   ```
   copy .env.example .env
   ```
   At minimum set `WA_EXPECTED_ACCOUNT` (your Instagram user name), `WA_DATABASE_URL`, and
   `WA_IG_PASSWORD` (or leave the Instagram credentials empty and log in by hand).
4. Check the database connection. This also creates the tables:
   ```
   uv run web-automation --db-check
   ```
5. Check the workflow file:
   ```
   uv run web-automation --config configs/instagram_export.json --validate-only
   ```

`.env` is ignored by git. Never commit it: it holds your passwords and database URL.

## Running it

### Run the Actual Server and Web page
```
uv run web-automation --ui
```
or double-click `start_ui.bat`. See [The web page](#the-web-page).

### Run it automatically
Set `WA_SCHEDULE_MINUTES` in `.env` and start the web page. The workflow then runs by itself every that
many minutes. **It only runs while the web page's program is open**: if you close it, or the computer
sleeps, nothing runs. See [Automatic runs](#automatic-runs).


### Run the export from the command line - Automation alone
```
uv run web-automation --config configs/instagram_export.json
```
On the very first run, Chrome opens on Instagram's login page. Enter your credentials (or let the
script fill them from `.env`) and type the verification code when the terminal asks for it. Your login
is saved in `browser_data/<profile>/`, so later runs skip all of that. Add `--keep-open` to leave Chrome
open until you press Enter.

**This submits a real export request** when no request is pending. It is not a dry run.

| Exit code | Meaning |
|---|---|
| 0 | The run finished successfully |
| 1 | A setup problem (bad config, browser, database): nothing was run |
| 2 | A step failed |
| 3 | A safety check stopped the run on purpose, for example an export is already pending. Nothing was submitted |

### Other commands
```
uv run web-automation --help                                        # every option
uv run web-automation --keep-open                                   # just open WA_URL in Chrome (also: start.bat)
uv run web-automation --inspect --keep-open --url https://www.instagram.com/   # save page snapshots, see "Updating selectors"
uv run web-automation --profile other --keep-open                   # use a different saved profile
```

## Settings

All settings live in `.env` (copy `.env.example`). A real environment variable overrides `.env`, and a
command-line flag (`--url`, `--profile`, `--headless`) overrides both for one run.

| Setting | Default in code | What it does |
|---|---|---|
| `WA_EXPECTED_ACCOUNT` | none | Your Instagram user name. The workflow checks the export screen names this account, and the followers table is stored under it |
| `WA_DATABASE_URL` | none | `postgresql://user:password@host:5432/dbname`. Hosted databases often need `?sslmode=require` at the end |
| `WA_IG_USERNAME`, `WA_IG_PASSWORD` | empty | Optional. Filled in on Instagram's login page and on the "re-enter your password" prompts. Leave empty to log in by hand. The verification code is never stored |
| `WA_PROFILE` | `chrome_profile` | Name of the saved Chrome profile (`browser_data/<name>/`). The template uses `script` |
| `WA_PROFILE_ROOT` | `browser_data` | Folder holding the profiles |
| `WA_HEADLESS` | `false` | Hide the Chrome window. Do not use it for the Instagram workflow: the login and password prompts need the window |
| `WA_CHROME_CHANNEL` | `chrome` | Use the installed Google Chrome |
| `WA_TIMEOUT_MS` | `30000` | How long a step waits for something on the page |
| `WA_LOGIN_TIMEOUT_S` | `300` | How long to wait for login and the verification code |
| `WA_EXPORT_DIR` | `exports` | Where downloaded zips are saved |
| `WA_URL` | `https://example.com/` | The page opened when you run without `--config` (the template uses Instagram) |
| `WA_SCHEDULE_MINUTES` | `0` (off) | Minutes between automatic runs, 5 to 10080. The template ships with `360` (every 6 hours) |
| `WA_SCHEDULE_CONFIG` | `instagram_export.json` | Which file in `configs/` the schedule runs |
| `WA_ANSWER_WAIT_MINUTES` | `10` | How long a run waits for you to type the verification code before giving up (1 to 240) |
| `WA_UI_USERNAME`, `WA_UI_PASSWORD` | empty | Sign-in for the web page. Set both (password at least 8 characters). Empty means the one-time link is used |
| `WA_UI_HOST` | `127.0.0.1` | Where the web page listens. Anything other than this computer (the Docker image sets `0.0.0.0`) is refused unless the sign-in above is set |
| `WA_UI_ALLOWED_HOSTS` | empty | Extra host names the page may be opened as, comma separated (for example your domain behind an HTTPS proxy). `localhost` and `127.0.0.1` always work; any other name is refused |

An invalid value stops the program with a clear message instead of being ignored.

## The web page

Start it with `uv run web-automation --ui`. It listens **only on this computer** (`127.0.0.1:8000`; use
`--port` to change the port) and cannot be reached from other machines.

**Getting in.** There are two ways, chosen by `.env`:
- **Sign-in page (recommended).** Set `WA_UI_USERNAME` and `WA_UI_PASSWORD`, then open
  `http://127.0.0.1:8000/` and sign in. A login lasts 12 hours. After 5 wrong attempts within 5 minutes,
  sign-in is refused for 5 minutes. Restarting the program signs you out.
- **One-time link.** If you leave both empty, open the exact link printed in the terminal. It ends in
  `#token=...`, changes at every start, and is your access key, so keep it private.

**Tabs**

| Tab | Shows |
|---|---|
| Dashboard | **Run now**, the live progress of the current run, the follower counts (now, new and unfollowed in the last 7 days, unfollowed in total) and the last run |
| Followers | Everyone who follows you now, with a user name search and the date each follower was first seen |
| Recent followers | New followers, filtered by 1, 7 or 30 days or all time |
| Unfollowed | People who stopped following you, with the date the run noticed it |
| Runs | Every run: when, **Manual** or **Automatic**, status, whether the request was submitted, accepted and downloaded. **Details** lists each step with its time, tries and message |
| Logs | The log lines of all runs, filtered by run number, level or text |

**The verification code box.** When a run needs a code, an orange box appears at the top of every tab with
a masked input. Type the code and press Submit. It is passed to the login page and is never stored,
logged or sent back. If nobody answers within `WA_ANSWER_WAIT_MINUTES`, or you press **Cancel**, the run
stops.

Only one run happens at a time. A second **Run now** is refused while one is running.

### Automatic runs
With `WA_SCHEDULE_MINUTES` set, the web page's program starts the workflow on that interval.
- The first automatic run happens one full interval after start-up, never immediately.
- A run is never started on top of one that is still running; that tick is skipped.
- Runs are marked **Automatic** in the Runs tab, and the **Run now** button always records **Manual**.
- If Instagram asks for a code during an unattended run, it waits `WA_ANSWER_WAIT_MINUTES` for you, then
  stops. The next scheduled run tries again. A saved login normally avoids this.
- Each run that finds no pending request creates a **new real export request**. A short interval means
  many requests to Instagram. For this workflow, once a day (`1440`) is a sensible choice.

## What is stored

**In the database** (tables are created and updated automatically):

| Table | Holds |
|---|---|
| `wa_followers` | The current followers of each account. One row per follower, with the time it was first seen. New followers are added, and anyone missing from the latest export is removed |
| `wa_follower_events` | A history of every `added` and `removed` change with its time, so unfollowers stay on record after their row is deleted |
| `wa_runs` | One row per run: times, status, manual or automatic, and whether the request was submitted, acknowledged and downloaded |
| `wa_run_steps` | Every step of every run: status, duration, tries and message |
| `wa_logs` | The log lines of every run |

Safety limits on the followers sync: an empty list, or a list that would remove more than half of 20 or
more stored followers, is refused and nothing is changed.

**On disk**

| Where | What |
|---|---|
| `exports/downloads/` | The latest downloaded export zip. Older ones are deleted after a verified new download |
| `browser_data/<profile>/` | The saved Chrome profile, including your Instagram login. Keep it; deleting it means logging in again |
| `logs/inspect/` | Page snapshots written by `--inspect` |

There is no log file: logs go to the console and to the database. No screenshots are taken.

## How it works

**Recipe and cook.** The workflow is a JSON file in `configs/`. The Python engine reads it, checks it,
and runs each step in order against Chrome. There are no Instagram steps in the code; to change what
happens, edit the JSON.

```json
{ "id": "create_export", "action": "click",
  "selector": { "type": "role", "role": "button", "name": "Create export" },
  "retry": { "max_attempts": 3, "delay_ms": 1500 } }
```

The file is validated before Chrome opens: unknown actions, missing or misspelled fields, a literal
password, or a `..` path are rejected up front, and every problem is listed at once.

**Finding things on the page.** A step names an element the way a screen reader would: a *role* and an
*accessible name*, such as the button called "Start export". Other selector types are `label`,
`placeholder`, `text`, `css` and `xpath`. Options: `exact`, `starts_with` (role names), and `within_text`,
which limits the search to the popup containing that text so a button on the page behind it cannot be
hit. Before it clicks, the engine checks the element is there, visible and enabled. If it matches nothing,
or more than one thing, the step fails; it never guesses another element.

**Actions**

| Action | What it does |
|---|---|
| `navigate` | Open an `http(s)` URL |
| `click` | Click an element |
| `click_if_visible` | Click it if it appears (for example a popup), otherwise skip |
| `fill`, `select` | Type into a field, or pick an option. `fill` can read its value from an environment variable (`value_env`) |
| `set_checkboxes` | Make exactly the listed checkboxes ticked and clear the others |
| `wait` | Wait for an element, a URL fragment, a load state, or a fixed pause |
| `verify` | Fail unless an element's text contains the expected text |
| `abort_if_visible` | Stop the run cleanly if something is on the page (used for "an export is already pending") |
| `extract_text`, `extract_attribute` | Save a value into a variable |
| `ensure_login` | Log in only if the login page shows. Asks for the verification code |
| `enter_password` | Handle a "re-enter your password" prompt from `WA_IG_PASSWORD`. Skipped if none appears |
| `download_latest_export` | Download the newest finished export through Instagram's popup and password prompt |
| `extract_from_zip` | Copy one file out of a zip (size-limited, paths kept inside `exports/`) |
| `sync_followers` | Read the followers file from the zip and update the database |
| `pause_for_user`, `confirm` | Wait for you, or ask a yes/no question |

**Conditions and retries.** A step can carry `"when": {"variable": "login_result", "equals": "logged_in"}`
to run only when an earlier step saved that value; otherwise it is skipped. A step can carry
`"retry": {"max_attempts": 3}` (2 to 5). Only actions that are safe to repeat accept it: login, password
entry, downloads and database writes can never be retried, and the configuration is rejected if you try.
A click is retried only when its element was not found, meaning nothing had been clicked yet. The shipped
workflow puts no retry on the final **Start export** step, and a test checks that.

**The run report.** A run reports four separate facts: execution completed, request submitted, request
acknowledged by Instagram, and file downloaded. Clicking the final button alone is never reported as a
successful export.

## Safety

- A dedicated Chrome profile is used, and the code refuses your everyday Chrome profile.
- Passwords live only in `.env`, never in the JSON workflow. The code never logs or prints passwords,
  verification codes, cookies, session tokens or the database URL, and driver errors are stripped of
  connection details.
- Before **Start export**, the workflow checks that no request is pending, that the account is yours
  (`WA_EXPECTED_ACCOUNT`), and that the range is All time and the format is JSON. If any check fails, it
  stops without submitting.
- The final **Start export** step has no retry, to avoid duplicate requests.
- The web page listens only on `127.0.0.1`, needs the sign-in or the link token for every request, rejects
  requests from other websites, shows data as plain text, and serves with a strict content policy.
- `.env`, `browser_data/` and `exports/` are in `.gitignore`. Treat your followers list and the exported
  data as sensitive.

## Running with Docker

The image contains the program, Google Chrome and a virtual screen, so it runs the same workflow on a
server without a desktop. It is **Linux/amd64 only**, because Google Chrome for Linux is.

1. Copy `.env.example` to `.env` and fill it in. **`WA_UI_USERNAME` and `WA_UI_PASSWORD` are required**:
   the container listens on all of its addresses, and the program refuses to do that without a sign-in.
   Also set `WA_DATABASE_URL`, `WA_EXPECTED_ACCOUNT`, and `WA_IG_USERNAME` / `WA_IG_PASSWORD`.
2. Build and start it:
   ```
   docker compose up -d --build
   ```
3. Open `http://127.0.0.1:8000/` on that machine and sign in. When a run needs a verification code, the
   box appears on the page as usual.

| Task | Command |
|---|---|
| Watch the output | `docker compose logs -f` |
| Stop it (your data is kept) | `docker compose down` |
| Update after pulling new code | `docker compose up -d --build` |
| Check the database | `docker compose run --rm web-automation web-automation --db-check` |
| Check the workflow file | `docker compose run --rm web-automation web-automation --config configs/instagram_export.json --validate-only` |

**Data.** Two named volumes hold everything that must survive a rebuild: `browser_data` (the saved
Instagram login, so keep it private) and `exports` (the downloaded zip). Followers, runs and logs are in your
database, not in the container. `docker compose down -v` deletes the volumes, and with them your login.

**Reaching it from another machine.** The port is published on the server's own loopback
(`127.0.0.1:8000`) on purpose, because the page speaks plain HTTP. Two safe ways in:
- an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 you@your-server`, then open `http://127.0.0.1:8000/`;
- an HTTPS reverse proxy in front of it. Set `WA_UI_ALLOWED_HOSTS=your.domain`, pass the `Host` header
  through, and send `X-Forwarded-Proto: https` so the sign-in cookie is marked Secure.

Do not change the `ports:` line to publish port 8000 on every interface.

**Things that behave differently in a container**
- **You cannot see the Chrome window.** It draws on a virtual screen. The normal login (password from
  `.env`, then the code typed in the web page) works without looking at it, but a challenge that needs
  clicking on the screen, such as a CAPTCHA or "was this you?", cannot be completed. Instagram may also
  challenge a login from a data-centre address more often than one from your home. Do the first login
  with that in mind.
- **A saved login from your Windows computer will most likely not work in the container.** Chrome encrypts
  its cookies with a key that belongs to the operating system, so log in inside the container instead.
- **Automatic runs** keep going while the container runs (`restart: unless-stopped`). Check
  `docker compose logs` if they seem to stop.
- **The workflow file is built into the image.** After editing `configs/instagram_export.json`, rebuild.
- **Chrome is whatever version Google ships on the day you build**, so an image rebuilt later may behave
  slightly differently from an older one.

## Updating selectors when Instagram changes

Instagram changes its pages from time to time. If a step starts failing with "element not visible":
1. Run `uv run web-automation --inspect --keep-open --url https://www.instagram.com/`.
2. Click to the page that fails, type a label in the terminal, and press Enter. A text snapshot of the
   page's buttons, links and fields is saved in `logs/inspect/`. Blank label to finish.
3. Open the snapshot, find the element's current name, and update the step in `configs/instagram_export.json`.
4. Check the file with `--validate-only`.

Snapshots can contain your account name, so look before sharing one.

## Project layout

```
configs/instagram_export.json     the workflow (the "recipe")
src/web_automation/
  main.py                         command line: --config, --ui, --db-check, --inspect
  settings.py                     reads .env
  browser/                        Chrome, saved profile, login helper, --inspect
  config/                         JSON schema and loader (validation)
  actions/                        every step type (click, wait, download, sync_followers, ...)
  engine/                         runs the steps, conditions, retries, run report
  db/                             schema, run history, followers sync, queries for the page
  web/                            the web page: server, background runner, scheduler, sign-in, static files
  utils/                          errors and logging
tests/                            automated tests
```

`COMMANDS.md` lists every command in one place.

## Tests

```
uv run pytest                      # everything (about 7 minutes; opens Chrome headless)
uv run pytest -m "not browser"     # fast tests only, no Chrome (seconds)
```

The tests run against small local pages that imitate Instagram, so they do not touch Instagram or your
real database or credentials. A few database tests are skipped unless you set `WA_TEST_DATABASE_URL`
(they run in a temporary schema they delete afterwards).

## Troubleshooting

| Message | What to do |
|---|---|
| `Profile '...' is already open in another Chrome window` | Close the automation's Chrome window, then run again |
| `STOPPED ... export request is already pending` | Wait for Instagram to finish it (or cancel it there), then run again. Nothing was submitted |
| `Environment variable ... is not set` | Fill that setting in `.env` |
| `Could not connect to the database` | Check `WA_DATABASE_URL`; add `?sslmode=require` for most hosted databases |
| The dashboard says the tables are out of date | Restart the web page, or run `uv run web-automation --db-check` |
| A step failed | Open the run in the web page (Runs, then Details) for the failing step and its message, and the Logs tab for the lines around it |
| `WA_UI_USERNAME and WA_UI_PASSWORD must both be set` | Set both, or leave both empty |

## Limits

- The workflow's button and link names were taken from Instagram's current pages. They will need updating
  when Instagram changes them (see above), and the automated tests cannot detect that.
- Automatic runs need the web page's program to stay open, and the computer awake.
- Built and tested on Windows. Other systems are untested.
- Each run that finds no pending request creates a real export request.
- The Docker image is Linux/amd64 only, and you cannot see or click in Chrome inside it.
