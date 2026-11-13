"use strict";

// Two ways in. With a user name and password set in .env the page shows a sign-in form and the
// browser keeps an HttpOnly cookie. Without them, the link printed in the terminal carries a
// one-time token in its #fragment (never sent to the server in a URL or logs).
const params = new URLSearchParams(location.hash.slice(1));
let TOKEN = params.get("token") || sessionStorage.getItem("wa_token") || "";
if (params.get("token")) {
  sessionStorage.setItem("wa_token", TOKEN);
  history.replaceState(null, "", location.pathname); // remove the token from the address bar
}
let LOGIN_MODE = false;

const PAGE_SIZE = 50;
const LOADING = "Loading…";
const state = { account: "", pages: { followers: 0, recent: 0, unfollowed: 0, runs: 0, logs: 0 }, openRun: null };
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------- loading indicators
// `pending` counts requests the person is waiting for. Background polling passes
// { quiet: true } so the progress bar does not flash every second and a half.
let pending = 0;
function trackBusy(delta) {
  pending = Math.max(0, pending + delta);
  $("loadbar").hidden = pending === 0;
  document.querySelector("main").setAttribute("aria-busy", String(pending > 0));
}
// Each section counts its own requests: a slow, older response must never overwrite a newer one.
const latest = {};
const nextRequest = (name) => (latest[name] = (latest[name] || 0) + 1);

// Everything shown is inserted as text, never as HTML.
function cell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  if (cls) td.className = cls;
  return td;
}
function messageRow(bodyId, columns, text, cls, spinner) {
  const body = $(bodyId);
  body.replaceChildren();
  const tr = document.createElement("tr");
  const td = cell(text, cls);
  td.colSpan = columns;
  if (spinner) {
    const s = document.createElement("span");
    s.className = "spinner";
    s.setAttribute("aria-hidden", "true");
    td.prepend(s);
  }
  tr.append(td);
  body.append(tr);
}
function fill(tbodyId, rows, columns, emptyText) {
  if (!rows.length) return messageRow(tbodyId, columns, emptyText || "Nothing to show yet.", "muted");
  const body = $(tbodyId);
  body.replaceChildren();
  for (const cells of rows) {
    const tr = document.createElement("tr");
    for (const c of cells) tr.append(c instanceof Node ? c : cell(c));
    body.append(tr);
  }
}
const when = (v) => (v ? new Date(v).toLocaleString() : "");
const TRIGGER_LABEL = { manual: "Manual", scheduled: "Automatic" };
const triggerLabel = (t) => TRIGGER_LABEL[t] || "";
const triggerCell = (t) => cell(triggerLabel(t), t === "scheduled" ? "warn" : "");
const yes = (v) => cell(v ? "yes" : "no", v ? "ok" : "muted");
function statusCell(s) {
  const cls = { success: "ok", ok: "ok", failed: "bad", error: "bad", stopped: "warn", skipped: "muted" }[s] || "";
  return cell(s, cls);
}

async function api(path, options = {}) {
  const { quiet = false, ...fetchOptions } = options;
  if (!quiet) trackBusy(1);
  try {
    const response = await fetch(path, {
      ...fetchOptions,
      headers: { "X-Access-Token": TOKEN, "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
    });
    if (response.status === 401) {
      if (LOGIN_MODE) showLogin("Your session ended. Please sign in again.");
      else showBanner("The access link is missing or old. Use the link printed in the terminal when the UI started.");
      throw new Error("unauthorised");
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
    return body;
  } finally {
    if (!quiet) trackBusy(-1);
  }
}
let appStarted = false;
let timers = [];
function showLogin(message) {
  $("login").hidden = false;
  $("app").hidden = true;
  $("logout").hidden = true;
  $("login-error").hidden = !message;
  $("login-error").textContent = message || "";
  $("login-password").value = "";
  timers.forEach(clearInterval);  // stop background refreshing while signed out
  timers = [];
  appStarted = false;
  setTimeout(() => $("login-user").focus(), 0);
}
function showBanner(text) {
  $("banner").hidden = !text;
  $("banner").textContent = text || "";
}
const query = (obj) => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== "" && v != null)).toString();
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ------------------------------------------------------------------ tabs / paging
function showTab(name) {
  for (const b of document.querySelectorAll("nav button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== `tab-${name}`;
  load(name);
}
function setPagerBusy(name, busy) {
  for (const b of document.querySelectorAll(`[data-pager="${name}"]`)) b.disabled = busy;
}
function pager(name, total) {
  const page = state.pages[name];
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  $(`${name}-page`).textContent = `Page ${page + 1} of ${pages} (${total} in total)`;
  for (const b of document.querySelectorAll(`[data-pager="${name}"]`)) {
    b.disabled = Number(b.dataset.dir) < 0 ? page === 0 : (page + 1) * PAGE_SIZE >= total;
  }
}
function sinceFor(selectId) {
  const days = Number($(selectId).value);
  return days ? new Date(Date.now() - days * 86400000).toISOString() : "";
}

function runButtons(r) {
  const td = document.createElement("td");
  const btn = document.createElement("button");
  btn.textContent = "Details";
  btn.addEventListener("click", () => openRun(r));
  td.append(btn);
  return td;
}
const eventRow = (r) => [r.username, when(r.happened_at), r.run_id ? `#${r.run_id}` : ""];
const SECTIONS = {
  followers: {
    body: "followers-body", cols: 3, needsAccount: true,
    path: () => "/api/followers?" + query({ account: state.account, search: $("followers-search").value, limit: PAGE_SIZE, offset: state.pages.followers * PAGE_SIZE }),
    row: (r) => [r.username, when(r.added_at), when(r.followed_at)],
  },
  recent: {
    body: "recent-body", cols: 3, needsAccount: true,
    path: () => "/api/follower-events?" + query({ account: state.account, event: "added", since: sinceFor("recent-days"), limit: PAGE_SIZE, offset: state.pages.recent * PAGE_SIZE }),
    row: eventRow,
  },
  unfollowed: {
    body: "unfollowed-body", cols: 3, needsAccount: true,
    path: () => "/api/follower-events?" + query({ account: state.account, event: "removed", since: sinceFor("unfollowed-days"), limit: PAGE_SIZE, offset: state.pages.unfollowed * PAGE_SIZE }),
    row: eventRow,
  },
  runs: {
    body: "runs-body", cols: 9,
    path: () => "/api/runs?" + query({ limit: PAGE_SIZE, offset: state.pages.runs * PAGE_SIZE }),
    row: (r) => [r.id, when(r.started_at), triggerCell(r.trigger), statusCell(r.status), yes(r.export_request_submitted), yes(r.export_request_acknowledged), yes(r.export_file_downloaded), cell(r.stop_reason || r.message || "", "msg"), runButtons(r)],
  },
  logs: {
    body: "logs-body", cols: 4,
    path: () => "/api/logs?" + query({ run_id: $("logs-run").value, level: $("logs-level").value, search: $("logs-search").value, limit: PAGE_SIZE, offset: state.pages.logs * PAGE_SIZE }),
    row: (r) => [when(r.logged_at), r.run_id ?? "", cell(r.level, { ERROR: "bad", WARNING: "warn" }[r.level] || ""), cell(r.message, "msg")],
  },
};

async function load(name) {
  showBanner("");
  if (name === "dashboard") return loadDashboard();
  const section = SECTIONS[name];
  const mine = nextRequest(name);
  if (section.needsAccount && !state.account) {
    messageRow(section.body, section.cols, "Set WA_EXPECTED_ACCOUNT in .env to see this.", "muted");
    return;
  }
  setPagerBusy(name, true);
  messageRow(section.body, section.cols, LOADING, "muted", true);
  try {
    const d = await api(section.path());
    if (mine !== latest[name]) return; // a newer request took over
    fill(section.body, d.items.map(section.row), section.cols);
    pager(name, d.total);
  } catch (e) {
    if (mine !== latest[name]) return;
    setPagerBusy(name, false); // let the person try again
    if (e.message === "unauthorised") return messageRow(section.body, section.cols, "Not authorised.", "bad");
    messageRow(section.body, section.cols, `Could not load: ${e.message}`, "bad");
    showBanner(e.message);
  }
}

const SUMMARY_CELLS = ["s-followers", "s-added", "s-removed", "s-removed-all", "s-last"];
function setSummary(values, cls) {
  SUMMARY_CELLS.forEach((id, i) => { $(id).textContent = values[i]; $(id).className = cls || ""; });
}
async function loadDashboard({ quiet = false } = {}) {
  const mine = nextRequest("dashboard");
  if (!state.account) { setSummary(Array(5).fill("–")); return; }
  if (!quiet) setSummary(Array(5).fill(LOADING), "muted"); // background refreshes keep the old numbers
  try {
    const s = await api("/api/summary?" + query({ account: state.account }), { quiet });
    if (mine !== latest.dashboard) return;
    setSummary([
      s.followers, s.added_last_7_days, s.unfollowed_last_7_days, s.unfollowed_total,
      s.last_run ? `#${s.last_run.id} – ${triggerLabel(s.last_run.trigger)} – ${s.last_run.status} – ${when(s.last_run.started_at)}` : "no runs yet",
    ]);
  } catch (e) {
    if (mine !== latest.dashboard || quiet) return;
    setSummary(Array(5).fill("–"));
    if (e.message !== "unauthorised") showBanner(e.message);
  }
}

async function openRun(run) {
  state.openRun = run.id;
  $("run-detail").hidden = false;
  $("run-detail-title").textContent = `Run #${run.id} – ${triggerLabel(run.trigger)} – ${run.status}`;
  messageRow("steps-body", 7, "Loading steps…", "muted", true);
  try {
    const steps = await api(`/api/runs/${run.id}/steps`);
    if (state.openRun !== run.id) return;
    fill("steps-body", steps.map((s) => [s.position, s.step_id, s.action, statusCell(s.status), s.duration_ms, s.attempts, cell(s.message || "", "msg")]), 7);
  } catch (e) {
    if (state.openRun !== run.id) return;
    messageRow("steps-body", 7, `Could not load: ${e.message}`, "bad");
  }
}

// ------------------------------------------------------------ run + the code box
let lastQuestionId = null;
async function refreshRun() {
  let r;
  try { r = await api("/api/run", { quiet: true }); } catch { return; }
  $("run-status").textContent = r.status;
  $("run-status").className = { success: "ok", failed: "bad", error: "bad", stopped: "warn" }[r.status] || "";
  $("run-message").textContent = r.message || "";
  $("run-trigger").textContent = r.status === "idle" ? "" : `(${triggerLabel(r.trigger).toLowerCase()} run)`;
  const running = r.status === "running";
  $("run-start").disabled = running;
  $("run-cancel").hidden = !running;
  const events = $("run-events");
  events.replaceChildren(...r.events.map((e) => {
    const li = document.createElement("li");
    li.textContent = `${new Date(e.at).toLocaleTimeString()}  ${e.text}`;
    return li;
  }));
  events.scrollTop = events.scrollHeight;

  const q = r.question;
  $("question").hidden = !q;
  if (q) {
    const secret = q.kind === "secret";
    $("question-title").textContent = secret ? "Verification code needed" : "Action needed";
    $("question-prompt").textContent = secret
      ? "Instagram sent a code (for example to WhatsApp). Type it here; it is passed to the login page and never stored."
      : q.prompt;
    $("question-input").hidden = !secret;
    $("question-no").hidden = q.kind !== "confirm";
    $("question-submit").textContent = secret ? "Submit code" : q.kind === "confirm" ? "Yes" : "Continue";
    if (q.id !== lastQuestionId) {
      lastQuestionId = q.id;
      $("question-input").value = "";
      if (secret) $("question-input").focus();
    }
  } else {
    lastQuestionId = null;
    $("question-input").value = "";
  }
}

async function answer(value) {
  const submit = $("question-submit");
  const label = submit.textContent;
  submit.disabled = true;
  submit.textContent = "Sending…";
  try {
    const q = (await api("/api/run", { quiet: true })).question;
    if (!q) return;
    await api("/api/run/answer", { method: "POST", body: JSON.stringify({ question_id: q.id, value }) });
    $("question-input").value = "";
  } catch (e) {
    showBanner(e.message);
  } finally {
    submit.disabled = false;
    submit.textContent = label;
  }
  refreshRun();
}

$("question-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const secret = !$("question-input").hidden;
  if (secret && !$("question-input").value.trim()) return;
  answer(secret ? $("question-input").value : true);
});
$("question-no").addEventListener("click", () => answer(false));
$("run-start").addEventListener("click", async () => {
  const button = $("run-start");
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    await api("/api/run", { method: "POST", body: JSON.stringify({ config: $("config").value }) });
    showBanner("");
  } catch (e) { showBanner(e.message); }
  button.textContent = "Run now";
  refreshRun(); // re-enables the button unless a run is in progress
});
$("run-cancel").addEventListener("click", async () => {
  try { await api("/api/run/cancel", { method: "POST" }); } catch (e) { showBanner(e.message); }
  refreshRun();
});

// ------------------------------------------------------------------------- wiring
for (const b of document.querySelectorAll("nav button")) b.addEventListener("click", () => showTab(b.dataset.tab));
for (const b of document.querySelectorAll("[data-pager]")) {
  b.addEventListener("click", () => {
    const name = b.dataset.pager;
    state.pages[name] = Math.max(0, state.pages[name] + Number(b.dataset.dir));
    load(name);
  });
}
function resetAndLoad(name) { state.pages[name] = 0; load(name); }
$("followers-search").addEventListener("input", debounce(() => resetAndLoad("followers"), 300));
$("recent-days").addEventListener("change", () => resetAndLoad("recent"));
$("unfollowed-days").addEventListener("change", () => resetAndLoad("unfollowed"));
$("logs-apply").addEventListener("click", () => resetAndLoad("logs"));
$("run-detail-logs").addEventListener("click", () => {
  $("logs-run").value = state.openRun;
  showTab("logs");
});

function showSchedule(s) {
  const line = $("schedule-line");
  line.hidden = false;
  if (!s || !s.enabled) {
    line.textContent = "Automatic runs: off. Set WA_SCHEDULE_MINUTES in .env and restart the UI to turn them on.";
    return;
  }
  const next = s.next_run ? new Date(s.next_run).toLocaleTimeString() : "–";
  line.textContent = `Automatic runs: every ${s.minutes} minute(s), next at ${next}. They run only while this UI stays open.`
    + (s.last_tick_result ? ` Last automatic attempt: ${s.last_tick_result}.` : "");
}

async function startApp() {
  if (appStarted) return;
  appStarted = true;
  $("login").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = !LOGIN_MODE;
  setSummary(Array(5).fill(LOADING), "muted");
  try {
    const info = await api("/api/info");
    state.account = info.account;
    $("account").textContent = info.account ? `@${info.account}` : "";
    $("config").replaceChildren(...info.configs.map((c) => Object.assign(document.createElement("option"), { value: c, textContent: c })));
    if (info.configs.includes("instagram_export.json")) $("config").value = "instagram_export.json";
    showSchedule(info.schedule);
    if (!info.database_configured) showBanner("WA_DATABASE_URL is not set in .env, so followers, runs and logs cannot be shown.");
  } catch { setSummary(Array(5).fill("–")); appStarted = false; return; }
  showTab("dashboard");
  refreshRun();
  timers.push(setInterval(refreshRun, 1500));
  timers.push(setInterval(() => api("/api/schedule", { quiet: true }).then(showSchedule).catch(() => {}), 30000));
  timers.push(setInterval(() => { if (!$("tab-dashboard").hidden) loadDashboard({ quiet: true }); }, 15000));
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const button = $("login-submit");
  button.disabled = true;
  button.textContent = "Signing in…";
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("login-user").value, password: $("login-password").value }),
    });
    if (response.ok) { $("login-password").value = ""; $("login-error").hidden = true; await startApp(); return; }
    const body = await response.json().catch(() => ({}));
    $("login-error").hidden = false;
    $("login-error").textContent = typeof body.detail === "string" ? body.detail : "Could not sign in.";
    $("login-password").value = "";
  } catch {
    $("login-error").hidden = false;
    $("login-error").textContent = "Could not reach the server.";
  } finally {
    button.disabled = false;
    button.textContent = "Sign in";
  }
});

$("logout").addEventListener("click", async () => {
  try { await fetch("/auth/logout", { method: "POST" }); } catch { /* signing out locally anyway */ }
  showLogin("");
});

(async function init() {
  let mode = { login_required: false, signed_in: false };
  try { mode = await (await fetch("/auth/mode")).json(); } catch { /* fall through to the token path */ }
  LOGIN_MODE = !!mode.login_required;
  if (LOGIN_MODE) {
    if (mode.signed_in) return startApp();
    return showLogin("");
  }
  if (!TOKEN) { showBanner("Open the link printed in the terminal when the UI started (it contains the access token)."); return; }
  startApp();
})();
