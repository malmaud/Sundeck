import json
import os
import sys
import threading
from pathlib import Path

from models import Settings, SyncLogEntry, _log_adapter

_PYTHON_DIR = Path(__file__).parent
if getattr(sys, "frozen", False):
    _SETTINGS_FILE = Path(sys.executable).parent / "settings.json"
    _LOGS_DIR = Path(sys.executable).parent / "logs"
else:
    _SETTINGS_FILE = _PYTHON_DIR / "settings.json"
    _LOGS_DIR = _PYTHON_DIR.parent / "logs"

_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOGS_DIR / "sync_log.json"

_KNOWN_CONFIG_PATHS = [
    r"C:\Program Files\Apollo\config\apps.json",
    r"C:\Program Files\Sunshine\config\apps.json",
]
_DEFAULT_CONFIG_PATH = r"C:\Program Files\Apollo\config\apps.json"


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


def _load_settings() -> Settings:
    if _SETTINGS_FILE.exists():
        try:
            raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
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
