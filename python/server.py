"""Flask server for the SteamLaunch UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""

import base64
import ctypes
import os
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

from steam import get_recent_games
from sunshine import (
    _detect_streaming_service,
    build_sunshine_config,
    get_managed_apps,
    load_sunshine_config,
)

flags.DEFINE_integer("port", 5000, "Port to listen on.")

_PYTHON_DIR = Path(__file__).parent
if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle; data files are under sys._MEIPASS,
    # but thumbnails must be writable so store them next to the executable.
    _UI_DIR = Path(sys._MEIPASS) / "ui"  # type: ignore[attr-defined]
    _THUMBNAIL_DIR = Path(sys.executable).parent / "thumbnails"
    _SETTINGS_FILE = Path(sys.executable).parent / "settings.json"
    _LOG_FILE = Path(sys.executable).parent / "sync_log.json"
else:
    _UI_DIR = _PYTHON_DIR.parent / "ui"
    _THUMBNAIL_DIR = _PYTHON_DIR / "thumbnails"
    _SETTINGS_FILE = _PYTHON_DIR / "settings.json"
    _LOG_FILE = _PYTHON_DIR / "sync_log.json"

_KNOWN_CONFIG_PATHS = [
    r"C:\Program Files\Apollo\config\apps.json",
    r"C:\Program Files\Sunshine\config\apps.json",
]
_DEFAULT_CONFIG_PATH = r"C:\Program Files\Apollo\config\apps.json"


class Settings(BaseModel):
    config_path: str | None = None
    unchecked_games: list[int] = Field(default_factory=list)
    show_debug: bool = False
    count: int = 10
    auto_sync_hours: float = 0.0
    last_sync_time: float = 0.0


class SettingsPatch(BaseModel):
    config_path: str | None = None
    unchecked_games: list[int] | None = None
    show_debug: bool | None = None
    count: int | None = None
    auto_sync_hours: float | None = None

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

    @field_validator("auto_sync_hours")
    @classmethod
    def _clamp_auto_sync(cls, v: float | None) -> float | None:
        return max(0.0, v) if v is not None else None


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


def _load_settings() -> Settings:
    if _SETTINGS_FILE.exists():
        try:
            return Settings.model_validate_json(_SETTINGS_FILE.read_text(encoding="utf-8"))
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


@app.route("/thumbnails/<path:filename>")
def thumbnails(filename: str) -> Response:
    return send_from_directory(_THUMBNAIL_DIR, filename)


def _thumbnail_data_uri(path: str) -> str:
    try:
        from PIL import Image
        p = Path(path)
        small = p.parent / f"{p.stem}_small.jpg"
        if not small.exists():
            img = Image.open(p).convert("RGB")
            img.thumbnail((300, 450), Image.Resampling.LANCZOS)
            img.save(small, format="JPEG", quality=85)
        with open(small, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:image/jpeg;base64,{data}"
    except Exception:
        return ""


@app.route("/api/games")
def api_games() -> Response:
    count = request.args.get("count", 10, type=int)
    games = get_recent_games(count)
    return jsonify(
        [
            {
                "app_id": g.app_id,
                "name": g.name,
                "thumbnail": _thumbnail_data_uri(g.thumbnail) if g.thumbnail else "",
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
    return jsonify({"status": "ok"})



@app.route("/api/config", methods=["GET"])
def api_get_config() -> Response:
    try:
        return jsonify(get_managed_apps(_load_config_path()))
    except Exception:
        return jsonify([])


@app.route("/api/config", methods=["POST"])
def api_update_config() -> Response | tuple[Response, int]:
    body = request.get_json(silent=True) or {}
    app_ids = body.get("app_ids", [])
    if not app_ids:
        return jsonify({"error": "No app IDs provided"}), 400

    ids = set(app_ids)
    games = get_recent_games(len(ids), only_ids=ids)
    order = {aid: i for i, aid in enumerate(app_ids)}
    games.sort(key=lambda g: order.get(g.app_id, 0))

    apollo_config = _load_config_path()
    existing = load_sunshine_config(apollo_config)
    config = build_sunshine_config(existing, games)
    config_json = config.model_dump_json(by_alias=True, indent=4)

    try:
        _write_elevated(apollo_config, config_json)
        _restart_elevated()
    except Exception as exc:
        _append_log("manual", False, str(exc).splitlines()[0], detail=traceback.format_exc())
        return jsonify({"error": str(exc).splitlines()[0]}), 500
    _append_log("manual", True, f"Synced {len(app_ids)} games")

    return jsonify({"status": "ok", "count": len(app_ids)})


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
    """Return True if an active Sunshine/Apollo stream is detected via TCP connections."""
    try:
        out = subprocess.check_output(
            ["netstat", "-n"], text=True, creationflags=0x08000000
        )
        # These ports carry active stream traffic (RTSP control, video, audio, input)
        stream_ports = {":48010", ":47998", ":47999", ":48000"}
        for line in out.splitlines():
            if "ESTABLISHED" in line and any(p in line for p in stream_ports):
                return True
    except Exception:
        pass
    return False


def _do_auto_sync() -> None:
    settings = _load_settings()
    unchecked = set(settings.unchecked_games)
    games = get_recent_games(settings.count)
    checked_games = [g for g in games if g.app_id not in unchecked]
    if not checked_games:
        return
    config_path = _load_config_path()
    existing = load_sunshine_config(config_path)
    config = build_sunshine_config(existing, checked_games)
    _write_elevated(config_path, config.model_dump_json(by_alias=True, indent=4))
    _restart_elevated()


_sync_stop = threading.Event()


def _sync_worker() -> None:
    """Background thread: performs auto-sync on the configured interval."""
    while not _sync_stop.wait(timeout=60):
        try:
            settings = _load_settings()
            if settings.auto_sync_hours <= 0:
                continue
            if time.time() < settings.last_sync_time + settings.auto_sync_hours * 3600:
                continue
            if _is_streaming_active():
                continue
        except Exception:
            continue
        try:
            _do_auto_sync()
            _patch_settings(last_sync_time=time.time())
            _append_log("auto", True, "Synced games")
        except Exception as exc:
            _append_log("auto", False, str(exc).splitlines()[0] or "Auto-sync failed", detail=traceback.format_exc())


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


def _main(_argv):
    del _argv
    port = flags.FLAGS.port
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        _check_port(port)
        webbrowser.open(f"http://localhost:{port}")
    else:
        threading.Thread(target=_sync_worker, daemon=True).start()
    app.run(port=port, debug=True, threaded=True)


if __name__ == "__main__":
    absl_app.run(_main)
