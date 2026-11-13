import functools
import http.server
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from web_automation.browser import BrowserConfig, BrowserManager
from web_automation.engine import ExecutionContext

from downloads_fixtures import downloads_site  # noqa: F401  (pytest fixture, imported for use)

PAGE = """<!doctype html>
<html><head><title>Test Page</title></head>
<body><h1 id="greeting">Hello automation</h1>
<button id="go" onclick="document.getElementById('greeting').textContent='Clicked'">Go</button>
<button disabled>Locked</button>
<label for="email">Email</label><input id="email" placeholder="you@example.com">
<a id="link" href="/other.html" data-id="42">Other page</a>
<select id="colour"><option value="r">Red</option><option value="g">Green</option></select>
<button onclick="setTimeout(()=>{const d=document.createElement('div');d.id='late';d.textContent='Arrived';document.body.append(d)},600)">Load late</button>
<button class="dup">Twin</button><button class="dup">Twin</button>
<button onclick="document.getElementById('greeting').textContent='Format chosen'">Format HTML</button><button>Customise information</button>
<article><button onclick="document.getElementById('greeting').textContent='Wrong save (feed post)'">Save</button></article>
<div id="popup"><h3>Save your login info to Instagram?</h3>
<button onclick="document.getElementById('greeting').textContent='Info saved'">Save</button><button>Not now</button></div>
<button onclick="setTimeout(()=>{document.getElementById('greeting').textContent='Settled'},600)">Settle</button>
<div role="group" aria-label="Fields"><label><input type="checkbox"> Alpha</label><label><input type="checkbox" checked> Beta</label><label><input type="checkbox"> Gamma</label><label><input type="checkbox" checked> Delta</label></div>
<input id="secret" aria-label="Secret" onkeydown="if(event.key==='Enter')document.getElementById('greeting').textContent='Submitted:'+this.value.length">
</body></html>"""


@pytest.fixture(scope="session")
def test_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A tiny local HTTP site, so browser tests never touch the internet."""
    root = tmp_path_factory.mktemp("site")
    (root / "index.html").write_text(PAGE, encoding="utf-8")
    (root / "other.html").write_text("<title>Other</title><h1>Other page</h1>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()
    server.server_close()


LOGIN_PAGE = """<!doctype html><title>Login</title>
<input name="username" aria-label="user"><input type="password" aria-label="pw">
<script>
document.querySelector('input[type=password]').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const u = document.querySelector('input[name=username]').value;
    const p = document.querySelector('input[type=password]').value;
    if (u === 'alice' && p === 's3cret') location.href = '/home.html';
  }
});
</script>"""


@pytest.fixture(scope="session")
def login_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Local site: /accounts/login/ (alice / s3cret) and /home.html (logged in)."""
    root = tmp_path_factory.mktemp("login_site")
    (root / "accounts" / "login").mkdir(parents=True)
    (root / "accounts" / "login" / "index.html").write_text(LOGIN_PAGE, encoding="utf-8")
    (root / "home.html").write_text("<title>Home</title><h1>Feed</h1>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser_config(tmp_path: Path) -> BrowserConfig:
    """Throwaway profile in tmp_path. Headless by default; WA_TEST_HEADED=1 shows Chrome."""
    return BrowserConfig(
        profile_root=tmp_path / "browser_data",
        profile_name="test",
        headless=os.environ.get("WA_TEST_HEADED") != "1",
    )


@pytest.fixture
def ctx(browser_config: BrowserConfig, test_site: str, tmp_path: Path) -> Iterator[ExecutionContext]:
    """A live page on the test site wrapped in an ExecutionContext with scripted prompts."""
    with BrowserManager(browser_config) as session:
        session.page.goto(test_site)
        yield ExecutionContext(
            page=session.page,
            timeout_ms=3000,
            notify=lambda message: None,
            wait_for_user=lambda message: None,
            confirm_with_user=lambda message: True,
        )


PASSWORD_PAGE = """<!doctype html><title>Prompt</title><h1 id="state">waiting</h1>
<button onclick="document.getElementById('d').style.display='block'">Start</button>
<div id="d" role="dialog" style="display:none">
  <p>For your security, please re-enter your password</p>
  <label for="pw">Password</label><input id="pw" type="password">
  <button id="cont" disabled>Continue</button><button>Close</button>
</div>
<script>
const pw=document.getElementById('pw'), cont=document.getElementById('cont');
pw.addEventListener('input',()=>{cont.disabled=pw.value.length===0});
cont.addEventListener('click',()=>{
  document.getElementById('state').textContent = pw.value==='right-pass' ? 'accepted' : 'rejected';
  if(pw.value==='right-pass') document.getElementById('d').style.display='none';
});
</script>"""


@pytest.fixture(scope="session")
def password_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Local page whose Start button opens a 're-enter your password' dialog."""
    root = tmp_path_factory.mktemp("password_site")
    (root / "index.html").write_text(PASSWORD_PAGE, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()
    server.server_close()


PENDING_PAGE = """<!doctype html><title>Exports</title>
<button id="create" onclick="document.getElementById('created').textContent='created'">Create export</button>
<span id="created"></span>
<div id="list"></div>
<script>
// the earlier-exports list loads AFTER the page opens, like Instagram's dialog
setTimeout(() => {
  if (location.search.includes('pending')) {
    document.getElementById('list').innerHTML =
      '<h2>Requested</h2><p>Your information is being prepared for export.</p>';
  }
}, 700);
</script>"""


@pytest.fixture(scope="session")
def pending_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Local page; add ?pending to make a 'Requested' heading appear after a delay."""
    root = tmp_path_factory.mktemp("pending_site")
    (root / "index.html").write_text(PENDING_PAGE, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()
    server.server_close()


OTP_LOGIN_PAGE = """<!doctype html><title>Login</title>
<div id="step1"><input name="username" aria-label="user"><input type="password" aria-label="pw"></div>
<div id="step2" style="display:none">
  <p>Enter the 6-digit code we sent you</p>
  <input name="verificationCode" aria-label="Security code" autocomplete="one-time-code">
  <p id="err"></p>
</div>
<script>
const pw = document.querySelector('input[type=password]');
pw.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  pw.value = '';                      // password field vanishes after step 1, like Instagram
  document.getElementById('step1').style.display = 'none';
  document.getElementById('step2').style.display = 'block';
  history.pushState({}, '', '/challenge/');
});
document.querySelector('input[name=verificationCode]').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (e.target.value === '123456') location.href = '/home.html';
  else { document.getElementById('err').textContent = 'wrong code'; e.target.value = ''; }
});
</script>"""


@pytest.fixture(scope="session")
def otp_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Local login with a second step asking for a code. Right code: 123456."""
    root = tmp_path_factory.mktemp("otp_site")
    (root / "accounts" / "login").mkdir(parents=True)
    (root / "accounts" / "login" / "index.html").write_text(OTP_LOGIN_PAGE, encoding="utf-8")
    (root / "challenge").mkdir()
    (root / "challenge" / "index.html").write_text(OTP_LOGIN_PAGE, encoding="utf-8")
    (root / "home.html").write_text("<title>Home</title><h1>Feed</h1>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def isolate_from_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test pick up the developer's real .env values.

    Settings.load() reads the project's .env for any variable missing from the environment.
    Setting these to an empty string (present, so dotenv will not override them) keeps tests
    away from the real hosted database and the real Instagram credentials.
    """
    for name in ("WA_DATABASE_URL", "WA_IG_USERNAME", "WA_IG_PASSWORD", "WA_EXPECTED_ACCOUNT"):
        monkeypatch.setenv(name, "")
