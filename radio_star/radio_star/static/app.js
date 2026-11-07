/* Radio-Star frontend */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let state = {
  tab: "timeline",
  status: null,
  timers: [],
  automations: [],
  scripts: [],
  currentScript: null,
  mp3s: null,
  voices: [],
  players: [],
};

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 3500);
}

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

/* ---------------- clock ---------------- */
function tickClock() {
  const el = $("#clock");
  if (!el) return;
  const d = new Date();
  el.textContent = d.toLocaleTimeString("de-AT", { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

/* ---------------- status ---------------- */
async function refreshStatus() {
  try {
    state.status = await api("/api/status");
    const ha = state.status.ha || {};
    $("#ha-status").className = "dot " + (ha.ok ? "dot-on" : "dot-off");
    $("#ha-status").title = ha.ok ? `HA ${ha.version} · ${ha.location || ""}` : (ha.error || "HA offline");
    $("#ha-version").textContent = ha.ok ? `HA ${ha.version}` : "";
  } catch (e) {
    $("#ha-status").className = "dot dot-off";
    $("#ha-status").title = "Backend offline: " + e.message;
  }
}

/* ---------------- tabs ---------------- */
function setTab(name) {
  state.tab = name;
  location.hash = "/" + name;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  render();
}
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) setTab(b.dataset.tab);
});
window.addEventListener("hashchange", () => {
  const h = location.hash.replace(/^\/+/, "");
  if (h && ["timeline", "timers", "automations", "scripts", "mp3", "tts"].includes(h)) setTab(h);
});

async function render() {
  const t = state.tab;
  try {
    if (t === "timeline") return await renderTimeline();
    if (t === "timers") return await renderTimers();
    if (t === "automations") return await renderAutomations();
    if (t === "scripts") return await renderScripts();
    if (t === "mp3") return await renderMp3();
    if (t === "tts") return await renderTts();
  } catch (e) {
    $("#tab-" + t).innerHTML = `<div class="card">Fehler: ${esc(e.message)}</div>`;
  }
}

const CAT_LABEL = { news: "News", weather: "Wetter", titel: "Titel", announce: "Ansage", stream: "Stream", system: "System", custom: "Custom" };
const chip = (c) => `<span class="chip ${esc(c || "custom")}">${esc(CAT_LABEL[c] || c || "custom")}</span>`;

function fmtCountdown(sec) {
  if (sec < 60) return `in ${sec} s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `in ${m} min`;
  return `in ${Math.floor(m / 60)} h ${m % 60} min`;
}
function fmtAge(iso) {
  if (!iso) return "noch nie";
  const d = new Date(iso);
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 90) return `vor ${Math.round(sec)} s`;
  if (sec < 5400) return `vor ${Math.round(sec / 60)} min`;
  if (sec < 172800) return `vor ${(sec / 3600).toFixed(1)} h`;
  return d.toLocaleString("de-AT");
}
function fmtSize(b) {
  if (b == null) return "";
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}

/* ================= TIMELINE ================= */
async function renderTimeline() {
  if (!state.status) await refreshStatus();
  const sec = $("#tab-timeline");
  const tl = await api("/api/timeline?minutes=60");
  const st = state.status || {};
  const pl = st.player || {};
  const badge = pl.state === "playing" ? "playing" : pl.state === "paused" ? "other" : "idle";
  const pos = pl.position != null ? `${Math.floor(pl.position / 60)}:${String(pl.position % 60).padStart(2, "0")}` : "";

  const title = pl.title || (pl.url ? decodeURIComponent(pl.url.split("/").pop()) : "—");
  let html = `
  <div class="card">
    <div class="player">
      <span class="badge ${badge}">${esc(pl.state || "unbekannt")}</span>
      <div style="flex:1">
        <div class="big">${esc(title)}</div>
        <div class="sub">${esc(pl.url || "")} ${pos ? "· " + pos : ""}</div>
      </div>
    </div>
  </div>`;

  if (!tl.events.length) {
    html += `<div class="card"><div class="empty">Keine aktiven Timer in den nächsten 60 Minuten.</div></div>`;
  } else {
    html += `<div class="card"><h3>Nächste Stunde</h3>`;
    tl.events.forEach((e, i) => {
      const aState = e.automation_state === "on"
        ? `<span class="stat-on">● ${esc(e.automation_id)}</span> (letzt. ${fmtAge(e.automation_last_triggered)})`
        : e.automation_state
          ? `<span class="stat-off">○ ${esc(e.automation_state)} · ${esc(e.automation_id)}</span>`
          : (e.automation_id ? esc(e.automation_id) : "");
      html += `
      <div class="event ${i === 0 ? "next" : ""}">
        <div><div class="time">${esc(e.time_hm)}</div><div class="countdown">${fmtCountdown(e.countdown_sec)}</div></div>
        <div class="body">
          <div class="name">${esc(e.name)}${chip(e.category)}</div>
          ${e.description ? `<div class="desc">${esc(e.description)}</div>` : ""}
          <div class="meta">${aState}${e.shell_command ? " · <span class='mono'>" + esc(e.shell_command) + "</span>" : ""}</div>
        </div>
      </div>`;
    });
    html += `</div>`;
  }
  sec.innerHTML = html;
}
setInterval(() => { if (state.tab === "timeline") renderTimeline().catch(() => {}); }, 15000);

/* ================= TIMERS ================= */
function schedText(s) {
  if (!s) return "";
  if (s.kind === "minutes") {
    const ms = (Array.isArray(s.minutes) ? s.minutes : [s.minutes]).map((m) => ":" + String(m).padStart(2, "0")).join(", ");
    return `stündlich ${ms}`;
  }
  if (s.kind === "at") return `täglich ${s.at}`;
  if (s.kind === "interval") return `alle ${s.minutes} min`;
  return "";
}

async function renderTimers() {
  const sec = $("#tab-timers");
  const d = await api("/api/timers");
  state.timers = d.timers;
  let rows = state.timers.map((t) => `
    <tr>
      <td class="mono" style="white-space:nowrap">${esc(schedText(t.schedule))}</td>
      <td><b>${esc(t.name)}</b>${chip(t.category)}<div class="muted" style="font-size:11.5px">${esc(t.description || "")}</div></td>
      <td class="mono" style="font-size:12px">${esc(t.automation_id || "")}</td>
      <td class="mono" style="font-size:12px">${esc(t.shell_command || "")}</td>
      <td><label class="switch"><input type="checkbox" data-act="toggle" data-id="${esc(t.id)}" ${t.enabled ? "checked" : ""}><span class="sl"></span></label></td>
      <td style="white-space:nowrap">
        <button class="small" data-act="edit" data-id="${esc(t.id)}">Bearbeiten</button>
        <button class="small danger" data-act="del" data-id="${esc(t.id)}">Löschen</button>
      </td>
    </tr>`).join("");

  sec.innerHTML = `
  <div class="section-head">
    <h2>Timer (${state.timers.length})</h2>
    <div>
      <button class="small" data-act="reset">Zurücksetzen</button>
      <button class="small primary" data-act="new">+ Neuer Timer</button>
    </div>
  </div>
  <div class="card"><table>
    <thead><tr><th>Zeitplan</th><th>Timer</th><th>Automation</th><th>Shell-Command</th><th>Aktiv</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>
  <div class="card" id="timer-form" style="display:none"><h3 id="timer-form-title">Neuer Timer</h3>
    ${timerForm()}
  </div>`;
}

function timerForm(t) {
  t = t || {};
  const s = t.schedule || { kind: "minutes", minutes: [] };
  const mins = (Array.isArray(s.minutes) ? s.minutes : [s.minutes || ""]).join(",");
  return `
  <div class="formrow">
    <div style="flex:2"><label>Name</label><input id="tf-name" value="${esc(t.name || "")}" placeholder="z.B. Wetter TTS"></div>
    <div><label>Kategorie</label><select id="tf-cat">${Object.entries(CAT_LABEL).map(([k, v]) => `<option value="${k}" ${t.category === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div><label>Typ</label><select id="tf-kind">
      <option value="minutes" ${s.kind === "minutes" ? "selected" : ""}>Stündliche Minute(n)</option>
      <option value="at" ${s.kind === "at" ? "selected" : ""}>Täglich um Uhrzeit</option>
      <option value="interval" ${s.kind === "interval" ? "selected" : ""}>Intervall (Minuten)</option>
    </select></div>
    <div id="tf-mins-wrap"><label>Minuten (kommagetrennt, z.B. 25, 55)</label><input id="tf-mins" value="${esc(mins)}"></div>
    <div id="tf-at-wrap" style="display:none"><label>Uhrzeit (HH:MM)</label><input id="tf-at" value="${esc(s.at || "07:00")}" placeholder="23:55"></div>
    <div id="tf-int-wrap" style="display:none"><label>Jede n. Minute</label><input id="tf-int" type="number" min="1" value="${esc(s.minutes && s.kind === "interval" ? s.minutes : 5)}"></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div style="flex:2"><label>Automation-ID (optional, verknüpft)</label><input id="tf-auto" class="mono" value="${esc(t.automation_id || "")}" placeholder="sonos_fm_news"></div>
    <div style="flex:2"><label>Shell-Command (optional)</label><input id="tf-shell" class="mono" value="${esc(t.shell_command || "")}" placeholder="sonos_fm_news"></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div style="flex:3"><label>Beschreibung</label><input id="tf-desc" value="${esc(t.description || "")}"></div>
    <div><label></label><label style="display:flex;align-items:center;gap:8px;height:34px"><input type="checkbox" id="tf-enabled" style="width:auto" ${t.enabled !== false ? "checked" : ""}> Aktiv</label></div>
  </div>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="primary" data-act="save-timer" data-id="${esc(t.id || "")}">Speichern</button>
    <button data-act="cancel-timer">Abbrechen</button>
  </div>`;
}

$("#tab-timers").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const act = b.dataset.act;
  if (act === "new") {
    const f = $("#timer-form");
    f.style.display = "";
    $("#timer-form").innerHTML = `<h3>Neuer Timer</h3>` + timerForm();
  }
  if (act === "edit") {
    const t = state.timers.find((x) => x.id === b.dataset.id);
    if (!t) return;
    const f = $("#timer-form");
    f.style.display = "";
    $("#timer-form").innerHTML = `<h3>Timer bearbeiten: ${esc(t.name)}</h3>` + timerForm(t);
  }
  if (act === "cancel-timer") $("#timer-form").style.display = "none";
  if (act === "save-timer") saveTimer(b.dataset.id);
  if (act === "del") delTimer(b.dataset.id);
  if (act === "reset") resetTimers();
});
$("#tab-timers").addEventListener("change", (e) => {
  if (e.target.id === "tf-kind") {
    const k = e.target.value;
    $("#tf-mins-wrap").style.display = k === "minutes" ? "" : "none";
    $("#tf-at-wrap").style.display = k === "at" ? "" : "none";
    $("#tf-int-wrap").style.display = k === "interval" ? "" : "none";
  }
  if (e.target.dataset.act === "toggle") {
    const t = state.timers.find((x) => x.id === e.target.dataset.id);
    if (t) {
      t.enabled = e.target.checked;
      api(`/api/timers/${t.id}`, "PUT", t).then(renderTimers).catch((err) => toast(err.message, true));
    }
  }
});

async function saveTimer(id) {
  const kind = $("#tf-kind").value;
  const schedule =
    kind === "minutes"
      ? { kind, minutes: $("#tf-mins").value.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n) && n >= 0 && n < 60) }
      : kind === "at"
        ? { kind, at: $("#tf-at").value }
        : { kind, minutes: parseInt($("#tf-int").value, 10) };
  const t = {
    id: id || undefined,
    name: $("#tf-name").value.trim(),
    category: $("#tf-cat").value,
    schedule,
    automation_id: $("#tf-auto").value.trim() || null,
    shell_command: $("#tf-shell").value.trim() || null,
    description: $("#tf-desc").value.trim(),
    enabled: $("#tf-enabled").checked,
  };
  try {
    if (id) await api(`/api/timers/${id}`, "PUT", t);
    else await api("/api/timers", "POST", t);
    toast("Timer gespeichert");
    renderTimers();
  } catch (e) { toast(e.message, true); }
}
async function delTimer(id) {
  if (!confirm(`Timer "${id}" löschen?`)) return;
  try { await api(`/api/timers/${id}`, "DELETE"); toast("Timer gelöscht"); renderTimers(); }
  catch (e) { toast(e.message, true); }
}
async function resetTimers() {
  if (!confirm("Timer-Liste auf Werkseinstellungen zurücksetzen?")) return;
  try { await api("/api/timers/reset", "POST"); toast("Zurückgesetzt"); renderTimers(); }
  catch (e) { toast(e.message, true); }
}

/* ================= AUTOMATIONS ================= */
async function renderAutomations() {
  const sec = $("#tab-automations");
  const d = await api("/api/automations");
  state.automations = d.automations;
  const timerIds = new Set(state.timers.map((t) => t.automation_id).filter(Boolean));
  const managed = state.automations.filter((a) => a.id.startsWith("radio_star_"));
  const linked = state.automations.filter((a) => !a.id.startsWith("radio_star_") && timerIds.has(a.id));
  const others = state.automations.filter((a) => !a.id.startsWith("radio_star_") && !timerIds.has(a.id));

  const row = (a, extra = "") => `
    <tr>
      <td><label class="switch"><input type="checkbox" data-act="toggle-auto" data-id="${esc(a.id)}" ${a.state === "on" ? "checked" : ""}><span class="sl"></span></label></td>
      <td><b>${esc(a.alias || a.id)}</b>${extra}<div class="muted" style="font-size:11.5px">${esc(a.description || "")}</div></td>
      <td class="mono" style="font-size:12px">${esc(a.trigger_summary || "—")}</td>
      <td class="mono" style="font-size:12px;white-space:nowrap">${fmtAge(a.last_triggered)}</td>
      <td style="white-space:nowrap">
        <button class="small" data-act="show-auto" data-id="${esc(a.id)}">Detail</button>
        ${a.id.startsWith("radio_star_") ? `<button class="small danger" data-act="del-auto" data-id="${esc(a.id)}">Löschen</button>` : ""}
      </td>
    </tr>`;

  sec.innerHTML = `
  <div class="section-head"><h2>Automationen</h2>
    <div><button class="small" data-act="toggle-others">Alle anzeigen/ausblenden</button>
    <button class="small primary" data-act="new-auto">+ Neue Automation</button></div>
  </div>
  <div class="card"><h3>Radio-Star verwaltet (${managed.length})</h3>
    ${managed.length ? `<table><tbody>${managed.map((a) => row(a)).join("")}</tbody></table>` : `<div class="empty">Keine — über „+ Neue Automation“ anlegen.</div>`}
  </div>
  <div class="card"><h3>Verknüpft mit Timern (${linked.length})</h3>
    ${linked.length ? `<table><tbody>${linked.map((a) => row(a)).join("")}</tbody></table>` : `<div class="empty">Keine.</div>`}
  </div>
  <div class="card" id="others-card" style="display:none"><h3>Alle anderen (${others.length})</h3>
    <table><tbody>${others.map((a) => row(a)).join("")}</tbody></table>
  </div>
  <div class="card" id="auto-form" style="display:none"><h3>Neue Automation</h3>${autoForm()}</div>
  <div class="card" id="auto-detail" style="display:none"></div>`;
}

function autoForm() {
  return `
  <div class="formrow">
    <div style="flex:2"><label>Name (Alias)</label><input id="af-name" placeholder="z.B. Wetter TTS 7:30"></div>
    <div><label>ID (leer = automatisch radio_star_…)</label><input id="af-id" class="mono" placeholder="auto"></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div><label>Typ</label><select id="af-kind">
      <option value="minutes">Stündliche Minute(n)</option>
      <option value="at">Täglich um Uhrzeit</option>
      <option value="interval">Intervall (Minuten)</option>
    </select></div>
    <div id="af-mins-wrap"><label>Minuten (z.B. 25, 55)</label><input id="af-mins" value="25"></div>
    <div id="af-at-wrap" style="display:none"><label>Uhrzeit</label><input id="af-at" value="07:30"></div>
    <div id="af-int-wrap" style="display:none"><label>Minuten</label><input id="af-int" type="number" value="5"></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div><label>Aktion</label><select id="af-action">
      <option value="shell">Shell-Command</option>
      <option value="media">Medien abspielen</option>
      <option value="service">Service aufrufen</option>
    </select></div>
    <div id="af-shell-wrap" style="flex:2"><label>Shell-Command (ohne Prefix)</label><input id="af-shell" class="mono" placeholder="sonos_fm_news"></div>
    <div id="af-media-wrap" style="display:none;flex:2"><label>Media-URL</label><input id="af-media" class="mono" placeholder="http://192.168.0.50/audio/tts/wetter.mp3"></div>
    <div id="af-dom-wrap" style="display:none"><label>Domain</label><input id="af-dom" class="mono" placeholder="light"></div>
    <div id="af-svc-wrap" style="display:none"><label>Service</label><input id="af-svc" class="mono" placeholder="turn_on"></div>
    <div id="af-sdata-wrap" style="display:none;flex:2"><label>Data (JSON, optional)</label><input id="af-sdata" class="mono" placeholder='{"entity_id": "light.kitchen"}'></div>
  </div>
  <div class="formrow" style="margin-top:10px">
    <div style="flex:2"><label>Nur auslösen, wenn Player läuft (optional)</label><input id="af-player" class="mono" placeholder="media_player.sonos_fm"></div>
    <div style="flex:3"><label>Beschreibung</label><input id="af-desc"></div>
  </div>
  <label style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="af-timer" style="width:auto"> Gleichzeitig als Timer anlegen</label>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="primary" data-act="save-auto">Anlegen</button>
    <button data-act="cancel-auto">Abbrechen</button>
  </div>`;
}

$("#tab-automations").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const act = b.dataset.act;
  if (act === "new-auto") {
    const f = $("#auto-form");
    f.style.display = "";
    $("#auto-form").innerHTML = `<h3>Neue Automation</h3>` + autoForm();
  }
  if (act === "cancel-auto") $("#auto-form").style.display = "none";
  if (act === "save-auto") saveAutomation();
  if (act === "del-auto") delAutomation(b.dataset.id);
  if (act === "show-auto") showAutomation(b.dataset.id);
  if (act === "toggle-others") {
    const c = $("#others-card");
    c.style.display = c.style.display === "none" ? "" : "none";
  }
});
$("#tab-automations").addEventListener("change", (e) => {
  if (e.target.id === "af-kind") {
    const k = e.target.value;
    $("#af-mins-wrap").style.display = k === "minutes" ? "" : "none";
    $("#af-at-wrap").style.display = k === "at" ? "" : "none";
    $("#af-int-wrap").style.display = k === "interval" ? "" : "none";
  }
  if (e.target.id === "af-action") {
    const k = e.target.value;
    $("#af-shell-wrap").style.display = k === "shell" ? "" : "none";
    $("#af-media-wrap").style.display = k === "media" ? "" : "none";
    $("#af-dom-wrap").style.display = k === "service" ? "" : "none";
    $("#af-svc-wrap").style.display = k === "service" ? "" : "none";
    $("#af-sdata-wrap").style.display = k === "service" ? "" : "none";
  }
  if (e.target.dataset.act === "toggle-auto") {
    api(`/api/automations/${e.target.dataset.id}/toggle`, "POST")
      .then(() => { toast(e.target.checked ? "Automatisierung aktiviert" : "Automatisierung deaktiviert"); setTimeout(renderAutomations, 600); })
      .catch((err) => { e.target.checked = !e.target.checked; toast(err.message, true); });
  }
});

async function saveAutomation() {
  const kind = $("#af-kind").value;
  const schedule =
    kind === "minutes"
      ? { kind, minutes: $("#af-mins").value.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n)) }
      : kind === "at"
        ? { kind, at: $("#af-at").value }
        : { kind, minutes: parseInt($("#af-int").value, 10) };
  const atype = $("#af-action").value;
  const action =
    atype === "shell"
      ? { type: "shell", name: $("#af-shell").value.trim() }
      : atype === "media"
        ? { type: "media", url: $("#af-media").value.trim() }
        : (() => {
            const a = { type: "service", domain: $("#af-dom").value.trim(), name: $("#af-svc").value.trim() };
            const sd = $("#af-sdata").value.trim();
            if (sd) { try { a.data = JSON.parse(sd); } catch { toast("Data: ungültiges JSON", true); return null; } }
            return a;
          })();
  if (!action) return;
  const body = {
    alias: $("#af-name").value.trim(),
    id: $("#af-id").value.trim(),
    description: $("#af-desc").value.trim(),
    schedule,
    action,
    player_condition: $("#af-player").value.trim(),
    add_timer: $("#af-timer").checked,
  };
  try {
    const r = await api("/api/automations", "POST", body);
    toast(`Automation ${r.id} angelegt`);
    $("#auto-form").style.display = "none";
    renderAutomations();
  } catch (e) { toast(e.message, true); }
}
async function delAutomation(id) {
  if (!confirm(`Automation "${id}" löschen? (entfernt sie aus HA)`)) return;
  try { await api(`/api/automations/${id}`, "DELETE"); toast("Gelöscht"); renderAutomations(); }
  catch (e) { toast(e.message, true); }
}
async function showAutomation(id) {
  const c = $("#auto-detail");
  c.style.display = "";
  c.innerHTML = `<h3>Detail: ${esc(id)}</h3><div class="muted">wird geladen…</div>`;
  try {
    const d = await api(`/api/automations/${id}`);
    c.innerHTML = `<h3>Detail: ${esc(id)}</h3><pre class="mono" style="white-space:pre-wrap;background:var(--bg);padding:10px;border-radius:8px">${esc(JSON.stringify(d.config, null, 2))}</pre>`;
  } catch (e) { c.innerHTML = `<h3>Detail</h3><div class="muted">${esc(e.message)}</div>`; }
}

/* ================= SCRIPTS ================= */
async function renderScripts() {
  const sec = $("#tab-scripts");
  const d = await api("/api/scripts");
  state.scripts = d.scripts;
  const list = state.scripts.map((s) => `
    <div class="row ${state.currentScript === s.path ? "sel" : ""}" data-path="${esc(s.path)}">
      <span class="mono">${esc(s.path)}</span><span class="muted">${fmtSize(s.size)} · ${esc(s.mtime ? s.mtime.replace("T", " ") : "")}</span>
    </div>`).join("");
  sec.innerHTML = `
  <div class="section-head"><h2>Py-Skripte im HA-Config-Verzeichnis</h2>
    <div class="muted">${esc(d.config_dir || "Config-Dir nicht gefunden")}</div></div>
  <div class="grid2">
    <div class="card"><h3>Dateien</h3>
      <div style="display:flex;gap:8px;margin-bottom:10px"><button class="small primary" data-act="new-script">+ Neue Datei</button></div>
      <div class="script-list">${list || '<div class="empty">Keine .py-Dateien gefunden</div>'}</div>
    </div>
    <div class="card" id="editor-card"><h3>Editor</h3><div class="empty">Datei wählen</div></div>
  </div>`;
  if (state.currentScript) openScript(state.currentScript);
}

$("#tab-scripts").addEventListener("click", (e) => {
  const r = e.target.closest(".script-list .row");
  if (r) return openScript(r.dataset.path);
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "new-script") newScript();
  if (b.dataset.act === "save-script") saveScript();
  if (b.dataset.act === "del-script") delScript();
});

async function openScript(path) {
  state.currentScript = path;
  const c = $("#editor-card");
  c.innerHTML = `<h3>${esc(path)} <span class="muted">(lädt…)</span></h3>`;
  try {
    const d = await api("/api/scripts/content?path=" + encodeURIComponent(path));
    c.innerHTML = `
      <h3 class="mono">${esc(path)}</h3>
      <textarea id="script-editor" class="code" spellcheck="false">${esc(d.content)}</textarea>
      <div style="margin-top:10px;display:flex;gap:8px">
        <button class="primary" data-act="save-script">Speichern (mit Backup)</button>
        <button class="danger" data-act="del-script">Löschen</button>
      </div>`;
    $$(".script-list .row").forEach((x) => x.classList.toggle("sel", x.dataset.path === path));
  } catch (err) { c.innerHTML = `<h3>${esc(path)}</h3><div class="muted">${esc(err.message)}</div>`; }
}
async function newScript() {
  const p = prompt("Neuer Pfad (relativ zum Config-Dir, mit .py):", "scripts/mein_skript.py");
  if (!p) return;
  try {
    await api("/api/scripts", "POST", { path: p, content: "# Mein Skript\n\nprint('hallo')\n" });
    toast("Angelegt");
    state.currentScript = p;
    renderScripts();
  } catch (e) { toast(e.message, true); }
}
async function saveScript() {
  const ta = $("#script-editor");
  if (!ta || !state.currentScript) return;
  try {
    await api("/api/scripts?path=" + encodeURIComponent(state.currentScript), "PUT", { content: ta.value });
    toast("Gespeichert (Backup erstellt)");
    renderScripts();
  } catch (e) { toast(e.message, true); }
}
async function delScript() {
  if (!state.currentScript || !confirm(`${state.currentScript} wirklich löschen?`)) return;
  try {
    await api("/api/scripts?path=" + encodeURIComponent(state.currentScript), "DELETE");
    state.currentScript = null;
    toast("Gelöscht");
    renderScripts();
  } catch (e) { toast(e.message, true); }
}

/* ================= MP3 ================= */
async function renderMp3() {
  const sec = $("#tab-mp3");
  sec.innerHTML = `<div class="card"><div class="empty">Lade MP3-Bibliothek vom Webserver…</div></div>`;
  const d = await api("/api/mp3s");
  state.mp3s = d;
  if (d.error) {
    sec.innerHTML = `<div class="card"><h3>Webserver ${esc(d.webserver_url)}</h3><div class="empty">Fehler: ${esc(d.error)}</div>
      <button class="small" data-act="mp3-refresh">Erneut laden</button></div>`;
    return;
  }
  const groups = d.groups.map((g) => {
    const rows = g.files.map((f) => `
      <tr>
        <td class="mono" style="font-size:12.5px">${esc(f.name)}</td>
        <td style="white-space:nowrap;font-size:12.5px">${fmtSize(f.size)}</td>
        <td class="mono" style="font-size:12px;white-space:nowrap">${esc(f.mtime ? f.mtime.replace("T", " ") : "")}</td>
        <td><button class="small playbtn" data-act="mp3-play" data-url="${esc(g.url + f.name)}">▶ Abspielen</button></td>
      </tr>`).join("");
    return `<div class="card"><h3>${esc(g.dir)} (${g.files.length})</h3>
      <table><thead><tr><th>Datei</th><th>Größe</th><th>Geändert</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">leer</td></tr>'}</tbody></table></div>`;
  }).join("");
  sec.innerHTML = `
  <div class="section-head"><h2>MP3-Bibliothek · ${esc(d.webserver_url)}</h2>
    <button class="small" data-act="mp3-refresh">⟳ Aktualisieren</button></div>
  ${groups || '<div class="card"><div class="empty">Keine Dateien gefunden.</div></div>'}`;
}
$("#tab-mp3").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "mp3-refresh") { renderMp3(); }
  if (b.dataset.act === "mp3-play") {
    b.disabled = true;
    api("/api/mp3s/play", "POST", { url: b.dataset.url })
      .then(() => { toast("Wiedergabe gestartet"); setTimeout(renderMp3, 800); })
      .catch((err) => { toast(err.message, true); setTimeout(renderMp3, 500); });
  }
});

/* ================= TTS ================= */
async function renderTts() {
  const sec = $("#tab-tts");
  let voices = [], players = [];
  try { voices = (await api("/api/tts/voices")).voices; } catch {}
  try { players = (await api("/api/tts/players")).players; } catch {}
  const d = await api("/api/timers");
  state.timers = d.timers;
  const regen = state.timers.filter((t) => t.shell_command);
  let lib = null;
  try { lib = await api("/api/mp3s"); } catch {}
  const mp3Times = {};
  if (lib) for (const g of lib.groups || []) for (const f of g.files) mp3Times[f.name] = f.mtime;

  sec.innerHTML = `
  <div class="grid2">
    <div class="card"><h3>Text erzeugen &amp; ansagen (TTS)</h3>
      <label>Text</label>
      <textarea id="tts-text" rows="3" placeholder="z.B. Es ist 5 Uhr, die Temperatur beträgt 18 Grad."></textarea>
      <div class="formrow" style="margin-top:10px">
        <div><label>Stimme</label><select id="tts-voice">${voices.map((v) => `<option>${esc(v)}</option>`).join("") || "<option value=''>— keine —</option>"}</select></div>
        <div><label>Player (optional, spielt ab)</label><select id="tts-player"><option value="">— nur erzeugen —</option>${players.map((p) => `<option>${esc(p)}</option>`).join("")}</select></div>
      </div>
      <div style="margin-top:12px"><button class="primary" data-act="tts-say">Sagen</button></div>
    </div>
    <div class="card"><h3>MP3s neu erzeugen (Runner aufrufen)</h3>
      <div style="max-height:380px;overflow:auto">
      ${regen.map((t) => `
        <div class="event">
          <div class="body" style="flex:1">
            <div class="name" style="font-size:13.5px">${esc(t.name)} ${chip(t.category)}</div>
            <div class="meta mono" style="font-size:11.5px">${esc(schedText(t.schedule))} · ${esc(t.shell_command)}
              ${mp3Times[shellToMp3(t.shell_command)] ? " · letztes MP3: " + esc(mp3Times[shellToMp3(t.shell_command)].replace("T", " ")) : ""}</div>
          </div>
          <button class="small" data-act="tts-regen" data-cmd="${esc(t.shell_command)}">Jetzt</button>
        </div>`).join("") || '<div class="empty">Keine Runner-Timer.</div>'}
      </div>
    </div>
  </div>`;
}
function shellToMp3(cmd) {
  const map = {
    sonos_fm_news: "nachrichten.mp3",
    update_wetter_nachrichten: "wetter.mp3",
    update_sonnenaufgang: "Sonnenaufgang.mp3",
    update_sonnenuntergang: "Sonnenuntergang.mp3",
    update_wetter_intro: "wetter_intro.mp3",
    update_tageslaenge: "tageslänge.mp3",
    stunden_ansage: "uhrzeit_wochentag.mp3",
    run_titel: "Run.mp3",
  };
  return map[cmd] || cmd + ".mp3";
}
$("#tab-tts").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "tts-say") {
    const text = $("#tts-text").value.trim();
    if (!text) return toast("Kein Text", true);
    api("/api/tts/say", "POST", { text, voice: $("#tts-voice").value || null, player: $("#tts-player").value || null })
      .then(() => toast("TTS gestartet"))
      .catch((err) => toast(err.message, true));
  }
  if (b.dataset.act === "tts-regen") {
    b.disabled = true;
    api("/api/tts/regen", "POST", { shell_command: b.dataset.cmd })
      .then(() => { toast("Runner gestartet (MP3 wird neu erzeugt)"); setTimeout(renderTts, 3000); })
      .catch((err) => { toast(err.message, true); b.disabled = false; });
  }
});

/* ================= init ================= */
refreshStatus();
setInterval(refreshStatus, 30000);
const initHash = location.hash.replace(/^\/+/, "");
setTab(["timeline", "timers", "automations", "scripts", "mp3", "tts"].includes(initHash) ? initHash : "timeline");
