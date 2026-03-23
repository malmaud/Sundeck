"""Tests for sunshine.py — config building, loading, saving, and service management."""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from steam import SteamGame
from sunshine import (
    SunshineApp,
    SunshineConfig,
    _detect_streaming_service,
    build_sunshine_config,
    load_sunshine_config,
    restart_streaming_service,
    save_sunshine_config,
)


FAKE_GAMES = [
    SteamGame(app_id=100, name="Half-Life", thumbnail="/t/100.png"),
    SteamGame(app_id=200, name="Portal", thumbnail="/t/200.png"),
]


# ---------------------------------------------------------------------------
# build_sunshine_config
# ---------------------------------------------------------------------------


class TestBuildSunshineConfig(unittest.TestCase):
    def _build(self, games, existing=None, **kwargs):
        return build_sunshine_config(existing or SunshineConfig(), games, **kwargs)

    def test_includes_provided_games(self):
        config = self._build(FAKE_GAMES)
        names = {a.name for a in config.apps}
        self.assertIn("Half-Life", names)
        self.assertIn("Portal", names)

    def test_cmd_uses_absl_flag_style(self):
        config = self._build([FAKE_GAMES[0]])
        app = next(a for a in config.apps if a.name == "Half-Life")
        self.assertIn("cli.py launch", app.cmd)
        self.assertIn("--app_id=100", app.cmd)

    def test_cmd_uses_uv_run_with_directory(self):
        config = self._build([FAKE_GAMES[0]])
        app = next(a for a in config.apps if a.name == "Half-Life")
        self.assertIn("uv", app.cmd)
        self.assertIn("--directory", app.cmd)

    def test_image_path_set_from_thumbnail(self):
        config = self._build([FAKE_GAMES[0]])
        app = next(a for a in config.apps if a.name == "Half-Life")
        self.assertEqual(app.image_path, "/t/100.png")

    def test_wait_all_is_false_for_steam_apps(self):
        config = self._build([FAKE_GAMES[0]])
        app = next(a for a in config.apps if a.name == "Half-Life")
        self.assertFalse(app.wait_all)

    def test_cli_script_parent_used_as_directory(self):
        fake_script = Path("/custom/dir/cli.py")
        config = self._build([FAKE_GAMES[0]], cli_script=fake_script)
        app = next(a for a in config.apps if a.name == "Half-Life")
        self.assertIn(str(fake_script.parent), app.cmd)

    def test_preserves_non_managed_apps(self):
        existing = SunshineConfig(apps=[SunshineApp(name="Desktop", cmd="notepad.exe")])
        config = self._build(FAKE_GAMES, existing=existing)
        self.assertIn("Desktop", {a.name for a in config.apps})

    def test_replaces_old_launch_py_entries(self):
        existing = SunshineConfig(apps=[
            SunshineApp(name="Half-Life", cmd="uv run launch.py --app_id=100"),
        ])
        config = self._build([FAKE_GAMES[0]], existing=existing)
        entries = [a for a in config.apps if a.name == "Half-Life"]
        self.assertEqual(len(entries), 1)
        self.assertIn("cli.py launch", entries[0].cmd)

    def test_replaces_existing_cli_launch_entries(self):
        existing = SunshineConfig(apps=[
            SunshineApp(name="Half-Life", cmd="uv run cli.py launch --app_id=100"),
        ])
        config = self._build([FAKE_GAMES[0]], existing=existing)
        entries = [a for a in config.apps if a.name == "Half-Life"]
        self.assertEqual(len(entries), 1)

    def test_new_games_precede_preserved_apps(self):
        existing = SunshineConfig(apps=[SunshineApp(name="Desktop", cmd="notepad.exe")])
        config = self._build([FAKE_GAMES[0]], existing=existing)
        names = [a.name for a in config.apps]
        self.assertLess(names.index("Half-Life"), names.index("Desktop"))

    def test_empty_games_removes_all_managed_entries(self):
        existing = SunshineConfig(apps=[
            SunshineApp(name="Half-Life", cmd="uv run cli.py launch --app_id=100"),
            SunshineApp(name="Desktop", cmd="notepad.exe"),
        ])
        config = self._build([], existing=existing)
        names = {a.name for a in config.apps}
        self.assertNotIn("Half-Life", names)
        self.assertIn("Desktop", names)

    def test_empty_existing_config(self):
        config = self._build(FAKE_GAMES)
        self.assertEqual(len(config.apps), 2)


# ---------------------------------------------------------------------------
# load_sunshine_config
# ---------------------------------------------------------------------------


class TestLoadSunshineConfig(unittest.TestCase):
    def test_returns_empty_config_when_file_missing(self):
        fake = MagicMock(spec=Path)
        fake.exists.return_value = False
        config = load_sunshine_config(fake)
        self.assertEqual(config.apps, [])

    def test_parses_apps_from_json(self):
        fake = MagicMock(spec=Path)
        fake.exists.return_value = True
        fake.read_text.return_value = json.dumps({
            "apps": [{"name": "Desktop", "cmd": "notepad.exe"}]
        })
        config = load_sunshine_config(fake)
        self.assertEqual(len(config.apps), 1)
        self.assertEqual(config.apps[0].name, "Desktop")

    def test_preserves_extra_fields_on_apps(self):
        fake = MagicMock(spec=Path)
        fake.exists.return_value = True
        fake.read_text.return_value = json.dumps({
            "apps": [{"name": "X", "cmd": "x.exe", "env": {"KEY": "val"}}]
        })
        config = load_sunshine_config(fake)
        self.assertEqual(config.apps[0].model_extra.get("env"), {"KEY": "val"})

    def test_parses_hyphenated_alias_fields(self):
        fake = MagicMock(spec=Path)
        fake.exists.return_value = True
        fake.read_text.return_value = json.dumps({
            "apps": [{"name": "G", "cmd": "c", "image-path": "/img.png", "wait-all": False}]
        })
        config = load_sunshine_config(fake)
        self.assertEqual(config.apps[0].image_path, "/img.png")
        self.assertFalse(config.apps[0].wait_all)


# ---------------------------------------------------------------------------
# save_sunshine_config
# ---------------------------------------------------------------------------


class TestSaveSunshineConfig(unittest.TestCase):
    def _written(self, config):
        fake = MagicMock(spec=Path)
        save_sunshine_config(config, fake)
        return fake.write_text.call_args[0][0]

    def test_writes_valid_json_with_apps_key(self):
        config = SunshineConfig(apps=[SunshineApp(name="Game", cmd="run.exe")])
        data = json.loads(self._written(config))
        self.assertIn("apps", data)
        self.assertEqual(data["apps"][0]["name"], "Game")

    def test_uses_hyphenated_alias_keys(self):
        app = SunshineApp.model_validate({"name": "G", "cmd": "c", "image-path": "/img.png"})
        written = self._written(SunshineConfig(apps=[app]))
        self.assertIn("image-path", written)
        self.assertNotIn("image_path", written)

    def test_wait_all_serialized_with_hyphen(self):
        app = SunshineApp.model_validate({"name": "G", "cmd": "c", "wait-all": False})
        written = self._written(SunshineConfig(apps=[app]))
        self.assertIn("wait-all", written)


# ---------------------------------------------------------------------------
# _detect_streaming_service
# ---------------------------------------------------------------------------


class TestDetectStreamingService(unittest.TestCase):
    def _sc(self, returncode):
        m = MagicMock()
        m.returncode = returncode
        return m

    def test_returns_first_available_service(self):
        with patch("sunshine.subprocess.run", return_value=self._sc(0)):
            self.assertEqual(_detect_streaming_service(), "SunshineService")

    def test_skips_unavailable_and_returns_next(self):
        with patch("sunshine.subprocess.run", side_effect=[self._sc(1), self._sc(0)]):
            self.assertEqual(_detect_streaming_service(), "ApolloService")

    def test_raises_runtime_error_when_none_found(self):
        with patch("sunshine.subprocess.run", return_value=self._sc(1)):
            with self.assertRaises(RuntimeError):
                _detect_streaming_service()

    def test_error_message_names_tried_services(self):
        with patch("sunshine.subprocess.run", return_value=self._sc(1)):
            with self.assertRaises(RuntimeError) as ctx:
                _detect_streaming_service()
        self.assertIn("ApolloService", str(ctx.exception))


# ---------------------------------------------------------------------------
# restart_streaming_service
# ---------------------------------------------------------------------------


class TestRestartStreamingService(unittest.TestCase):
    def _restart(self, service="ApolloService"):
        with patch("sunshine._detect_streaming_service", return_value=service), \
             patch("sunshine.subprocess.run") as mock_run:
            restart_streaming_service()
        return [c[0][0] for c in mock_run.call_args_list]

    def test_calls_net_stop_and_start(self):
        cmds = self._restart()
        self.assertIn(["net", "stop", "ApolloService"], cmds)
        self.assertIn(["net", "start", "ApolloService"], cmds)

    def test_stop_before_start(self):
        cmds = self._restart()
        self.assertLess(
            cmds.index(["net", "stop", "ApolloService"]),
            cmds.index(["net", "start", "ApolloService"]),
        )

    def test_uses_detected_service_name(self):
        cmds = self._restart(service="SunshineService")
        self.assertIn(["net", "stop", "SunshineService"], cmds)
        self.assertIn(["net", "start", "SunshineService"], cmds)


if __name__ == "__main__":
    unittest.main()
