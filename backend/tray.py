"""System tray icon for SunDeck."""

import threading
import time

_TRAY_COLORS = {
    "idle":    (100, 210, 100),  # green
    "syncing": ( 50, 160, 255),  # blue
}
_TRAY_TITLES = {
    "idle":    "SunDeck",
    "syncing": "SunDeck — Syncing…",
}
_TRAY_STATUS_TEXT = {
    "idle":    "Idle",
    "syncing": "Syncing…",
}


def _create_tray_image(color: tuple = (100, 210, 100)):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill=(30, 35, 50, 255))
    draw.polygon([(20, 16), (20, 48), (50, 32)], fill=(*color, 255))
    return img


def _run_tray(port: int, get_sync_state, try_auto_sync) -> None:
    import pystray
    import webbrowser

    def _status_text(_item):
        return _TRAY_STATUS_TEXT.get(get_sync_state(), "…")

    menu = pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Open SunDeck",
            lambda _icon, _item: webbrowser.open(f"http://localhost:{port}"),
            default=True,
        ),
        pystray.MenuItem(
            "Sync Now",
            lambda _icon, _item: threading.Thread(target=try_auto_sync, daemon=True).start(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda icon, _item: icon.stop()),
    )
    icon = pystray.Icon("SunDeck", _create_tray_image(), "SunDeck", menu)

    def _watch_state():
        last = None
        while True:
            state = get_sync_state()
            if state != last:
                icon.icon = _create_tray_image(_TRAY_COLORS.get(state, _TRAY_COLORS["idle"]))
                icon.title = _TRAY_TITLES.get(state, "SunDeck")
                last = state
            time.sleep(0.5)

    threading.Thread(target=_watch_state, daemon=True).start()
    threading.Thread(target=icon.run, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        icon.stop()
