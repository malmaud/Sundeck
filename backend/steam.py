import ctypes
import ctypes.wintypes
import json
import logging
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import winreg
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

_ADVAPI32 = ctypes.windll.advapi32
_KERNEL32 = ctypes.windll.kernel32
_REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_INFINITE = 0xFFFFFFFF
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_SUCCESS = 0
# WaitForSingleObject returns a DWORD; use unsigned so 0xFFFFFFFF == _WAIT_FAILED works.
_KERNEL32.WaitForSingleObject.restype = ctypes.wintypes.DWORD

from PIL import Image


@dataclass
class SteamGame:
    app_id: int
    name: str
    thumbnail: str
    last_played: int = 0


_STEAM_HEADER_URL = (
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
)
_STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&filters=basic"
_CACHE_BASE = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).parent
)
_THUMBNAIL_CACHE_DIR = _CACHE_BASE / "thumbnails"
_NAME_CACHE_FILE = _CACHE_BASE / "name_cache.json"


def _load_name_cache() -> dict[str, str]:
    try:
        return json.loads(_NAME_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_name_cache(cache: dict[str, str]) -> None:
    try:
        _NAME_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def _fetch_name_from_steam(app_id: str) -> str | None:
    """Fetch the name for a single app from the Steam store API."""
    try:
        url = _STEAM_APPDETAILS_URL.format(app_id=app_id)
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        entry = data.get(app_id, {})
        if entry.get("success") and entry.get("data"):
            return entry["data"].get("name")
    except Exception:
        pass
    return None


def get_vdf_path() -> Path | None:
    """Return the most-recently-modified localconfig.vdf, or None."""
    steam_root = (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    )
    paths = list(steam_root.glob(r"userdata\*\config\localconfig.vdf"))
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def get_recent_games(count: int | None = 10, only_ids: set[int] | None = None, fetch_thumbnails: bool = True) -> list[SteamGame]:
    """Return the most recently played Steam games from localconfig.vdf."""
    vdf_path = get_vdf_path()
    if vdf_path is None:
        return []

    # Collect names: persistent Steam API cache, then registry (both best-effort)
    names: dict[str, str] = _load_name_cache()
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
                            names[subkey_name] = str(name)
                        except OSError:
                            pass
                except OSError:
                    break
    except OSError:
        pass

    # Parse LastPlayed timestamps and names from localconfig.vdf
    results: list[tuple[int, int, str]] = []
    in_apps = False
    current_app_id = None
    current_last_played = 0
    current_vdf_name: str | None = None
    depth = 0
    apps_depth = -1

    def _flush():
        if current_app_id and current_last_played:
            aid = current_app_id
            name = current_vdf_name or names.get(aid, aid)
            results.append((current_last_played, int(aid), name))

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
                        _flush()
                        in_apps = False
                        current_app_id = None
                        current_last_played = 0
                        current_vdf_name = None
                    elif depth == apps_depth + 1:
                        _flush()
                        current_app_id = None
                        current_last_played = 0
                        current_vdf_name = None
                elif current_app_id is None:
                    m = re.match(r'^"(\d+)"$', stripped)
                    if m:
                        current_app_id = m.group(1)
                else:
                    m = re.match(r'^"LastPlayed"\s+"(\d+)"$', stripped)
                    if m:
                        current_last_played = int(m.group(1))
                        continue
                    m = re.match(r'^"name"\s+"(.+)"$', stripped)
                    if m:
                        current_vdf_name = m.group(1)

    results.sort(reverse=True)
    if only_ids is not None:
        results = [r for r in results if r[1] in only_ids]
    top = results[:count] if count is not None else results
    if not top:
        return []

    # Fetch names from Steam API for any game still identified only by its app_id string.
    name_cache = names  # already loaded above
    missing = [r for r in top if r[2] == str(r[1])]
    if missing:
        def _resolve_name(entry: tuple) -> None:
            _, app_id, _ = entry
            key = str(app_id)
            if key not in name_cache:
                fetched = _fetch_name_from_steam(key)
                if fetched:
                    name_cache[key] = fetched

        with ThreadPoolExecutor(max_workers=min(len(missing), 10)) as executor:
            list(executor.map(_resolve_name, missing))
        _save_name_cache(name_cache)
        top = [
            (lp, app_id, name_cache.get(str(app_id), name))
            for lp, app_id, name in top
        ]

    top = [(lp, app_id, name) for lp, app_id, name in top if name != str(app_id)]
    if not top:
        return []

    if fetch_thumbnails:
        with ThreadPoolExecutor(max_workers=min(len(top), 10)) as executor:
            thumbnails = list(executor.map(lambda r: get_thumbnail(r[1]), top))
        return [
            SteamGame(app_id=app_id, name=name, thumbnail=thumbnail, last_played=last_played)
            for (last_played, app_id, name), thumbnail in zip(top, thumbnails)
        ]
    return [
        SteamGame(app_id=app_id, name=name, thumbnail="", last_played=last_played)
        for last_played, app_id, name in top
    ]


def get_thumbnail(app_id: int) -> str:
    """Download the Steam header image for app_id if not cached; return local PNG path or empty string."""
    _THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _THUMBNAIL_CACHE_DIR / f"{app_id}.png"
    if not path.exists():
        url = _STEAM_HEADER_URL.format(app_id=app_id)
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                urllib.request.urlretrieve(url, tmp.name)
                img = Image.open(tmp.name)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                img.save(path, format="PNG")
        except urllib.error.HTTPError:
            return ""
    return str(path)


def _open_steam_key() -> winreg.HKEYType:
    """Open the Steam registry key for notification + query access.

    Tries HKCU first (normal user context), then scans HKEY_USERS
    (SYSTEM context, e.g. when launched from a Sunshine service).
    """
    access = winreg.KEY_NOTIFY | winreg.KEY_QUERY_VALUE
    try:
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", access=access)
    except OSError as e:
        _log.debug("_open_steam_key: HKCU failed (%s), scanning HKEY_USERS", e)
    with winreg.OpenKey(winreg.HKEY_USERS, "") as users_key:
        i = 0
        while True:
            try:
                sid = winreg.EnumKey(users_key, i)
            except OSError:
                break
            i += 1
            try:
                return winreg.OpenKey(
                    winreg.HKEY_USERS,
                    rf"{sid}\Software\Valve\Steam",
                    access=access,
                )
            except OSError:
                pass
    raise RuntimeError("Steam registry key not found in any user hive")


def _wait_registry_change(key: winreg.HKEYType, timeout_ms: int) -> None:
    """Block until a value in *key* changes, or *timeout_ms* milliseconds elapse."""
    # CreateEventW(lpEventAttributes, bManualReset, bInitialState, lpName)
    #   lpEventAttributes=None  no inherited security, default ACL
    #   bManualReset=False      auto-reset: event clears itself after WaitForSingleObject returns
    #   bInitialState=False     start non-signalled
    #   lpName=None             unnamed event, no cross-process sharing needed
    event = _KERNEL32.CreateEventW(None, False, False, None)
    if not event:
        raise ctypes.WinError()
    try:
        # RegNotifyChangeKeyValue(hKey, bWatchSubtree, dwNotifyFilter, hEvent, fAsynchronous)
        #   bWatchSubtree=False             only watch this key, not its children
        #   dwNotifyFilter=LAST_SET         fire when any value in the key is set/deleted
        #   hEvent                          event to signal instead of blocking the thread
        #   fAsynchronous=True              return immediately; signal the event later on change
        ret = _ADVAPI32.RegNotifyChangeKeyValue(
            int(key),
            False,
            _REG_NOTIFY_CHANGE_LAST_SET,
            event,
            True,
        )
        if ret != _ERROR_SUCCESS:
            raise ctypes.WinError(ret)
        result = _KERNEL32.WaitForSingleObject(event, timeout_ms)
        if result == _WAIT_FAILED:
            raise ctypes.WinError()
    finally:
        _KERNEL32.CloseHandle(event)


def get_running_app_id() -> int:
    # Try the current user's hive first (works when running in user context).
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
            _log.debug("get_running_app_id: HKCU -> %s", value)
            return value
    except OSError as e:
        _log.debug("get_running_app_id: HKCU failed (%s), scanning HKEY_USERS", e)
    # Fallback: scan all loaded user hives (works when running as SYSTEM,
    # e.g. when Sunshine launches cli.py from its service process).
    with winreg.OpenKey(winreg.HKEY_USERS, "") as users_key:
        i = 0
        while True:
            try:
                sid = winreg.EnumKey(users_key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(winreg.HKEY_USERS, rf"{sid}\Software\Valve\Steam") as key:
                    value, _ = winreg.QueryValueEx(key, "RunningAppID")
                    _log.debug("get_running_app_id: HKEY_USERS\\%s -> %s", sid, value)
                    return value
            except OSError:
                pass
    raise RuntimeError("Steam registry key not found in any user hive")


def launch_game(app_id: int) -> None:
    subprocess.Popen(["start", f"steam://rungameid/{app_id}"], shell=True)


def wait_for_game(app_id: int, launch_timeout: int, poll_interval: float) -> None:
    _log.info("wait_for_game: waiting for app_id=%s (timeout=%ss)", app_id, launch_timeout)
    key = _open_steam_key()
    with key:
        # Phase 1: wait for the game to appear, with a wall-clock timeout.
        deadline = time.monotonic() + launch_timeout
        while True:
            try:
                value, _ = winreg.QueryValueEx(key, "RunningAppID")
            except OSError:
                value = 0
            if value == app_id:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Game {app_id} did not start within {launch_timeout} seconds")
            _log.debug("wait_for_game: running=%s, remaining=%.0fs", value, remaining)
            _wait_registry_change(key, max(1, int(remaining * 1000)))

        _log.info("wait_for_game: game started, waiting for exit")

        # Phase 2: block until RunningAppID changes away from this game.
        while True:
            try:
                value, _ = winreg.QueryValueEx(key, "RunningAppID")
            except OSError:
                value = 0
            if value != app_id:
                break
            _wait_registry_change(key, _INFINITE)

    _log.info("wait_for_game: game exited")
