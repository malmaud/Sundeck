"""Flask server for the SteamLaunch UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""

import base64
import json
import logging
import logging.handlers
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from absl import app as absl_app, flags
from flask import Flask, Response, jsonify, request, send_from_directory
from pydantic import ValidationError

from elevation import _write_elevated, _restart_elevated  # noqa: F401 (re-exported for tests)
from models import Settings, SettingsPatch, SyncLogEntry  # noqa: F401 (re-exported for tests)
from persistence import (
    _load_settings, _load_config_path, _patch_settings,
    _KNOWN_CONFIG_PATHS, _LOGS_DIR,
)
from steam import get_recent_games, get_thumbnail
from sunshine import get_managed_apps
from sync_engine import (
    _do_auto_sync, _is_streaming_active, _try_auto_sync,  # noqa: F401 (re-exported for tests)
    _SyncEventHandler, _schedule_sync, _set_sync_state, _get_sync_state,  # noqa: F401
    _get_games_version, _bump_games_version,  # noqa: F401
    _sync_log, _sync_log_lock,
    _sse_subscribers, _sse_lock, _sse_push, _sse_push_sync_status,  # noqa: F401
    _sync_timer, _sync_timer_lock,  # noqa: F401 (re-exported for tests)
    _append_log, _save_log,  # noqa: F401 (re-exported for tests)
    _start_watchers,
)
from tray import _run_tray

flags.DEFINE_integer("port", 5000, "Port to listen on.")
flags.DEFINE_boolean("dev", False, "Enable dev mode: Werkzeug reloader, no tray icon.")

_PYTHON_DIR = Path(__file__).parent
if getattr(sys, "frozen", False):
    _UI_DIR = Path(sys._MEIPASS) / "ui"  # type: ignore[attr-defined]
    _IMAGES_DIR = Path(sys._MEIPASS) / "images"  # type: ignore[attr-defined]
    _THUMBNAIL_DIR = Path(sys.executable).parent / "thumbnails"
else:
    _UI_DIR = _PYTHON_DIR.parent / "ui"
    _IMAGES_DIR = _PYTHON_DIR.parent / "images"
    _THUMBNAIL_DIR = _PYTHON_DIR / "thumbnails"

_SERVER_LOG_FILE = _LOGS_DIR / "server_log.txt"
_server_log_handler = logging.handlers.RotatingFileHandler(
    _SERVER_LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_server_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_slog = logging.getLogger("steamlaunch.server")
_slog.addHandler(_server_log_handler)
_slog.setLevel(logging.DEBUG)

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
        if not full_path.exists():
            try:
                get_thumbnail(int(stem))
            except Exception:
                pass
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
    settings = _load_settings()
    data = settings.model_dump()
    data["config_path"] = str(_load_config_path())
    data["needs_setup"] = settings.config_path is None
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
    if {"excluded_games", "included_games", "count", "auto_sync", "config_path"} & updates.keys():
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


def _check_port(port: int) -> None:
    """Exit with a helpful error if another process is already using *port*."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return
    except OSError:
        pass

    owner = "(unknown process)"
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

    pid_match = re.search(r"PID (\d+)", owner)
    kill_hint = (
        f"  Kill it with:  taskkill /PID {pid_match.group(1)} /F" if pid_match else ""
    )
    print(
        f"ERROR: Port {port} is already in use by {owner}.{chr(10) + kill_hint if kill_hint else ''}",
        file=sys.stderr,
    )
    sys.exit(1)


def _main(_argv):
    del _argv
    port = flags.FLAGS.port
    if flags.FLAGS.dev:
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
        _run_tray(port, _get_sync_state, _try_auto_sync)
        os._exit(0)


if __name__ == "__main__":
    absl_app.run(_main)
