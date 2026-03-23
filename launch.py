from absl import app, flags
from steam import launch_game, wait_for_game

FLAGS = flags.FLAGS
flags.DEFINE_integer("app_id", None, "Steam App ID to launch.", required=True)
flags.DEFINE_integer("launch_timeout", 60, "Seconds to wait for the game to start.")
flags.DEFINE_float("poll_interval", 2.0, "Seconds between registry polls.")


def main(argv: list[str]) -> None:
    del argv  # unused
    launch_game(FLAGS.app_id)
    wait_for_game(FLAGS.app_id, FLAGS.launch_timeout, FLAGS.poll_interval)


if __name__ == "__main__":
    app.run(main)
