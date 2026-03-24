"""Flask server for the SteamLaunch UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""
import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from steam import get_recent_games
from sunshine import build_sunshine_config, get_managed_apps, load_sunshine_config

_PYTHON_DIR = Path(__file__).parent
if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle; data files are under sys._MEIPASS,
    # but thumbnails must be writable so store them next to the executable.
    _UI_DIR = Path(sys._MEIPASS) / "ui"  # type: ignore[attr-defined]
    _THUMBNAIL_DIR = Path(sys.executable).parent / "thumbnails"
    _SETTINGS_FILE = Path(sys.executable).parent / "settings.json"
else:
    _UI_DIR = _PYTHON_DIR.parent / "ui"
    _THUMBNAIL_DIR = _PYTHON_DIR / "thumbnails"
    _SETTINGS_FILE = _PYTHON_DIR / "settings.json"

_KNOWN_CONFIG_PATHS = [
    r"C:\Program Files\Apollo\config\apps.json",
    r"C:\Program Files\Sunshine\config\apps.json",
]
_DEFAULT_CONFIG_PATH = r"C:\Program Files\Apollo\config\apps.json"


def _load_config_path() -> Path:
    if _SETTINGS_FILE.exists():
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            if "config_path" in data:
                return Path(data["config_path"])
        except Exception:
            pass
    env = os.environ.get("APOLLO_CONFIG")
    if env:
        return Path(env)
    return Path(_DEFAULT_CONFIG_PATH)


def _save_config_path(path: Path) -> None:
    data: dict = {}
    if _SETTINGS_FILE.exists():
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["config_path"] = str(path)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

app = Flask(__name__, static_folder=str(_UI_DIR), static_url_path="")


@app.route("/")
def index() -> Response:
    return send_from_directory(_UI_DIR, "index.html")


@app.route("/thumbnails/<path:filename>")
def thumbnails(filename: str) -> Response:
    return send_from_directory(_THUMBNAIL_DIR, filename)


@app.route("/api/games")
def api_games() -> Response:
    count = request.args.get("count", 10, type=int)
    games = get_recent_games(count)
    return jsonify([
        {
            "app_id": g.app_id,
            "name": g.name,
            "thumbnail": f"/thumbnails/{Path(g.thumbnail).name}" if g.thumbnail else "",
            "last_played": g.last_played,
        }
        for g in games
    ])


@app.route("/api/settings", methods=["GET"])
def api_get_settings() -> Response:
    config_path = _load_config_path()
    suggestions = [p for p in _KNOWN_CONFIG_PATHS if Path(p).exists()]
    return jsonify({"config_path": str(config_path), "suggestions": suggestions})


@app.route("/api/settings", methods=["POST"])
def api_update_settings() -> Response | tuple[Response, int]:
    body = request.get_json(silent=True) or {}
    config_path = body.get("config_path", "").strip()
    if not config_path:
        return jsonify({"error": "config_path required"}), 400
    _save_config_path(Path(config_path))
    return jsonify({"status": "ok", "config_path": config_path})


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
    games = [g for g in get_recent_games(50) if g.app_id in ids]
    order = {aid: i for i, aid in enumerate(app_ids)}
    games.sort(key=lambda g: order.get(g.app_id, 0))

    apollo_config = _load_config_path()
    existing = load_sunshine_config(apollo_config)
    config = build_sunshine_config(existing, games)
    config_json = config.model_dump_json(by_alias=True, indent=4)

    _write_elevated(apollo_config, config_json)
    _restart_elevated()

    return jsonify({"status": "ok", "count": len(app_ids)})


def _encode_command(cmd: str) -> str:
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def _run_elevated(inner_cmd: str) -> None:
    encoded = _encode_command(inner_cmd)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Start-Process powershell -Verb RunAs -Wait -ArgumentList "
            f"'-NoProfile -NonInteractive -EncodedCommand {encoded}'",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("Elevated command failed or was cancelled.")


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
    _run_elevated("net stop ApolloService; net start ApolloService")


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
            ["netstat", "-ano"], text=True, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        pattern = re.compile(rf":{port}\b")
        for line in out.splitlines():
            if pattern.search(line) and "LISTENING" in line:
                pid = line.strip().rsplit(None, 1)[-1]
                try:
                    tl = subprocess.check_output(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        text=True, creationflags=0x08000000,
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
    kill_hint = f"  Kill it with:  taskkill /PID {pid_match.group(1)} /F" if pid_match else ""
    print(
        f"ERROR: Port {port} is already in use by {owner}.{chr(10) + kill_hint if kill_hint else ''}",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    _check_port(5000)
    webbrowser.open("http://localhost:5000")
    app.run(port=5000, debug=False)
