"""CLI bridge for the Electron UI and Sunshine. Outputs JSON to stdout.

Commands:
  games    List recently played Steam games.
  build    Build a Sunshine config JSON for the given app IDs.
  launch   Launch a Steam game and wait for it to exit.
  restart  Restart the streaming service (SunshineService / ApolloService).
"""
import json
import sys
from dataclasses import asdict

from absl import app, flags

from steam import get_recent_games, launch_game, wait_for_game
from sunshine import (
    load_sunshine_config,
    build_sunshine_config,
    restart_streaming_service,
)

FLAGS = flags.FLAGS

flags.DEFINE_integer("count", 10, "Number of recent games to fetch.", allow_override=True)
flags.DEFINE_string("app_ids", None, "Comma-separated Steam App IDs (build command).", allow_override=True)
flags.DEFINE_integer("app_id", None, "Steam App ID (launch command).", allow_override=True)
flags.DEFINE_integer("launch_timeout", 60, "Seconds to wait for game to start.", allow_override=True)
flags.DEFINE_float("poll_interval", 2.0, "Seconds between registry polls.", allow_override=True)


def cmd_games(count=10):
    games = get_recent_games(count)
    print(json.dumps([asdict(g) for g in games]))


def cmd_build(app_ids=None):
    if not app_ids:
        print(json.dumps({"error": "build requires --app_ids"}))
        sys.exit(1)
    ids = {int(x) for x in app_ids.split(",")}
    games = [g for g in get_recent_games(50) if g.app_id in ids]
    order = {aid: i for i, aid in enumerate(ids)}
    games.sort(key=lambda g: order.get(g.app_id, 0))
    existing = load_sunshine_config()
    config = build_sunshine_config(existing, games)
    print(config.model_dump_json(by_alias=True, indent=4))


def cmd_launch(app_id=None, launch_timeout=60, poll_interval=2.0):
    if app_id is None:
        print(json.dumps({"error": "launch requires --app_id"}))
        sys.exit(1)
    launch_game(app_id)
    wait_for_game(app_id, launch_timeout, poll_interval)


def cmd_restart():
    restart_streaming_service()
    print(json.dumps({"status": "ok"}))


COMMANDS = {
    "games": lambda: cmd_games(FLAGS.count),
    "build": lambda: cmd_build(FLAGS.app_ids),
    "launch": lambda: cmd_launch(FLAGS.app_id, FLAGS.launch_timeout, FLAGS.poll_interval),
    "restart": cmd_restart,
}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(json.dumps({"error": f"Usage: cli.py [{'/'.join(COMMANDS)}] [flags...]"}))
        sys.exit(1)
    try:
        COMMANDS[argv[1]]()
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    app.run(main)
