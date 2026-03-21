import winreg
import time
import subprocess
from absl import app, flags

FLAGS = flags.FLAGS
flags.DEFINE_integer("app_id", None, "Steam App ID to launch.", required=True)
flags.DEFINE_integer("launch_timeout", 60, "Seconds to wait for the game to start.")
flags.DEFINE_float("poll_interval", 2.0, "Seconds between registry polls.")


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


def main(argv: list[str]) -> None:
    del argv  # unused
    launch_game(FLAGS.app_id)
    wait_for_game(FLAGS.app_id, FLAGS.launch_timeout, FLAGS.poll_interval)


if __name__ == "__main__":
    app.run(main)
