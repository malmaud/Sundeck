"""Flask server for the SteamLaunch UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""

import base64
import ctypes
import json
import logging
import logging.handlers
import os
import queue
import traceback
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from absl import app as absl_app, flags
from flask import Flask, Response, jsonify, request, send_from_directory
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from steam import get_recent_games, get_thumbnail, get_vdf_path
from sunshine import (
    _detect_streaming_service,
    build_sunshine_config,
    get_managed_apps,
    load_sunshine_config,
)

flags.DEFINE_integer("port", 5000, "Port to listen on.")
flags.DEFINE_boolean("dev", False, "Enable dev mode: Werkzeug reloader, no tray icon.")

_PYTHON_DIR = Path(__file__).parent
if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle; data files are under sys._MEIPASS,
    # but thumbnails must be writable so store them next to the executable.
    _UI_DIR = Path(sys._MEIPASS) / "ui"  # type: ignore[attr-defined]
    _IMAGES_DIR = Path(sys._MEIPASS) / "images"  # type: ignore[attr-defined]
    _THUMBNAIL_DIR = Path(sys.executable).parent / "thumbnails"
    _SETTINGS_FILE = Path(sys.executable).parent / "settings.json"
    _LOGS_DIR = Path(sys.executable).parent / "logs"
else:
    _UI_DIR = _PYTHON_DIR.parent / "ui"
    _IMAGES_DIR = _PYTHON_DIR.parent / "images"
    _THUMBNAIL_DIR = _PYTHON_DIR / "thumbnails"
    _SETTINGS_FILE = _PYTHON_DIR / "settings.json"
    _LOGS_DIR = _PYTHON_DIR.parent / "logs"

_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOGS_DIR / "sync_log.json"
_SERVER_LOG_FILE = _LOGS_DIR / "server_log.txt"

_server_log_handler = logging.handlers.RotatingFileHandler(
    _SERVER_LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_server_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_slog = logging.getLogger("steamlaunch.server")
_slog.addHandler(_server_log_handler)
_slog.setLevel(logging.DEBUG)

_KNOWN_CONFIG_PATHS = [
    r"C:\Program Files\Apollo\config\apps.json",
    r"C:\Program Files\Sunshine\config\apps.json",
]
_DEFAULT_CONFIG_PATH = r"C:\Program Files\Apollo\config\apps.json"


class Settings(BaseModel):
    config_path: str | None = None
    excluded_games: list[int] = Field(default_factory=list)
    included_games: list[int] = Field(default_factory=list)
    show_debug: bool = False
    count: int = 10
    auto_sync: bool = True


class SettingsPatch(BaseModel):
    config_path: str | None = None
    excluded_games: list[int] | None = None
    included_games: list[int] | None = None
    show_debug: bool | None = None
    count: int | None = None
    auto_sync: bool | None = None

    @field_validator("config_path")
    @classmethod
    def _strip_config_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("config_path cannot be empty")
        return v

    @field_validator("count")
    @classmethod
    def _clamp_count(cls, v: int | None) -> int | None:
        return max(1, v) if v is not None else None


class SyncLogEntry(BaseModel):
    timestamp: float
    kind: str   # "manual" | "auto"
    success: bool
    message: str
    detail: str = ""


_log_adapter = TypeAdapter(list[SyncLogEntry])
_LOG_MAX = 100


def _load_log() -> list[SyncLogEntry]:
    if _LOG_FILE.exists():
        try:
            return _log_adapter.validate_json(_LOG_FILE.read_bytes())
        except Exception:
            pass
    return []


def _save_log(entries: list[SyncLogEntry]) -> None:
    try:
        _LOG_FILE.write_bytes(_log_adapter.dump_json(entries, indent=2))
    except Exception:
        pass


_sync_log: list[SyncLogEntry] = _load_log()
_sync_log_lock = threading.Lock()


def _append_log(kind: str, success: bool, message: str, detail: str = "") -> None:
    with _sync_log_lock:
        _sync_log.append(SyncLogEntry(timestamp=time.time(), kind=kind, success=success, message=message, detail=detail))
        del _sync_log[:-_LOG_MAX]
        _save_log(_sync_log)
    _sse_push("log_updated", "{}")


def _load_settings() -> Settings:
    if _SETTINGS_FILE.exists():
        try:
            raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            # Migrate from old unchecked_games → excluded_games
            if "unchecked_games" in raw:
                if "excluded_games" not in raw:
                    raw["excluded_games"] = raw["unchecked_games"]
                del raw["unchecked_games"]
            return Settings.model_validate(raw)
        except Exception:
            pass
    return Settings()


_settings_lock = threading.Lock()


def _patch_settings(**kwargs) -> None:
    with _settings_lock:
        settings = _load_settings()
        updated = settings.model_copy(update=kwargs)
        _SETTINGS_FILE.write_text(updated.model_dump_json(indent=2), encoding="utf-8")


def _load_config_path() -> Path:
    settings = _load_settings()
    if settings.config_path:
        return Path(settings.config_path)
    env = os.environ.get("APOLLO_CONFIG")
    if env:
        return Path(env)
    return Path(_DEFAULT_CONFIG_PATH)


app = Flask(__name__, static_folder=str(_UI_DIR), static_url_path="")


@app.route("/")
def index() -> Response:
    return send_from_directory(_UI_DIR, "index.html")


@app.route("/images/<path:filename>")
def images(filename: str) -> Response:
    return send_from_directory(_IMAGES_DIR, filename)


@app.route("/thumbnails/<path:filename>")
def thumbnails(filename: str) -> Response:
    stem = Path(filename).stem
    small_name = f"{stem}_small.jpg"
    small_path = _THUMBNAIL_DIR / small_name
    full_path = _THUMBNAIL_DIR / filename

    if not small_path.exists():
        # Ensure full-size image is downloaded
        if not full_path.exists():
            try:
                get_thumbnail(int(stem))
            except Exception:
                pass
        # Resize to a small JPEG for fast serving
        if full_path.exists():
            try:
                from PIL import Image
                img = Image.open(full_path).convert("RGB")
                img.thumbnail((300, 450), Image.Resampling.LANCZOS)
                img.save(small_path, format="JPEG", quality=80)
            except Exception:
                pass

    if small_path.exists():
        resp = send_from_directory(_THUMBNAIL_DIR, small_name)
        resp.cache_control.max_age = 31536000
        resp.cache_control.public = True
        return resp
    return Response(status=404)


def _small_thumbnail_uri(app_id: int) -> str:
    """Return a data URI if the small thumbnail is cached, else a URL for on-demand generation."""
    small = _THUMBNAIL_DIR / f"{app_id}_small.jpg"
    if not small.exists():
        full = _THUMBNAIL_DIR / f"{app_id}.png"
        if full.exists():
            try:
                from PIL import Image
                img = Image.open(full).convert("RGB")
                img.thumbnail((300, 450), Image.Resampling.LANCZOS)
                img.save(small, format="JPEG", quality=80)
            except Exception:
                return f"/thumbnails/{app_id}.png"
        else:
            return f"/thumbnails/{app_id}.png"
    data = base64.b64encode(small.read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


@app.route("/api/games")
def api_games() -> Response:
    games = get_recent_games(count=None, fetch_thumbnails=False)
    return jsonify(
        [
            {
                "app_id": g.app_id,
                "name": g.name,
                "thumbnail": _small_thumbnail_uri(g.app_id),
                "last_played": g.last_played,
            }
            for g in games
        ]
    )


@app.route("/api/settings", methods=["GET"])
def api_get_settings() -> Response:
    data = _load_settings().model_dump()
    data["config_path"] = str(_load_config_path())
    data["suggestions"] = [p for p in _KNOWN_CONFIG_PATHS if Path(p).exists()]
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def api_update_settings() -> Response | tuple[Response, int]:
    try:
        patch = SettingsPatch.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": e.errors()[0]["msg"]}), 400
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        return jsonify({"error": "no recognised fields"}), 400
    _patch_settings(**updates)
    # Sync-relevant fields changed → debounced auto-sync check.
    if {"excluded_games", "included_games", "count", "auto_sync"} & updates.keys():
        _schedule_sync()
    return jsonify({"status": "ok"})



@app.route("/api/config", methods=["GET"])
def api_get_config() -> Response:
    try:
        return jsonify(get_managed_apps(_load_config_path()))
    except Exception:
        return jsonify([])


@app.route("/api/sync", methods=["POST"])
def api_manual_sync() -> Response | tuple[Response, int]:
    _set_sync_state("syncing")
    try:
        synced = _do_auto_sync()
        if synced:
            _append_log("manual", True, "Synced games")
        else:
            _append_log("manual", True, "No changes, sync skipped")
        return jsonify({"status": "ok"})
    except Exception as exc:
        _append_log("manual", False, str(exc).splitlines()[0], detail=traceback.format_exc())
        return jsonify({"error": str(exc).splitlines()[0]}), 500
    finally:
        _set_sync_state("idle")


@app.route("/api/sync-status")
def api_sync_status() -> Response:
    return jsonify({"state": _get_sync_state(), "games_version": _get_games_version()})


@app.route("/api/log")
def api_get_log() -> Response:
    with _sync_log_lock:
        return jsonify([e.model_dump() for e in sorted(_sync_log, key=lambda e: e.timestamp, reverse=True)])


def _encode_command(cmd: str) -> str:
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_elevated(inner_cmd: str) -> None:
    if _is_admin():
        # Already elevated — run directly, no UAC prompt or extra window.
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", inner_cmd],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            capture_output=True, text=True,
        )
    else:
        encoded = _encode_command(inner_cmd)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
                f"'-NoProfile -NonInteractive -EncodedCommand {encoded}'",
            ],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Elevated command failed.\n{stderr}" if stderr else "Elevated command failed or was cancelled.")


def _write_elevated(target_path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    inner_cmd = (
        f"Copy-Item -LiteralPath '{tmp_path}' -Destination '{target_path}' -Force; "
        f"Remove-Item -LiteralPath '{tmp_path}'"
    )
    _run_elevated(inner_cmd)


def _restart_elevated() -> None:
    service = _detect_streaming_service()
    _run_elevated(f"net stop {service}; net start {service}")


def _is_streaming_active() -> bool:
    """Return True if an active Sunshine/Apollo stream is detected.

    Apollo's HTTP /serverinfo (port 47989) always returns currentgame=0
    by design — only the HTTPS endpoint returns the real value, but that
    requires mutual TLS with a paired client certificate.

    Instead, we check whether Apollo's streaming UDP ports are bound.
    These ports (video/control/audio at base+9/+10/+11) are only opened
    when an active streaming session exists.
    """
    # Default base port 47989; streaming offsets from Apollo source (stream.h):
    #   VIDEO_STREAM_PORT = 9, CONTROL_PORT = 10, AUDIO_STREAM_PORT = 11
    streaming_ports = {47998, 47999, 48000}
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "UDP"],
            text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "UDP":
                try:
                    port = int(parts[1].rsplit(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                if port in streaming_ports:
                    _slog.debug("_is_streaming_active: UDP port %d bound, streaming active", port)
                    return True
        _slog.debug("_is_streaming_active: no streaming UDP ports bound, inactive")
        return False
    except Exception as e:
        _slog.warning("_is_streaming_active: check failed (%s), assuming inactive", e)
        return False


def _do_auto_sync() -> bool:
    """Run an auto-sync.  Return True if config was written + service restarted."""
    settings = _load_settings()
    excluded = set(settings.excluded_games)
    included = set(settings.included_games)
    all_games = get_recent_games(count=None, fetch_thumbnails=False)

    # Top N non-excluded games
    sync_ids: set[int] = set()
    n = 0
    for g in all_games:
        if g.app_id in excluded:
            continue
        if n < settings.count:
            sync_ids.add(g.app_id)
            n += 1
    # Force-included games (that aren't excluded)
    sync_ids.update(aid for aid in included if aid not in excluded)

    synced_games = [g for g in all_games if g.app_id in sync_ids]
    if not synced_games:
        return False

    # Fetch thumbnails only for the synced subset
    for g in synced_games:
        g.thumbnail = get_thumbnail(g.app_id)

    config_path = _load_config_path()
    existing = load_sunshine_config(config_path)
    config = build_sunshine_config(existing, synced_games)
    if config.model_dump() == existing.model_dump():
        return False
    _set_sync_state("syncing")
    _bump_games_version()
    _write_elevated(config_path, config.model_dump_json(by_alias=True, indent=4))
    _restart_elevated()
    return True


_DEBOUNCE_SECONDS = 5.0

# "idle" | "pending" | "syncing"
_sync_state = "idle"
_sync_state_lock = threading.Lock()

_games_version = 0
_games_version_lock = threading.Lock()


def _bump_games_version() -> None:
    global _games_version
    with _games_version_lock:
        _games_version += 1
    _sse_push_sync_status()


def _get_games_version() -> int:
    with _games_version_lock:
        return _games_version


def _set_sync_state(state: str) -> None:
    global _sync_state
    with _sync_state_lock:
        _sync_state = state
    _sse_push_sync_status()


def _get_sync_state() -> str:
    with _sync_state_lock:
        return _sync_state


# ── Server-Sent Events ────────────────────────────────────────────────────────

_sse_subscribers: set[queue.SimpleQueue] = set()
_sse_lock = threading.Lock()


def _sse_push(event: str, data: str) -> None:
    msg = f"event: {event}\ndata: {data}\n\n"
    with _sse_lock:
        dead = set()
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.add(q)
        _sse_subscribers.difference_update(dead)


def _sse_push_sync_status() -> None:
    _sse_push("sync_status", json.dumps({
        "state": _get_sync_state(),
        "games_version": _get_games_version(),
    }))


@app.route("/api/events")
def api_events() -> Response:
    def stream():
        q: queue.SimpleQueue = queue.SimpleQueue()
        with _sse_lock:
            _sse_subscribers.add(q)
        try:
            yield f"event: sync_status\ndata: {json.dumps({'state': _get_sync_state(), 'games_version': _get_games_version()})}\n\n"
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                _sse_subscribers.discard(q)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _try_auto_sync() -> None:
    """Attempt an auto-sync if enabled; defer while streaming."""
    try:
        if not _load_settings().auto_sync:
            _set_sync_state("idle")
            return
        if _is_streaming_active():
            # Re-check after a short delay once the stream ends.
            _schedule_sync(_DEBOUNCE_SECONDS)
            return
    except Exception:
        _set_sync_state("idle")
        return
    try:
        synced = _do_auto_sync()
        if synced:
            _append_log("auto", True, "Synced games")
    except Exception as exc:
        _append_log("auto", False, str(exc).splitlines()[0] or "Auto-sync failed", detail=traceback.format_exc())
    finally:
        _set_sync_state("idle")


_sync_timer: threading.Timer | None = None
_sync_timer_lock = threading.Lock()


def _schedule_sync(delay: float = _DEBOUNCE_SECONDS) -> None:
    """Schedule a debounced auto-sync attempt *delay* seconds from now."""
    global _sync_timer
    _set_sync_state("pending")
    with _sync_timer_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(delay, _try_auto_sync)
        _sync_timer.daemon = True
        _sync_timer.start()


class _SyncEventHandler(FileSystemEventHandler):
    """Triggers a debounced auto-sync when watched files change."""

    def __init__(self, filenames: set[str]) -> None:
        self._filenames = {f.lower() for f in filenames}

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        if Path(str(event.src_path)).name.lower() in self._filenames:
            _schedule_sync()


def _start_watchers() -> None:
    """Start a watchdog observer for Steam's localconfig.vdf."""
    vdf = get_vdf_path()
    if vdf is None:
        return

    observer = Observer()
    observer.schedule(
        _SyncEventHandler({vdf.name}),
        str(vdf.parent),
        recursive=False,
    )
    observer.daemon = True
    observer.start()
    _schedule_sync(delay=0)


def _check_port(port: int) -> None:
    """Exit with a helpful error if another process is already using *port*."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return
    except OSError:
        pass

    # Port is taken – try to identify the culprit.
    owner = f"(unknown process)"
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        pattern = re.compile(rf":{port}\b")
        for line in out.splitlines():
            if pattern.search(line) and "LISTENING" in line:
                pid = line.strip().rsplit(None, 1)[-1]
                try:
                    tl = subprocess.check_output(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        text=True,
                        creationflags=0x08000000,
                    )
                    name = tl.strip().split(",")[0].strip('"')
                    owner = f"{name} (PID {pid})"
                except Exception:
                    owner = f"PID {pid}"
                break
    except Exception:
        pass

    # Extract numeric PID for the kill hint if we found one.
    pid_match = re.search(r"PID (\d+)", owner)
    kill_hint = (
        f"  Kill it with:  taskkill /PID {pid_match.group(1)} /F" if pid_match else ""
    )
    print(
        f"ERROR: Port {port} is already in use by {owner}.{chr(10) + kill_hint if kill_hint else ''}",
        file=sys.stderr,
    )
    sys.exit(1)


_TRAY_COLORS = {
    "idle":    (100, 210, 100),  # green
    "syncing": ( 50, 160, 255),  # blue
}
_TRAY_TITLES = {
    "idle":    "SteamLaunch",
    "syncing": "SteamLaunch — Syncing…",
}
_TRAY_STATUS_TEXT = {
    "idle":    "Idle",
    "syncing": "Syncing…",
}


def _create_tray_image(color: tuple = (100, 210, 100)):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill=(30, 35, 50, 255))
    draw.polygon([(20, 16), (20, 48), (50, 32)], fill=(*color, 255))
    return img


def _run_tray(port: int) -> None:
    import pystray

    def _status_text(_item):
        return _TRAY_STATUS_TEXT.get(_get_sync_state(), "…")

    menu = pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Open SteamLaunch",
            lambda _icon, _item: webbrowser.open(f"http://localhost:{port}"),
            default=True,
        ),
        pystray.MenuItem(
            "Sync Now",
            lambda _icon, _item: threading.Thread(target=_try_auto_sync, daemon=True).start(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda icon, _item: icon.stop()),
    )
    icon = pystray.Icon("SteamLaunch", _create_tray_image(), "SteamLaunch", menu)

    def _watch_state():
        last = None
        while True:
            state = _get_sync_state()
            if state != last:
                icon.icon = _create_tray_image(_TRAY_COLORS.get(state, _TRAY_COLORS["idle"]))
                icon.title = _TRAY_TITLES.get(state, "SteamLaunch")
                last = state
            time.sleep(0.5)

    threading.Thread(target=_watch_state, daemon=True).start()
    threading.Thread(target=icon.run, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        icon.stop()


def _main(_argv):
    del _argv
    port = flags.FLAGS.port
    if flags.FLAGS.dev:
        # Dev mode: Werkzeug reloader active, no tray icon.
        if not os.environ.get("WERKZEUG_RUN_MAIN"):
            _check_port(port)
            webbrowser.open(f"http://localhost:{port}")
        else:
            _start_watchers()
        app.run(port=port, debug=True, threaded=True)
    else:
        _check_port(port)
        flask_thread = threading.Thread(
            target=lambda: app.run(port=port, debug=False, threaded=True, use_reloader=False),
            daemon=True,
        )
        flask_thread.start()
        _start_watchers()
        webbrowser.open(f"http://localhost:{port}")
        _run_tray(port)
        os._exit(0)


if __name__ == "__main__":
    absl_app.run(_main)
