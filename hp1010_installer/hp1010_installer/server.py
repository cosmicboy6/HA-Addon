#!/usr/bin/env python3
"""HP1010 Print Server Installer - Home Assistant app backend.

Builds and flashes the ESP32-S3 HP LaserJet 1010 print-server firmware from
inside Home Assistant, over USB. Pure stdlib; it shells out to the bundled
ESPHome (and esptool) for the actual work.

Endpoints (all relative to the ingress base):
  GET  /api/status          app + esphome + job + port summary
  GET  /api/settings        current settings (form prefill)
  POST /api/settings        save settings -> renders project/secrets.yaml
  POST /api/key             generate a fresh API encryption key
  GET  /api/ports           list serial ports
  POST /api/build           esphome compile
  POST /api/flash           esphome upload --device <port>
  POST /api/quickflash      esptool write_flash 0x0 firmware.factory.bin
  POST /api/cancel          terminate the running job
  GET  /api/log?since=N     job log lines after sequence N
  GET  /api/health          200 (watchdog)
"""
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_VERSION = "1.0.0"

DATA_DIR = Path(os.environ.get("HP1010_INSTALLER_DATA", "/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"
PROJECT_SRC = Path(os.environ.get("HP1010_INSTALLER_SRC", "/opt/hp1010_installer/project"))
WORK_DIR = DATA_DIR / "project"
CONFIG_NAME = "hp1010-printserver.yaml"
FACTORY_BIN = WORK_DIR / "firmware.factory.bin"
STATIC_DIR = Path(__file__).resolve().parent / "static"

ESPHOME_BIN = shutil.which("esphome") or "/opt/venv/bin/esphome"
ESPTOOL_BIN = shutil.which("esptool") or "/opt/venv/bin/esptool"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

DEFAULT_SETTINGS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "ap_ssid": "HP1010 Print Fallback",
    "ap_password": "hp1010setup",
    "api_key": "",
    "device_port": "",
}


def log(msg):
    print(f"[hp1010 {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- log
class LogBuffer:
    def __init__(self, max_lines=4000):
        self._lines = []
        self._seq = 0
        self._lock = threading.Lock()
        self._max = max_lines

    def add(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            for raw in str(text).replace("\r", "").split("\n"):
                self._seq += 1
                self._lines.append({"seq": self._seq, "t": stamp, "text": ANSI.sub("", raw)})
            if len(self._lines) > self._max:
                self._lines = self._lines[-self._max:]

    def since(self, seq):
        with self._lock:
            return [ln for ln in self._lines if ln["seq"] > seq], self._seq

    def clear(self):
        with self._lock:
            self._lines = []


# --------------------------------------------------------------------------- jobs
class JobRunner:
    def __init__(self, logbuf):
        self.log = logbuf
        self._lock = threading.Lock()
        self._proc = None
        self._thread = None
        self.kind = None
        self.running = False
        self.rc = None
        self.started = None
        self.finished = None

    def status(self):
        return {
            "kind": self.kind,
            "running": self.running,
            "rc": self.rc,
            "started": self.started,
            "finished": self.finished,
        }

    def start(self, kind, cmd, cwd):
        with self._lock:
            if self.running:
                return False, "a job is already running"
            self.kind = kind
            self.running = True
            self.rc = None
            self.started = time.time()
            self.finished = None
            self._thread = threading.Thread(target=self._run, args=(cmd, cwd), daemon=True)
            self._thread.start()
            return True, None

    def _run(self, cmd, cwd):
        self.log.add(f"$ {' '.join(str(c) for c in cmd)}")
        self.log.add(f"  (working directory: {cwd})")
        try:
            self._proc = subprocess.Popen(
                [str(c) for c in cmd], cwd=str(cwd),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=os.environ.copy(),
            )
            for line in self._proc.stdout:
                self.log.add(line.rstrip("\n"))
            self.rc = self._proc.wait()
            self.log.add(f"[{self.kind}] finished with exit code {self.rc}")
        except Exception as exc:  # noqa: BLE001
            self.log.add(f"[{self.kind}] ERROR: {exc}")
            self.log.add(traceback.format_exc())
            self.rc = -1
        finally:
            self.running = False
            self.finished = time.time()
            self._proc = None

    def cancel(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            return True
        return False


LOG = LogBuffer()
RUNNER = JobRunner(LOG)
_VERSION_CACHE = {"value": None}


# --------------------------------------------------------------------------- helpers
def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            for key, value in json.load(fh).items():
                if value is not None:
                    settings[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log(f"could not read settings: {exc}")
    # fall back to the app options for the fallback-AP defaults
    try:
        with open(DATA_DIR / "options.json", encoding="utf-8") as fh:
            opts = json.load(fh)
        if not settings["ap_ssid"] and opts.get("default_ap_ssid"):
            settings["ap_ssid"] = opts["default_ap_ssid"]
        if not settings["ap_password"] and opts.get("default_ap_password"):
            settings["ap_password"] = opts["default_ap_password"]
    except Exception:  # noqa: BLE001
        pass
    return settings


def save_settings(settings):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def generate_key():
    return base64.b64encode(os.urandom(32)).decode()


def _yaml_str(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_secrets(settings):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    text = (
        "# Generated by the HP1010 Print Server Installer app.\n"
        "# Edit the values in the app panel, not here - they are overwritten on save/build.\n\n"
        f"wifi_ssid: {_yaml_str(settings['wifi_ssid'])}\n"
        f"wifi_password: {_yaml_str(settings['wifi_password'])}\n"
        f"ap_ssid: {_yaml_str(settings['ap_ssid'])}\n"
        f"ap_password: {_yaml_str(settings['ap_password'])}\n"
        f"api_encryption_key: {_yaml_str(settings['api_key'])}\n"
    )
    (WORK_DIR / "secrets.yaml").write_text(text, encoding="utf-8")


def sync_project():
    """Copy the bundled project into the persistent work dir.

    ``secrets.yaml`` is not part of the bundle, so a user's generated secrets
    survive restarts and image updates.
    """
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    for item in sorted(PROJECT_SRC.iterdir()):
        dest = WORK_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


def list_ports():
    ports = []
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/ttyAMA*"):
        ports.extend(sorted(glob.glob(pattern)))
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        ports.extend(sorted(str(p) for p in by_id.iterdir()))
    # de-duplicate, keeping order
    seen, out = set(), []
    for port in ports:
        if port not in seen:
            seen.add(port)
            out.append(port)
    return out


def compiled_image():
    build = WORK_DIR / ".esphome" / "build" / "hp1010-printserver" / "build"
    for name in ("firmware.factory.bin", "firmware.bin", "firmware.ota.bin"):
        candidate = build / name
        if candidate.is_file():
            return candidate
    return None


def esphome_version():
    if _VERSION_CACHE["value"] is not None:
        return _VERSION_CACHE["value"]
    value = "unknown"
    try:
        proc = subprocess.run([ESPHOME_BIN, "version"], capture_output=True, text=True, timeout=60)
        text = (proc.stdout or proc.stderr or "").strip()
        match = re.search(r"Version:\s*(\S+)", text)
        value = match.group(1) if match else (text.splitlines()[-1] if text else "unknown")
    except Exception as exc:  # noqa: BLE001
        value = f"error: {exc}"
    _VERSION_CACHE["value"] = value
    return value


def public_settings(settings):
    return {
        "wifi_ssid": settings["wifi_ssid"],
        "wifi_password": settings["wifi_password"],
        "ap_ssid": settings["ap_ssid"],
        "ap_password": settings["ap_password"],
        "api_key": settings["api_key"],
        "device_port": settings["device_port"],
    }


def api_status():
    image = compiled_image()
    return {
        "app_version": APP_VERSION,
        "esphome_version": esphome_version(),
        "esphome_bin": ESPHOME_BIN,
        "esptool_bin": ESPTOOL_BIN,
        "ports": list_ports(),
        "settings": public_settings(load_settings()),
        "job": RUNNER.status(),
        "project_dir": str(WORK_DIR),
        "compiled": image is not None,
        "compiled_path": str(image) if image else None,
        "prebuilt": FACTORY_BIN.is_file(),
    }


# --------------------------------------------------------------------------- actions
def do_build(payload):
    settings = load_settings()
    _apply_payload(settings, payload)
    save_settings(settings)
    write_secrets(settings)
    sync_project()
    write_secrets(settings)
    return RUNNER.start("build", [ESPHOME_BIN, "compile", CONFIG_NAME], WORK_DIR)


def do_flash(payload):
    settings = load_settings()
    _apply_payload(settings, payload)
    save_settings(settings)
    write_secrets(settings)
    port = settings.get("device_port") or ""
    if not port:
        return False, "no serial port selected"
    if not Path(port).exists() and not port.endswith((".local", ".localdomain")) and "." not in port:
        return False, f"serial port not found: {port}"
    return RUNNER.start("flash", [ESPHOME_BIN, "upload", CONFIG_NAME, "--device", port], WORK_DIR)


def do_quickflash(payload):
    settings = load_settings()
    _apply_payload(settings, payload)
    save_settings(settings)
    port = settings.get("device_port") or ""
    if not port:
        return False, "no serial port selected"
    if not FACTORY_BIN.is_file():
        return False, "bundled firmware.factory.bin is missing"
    cmd = [ESPTOOL_BIN, "--chip", "esp32s3", "--port", port, "--baud", "460800"]
    if payload.get("erase"):
        cmd.append("--erase-all")
    cmd += ["write_flash", "0x0", str(FACTORY_BIN)]
    return RUNNER.start("quickflash", cmd, WORK_DIR)


def _apply_payload(settings, payload):
    for key in ("wifi_ssid", "wifi_password", "ap_ssid", "ap_password", "api_key", "device_port"):
        if key in payload and payload[key] is not None:
            settings[key] = str(payload[key])


# --------------------------------------------------------------------------- http
def strip_ingress(path):
    match = re.match(r"^/(?:api/hassio_ingress|hassio/ingress)/[^/]+", path)
    if match:
        path = path[match.end():]
    return path or "/"


class Handler(BaseHTTPRequestHandler):
    server_version = "hp1010-installer"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence default request logging
        pass

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    # -- helpers -----------------------------------------------------------
    def _send(self, data, code=200, ctype="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path, ctype):
        if not path.is_file():
            return self._send({"error": "not found"}, 404)
        self._send(path.read_bytes(), 200, ctype)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # -- router ------------------------------------------------------------
    def _route(self, method):
        path = strip_ingress(self.path.split("?", 1)[0])
        query = {}
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    query[key] = value
        try:
            if path == "/api/health":
                return self._send({"ok": True})
            if path == "/api/status":
                return self._send(api_status())
            if path == "/api/ports":
                return self._send({"ports": list_ports()})
            if path == "/api/settings" and method == "GET":
                return self._send(public_settings(load_settings()))
            if path == "/api/settings" and method == "POST":
                settings = load_settings()
                _apply_payload(settings, self._body())
                save_settings(settings)
                write_secrets(settings)
                LOG.add("settings saved and project/secrets.yaml updated")
                return self._send({"ok": True})
            if path == "/api/key" and method == "POST":
                return self._send({"api_key": generate_key()})
            if path == "/api/build" and method == "POST":
                ok, err = do_build(self._body())
                return self._send({"ok": ok, "error": err}, 200 if ok else 409)
            if path == "/api/flash" and method == "POST":
                ok, err = do_flash(self._body())
                return self._send({"ok": ok, "error": err}, 200 if ok else 409)
            if path == "/api/quickflash" and method == "POST":
                ok, err = do_quickflash(self._body())
                return self._send({"ok": ok, "error": err}, 200 if ok else 409)
            if path == "/api/cancel" and method == "POST":
                return self._send({"ok": RUNNER.cancel()})
            if path == "/api/log":
                since = int(query.get("since", "0") or 0)
                lines, last = LOG.since(since)
                return self._send({"lines": lines, "last": last, "job": RUNNER.status()})
            # static
            if path in ("/", "/index.html"):
                return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            if path == "/style.css":
                return self._send_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
            return self._send({"error": "not found", "path": path}, 404)
        except Exception as exc:  # noqa: BLE001
            log(f"error handling {method} {path}: {exc}")
            traceback.print_exc()
            try:
                self._send({"error": str(exc)}, 500)
            except Exception:  # noqa: BLE001
                pass


def main():
    try:
        port = int(os.environ.get("INGRESS_PORT") or os.environ.get("PORT") or "8099")
        log(f"HP1010 Print Server Installer v{APP_VERSION}")
        log(f"data dir: {DATA_DIR}  work dir: {WORK_DIR}")
        log(f"esphome: {ESPHOME_BIN}   esptool: {ESPTOOL_BIN}")
        try:
            sync_project()
            settings = load_settings()
            write_secrets(settings)
            if not settings["api_key"]:
                settings["api_key"] = generate_key()
                save_settings(settings)
                write_secrets(settings)
                log("generated a new API encryption key")
            log(f"esphome version: {esphome_version()}")
            log(f"serial ports: {list_ports() or 'none'}")
        except Exception as exc:  # noqa: BLE001
            log(f"startup preparation failed: {exc}")
            traceback.print_exc()
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        log(f"web UI listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception:  # noqa: BLE001
        log("fatal startup error:")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
