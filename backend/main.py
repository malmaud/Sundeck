"""Single entry point for the steamlaunch executable.

When invoked as:
  steamlaunch launch --app_id=X   → launches the game via CLI
  steamlaunch                      → starts the UI server
"""
import sys

_POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("-")]
_IS_CLI = bool(_POSITIONAL) and _POSITIONAL[0] in {"games", "build", "launch", "restart"}

if _IS_CLI:
    from absl import app as absl_app
    from cli import main as cli_main
    absl_app.run(cli_main)
else:
    from absl import app as absl_app
    from server import _main
    absl_app.run(_main)
