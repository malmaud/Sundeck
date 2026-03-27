"""Sync engine: SSE broker, streaming detection, sync state machine, file watcher."""

import json
import logging
import queue
import threading
import time
import traceback
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from elevation import _write_elevated, _restart_elevated
from models import SyncLogEntry, _LOG_MAX
from persistence import _load_settings, _load_config_path, _load_log, _save_log
from steam import get_recent_games, get_thumbnail, get_vdf_path
from sunshine import load_sunshine_config, build_sunshine_config

_slog = logging.getLogger("steamlaunch.sync")

# ── Apollo session detection via log file parsing ─────────────────────────────

_APP_RUNNING_PATTERNS = [
    "Launching app",
    "Session pausing for app",
    "Session resuming for app",
    "Treating the app as a detached command",
]
_APP_STOPPED_PATTERNS = [
    "Process terminated",
    "All app processes have successfully exited",
    "Forcefully terminating the app",
    "App did not respond to a graceful termination request",
    "No graceful exit timeout was specified",
    "Terminating app",
    "Session already stopped",
]


def _is_streaming_active() -> bool:
    """Return True if Apollo has a running app (even without an active client connection)."""
    log_dir = _load_config_path().parent
    log_files = [log_dir / "sunshine.log", log_dir / "sunshine.log.backup"]

    for log_file in log_files:
        if not log_file.exists():
            continue
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            _slog.warning("_is_streaming_active: failed to read %s: %s", log_file, e)
            continue

        for line in reversed(lines):
            for pattern in _APP_RUNNING_PATTERNS:
                if pattern in line:
                    _slog.debug("_is_streaming_active: app running (matched %r in %s)", pattern, log_file.name)
                    return True
            for pattern in _APP_STOPPED_PATTERNS:
                if pattern in line:
                    _slog.debug("_is_streaming_active: app stopped (matched %r in %s)", pattern, log_file.name)
                    return False

    _slog.debug("_is_streaming_active: no session events found in logs, assuming inactive")
    return False


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


# ── Sync state ────────────────────────────────────────────────────────────────

_DEBOUNCE_SECONDS = 5.0

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


# ── Activity log ──────────────────────────────────────────────────────────────

_sync_log: list[SyncLogEntry] = _load_log()
_sync_log_lock = threading.Lock()


def _append_log(kind: str, success: bool, message: str, detail: str = "") -> None:
    with _sync_log_lock:
        _sync_log.append(SyncLogEntry(timestamp=time.time(), kind=kind, success=success, message=message, detail=detail))
        del _sync_log[:-_LOG_MAX]
        _save_log(_sync_log)
    _sse_push("log_updated", "{}")


# ── Sync logic ────────────────────────────────────────────────────────────────

def _do_auto_sync() -> bool:
    """Run an auto-sync. Return True if config was written + service restarted."""
    settings = _load_settings()
    excluded = set(settings.excluded_games)
    included = set(settings.included_games)
    all_games = get_recent_games(count=None, fetch_thumbnails=False)

    sync_ids: set[int] = set()
    n = 0
    for g in all_games:
        if g.app_id in excluded:
            continue
        if n < settings.count:
            sync_ids.add(g.app_id)
            n += 1
    sync_ids.update(aid for aid in included if aid not in excluded)

    synced_games = [g for g in all_games if g.app_id in sync_ids]
    if not synced_games:
        return False

    for g in synced_games:
        g.thumbnail = get_thumbnail(g.app_id)

    config_path = _load_config_path()
    if not config_path.parent.exists():
        raise RuntimeError(
            f"Config path not found: {config_path} — open Settings to correct it."
        )
    existing = load_sunshine_config(config_path)
    config = build_sunshine_config(existing, synced_games)
    if config.model_dump() == existing.model_dump():
        return False
    _set_sync_state("syncing")
    _bump_games_version()
    _write_elevated(config_path, config.model_dump_json(by_alias=True, indent=4))
    _restart_elevated()
    return True


def _try_auto_sync() -> None:
    """Attempt an auto-sync if enabled; defer while streaming."""
    try:
        settings = _load_settings()
        if not settings.auto_sync:
            _set_sync_state("idle")
            return
        if settings.config_path is None:
            _set_sync_state("idle")
            return
        if _is_streaming_active():
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
