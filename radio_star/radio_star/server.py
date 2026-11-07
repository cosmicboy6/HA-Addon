#!/usr/bin/env python3
"""Radio-Star - Sonos-FM-Aggregator.

Timer, Automationen, Py-Skripte, MP3-Bibliothek, TTS und Timeline.
Raeumt die stuenliche Wetter-/Nachrichten-TTS-Kette von HA auf .50.
"""

import json
import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

APP_VERSION = "1.0.0"
STARTED = datetime.now()

DATA_DIR = Path(os.environ.get("RADIO_STAR_DATA", "/data"))
TIMERS_FILE = DATA_DIR / "timers.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"
OPTIONS_FILE = DATA_DIR / "options.json"

LOCK = threading.Lock()

DEFAULT_OPTS = {
    "webserver_url": "http://192.168.0.50",
    "mp3_dir": "/audio/tts",
    "player": "media_player.sonos_fm",
}

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def load_options():
    opts = dict(DEFAULT_OPTS)
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            opts.update({k: v for k, v in json.load(f).items() if v not in (None, "")})
    except Exception:
        pass
    for key, env in (
        ("webserver_url", "RADIO_STAR_WEB"),
        ("mp3_dir", "RADIO_STAR_MP3DIR"),
        ("player", "RADIO_STAR_PLAYER"),
    ):
        val = os.environ.get(env)
        if val:
            opts[key] = val
    opts["webserver_url"] = opts["webserver_url"].rstrip("/")
    return opts


OPTS = load_options()


# ---------------------------------------------------------------------------
# HA client
# ---------------------------------------------------------------------------


class HAError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HA {status}: {message}")
        self.status = status
        self.message = message


class HA:
    def __init__(self):
        token = os.environ.get("HA_API_TOKEN") or os.environ.get("HA_TOKEN") or os.environ.get(
            "SUPERVISOR_TOKEN"
        )
        base = os.environ.get("HA_API_URL")
        if not base:
            if os.environ.get("SUPERVISOR_TOKEN"):
                base = "http://supervisor/core/api"
            else:
                host = os.environ.get("HA_HOST", "homeassistant")
                port = os.environ.get("HA_PORT", "8123")
                scheme = os.environ.get("HA_SCHEME", "http")
                base = f"{scheme}://{host}:{port}/api"
        self.base = base.rstrip("/")
        self.token = token
        self._ssl_ctx = None

    def _open(self, req, timeout):
        ctx = None
        if req.full_url.startswith("https"):
            if self._ssl_ctx is None:
                self._ssl_ctx = ssl.create_default_context()
            ctx = self._ssl_ctx
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)

    def _req(self, method, path, body=None, timeout=15):
        url = self.base + path
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                try:
                    return r.status, json.loads(raw) if raw else None
                except ValueError:
                    return r.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"message": raw}
        except urllib.error.URLError as e:
            raise HAError(0, f"HA nicht erreichbar: {e.reason}") from e

    def get(self, path):
        status, body = self._req("GET", path)
        if status >= 400:
            msg = body.get("message") if isinstance(body, dict) else str(body)
            raise HAError(status, msg)
        return body

    def post(self, path, body=None):
        status, body2 = self._req("POST", path, body)
        if status >= 400:
            msg = body2.get("message") if isinstance(body2, dict) else str(body2)
            raise HAError(status, msg)
        return body2

    def delete(self, path):
        status, body = self._req("DELETE", path)
        if status >= 400:
            msg = body.get("message") if isinstance(body, dict) else str(body)
            raise HAError(status, msg)
        return body

    def service(self, domain, service, data=None, target=None):
        body = dict(data or {})
        if target:
            body.update(target)
        return self.post(f"/services/{domain}/{service}", body or None)

    # -- convenience ------------------------------------------------------
    def all_states(self):
        now = time.time()
        if now - _ALL_STATES_CACHE["ts"] < 10:
            return _ALL_STATES_CACHE["data"]
        data = self.get("/states")
        _ALL_STATES_CACHE.update(ts=now, data=data)
        return data

    def states(self, domain=None):
        all_e = self.all_states()
        if domain is None:
            return all_e
        return [e for e in all_e if e["entity_id"].split(".", 1)[0] == domain]

    def state(self, entity_id):
        return self.get(f"/states/{urllib.parse.quote(entity_id)}")

    def config(self):
        return self.get("/config")

    def automation_get(self, aid):
        return self.get(f"/config/automation/config/{urllib.parse.quote(aid)}")

    def automation_save(self, aid, config):
        return self.post(f"/config/automation/config/{urllib.parse.quote(aid)}", config)

    def automation_delete(self, aid):
        return self.delete(f"/config/automation/config/{urllib.parse.quote(aid)}")


HA_CLIENT = HA()

# ---------------------------------------------------------------------------
# HA config dir (scripts, automations.yaml)
# ---------------------------------------------------------------------------


def config_dir():
    for cand in (
        os.environ.get("HA_CONFIG_DIR"),
        "/homeassistant",
        "/config",
    ):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


def safe_path(rel):
    base = config_dir()
    if base is None:
        return None
    p = (base / rel).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        return None
    return p


def list_py_scripts():
    base = config_dir()
    if base is None:
        return []
    out = []
    skip_dirs = {"__pycache__", ".git", "deps", "backups"}
    for root, dirs, files in os.walk(base):
        rp = Path(root).relative_to(base)
        depth = 0 if str(rp) == "." else len(rp.parts)
        if depth >= 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("."):
                fp = Path(root) / f
                try:
                    st = fp.stat()
                    rel = str(fp.relative_to(base))
                    out.append(
                        {
                            "path": rel,
                            "size": st.st_size,
                            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        }
                    )
                except OSError:
                    continue
    out.sort(key=lambda x: x["path"].lower())
    return out


def read_py_script(rel):
    p = safe_path(rel)
    if p is None or not p.is_file() or p.suffix != ".py":
        raise HAError(400, "Kein gueltiges .py-File")
    return p.read_text(encoding="utf-8", errors="replace")


def write_py_script(rel, content, create=False):
    p = safe_path(rel)
    if p is None or p.suffix != ".py":
        raise HAError(400, "Kein gueltiges .py-Pfad")
    if not create and not p.exists():
        raise HAError(404, "Datei nicht gefunden")
    if create and p.exists():
        raise HAError(400, "Datei existiert bereits")
    if ".." in rel or rel.startswith("/"):
        raise HAError(400, "Ungueltiger Pfad")
    with LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            bak = p.with_name(p.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(p, bak)
        p.write_text(content, encoding="utf-8")
    return str(p)


def delete_py_script(rel):
    p = safe_path(rel)
    if p is None or not p.is_file() or p.suffix != ".py":
        raise HAError(400, "Kein gueltiges .py-File")
    with LOCK:
        p.unlink()
    return True


# ---------------------------------------------------------------------------
# Automations (YAML file + live states)
# ---------------------------------------------------------------------------

_STATE_CACHE = {"ts": 0, "data": {}}
_ALL_STATES_CACHE = {"ts": 0, "data": []}


def invalidate_state_caches():
    _STATE_CACHE["ts"] = 0
    _ALL_STATES_CACHE["ts"] = 0


def automation_states():
    now = time.time()
    if now - _STATE_CACHE["ts"] < 10:
        return _STATE_CACHE["data"]
    try:
        data = {}
        for e in HA_CLIENT.states("automation"):
            aid = e["attributes"].get("id")
            data[aid or e["entity_id"].split(".", 1)[-1]] = {
                "entity_id": e["entity_id"],
                "state": e["state"],
                "last_triggered": e["attributes"].get("last_triggered"),
                "mode": e["attributes"].get("mode"),
                "friendly_name": e["attributes"].get("friendly_name"),
            }
        _STATE_CACHE.update(ts=now, data=data)
    except HAError:
        pass
    return _STATE_CACHE["data"]


def trigger_summary(triggers):
    if not triggers:
        return ""
    parts = []
    for t in triggers:
        if not isinstance(t, dict):
            continue
        plat = t.get("platform", "?")
        if plat == "time_pattern":
            mins = t.get("minutes")
            hrs = t.get("hours")
            secs = t.get("seconds")
            if mins and str(mins).startswith("/"):
                parts.append(f"alle {str(mins).lstrip('/')} min")
            else:
                h = f"{hrs}h " if hrs and hrs != "*" else ""
                s = f":{secs:02d}" if secs not in (None, "0") else ""
                parts.append(f"{h}:{str(mins).zfill(2)}{s}" if mins is not None else "zeit")
        elif plat == "time":
            at = t.get("at", "")
            parts.append(f"taeglich {at[:5]}")
        elif plat == "state":
            eid = t.get("entity_id")
            if isinstance(eid, list):
                eid = eid[0] if eid else "?"
            parts.append(f"state {eid}")
        elif plat == "sun":
            parts.append("sun " + str(t.get("event", "")))
        elif plat == "numeric_state":
            eid = t.get("entity_id", "?")
            parts.append(f"numeric {eid}")
        else:
            parts.append(plat)
    return " + ".join(parts)


def load_automations_yaml():
    base = config_dir()
    if base is None or yaml is None:
        return None
    p = base / "automations.yaml"
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            return None
        return data
    except Exception:
        return None


def automations_list():
    yamls = load_automations_yaml()
    states = automation_states()
    items = []
    if yamls is not None:
        seen = set()
        for a in yamls:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            st = states.get(aid, {})
            items.append(
                {
                    "id": aid,
                    "alias": a.get("alias", ""),
                    "description": a.get("description", ""),
                    "mode": a.get("mode", "single"),
                    "triggers": a.get("triggers") or a.get("trigger") or [],
                    "conditions": a.get("conditions") or a.get("condition") or [],
                    "actions": a.get("actions") or a.get("action") or [],
                    "trigger_summary": trigger_summary(a.get("triggers") or a.get("trigger")),
                    "state": st.get("state", "unknown"),
                    "last_triggered": st.get("last_triggered"),
                    "entity_id": st.get("entity_id", f"automation.{aid}"),
                    "source": "yaml",
                }
            )
    else:
        for aid, st in states.items():
            items.append(
                {
                    "id": aid,
                    "alias": st.get("friendly_name", aid),
                    "description": "",
                    "mode": st.get("mode"),
                    "triggers": [],
                    "conditions": [],
                    "actions": [],
                    "trigger_summary": "",
                    "state": st.get("state"),
                    "last_triggered": st.get("last_triggered"),
                    "entity_id": st.get("entity_id"),
                    "source": "states",
                }
            )
    items.sort(key=lambda x: (x["alias"] or x["id"]).lower())
    return items


def build_automation(body):
    sched = body.get("schedule") or {}
    kind = sched.get("kind", "minutes")
    triggers = []
    if kind == "minutes":
        mins = sched.get("minutes") or []
        if isinstance(mins, (int, str)):
            mins = [mins]
        for m in mins:
            t = {"platform": "time_pattern", "minutes": str(m)}
            if sched.get("seconds") not in (None, ""):
                t["seconds"] = str(sched["seconds"])
            triggers.append(t)
        if not triggers:
            raise HAError(400, "Keine Minuten angegeben")
    elif kind == "at":
        at = str(sched.get("at", ""))
        if not re.match(r"^\d{1,2}:\d{2}", at):
            raise HAError(400, "Ungültiges Zeitformat (HH:MM)")
        triggers = [{"platform": "time", "at": f"{int(at[:2]):02d}:{at[3:5]}:00"}]
    elif kind == "interval":
        n = int(sched.get("minutes", 5))
        if n < 1:
            raise HAError(400, "Interval muss >= 1 Minute sein")
        triggers = [{"platform": "time_pattern", "hours": "*", "minutes": f"/{n}", "seconds": "0"}]
    else:
        raise HAError(400, "Unbekannter Schedule-Typ")

    conditions = []
    player = body.get("player_condition")
    if player:
        conditions.append({"condition": "state", "entity_id": player, "state": "playing"})

    act = body.get("action") or {}
    atype = act.get("type", "shell")
    actions = []
    if atype == "shell":
        name = str(act.get("name", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise HAError(400, "Ungültiger Shell-Command-Name")
        actions = [{"action": f"shell_command.{name}", "data": {}}]
    elif atype == "media":
        url = str(act.get("url", "")).strip()
        if not url:
            raise HAError(400, "Keine Media-URL")
        actions = [
            {
                "action": "media_player.play_media",
                "target": {"entity_id": act.get("player") or OPTS["player"]},
                "data": {"media_content_id": url, "media_content_type": "music"},
            }
        ]
    elif atype == "service":
        dom = str(act.get("domain", "")).strip()
        svc = str(act.get("name", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", f"{dom}.{svc}"):
            raise HAError(400, "Ungültiger Service")
        a = {"action": f"{dom}.{svc}"}
        if act.get("data"):
            a["data"] = act["data"]
        if act.get("target"):
            a["target"] = act["target"]
        actions = [a]
    else:
        raise HAError(400, "Unbekannter Action-Typ")

    alias = str(body.get("alias", "")).strip()
    if not alias:
        raise HAError(400, "Name fehlt")
    aid = str(body.get("id", "")).strip()
    if not aid:
        slug = re.sub(r"[^a-z0-9]+", "_", alias.lower()).strip("_")
        if slug.startswith("radio_star_"):
            slug = slug[len("radio_star_"):]
        aid = "radio_star_" + slug
    if not re.match(r"^[A-Za-z0-9_]+$", aid):
        raise HAError(400, "Ungültige Automation-ID")

    config = {
        "id": aid,
        "alias": alias,
        "description": str(body.get("description", "")).strip(),
        "mode": body.get("mode", "single"),
        "triggers": triggers,
        "conditions": conditions,
        "actions": actions,
    }
    return aid, config


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------

DEFAULT_TIMERS = [
    {
        "id": "news_25_55",
        "name": "ORF-Nachrichten TTS",
        "category": "news",
        "enabled": True,
        "schedule": {"kind": "minutes", "minutes": [25, 55]},
        "automation_id": "sonos_fm_news",
        "shell_command": "sonos_fm_news",
        "description": "Holt um :25/:55 ORF-Nachrichten per TTS mit Glocke → nachrichten.mp3",
    },
    {
        "id": "wetter_26_56",
        "name": "Badewetter + Nachrichten MP3 Update",
        "category": "weather",
        "enabled": True,
        "schedule": {"kind": "minutes", "minutes": [26, 56]},
        "automation_id": "badewetter_news_updater_25_55",
        "shell_command": "update_wetter_nachrichten",
        "description": "Aktualisiert wetter.mp3 und nachrichten.mp3 (1 Minute nach den Sonos-Nachrichten)",
    },
    {
        "id": "stundenansage_00",
        "name": "Stundenansage (Uhrzeit + Wochentag)",
        "category": "announce",
        "enabled": True,
        "schedule": {"kind": "minutes", "minutes": [0]},
        "automation_id": "stunden_ansage_sonos_fm",
        "shell_command": "stunden_ansage",
        "description": "Um :00 Uhr Zeit/Wochentag ansagen + Nachrichten/Wetter/Sonnenaufgang als playPromptUrl über den Stream",
    },
    {
        "id": "random_stream_00",
        "name": "Zufalls-Stream",
        "category": "stream",
        "enabled": True,
        "schedule": {"kind": "minutes", "minutes": [0]},
        "automation_id": "play_random_stream_hourly",
        "shell_command": "pick_random_stream",
        "description": "Liest Play.m3u, wählt zufälligen Stream und spielt ihn auf Sonos FM",
    },
    {
        "id": "run_titel_15_45",
        "name": "Titel-Announcement (Run.mp3)",
        "category": "titel",
        "enabled": True,
        "schedule": {"kind": "minutes", "minutes": [15, 45]},
        "automation_id": "run_titel_on_sonos_fm",
        "shell_command": "run_titel",
        "description": "Um :15/:45 Streamtitel per TTS (Highbell-Intro) ansagen, danach Stream fortsetzen",
    },
    {
        "id": "sonnenaufgang_2355",
        "name": "Sonnenaufgang TTS",
        "category": "weather",
        "enabled": True,
        "schedule": {"kind": "at", "at": "23:55"},
        "automation_id": "sonnenaufgang_tts_updater",
        "shell_command": "update_sonnenaufgang",
        "description": "Erzeugt täglich um 23:55 Sonnenaufgang.mp3 mit dem nächsten Sonnenaufgang",
    },
    {
        "id": "sonnenuntergang_2355",
        "name": "Sonnenuntergang TTS",
        "category": "weather",
        "enabled": True,
        "schedule": {"kind": "at", "at": "23:55"},
        "automation_id": "sonnenuntergang_tts_updater",
        "shell_command": "update_sonnenuntergang",
        "description": "Erzeugt täglich um 23:55 Sonnenuntergang.mp3",
    },
    {
        "id": "wetter_intro_2357",
        "name": "Wetter-Intro TTS",
        "category": "weather",
        "enabled": True,
        "schedule": {"kind": "at", "at": "23:57"},
        "automation_id": "wetter_intro_tts_updater",
        "shell_command": "update_wetter_intro",
        "description": "Erzeugt täglich um 23:57 wetter_intro.mp3 (Prognose für den nächsten Tag)",
    },
    {
        "id": "tageslaenge_0200",
        "name": "Tageslänge TTS",
        "category": "weather",
        "enabled": True,
        "schedule": {"kind": "at", "at": "02:00"},
        "automation_id": "tageslaenge_tts_updater",
        "shell_command": "update_tageslaenge",
        "description": "Erzeugt täglich um 02:00 tageslänge.mp3 (Sonnenaufgang→Sonnenuntergang)",
    },
    {
        "id": "watchdog_5",
        "name": "Sonos FM Watchdog",
        "category": "system",
        "enabled": True,
        "schedule": {"kind": "interval", "minutes": 5},
        "automation_id": "sonos_fm_watchdog_15",
        "shell_command": None,
        "description": "Startet den Stream neu, wenn Sonos FM nicht spielt (alle 5 Minuten)",
    },
]


def load_timers():
    try:
        with open(TIMERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("timers"), list):
            return data["timers"]
    except Exception:
        pass
    return None


def save_timers(timers):
    global DATA_DIR, TIMERS_FILE
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not os.access(DATA_DIR, os.W_OK):
            raise OSError("not writable")
    except OSError:
        import tempfile

        DATA_DIR = Path(tempfile.gettempdir()) / "radio-star-data"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TIMERS_FILE = DATA_DIR / "timers.json"
    with LOCK:
        tmp = TIMERS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "timers": timers}, f, ensure_ascii=False, indent=2)
        tmp.replace(TIMERS_FILE)


def get_timers():
    t = load_timers()
    if t is None:
        t = [dict(x) for x in DEFAULT_TIMERS]
        save_timers(t)
    return t


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def tz():
    name = os.environ.get("TZ") or "Europe/Vienna"
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


def now_local():
    return datetime.now(tz())


def next_occurrences(timer, now, horizon):
    sched = timer.get("schedule") or {}
    kind = sched.get("kind", "minutes")
    limit = horizon.total_seconds() if isinstance(horizon, timedelta) else float(horizon)
    out = []
    try:
        if kind == "minutes":
            mins = sched.get("minutes") or []
            if isinstance(mins, (int, str)):
                mins = [mins]
            for m in mins:
                m = int(m) % 60
                t = now.replace(minute=m, second=0, microsecond=0)
                if t <= now:
                    t += timedelta(hours=1)
                while (t - now).total_seconds() <= limit:
                    out.append(t)
                    t += timedelta(hours=1)
        elif kind == "at":
            at = str(sched.get("at", "00:00"))
            hh, mm = int(at[:2]), int(at[3:5])
            t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            if (t - now).total_seconds() <= limit:
                out.append(t)
        elif kind == "interval":
            n = max(1, int(sched.get("minutes", 5)))
            base = now.replace(hour=0, minute=0, second=0, microsecond=0)
            step = n * 60
            secs = (now - base).total_seconds()
            t = base + timedelta(seconds=(int(secs) // step + 1) * step)
            while (t - now).total_seconds() <= limit:
                out.append(t)
                t += timedelta(seconds=step)
    except Exception as e:  # noqa: BLE001
        print(f"[radio-star] next_occurrences({timer.get('id')}): {e}")
    return sorted(out)


def build_timeline(minutes=60):
    now = now_local()
    horizon = timedelta(minutes=minutes)
    timers = get_timers()
    states = automation_states()
    events = []
    for timer in timers:
        if not timer.get("enabled", True):
            continue
        aid = timer.get("automation_id")
        st = states.get(aid, {}) if aid else {}
        for t in next_occurrences(timer, now, horizon):
            events.append(
                {
                    "time": t.isoformat(timespec="seconds"),
                    "time_hm": t.strftime("%H:%M"),
                    "countdown_sec": int((t - now).total_seconds()),
                    "timer_id": timer["id"],
                    "name": timer.get("name", timer["id"]),
                    "category": timer.get("category", ""),
                    "description": timer.get("description", ""),
                    "shell_command": timer.get("shell_command"),
                    "automation_id": aid,
                    "automation_state": st.get("state"),
                    "automation_last_triggered": st.get("last_triggered"),
                }
            )
    events.sort(key=lambda e: e["time"])
    return {"now": now.isoformat(timespec="seconds"), "events": events}


# ---------------------------------------------------------------------------
# Webserver (MP3s)
# ---------------------------------------------------------------------------

_MP3_CACHE = {"ts": 0, "data": None}
_MP3_TTL = 120


def http_get(url, timeout=10, headers=None):
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def http_head(url, timeout=8):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return dict(r.headers)


def list_dir(url):
    status, raw, _ = http_get(url + "/" if not url.endswith("/") else url)
    html = raw.decode("utf-8", "replace")
    hrefs = re.findall(r'href="([^"#?]+)"', html)
    files, dirs = [], []
    for h in hrefs:
        if h in ("../", "/") or h.startswith("/"):
            continue
        if h.endswith("/"):
            dirs.append(h[:-1])
        else:
            files.append(h)
    return files, dirs


def mp3_metadata(base_url, name):
    out = {"name": name, "size": None, "mtime": None}
    try:
        h = http_head(f"{base_url}/{urllib.parse.quote(name)}")
        out["size"] = int(h.get("Content-Length", 0)) or None
        lm = h.get("Last-Modified")
        if lm:
            out["mtime"] = datetime.fromtimestamp(
                time.mktime(time.strptime(lm, "%a, %d %b %Y %H:%M:%S %Z"))
            ).isoformat(timespec="seconds")
    except Exception:
        pass
    return out


def mp3_library():
    now = time.time()
    if now - _MP3_CACHE["ts"] < _MP3_TTL and _MP3_CACHE["data"] is not None:
        return _MP3_CACHE["data"]
    base = f"{OPTS['webserver_url']}{OPTS['mp3_dir']}"
    groups = []
    try:
        files, dirs = list_dir(base)
        with ThreadPoolExecutor(max_workers=8) as ex:
            metas = list(ex.map(lambda n: mp3_metadata(base, n), files))
        metas.sort(key=lambda m: m["name"].lower())
        groups.append(
            {
                "dir": OPTS["mp3_dir"] + "/",
                "url": base + "/",
                "files": [m for m in metas if m["size"] is not None],
            }
        )
        for d in dirs:
            durl = f"{base}/{d}"
            try:
                dfiles, _ = list_dir(durl)
            except Exception:
                continue
            dfiles = [f for f in dfiles if f.lower().endswith((".mp3", ".m3u"))]
            with ThreadPoolExecutor(max_workers=8) as ex:
                dmetas = list(ex.map(lambda n: mp3_metadata(durl, n), dfiles))
            dmetas.sort(key=lambda m: m["name"].lower())
            if dmetas:
                groups.append(
                    {
                        "dir": f"{OPTS['mp3_dir']}/{d}/",
                        "url": durl + "/",
                        "files": [m for m in dmetas if m["size"] is not None],
                    }
                )
    except Exception as e:
        _MP3_CACHE.update(ts=now, data={"error": str(e), "groups": []})
        return _MP3_CACHE["data"]
    _MP3_CACHE.update(ts=now, data={"error": None, "groups": groups})
    return _MP3_CACHE["data"]


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def jbody(body):
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise ApiError(400, "Ungültiges JSON")


def handle_api(self, path, method, body, query):
    p = path[len("/api"):]

    # -- status ------------------------------------------------------------
    if p == "/status" and method == "GET":
        info = {"version": APP_VERSION, "started": STARTED.isoformat(timespec="seconds"), "tz": str(tz())}
        try:
            cfg = HA_CLIENT.config()
            info["ha"] = {
                "ok": True,
                "version": cfg.get("version"),
                "location": cfg.get("location_name"),
                "time_zone": cfg.get("time_zone"),
            }
        except HAError as e:
            info["ha"] = {"ok": False, "error": str(e)}
        try:
            e = HA_CLIENT.state(OPTS["player"])
            info["player"] = {
                "entity_id": OPTS["player"],
                "state": e["state"],
                "title": e["attributes"].get("media_title"),
                "url": e["attributes"].get("media_content_id"),
                "position": e["attributes"].get("media_position"),
                "duration": e["attributes"].get("media_duration"),
                "volume": e["attributes"].get("volume_level"),
            }
        except HAError:
            info["player"] = {"entity_id": OPTS["player"], "state": None}
        info["config_dir"] = str(config_dir()) if config_dir() else None
        info["yaml_available"] = yaml is not None
        try:
            http_head(f"{OPTS['webserver_url']}/")
            info["webserver"] = {"ok": True, "url": OPTS["webserver_url"]}
        except Exception as e:
            info["webserver"] = {"ok": False, "error": str(e)}
        return info

    # -- options -----------------------------------------------------------
    if p == "/options" and method == "GET":
        return OPTS

    # -- timers ------------------------------------------------------------
    if p == "/timers" and method == "GET":
        return {"timers": get_timers()}
    if p == "/timers" and method == "POST":
        t = jbody(body)
        if not t.get("name"):
            raise ApiError(400, "Name fehlt")
        timers = get_timers()
        t["id"] = re.sub(r"[^a-z0-9_]+", "_", str(t.get("id") or t["name"]).lower()).strip("_")
        if any(x["id"] == t["id"] for x in timers):
            raise ApiError(400, f"Timer-ID {t['id']} existiert bereits")
        timers.append(t)
        save_timers(timers)
        return {"timers": timers}
    m = re.match(r"^/timers/([A-Za-z0-9_]+)$", p)
    if m and method == "PUT":
        tid = m.group(1)
        timers = get_timers()
        t = jbody(body)
        for i, x in enumerate(timers):
            if x["id"] == tid:
                timers[i] = {**x, **t, "id": tid}
                save_timers(timers)
                return {"timers": timers}
        raise ApiError(404, "Timer nicht gefunden")
    if m and method == "DELETE":
        tid = m.group(1)
        timers = get_timers()
        new = [x for x in timers if x["id"] != tid]
        if len(new) == len(timers):
            raise ApiError(404, "Timer nicht gefunden")
        save_timers(new)
        return {"timers": new}
    if p == "/timers/reset" and method == "POST":
        save_timers([dict(x) for x in DEFAULT_TIMERS])
        return {"timers": get_timers()}

    # -- timeline ----------------------------------------------------------
    if p == "/timeline" and method == "GET":
        minutes = int(query.get("minutes", ["60"])[0])
        return build_timeline(minutes)

    # -- automations -------------------------------------------------------
    if p == "/automations" and method == "GET":
        return {"automations": automations_list()}
    if p == "/automations" and method == "POST":
        body_data = jbody(body)
        aid, config = build_automation(body_data)
        try:
            HA_CLIENT.automation_save(aid, config)
        except HAError as e:
            raise ApiError(e.status or 500, e.message)
        resp = {"id": aid, "entity_id": f"automation.{aid}"}
        if body_data.get("add_timer"):
            sched = body_data.get("schedule") or {}
            timer = {
                "id": aid,
                "name": config["alias"],
                "category": body_data.get("category", "custom"),
                "enabled": True,
                "schedule": sched,
                "automation_id": aid,
                "shell_command": (body_data.get("action") or {}).get("name")
                if (body_data.get("action") or {}).get("type") == "shell"
                else None,
                "description": config["description"],
            }
            timers = get_timers()
            timers.append(timer)
            save_timers(timers)
        return resp
    m = re.match(r"^/automations/([A-Za-z0-9_.-]+)/toggle$", p)
    if m and method == "POST":
        aid = m.group(1)
        st = automation_states().get(aid)
        entity = st["entity_id"] if st else f"automation.{aid}"
        svc = "turn_off" if st and st["state"] == "on" else "turn_on"
        HA_CLIENT.service("automation", svc, target={"entity_id": entity})
        invalidate_state_caches()
        return {"id": aid, "service": svc}
    m = re.match(r"^/automations/([A-Za-z0-9_.-]+)$", p)
    if m and method == "GET":
        aid = m.group(1)
        try:
            cfg = HA_CLIENT.automation_get(aid)
            return {"id": aid, "config": cfg}
        except HAError:
            for a in automations_list():
                if a["id"] == aid:
                    return {"id": aid, "config": {k: a[k] for k in a if k != "state"}}
            raise ApiError(404, "Automation nicht gefunden")
    if m and method == "DELETE":
        aid = m.group(1)
        try:
            HA_CLIENT.automation_delete(aid)
        except HAError as e:
            raise ApiError(e.status or 500, e.message)
        invalidate_state_caches()
        return {"id": aid, "deleted": True}

    # -- scripts -----------------------------------------------------------
    if p == "/scripts" and method == "GET":
        return {"scripts": list_py_scripts(), "config_dir": str(config_dir()) if config_dir() else None}
    if p == "/scripts" and method == "POST":
        d = jbody(body)
        rel = str(d.get("path", "")).strip("/")
        if not rel.endswith(".py"):
            raise ApiError(400, "Nur .py-Dateien")
        try:
            write_py_script(rel, d.get("content", ""), create=True)
        except HAError as e:
            raise ApiError(e.status, e.message)
        return {"path": rel, "saved": True}
    if p == "/scripts" and method == "PUT":
        rel = str(query.get("path", [""])[0]).strip("/")
        d = jbody(body)
        try:
            write_py_script(rel, d.get("content", ""))
        except HAError as e:
            raise ApiError(e.status, e.message)
        return {"path": rel, "saved": True}
    if p == "/scripts" and method == "DELETE":
        rel = str(query.get("path", [""])[0]).strip("/")
        try:
            delete_py_script(rel)
        except HAError as e:
            raise ApiError(e.status, e.message)
        return {"path": rel, "deleted": True}
    m = re.match(r"^/scripts/content$", p)
    if m and method == "GET":
        rel = str(query.get("path", [""])[0]).strip("/")
        try:
            content = read_py_script(rel)
        except HAError as e:
            raise ApiError(e.status, e.message)
        return {"path": rel, "content": content}

    # -- mp3 ----------------------------------------------------------------
    if p == "/mp3s" and method == "GET":
        lib = mp3_library()
        lib["webserver_url"] = OPTS["webserver_url"]
        return lib
    if p == "/mp3s/play" and method == "POST":
        d = jbody(body)
        url = str(d.get("url", "")).strip()
        if not url:
            raise ApiError(400, "Keine URL")
        if not url.startswith("http"):
            url = f"{OPTS['webserver_url']}/{url.lstrip('/')}"
        player = d.get("player") or OPTS["player"]
        HA_CLIENT.service(
            "media_player",
            "play_media",
            data={"media_content_id": url, "media_content_type": "music"},
            target={"entity_id": player},
        )
        return {"url": url, "player": player, "playing": True}

    # -- tts ----------------------------------------------------------------
    if p == "/tts/voices" and method == "GET":
        return {"voices": [e["entity_id"] for e in HA_CLIENT.states("tts")]}
    if p == "/tts/players" and method == "GET":
        return {
            "players": [
                e["entity_id"]
                for e in HA_CLIENT.states("media_player")
                if e["state"] in ("idle", "playing", "paused")
            ]
        }
    if p == "/tts/say" and method == "POST":
        d = jbody(body)
        text = str(d.get("text", "")).strip()
        if not text:
            raise ApiError(400, "Kein Text")
        voice = d.get("voice")
        data = {"message": text}
        if d.get("player"):
            data["media_player"] = d["player"]
        target = {"entity_id": voice} if voice else None
        if not voice and not d.get("player"):
            raise ApiError(400, "Keine Stimme und kein Player gewählt")
        HA_CLIENT.service("tts", "say", data=data, target=target)
        return {"ok": True}
    if p == "/tts/regen" and method == "POST":
        d = jbody(body)
        cmd = str(d.get("shell_command", "")).strip()
        if not re.match(r"^[A-Za-z0-9_]+$", cmd):
            raise ApiError(400, "Ungültiger Shell-Command")
        HA_CLIENT.service("shell_command", cmd)
        _MP3_CACHE["ts"] = 0
        return {"shell_command": cmd, "started": True}
    if p == "/shell" and method == "POST":
        d = jbody(body)
        cmd = str(d.get("name", "")).strip()
        if not re.match(r"^[A-Za-z0-9_]+$", cmd):
            raise ApiError(400, "Ungültiger Shell-Command")
        HA_CLIENT.service("shell_command", cmd)
        return {"shell_command": cmd, "started": True}

    raise ApiError(404, f"Unbekannter Endpunkt: {p}")


class Handler(BaseHTTPRequestHandler):
    server_version = "RadioStar/" + APP_VERSION

    def log_message(self, fmt, *args):  # quieter
        pass

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path):
        try:
            raw = path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self):
        if os.environ.get("RADIO_STAR_ALLOW_ALL") == "1":
            return True
        ip = self.client_address[0]
        return ip in ("172.30.32.2", "127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._allowed():
            self._send_json({"error": "Forbidden (nur Supervisor/localhost)"}, 403)
            return
        body = self._read_body() if method in ("POST", "PUT") else b""
        try:
            if path.startswith("/api/"):
                self._send_json(handle_api(self, path, method, body, query))
            elif path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html")
            elif path in ("/app.js", "/style.css", "/icon.svg"):
                self._send_file(STATIC_DIR / path.lstrip("/"))
            else:
                self._send_json({"error": "not found"}, 404)
        except ApiError as e:
            self._send_json({"error": e.message}, e.status)
        except HAError as e:
            self._send_json({"error": str(e)}, 502)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"Interner Fehler: {e}"}, 500)


def main():
    port = int(os.environ.get("PORT", os.environ.get("RADIO_STAR_PORT", "8099")))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[radio-star] v{APP_VERSION} laeuft auf Port {port}")
    print(f"[radio-star] HA: {HA_CLIENT.base}")
    print(f"[radio-star] Webserver: {OPTS['webserver_url']}{OPTS['mp3_dir']}")
    print(f"[radio-star] Config-Dir: {config_dir()}")
    server.serve_forever()


if __name__ == "__main__":
    main()
