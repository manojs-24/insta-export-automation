"""Local export-list site for the download tests (kept out of conftest.py to stay readable)."""

import functools
import http.server
import io
import threading
import zipfile
from collections.abc import Iterator

import pytest

FOLLOWERS = "connections/followers_and_following/followers_1.json"


def zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


ROW = (
    "example_user (Instagram) Contacts, Followers and following Export to device · Once · {fmt} "
    "Expires on {expires} · 1 MB"
)

DOWNLOADS_PAGE = f"""<!doctype html><title>Exports</title>
<div role="dialog" aria-label="Export your information"><h2>Export your information</h2>
<ul>
 <li>{ROW.format(fmt="JSON", expires="12/11/2026")} <a href="/old.zip">Download</a><button>Cancel</button></li>
 <li>{ROW.format(fmt="JSON", expires="14/11/2026")}
     <button onclick="location.href='/new.zip'">Download</button><button>Cancel</button></li>
 <li>{ROW.format(fmt="HTML", expires="15/11/2026")} <a href="/html.zip">Download</a><button>Cancel</button></li>
 <li>example_user (Instagram) Contacts, Followers and following Export to device · Once · JSON
     Requested on 11/11/2026 <button>Cancel</button></li>
</ul></div>"""

NOTHING_READY_PAGE = """<!doctype html><title>Exports</title>
<div role="dialog" aria-label="Export your information"><h2>Requested</h2>
<ul><li>example_user (Instagram) Contacts, Followers and following Export to device · Once · JSON
 Requested on 11/11/2026 <button>Cancel</button></li></ul></div>"""


# Mirrors Instagram: row Download -> "Download your files" popup (own Download button)
# -> "re-enter your password" prompt (Continue stays disabled until typed) -> file download.
FLOW_PAGE = """<!doctype html><title>Exports</title>
<div role="dialog" aria-label="Export your information"><h2>Export your information</h2>
<ul>
 <li>example_user (Instagram) Contacts, Followers and following Export to device Once JSON
     Expires on 14/11/2026 <button id="row" onclick="show('files')">Download</button><button>Cancel</button></li>
</ul></div>
<div id="files" role="dialog" aria-label="files" style="display:none">
  <h2>Download your files</h2><p>File 1 of 1 - Less than 1 MB</p>
  <button id="popup" onclick="show('pw')">Download</button></div>
<div id="pw" role="dialog" aria-label="pw" style="display:none">
  <h2>For your security, please re-enter your password</h2>
  <label for="p">Password</label><input id="p" type="password">
  <button id="go" disabled>Continue</button></div>
<script>
function show(id){document.getElementById(id).style.display='block'}
const p=document.getElementById('p'), go=document.getElementById('go');
p.addEventListener('input',()=>{go.disabled=p.value.length===0});
go.addEventListener('click',()=>{ if(p.value==='right-pass') location.href='/new.zip'; });
</script>"""

ALREADY_DOWNLOADS = """<!doctype html><title>Exports</title>
<div role="dialog" aria-label="Export your information"><h2>Export your information</h2>
<ul><li>example_user (Instagram) Contacts, Followers and following Export to device Once JSON
 Expires on 14/11/2026 <button onclick="location.href='/new.zip'">Download</button></li></ul></div>"""

TWO_FILES = FLOW_PAGE.replace(
    '<button id="popup" onclick="show(&#39;pw&#39;)">Download</button>'.replace("&#39;", "'"),
    '<button>Download</button><button>Download</button>',
)


@pytest.fixture(scope="session")
def downloads_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Export list with several rows and downloadable zips (old/new JSON, an HTML export)."""
    root = tmp_path_factory.mktemp("downloads_site")
    (root / "index.html").write_text(DOWNLOADS_PAGE, encoding="utf-8")
    (root / "empty.html").write_text(NOTHING_READY_PAGE, encoding="utf-8")
    (root / "flow.html").write_text(FLOW_PAGE, encoding="utf-8")
    (root / "direct.html").write_text(ALREADY_DOWNLOADS, encoding="utf-8")
    (root / "twofiles.html").write_text(TWO_FILES, encoding="utf-8")
    (root / "old.zip").write_bytes(zip_bytes({FOLLOWERS: '[{"version": "old"}]'}))
    (root / "new.zip").write_bytes(zip_bytes({FOLLOWERS: '[{"version": "new"}]', "readme.txt": "x"}))
    (root / "html.zip").write_bytes(
        zip_bytes({"connections/followers_and_following/followers_1.html": "<p>"})
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
