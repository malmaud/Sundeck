import glob
import json
import os
import re
import urllib.error
import urllib.request
import winreg
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass
class SteamGame:
    app_id: int
    name: str
    thumbnail: str


_SUNSHINE_CONFIG_DEFAULT = r"C:\Program Files\Sunshine\config\apps.json"
_SUNSHINE_CMD_MARKER = "launch.py"
_STEAM_HEADER_URL = (
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg"
)
_THUMBNAIL_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "thumbnails"
)
_LAUNCH_SCRIPT_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "launch.py"
)


def get_recent_games(count: int = 10) -> list[SteamGame]:
    """Return the most recently played Steam games from localconfig.vdf."""
    steam_root = (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)") + r"\Steam"
    )
    paths = glob.glob(steam_root + r"\userdata\*\config\localconfig.vdf")
    if not paths:
        return []
    vdf_path = max(paths, key=os.path.getmtime)

    # Collect names from registry (best-effort)
    names: dict[str, str] = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\Apps"
        ) as apps_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(apps_key, i)
                    i += 1
                    with winreg.OpenKey(apps_key, subkey_name) as app_key:
                        try:
                            name, _ = winreg.QueryValueEx(app_key, "Name")
                            names[subkey_name] = name
                        except OSError:
                            pass
                except OSError:
                    break
    except OSError:
        pass

    # Parse LastPlayed timestamps from localconfig.vdf
    results = []
    in_apps = False
    current_app_id = None
    depth = 0
    apps_depth = None

    with open(vdf_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not in_apps:
                if stripped == '"apps"':
                    in_apps = True
                    apps_depth = depth
                elif stripped == "{":
                    depth += 1
                elif stripped == "}":
                    depth -= 1
            else:
                if stripped == "{":
                    depth += 1
                elif stripped == "}":
                    depth -= 1
                    if depth == apps_depth:
                        in_apps = False
                        current_app_id = None
                elif current_app_id is None:
                    m = re.match(r'^"(\d+)"$', stripped)
                    if m:
                        current_app_id = m.group(1)
                else:
                    m = re.match(r'^"LastPlayed"\s+"(\d+)"$', stripped)
                    if m:
                        last_played = int(m.group(1))
                        if last_played:
                            name = names.get(current_app_id, current_app_id)
                            results.append((last_played, int(current_app_id), name))
                        current_app_id = None

    results.sort(reverse=True)
    top = results[:count]
    with ThreadPoolExecutor(max_workers=min(len(top), 10)) as executor:
        thumbnails = list(executor.map(lambda r: _get_thumbnail(r[1]), top))
    return [
        SteamGame(app_id=app_id, name=name, thumbnail=thumbnail)
        for (_, app_id, name), thumbnail in zip(top, thumbnails)
    ]


def _get_thumbnail(app_id: int) -> str:
    """Download the Steam header image for app_id if not cached; return local path or empty string."""
    os.makedirs(_THUMBNAIL_CACHE_DIR, exist_ok=True)
    path = os.path.join(_THUMBNAIL_CACHE_DIR, f"{app_id}.jpg")
    if not os.path.exists(path):
        url = _STEAM_HEADER_URL.format(app_id=app_id)
        try:
            urllib.request.urlretrieve(url, path)
        except urllib.error.HTTPError:
            return ""
    return path


def update_sunshine_config(
    config_path: str = _SUNSHINE_CONFIG_DEFAULT,
    launch_script: str = _LAUNCH_SCRIPT_DEFAULT,
) -> None:
    """Sync the Sunshine apps config with the most recently played Steam games.

    Entries previously written by this function (identified by _SUNSHINE_CMD_MARKER
    in their 'cmd') are replaced; all other entries are preserved.
    """
    games = get_recent_games()

    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"apps": []}

    kept = [
        a
        for a in config.get("apps", [])
        if _SUNSHINE_CMD_MARKER not in a.get("cmd", "")
    ]
    new_entries = [
        {
            "name": game.name,
            "cmd": f'uv run --directory "{os.path.dirname(launch_script)}" "{launch_script}" --app_id={game.app_id}',
            "image-path": game.thumbnail,
        }
        for game in games
    ]
    config["apps"] = kept + new_entries

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def get_running_app_id() -> int:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
        value, _ = winreg.QueryValueEx(key, "RunningAppID")
        return value


def launch_game(app_id: int) -> None:
    subprocess.Popen(["start", f"steam://rungameid/{app_id}"], shell=True)


def wait_for_game(app_id: int, launch_timeout: int, poll_interval: float) -> None:
    print(f"Waiting for game {app_id} to start...")
    elapsed = 0
    while get_running_app_id() != app_id:
        time.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed >= launch_timeout:
            raise TimeoutError(
                f"Game {app_id} did not start within {launch_timeout} seconds"
            )

    print("Game started, waiting for exit...")
    while get_running_app_id() == app_id:
        time.sleep(poll_interval)

    print("Game exited.")
