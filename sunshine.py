import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from steam import SteamGame, get_recent_games


_SUNSHINE_CONFIG_DEFAULT = Path(r"C:\Program Files\Apollo\config\apps.json")
_SUNSHINE_CMD_MARKER = "launch.py"
_LAUNCH_SCRIPT_DEFAULT = Path(__file__).parent / "launch.py"
_KNOWN_STREAMING_SERVICES = ["SunshineService", "ApolloService"]


class SunshineApp(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    cmd: str = ""
    image_path: str = Field(default="", alias="image-path")


class SunshineConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    apps: list[SunshineApp] = []


def build_sunshine_config(
    existing: SunshineConfig,
    games: list[SteamGame],
    launch_script: Path = _LAUNCH_SCRIPT_DEFAULT,
) -> SunshineConfig:
    """Return a new SunshineConfig with recent Steam games merged in.

    Entries previously written by this function (identified by _SUNSHINE_CMD_MARKER
    in their 'cmd') are replaced; all other entries are preserved.
    """
    uv = shutil.which("uv") or "uv"
    kept = [a for a in existing.apps if _SUNSHINE_CMD_MARKER not in a.cmd]
    new_apps = [
        SunshineApp.model_validate(
            {
                "name": game.name,
                "cmd": f"{uv} run --directory {launch_script.parent} {launch_script} --app_id={game.app_id}",
                "image-path": game.thumbnail,
            }
        )
        for game in games
    ]
    return SunshineConfig(apps=kept + new_apps)


def load_sunshine_config(
    config_path: Path = _SUNSHINE_CONFIG_DEFAULT,
) -> SunshineConfig:
    if config_path.exists():
        return SunshineConfig.model_validate(
            json.loads(config_path.read_text(encoding="utf-8"))
        )
    return SunshineConfig()


def save_sunshine_config(
    config: SunshineConfig,
    config_path: Path = _SUNSHINE_CONFIG_DEFAULT,
) -> None:
    config_path.write_text(
        config.model_dump_json(by_alias=True, indent=4), encoding="utf-8"
    )


def update_sunshine_config(
    config_path: Path = _SUNSHINE_CONFIG_DEFAULT,
    launch_script: Path = _LAUNCH_SCRIPT_DEFAULT,
    restart_sunshine: bool = False,
) -> None:
    """Sync the Sunshine apps.json on disk with the most recently played Steam games."""
    games = get_recent_games()
    config = build_sunshine_config(load_sunshine_config(config_path), games, launch_script)
    save_sunshine_config(config, config_path)
    if restart_sunshine:
        restart_streaming_service()


def restart_streaming_service() -> None:
    service = _detect_streaming_service()
    subprocess.run(["net", "stop", service], check=True)
    subprocess.run(["net", "start", service], check=True)


def _detect_streaming_service() -> str:
    for service in _KNOWN_STREAMING_SERVICES:
        result = subprocess.run(["sc", "query", service], capture_output=True)
        if result.returncode == 0:
            return service
    raise RuntimeError(
        f"No streaming service found. Tried: {_KNOWN_STREAMING_SERVICES}"
    )
