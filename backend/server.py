"""Flask server for the SunDeck UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""

import base64
import json
import logging
import logging.handlers
import os
import queue
import socket
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from absl import app as absl_app, flags
from flask import Flask, Response, jsonify, request, send_from_directory
from pydantic import ValidationError

from elevation import _write_elevated, _restart_elevated  # noqa: F401 (re-exported for tests)
from models import Settings, SettingsPatch, SyncLogEntry, SyncState  # noqa: F401 (re-exported for tests)
from persistence import (
    _load_settings, _load_config_path, _patch_settings,
    _KNOWN_CONFIG_PATHS, _LOGS_DIR,
)
from steam import get_recent_games, get_thumbnail
from sunshine import get_managed_apps, has_desktop_app
import sync_engine
from startup import get_run_at_startup, set_run_at_startup
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
    config_path = _load_config_path()
    data = settings.model_dump()
    data["config_path"] = str(config_path)
    data["needs_setup"] = settings.config_path is None
    data["suggestions"] = [p for p in _KNOWN_CONFIG_PATHS if Path(p).exists()]
    try:
        data["has_desktop_app"] = has_desktop_app(config_path)
    except Exception:
        data["has_desktop_app"] = False
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
    if "run_at_startup" in updates:
        set_run_at_startup(updates["run_at_startup"])
    if {"excluded_games", "included_games", "count", "auto_sync", "config_path", "desktop_position"} & updates.keys():
        sync_engine.schedule_sync()
    return jsonify({"status": "ok"})


@app.route("/api/config", methods=["GET"])
def api_get_config() -> Response:
    try:
        return jsonify(get_managed_apps(_load_config_path()))
    except Exception:
        return jsonify([])


@app.route("/api/sync", methods=["POST"])
def api_manual_sync() -> Response | tuple[Response, int]:
    sync_engine.set_sync_state(SyncState.SYNCING)
    try:
        synced = sync_engine.do_auto_sync()
        if synced:
            sync_engine.append_log("manual", True, "Synced games")
        else:
            sync_engine.append_log("manual", True, "No changes, sync skipped")
        return jsonify({"status": "ok"})
    except Exception as exc:
        sync_engine.append_log("manual", False, str(exc).splitlines()[0], detail=traceback.format_exc())
        return jsonify({"error": str(exc).splitlines()[0]}), 500
    finally:
        sync_engine.set_sync_state(SyncState.IDLE)


@app.route("/api/sync-status")
def api_sync_status() -> Response:
    return jsonify({"state": sync_engine.get_sync_state(), "games_version": sync_engine.get_games_version()})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown() -> Response:
    resp = jsonify({"status": "ok"})
    resp.call_on_close(lambda: os._exit(0))
    return resp


@app.route("/api/log")
def api_get_log() -> Response:
    with sync_engine.sync_log_lock:
        return jsonify([e.model_dump() for e in sorted(sync_engine.sync_log, key=lambda e: e.timestamp, reverse=True)])


@app.route("/api/events")
def api_events() -> Response:
    def stream():
        q: queue.SimpleQueue = queue.SimpleQueue()
        with sync_engine.sse_lock:
            sync_engine.sse_subscribers.add(q)
        try:
            yield f"event: sync_status\ndata: {json.dumps({'state': sync_engine.get_sync_state(), 'games_version': sync_engine.get_games_version()})}\n\n"
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with sync_engine.sse_lock:
                sync_engine.sse_subscribers.discard(q)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_port(preferred: int) -> int:
    """Return *preferred* if it's free.

    If *preferred* is taken by an existing SunDeck instance, open the browser
    to it and exit. Otherwise fall back to an OS-assigned free port.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", preferred))
        sock.close()
        return preferred
    except OSError:
        pass

    # Port is taken — check if it's already our app.
    import time
    import requests
    try:
        resp = requests.get(f"http://127.0.0.1:{preferred}/api/sync-status", timeout=1)
        if resp.status_code == 200:
            requests.post(f"http://127.0.0.1:{preferred}/api/shutdown", timeout=1)
            # Wait up to 5s for the port to free.
            for _ in range(50):
                time.sleep(0.1)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind(("127.0.0.1", preferred))
                    sock.close()
                    return preferred
                except OSError:
                    pass
    except Exception:
        pass

    # Taken by something else — fall back to any free port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _main(_argv):
    del _argv
    port = _resolve_port(flags.FLAGS.port)
    if flags.FLAGS.dev:
        if not os.environ.get("WERKZEUG_RUN_MAIN"):
            webbrowser.open(f"http://localhost:{port}")
        else:
            sync_engine.start_watchers()
        app.run(port=port, debug=True, threaded=True)
    else:
        # Re-apply startup registration in case the exe path changed (e.g. after an update).
        set_run_at_startup(_load_settings().run_at_startup)
        flask_thread = threading.Thread(
            target=lambda: app.run(port=port, debug=False, threaded=True, use_reloader=False),
            daemon=True,
        )
        flask_thread.start()
        sync_engine.start_watchers()
        webbrowser.open(f"http://localhost:{port}")
        _run_tray(port)
        os._exit(0)


if __name__ == "__main__":
    absl_app.run(_main)
