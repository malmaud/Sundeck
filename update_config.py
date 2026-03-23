import ctypes
import sys
from absl import app, flags
from sunshine import update_sunshine_config

FLAGS = flags.FLAGS
flags.DEFINE_integer("count", 10, "Number of recent games to include.")


def is_admin() -> bool:
    return ctypes.windll.shell32.IsUserAnAdmin()


def relaunch_as_admin() -> None:
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )


def main(argv: list[str]) -> None:
    del argv
    if not is_admin():
        relaunch_as_admin()
    else:
        update_sunshine_config(count=FLAGS.count)


if __name__ == "__main__":
    app.run(main)
