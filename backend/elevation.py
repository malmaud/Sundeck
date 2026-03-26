import base64
import ctypes
import subprocess
import tempfile
from pathlib import Path

from sunshine import _detect_streaming_service


def _encode_command(cmd: str) -> str:
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_elevated(inner_cmd: str) -> None:
    if _is_admin():
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", inner_cmd],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            capture_output=True, text=True,
        )
    else:
        encoded = _encode_command(inner_cmd)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
                f"'-NoProfile -NonInteractive -EncodedCommand {encoded}'",
            ],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Elevated command failed.\n{stderr}" if stderr else "Elevated command failed or was cancelled.")


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
    service = _detect_streaming_service()
    _run_elevated(f"net stop {service}; net start {service}")
