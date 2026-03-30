"""Sync engine: SSE broker, streaming detection, sync state machine, file watcher."""

import json
import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from elevation import _write_elevated, _restart_elevated
from models import SyncLogEntry, SyncState, _LOG_MAX
from persistence import _load_settings, _load_config_path, _load_log, _save_log
from steam import get_recent_games, get_thumbnail, get_vdf_path
from sunshine import load_sunshine_config, build_sunshine_config

slog = logging.getLogger("sundeck.sync")

# ── Apollo session detection via log file parsing ─────────────────────────────

APP_RUNNING_PATTERNS = [
    "Launching app",
    "Session pausing for app",
    "Session resuming for app",
    "Treating the app as a detached command",
]
APP_STOPPED_PATTERNS = [
    "Process terminated",
    "All app processes have successfully exited",
    "Forcefully terminating the app",
    "App did not respond to a graceful termination request",
    "No graceful exit timeout was specified",
    "Terminating app",
    "Session already stopped",
]


def is_streaming_active() -> bool:
    """Return True if Apollo has a running app (even without an active client connection)."""
    log_dir = _load_config_path().parent
    log_files = [log_dir / "sunshine.log", log_dir / "sunshine.log.backup"]

    for log_file in log_files:
        if not log_file.exists():
            continue
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            slog.warning("is_streaming_active: failed to read %s: %s", log_file, e)
            continue

        for line in reversed(lines):
            for pattern in APP_RUNNING_PATTERNS:
                if pattern in line:
                    slog.debug("is_streaming_active: app running (matched %r in %s)", pattern, log_file.name)
                    return True
            for pattern in APP_STOPPED_PATTERNS:
                if pattern in line:
                    slog.debug("is_streaming_active: app stopped (matched %r in %s)", pattern, log_file.name)
                    return False

    slog.debug("is_streaming_active: no session events found in logs, assuming inactive")
    return False


# ── Server-Sent Events ────────────────────────────────────────────────────────

sse_subscribers: set[queue.SimpleQueue] = set()
sse_lock = threading.Lock()


def sse_push(event: str, data: str) -> None:
    msg = f"event: {event}\ndata: {data}\n\n"
    with sse_lock:
        dead = set()
        for q in sse_subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.add(q)
        sse_subscribers.difference_update(dead)


def sse_push_sync_status() -> None:
    sse_push("sync_status", json.dumps({
        "state": get_sync_state(),
        "games_version": get_games_version(),
    }))


# ── Sync state ────────────────────────────────────────────────────────────────

DEBOUNCE_SECONDS = 5.0

sync_state = SyncState.IDLE
sync_state_lock = threading.Lock()
sync_state_callbacks: set[Callable[[SyncState], None]] = set()


def register_sync_state_callback(cb: Callable[[SyncState], None]) -> None:
    sync_state_callbacks.add(cb)

games_version = 0
games_version_lock = threading.Lock()


def bump_games_version() -> None:
    global games_version
    with games_version_lock:
        games_version += 1
    sse_push_sync_status()


def get_games_version() -> int:
    with games_version_lock:
        return games_version


def set_sync_state(state: SyncState) -> None:
    global sync_state
    with sync_state_lock:
        sync_state = state
    sse_push_sync_status()
    for cb in sync_state_callbacks:
        cb(state)


def get_sync_state() -> SyncState:
    with sync_state_lock:
        return sync_state


# ── Activity log ──────────────────────────────────────────────────────────────

sync_log: list[SyncLogEntry] = _load_log()
sync_log_lock = threading.Lock()


def append_log(kind: str, success: bool, message: str, detail: str = "") -> None:
    with sync_log_lock:
        sync_log.append(SyncLogEntry(timestamp=time.time(), kind=kind, success=success, message=message, detail=detail))
        del sync_log[:-_LOG_MAX]
        _save_log(sync_log)
    sse_push("log_updated", "{}")


# ── Sync logic ────────────────────────────────────────────────────────────────

def do_auto_sync() -> bool:
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
    config = build_sunshine_config(existing, synced_games, desktop_position=settings.desktop_position)
    if config.model_dump() == existing.model_dump():
        return False
    set_sync_state(SyncState.SYNCING)
    bump_games_version()
    _write_elevated(config_path, config.model_dump_json(by_alias=True, indent=4))
    _restart_elevated()
    return True


def try_auto_sync() -> None:
    """Attempt an auto-sync if enabled; defer while streaming."""
    try:
        settings = _load_settings()
        if not settings.auto_sync:
            set_sync_state(SyncState.IDLE)
            return
        if settings.config_path is None:
            set_sync_state(SyncState.IDLE)
            return
        if is_streaming_active():
            schedule_sync()
            return
    except Exception:
        set_sync_state(SyncState.IDLE)
        return
    try:
        synced = do_auto_sync()
        if synced:
            append_log("auto", True, "Synced games")
    except Exception as exc:
        append_log("auto", False, str(exc).splitlines()[0] or "Auto-sync failed", detail=traceback.format_exc())
    finally:
        set_sync_state(SyncState.IDLE)


class Debouncer:
    """Cancels any pending timer and schedules *fn* to run after *delay* seconds."""

    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def schedule(self, fn: Callable[[], None], delay: float) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            t = threading.Timer(delay, fn)
            t.daemon = True
            t.start()
            self._timer = t


sync_debouncer = Debouncer()


def schedule_sync() -> None:
    """Schedule a debounced auto-sync attempt."""
    set_sync_state(SyncState.PENDING)
    sync_debouncer.schedule(try_auto_sync, DEBOUNCE_SECONDS)


class SyncEventHandler(FileSystemEventHandler):
    """Triggers a debounced auto-sync when watched files change."""

    def __init__(self, filenames: set[str]) -> None:
        self._filenames = {f.lower() for f in filenames}

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        if Path(str(event.src_path)).name.lower() in self._filenames:
            schedule_sync()


def start_watchers() -> None:
    """Start a watchdog observer for Steam's localconfig.vdf."""
    vdf = get_vdf_path()
    if vdf is None:
        return

    observer = Observer()
    observer.schedule(
        SyncEventHandler({vdf.name}),
        str(vdf.parent),
        recursive=False,
    )
    observer.daemon = True
    observer.start()
    set_sync_state(SyncState.PENDING)
    sync_debouncer.schedule(try_auto_sync, 0)
