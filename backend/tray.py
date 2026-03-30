"""System tray icon for SunDeck."""

import sys
import webbrowser
from pathlib import Path
from typing import Any

from PIL import Image
import pystray

from models import SyncState
from sync_engine import get_sync_state, register_sync_state_callback

if getattr(sys, "frozen", False):
    _IMAGES_DIR = Path(sys._MEIPASS) / "images"  # type: ignore[attr-defined]
else:
    _IMAGES_DIR = Path(__file__).parent.parent / "images"

_TRAY_TITLES = {
    SyncState.IDLE:    "SunDeck",
    SyncState.PENDING: "SunDeck — Sync pending…",
    SyncState.SYNCING: "SunDeck — Syncing…",
}
_TRAY_STATUS_TEXT = {
    SyncState.IDLE:    "Idle",
    SyncState.PENDING: "Sync pending…",
    SyncState.SYNCING: "Syncing…",
}


def _load_tray_image() -> Image.Image:
    return Image.open(_IMAGES_DIR / "favicon.png").convert("RGBA")


def _status_text(_item: Any) -> str:
    return _TRAY_STATUS_TEXT.get(get_sync_state(), _TRAY_STATUS_TEXT[SyncState.IDLE])


def _run_tray(port: int) -> None:
    menu = pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Open SunDeck",
            lambda _icon, _item: webbrowser.open(f"http://localhost:{port}"),
            default=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda icon, _item: icon.stop()),
    )
    icon = pystray.Icon("SunDeck", _load_tray_image(), "SunDeck", menu)
    register_sync_state_callback(lambda state: setattr(icon, "title", _TRAY_TITLES.get(state, _TRAY_TITLES[SyncState.IDLE])))
    icon.run()  # blocks until icon.stop() is called (e.g. from the Exit menu item)
