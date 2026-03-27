"""Windows registry helpers for run-at-startup."""

import sys
import winreg

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "SunDeck"


def _get_startup_exe() -> str:
    return sys.executable


def set_run_at_startup(enabled: bool) -> None:
    """Add or remove the SunDeck startup registry entry for the current user."""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            # Windows reads this key at login and launches every value it finds as a process.
            # Writing our exe path here is all that's needed to register as a startup app.
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _get_startup_exe())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass


def get_run_at_startup() -> bool:
    """Return True if the startup registry entry exists and points to this executable."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
            return value == _get_startup_exe()
    except FileNotFoundError:
        return False
