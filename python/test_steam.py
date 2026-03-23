"""Tests for steam.py — game launching, waiting, and VDF parsing."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam import get_recent_games, launch_game, wait_for_game


# A minimal localconfig.vdf with two played games and one with LastPlayed=0.
# App 440 is most recent, 730 is older, 100 has never been played.
VDF_CONTENT = """\
"UserLocalConfigStore"
{
\t"apps"
\t{
\t\t"440"
\t\t{
\t\t\t"LastPlayed"\t\t"1700000002"
\t\t}
\t\t"730"
\t\t{
\t\t\t"LastPlayed"\t\t"1700000001"
\t\t}
\t\t"100"
\t\t{
\t\t\t"LastPlayed"\t\t"0"
\t\t}
\t}
}
"""


def _run_get_recent_games(vdf_content, count=10):
    """Execute get_recent_games with a fake Steam install tree and no network calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vdf_dir = Path(tmpdir) / "Steam" / "userdata" / "12345" / "config"
        vdf_dir.mkdir(parents=True)
        (vdf_dir / "localconfig.vdf").write_text(vdf_content, encoding="utf-8")
        with patch.dict(os.environ, {"ProgramFiles(x86)": tmpdir}), \
             patch("steam.winreg.OpenKey", side_effect=OSError), \
             patch("steam._get_thumbnail", return_value=""):
            return get_recent_games(count)


# ---------------------------------------------------------------------------
# launch_game
# ---------------------------------------------------------------------------


class TestLaunchGame(unittest.TestCase):
    @patch("steam.subprocess.Popen")
    def test_opens_steam_rungameid_url(self, mock_popen):
        launch_game(440)
        args = mock_popen.call_args[0][0]
        self.assertIn("steam://rungameid/440", args)

    @patch("steam.subprocess.Popen")
    def test_uses_shell(self, mock_popen):
        launch_game(440)
        self.assertTrue(mock_popen.call_args[1]["shell"])

    @patch("steam.subprocess.Popen")
    def test_url_contains_correct_app_id(self, mock_popen):
        launch_game(730)
        args = mock_popen.call_args[0][0]
        self.assertIn("730", " ".join(str(a) for a in args))


# ---------------------------------------------------------------------------
# wait_for_game
# ---------------------------------------------------------------------------


class TestWaitForGame(unittest.TestCase):
    @patch("steam.time.sleep")
    @patch("steam.get_running_app_id")
    def test_waits_until_game_starts_then_exits(self, mock_id, _mock_sleep):
        # Not running × 2 → running × 2 → exited
        mock_id.side_effect = [0, 0, 100, 100, 0]
        wait_for_game(100, launch_timeout=60, poll_interval=1.0)  # must not raise

    @patch("steam.time.sleep")
    @patch("steam.get_running_app_id")
    def test_raises_timeout_when_game_never_starts(self, mock_id, _mock_sleep):
        mock_id.return_value = 0
        with self.assertRaises(TimeoutError) as ctx:
            wait_for_game(100, launch_timeout=5, poll_interval=2.0)
        self.assertIn("100", str(ctx.exception))

    @patch("steam.time.sleep")
    @patch("steam.get_running_app_id")
    def test_does_not_raise_when_game_starts_immediately(self, mock_id, _mock_sleep):
        mock_id.side_effect = [100, 0]
        wait_for_game(100, launch_timeout=1, poll_interval=1.0)  # must not raise

    @patch("steam.time.sleep")
    @patch("steam.get_running_app_id")
    def test_sleeps_at_given_poll_interval(self, mock_id, mock_sleep):
        mock_id.side_effect = [0, 100, 100, 0]
        wait_for_game(100, launch_timeout=60, poll_interval=2.5)
        for c in mock_sleep.call_args_list:
            self.assertEqual(c[0][0], 2.5)

    @patch("steam.time.sleep")
    @patch("steam.get_running_app_id")
    def test_timeout_fires_after_expected_polls(self, mock_id, _mock_sleep):
        # poll_interval=2, timeout=4: sleep-1 → elapsed=2 (ok), sleep-2 → elapsed=4 (timeout)
        mock_id.return_value = 0
        with self.assertRaises(TimeoutError):
            wait_for_game(100, launch_timeout=4, poll_interval=2.0)
        # while-check before sleep-1 + while-check before sleep-2 = 2 calls
        self.assertEqual(mock_id.call_count, 2)


# ---------------------------------------------------------------------------
# get_recent_games — VDF parsing
# ---------------------------------------------------------------------------


class TestGetRecentGames(unittest.TestCase):
    def test_returns_empty_when_no_vdf_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"ProgramFiles(x86)": tmpdir}):
                games = get_recent_games(10)
        self.assertEqual(games, [])

    def test_returns_games_sorted_by_recency(self):
        games = _run_get_recent_games(VDF_CONTENT)
        self.assertEqual(games[0].app_id, 440)
        self.assertEqual(games[1].app_id, 730)

    def test_skips_entries_with_zero_last_played(self):
        games = _run_get_recent_games(VDF_CONTENT)
        self.assertNotIn(100, {g.app_id for g in games})

    def test_respects_count_limit(self):
        games = _run_get_recent_games(VDF_CONTENT, count=1)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].app_id, 440)

    def test_falls_back_to_app_id_string_when_registry_unavailable(self):
        games = _run_get_recent_games(VDF_CONTENT)
        # With registry mocked to OSError, name is the raw app_id string
        self.assertEqual(games[0].name, "440")

    def test_returns_steam_game_objects(self):
        from steam import SteamGame
        games = _run_get_recent_games(VDF_CONTENT)
        for g in games:
            self.assertIsInstance(g, SteamGame)
            self.assertIsInstance(g.app_id, int)

    def test_handles_empty_apps_section(self):
        vdf = '"UserLocalConfigStore"\n{\n\t"apps"\n\t{\n\t}\n}\n'
        games = _run_get_recent_games(vdf)
        self.assertEqual(games, [])

    def test_thumbnail_path_stored_on_game(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vdf_dir = Path(tmpdir) / "Steam" / "userdata" / "1" / "config"
            vdf_dir.mkdir(parents=True)
            (vdf_dir / "localconfig.vdf").write_text(VDF_CONTENT, encoding="utf-8")
            with patch.dict(os.environ, {"ProgramFiles(x86)": tmpdir}), \
                 patch("steam.winreg.OpenKey", side_effect=OSError), \
                 patch("steam._get_thumbnail", return_value="/cache/440.png"):
                games = get_recent_games(1)
        self.assertEqual(games[0].thumbnail, "/cache/440.png")


if __name__ == "__main__":
    unittest.main()
