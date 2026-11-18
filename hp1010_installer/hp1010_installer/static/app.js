"use strict";

// The UI is served under the Home Assistant ingress prefix, so all requests
// must be relative to the current directory (never absolute "/api/...").
const API_BASE = (() => {
  const p = location.pathname;
  const dir = p.slice(0, p.lastIndexOf("/") + 1);
  return dir.endsWith("/") ? dir : dir + "/";
})();

async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(API_BASE + path.replace(/^\/+/, ""), opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

const $ = (id) => document.getElementById(id);
const FIELDS = ["printer", "wifi_ssid", "wifi_password", "ap_ssid", "ap_password", "api_key", "device_port"];

// Fallback-AP names the app treats as defaults; when the printer selection
// changes, an untouched default follows the newly selected printer.
let apDefaults = { hp1010: "HP1010 Print Fallback", dymo: "DYMO Print Fallback" };

function formValues() {
  const out = {};
  for (const f of FIELDS) out[f] = $(f).value;
  return out;
}

function fillForm(s) {
  for (const f of FIELDS) if (f !== "device_port" && s[f] !== undefined) $(f).value = s[f];
}

function renderPorts(ports, selected) {
  const sel = $("device_port");
  const current = selected || sel.value;
  sel.innerHTML = "";
  if (!ports.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "(no serial device found — plug in the board)";
    sel.appendChild(o);
    return;
  }
  for (const p of ports) {
    const o = document.createElement("option");
    o.value = p;
    o.textContent = p;
    if (p === current) o.selected = true;
    sel.appendChild(o);
  }
}

let lastSeq = 0;
let logAutoScroll = true;

function appendLog(lines) {
  if (!lines.length) return;
  const el = $("log");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  for (const ln of lines) {
    const div = document.createElement("div");
    div.className = "logline";
    div.innerHTML = `<span class="ts">${ln.t}</span> ${escapeHtml(ln.text)}`;
    el.appendChild(div);
  }
  while (el.childElementCount > 3000) el.removeChild(el.firstChild);
  if (atBottom || logAutoScroll) el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

let jobRunning = false;

function renderJob(job) {
  jobRunning = !!job.running;
  $("cancel").hidden = !jobRunning;
  for (const id of ["build", "flash", "quickflash", "save"]) $(id).disabled = jobRunning;
  const msg = $("job-msg");
  if (jobRunning) {
    msg.textContent = `${job.kind} running…`;
  } else if (job.kind) {
    msg.textContent = `${job.kind} ${job.rc === 0 ? "succeeded" : `failed (exit ${job.rc})`}`;
  } else {
    msg.textContent = "";
  }
}

async function refreshStatus() {
  try {
    const s = await api("api/status");
    if (s.printers) {
      apDefaults = Object.fromEntries(Object.entries(s.printers).map(([k, v]) => [k, v.ap_default]));
    }
    $("app-version").textContent = "v" + s.app_version;
    const label = (s.printers && s.printers[s.printer] && s.printers[s.printer].label) || s.printer;
    $("versions").textContent = `${label} · ESPHome ${s.esphome_version} · ` +
      (s.compiled ? "firmware built" : "no build yet") +
      (s.prebuilt ? " · prebuilt image available" : "");
    renderPorts(s.ports, s.settings.device_port);
    renderJob(s.job);
  } catch (e) {
    $("versions").textContent = "status error: " + e.message;
  }
}

async function pollLog() {
  try {
    const data = await api(`api/log?since=${lastSeq}`);
    appendLog(data.lines);
    lastSeq = data.last;
    renderJob(data.job);
  } catch (e) {
    /* transient during restart */
  }
}

async function action(id, path, extra) {
  const msg = $(id + "-msg") || $("job-msg");
  const setMsg = (t) => { if (msg) msg.textContent = t; };
  setMsg("working…");
  try {
    const body = Object.assign(formValues(), extra || {});
    // Flashing needs a serial port; fail fast with a clear message instead of
    // letting the backend reject it (or silently doing nothing).
    if ((path === "api/flash" || path === "api/quickflash") && !body.device_port) {
      setMsg("no serial port selected — plug in the board and press Refresh");
      return;
    }
    const r = await api(path, "POST", body);
    setMsg(r.ok ? "started — see the log below" : (r.error || "failed"));
    await refreshStatus();
  } catch (e) {
    setMsg(e.message);
  }
}

function wire() {
  $("printer").onchange = () => {
    const current = $("ap_ssid").value;
    if (!current || Object.values(apDefaults).includes(current)) {
      $("ap_ssid").value = apDefaults[$("printer").value] || current;
    }
  };
  $("save").onclick = async () => {
    const msg = $("save-msg");
    msg.textContent = "saving…";
    try {
      await api("api/settings", "POST", formValues());
      msg.textContent = "saved";
    } catch (e) {
      msg.textContent = e.message;
    }
  };
  $("gen-key").onclick = async () => {
    try {
      const r = await api("api/key", "POST", {});
      $("api_key").value = r.api_key;
    } catch (e) {
      $("save-msg").textContent = e.message;
    }
  };
  $("refresh-ports").onclick = refreshStatus;
  $("build").onclick = () => action("build", "api/build");
  $("flash").onclick = () => action("flash", "api/flash");
  $("quickflash").onclick = () => action("quickflash", "api/quickflash");
  $("cancel").onclick = async () => {
    try { await api("api/cancel", "POST", {}); } catch (e) { /* ignore */ }
  };
  $("clear-log").onclick = () => {
    $("log").innerHTML = "";
    logAutoScroll = true;
  };
}

async function main() {
  wire();
  try {
    fillForm(await api("api/settings"));
  } catch (e) { /* ignore */ }
  await refreshStatus();
  await pollLog();
  setInterval(pollLog, 1000);
  setInterval(refreshStatus, 5000);
}

main();
