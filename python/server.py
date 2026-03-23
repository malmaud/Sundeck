"""Flask server for the SteamLaunch UI.

Run with:  uv run server.py
Then open:  http://localhost:5000
"""
import base64
import os
import subprocess
import tempfile
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from steam import get_recent_games
from sunshine import build_sunshine_config, get_managed_apps, load_sunshine_config

_PYTHON_DIR = Path(__file__).parent
_UI_DIR = _PYTHON_DIR.parent / "ui"
_THUMBNAIL_DIR = _PYTHON_DIR / "thumbnails"
_APOLLO_CONFIG = Path(
    os.environ.get("APOLLO_CONFIG", r"C:\Program Files\Apollo\config\apps.json")
)

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
        }
        for g in games
    ])


@app.route("/api/config", methods=["GET"])
def api_get_config() -> Response:
    try:
        return jsonify(get_managed_apps(_APOLLO_CONFIG))
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

    existing = load_sunshine_config(_APOLLO_CONFIG)
    config = build_sunshine_config(existing, games)
    config_json = config.model_dump_json(by_alias=True, indent=4)

    _write_elevated(_APOLLO_CONFIG, config_json)
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


if __name__ == "__main__":
    webbrowser.open("http://localhost:5000")
    app.run(port=5000, debug=False)
