"""Tests for steam.py — game launching, waiting, and VDF parsing."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

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


def _run_get_recent_games(vdf_content, count=10, steam_api_names=None):
    """Execute get_recent_games with a fake Steam install tree and no network calls."""
    def fake_fetch_name(app_id):
        return (steam_api_names or {}).get(app_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        vdf_dir = Path(tmpdir) / "Steam" / "userdata" / "12345" / "config"
        vdf_dir.mkdir(parents=True)
        (vdf_dir / "localconfig.vdf").write_text(vdf_content, encoding="utf-8")
        with patch.dict(os.environ, {"ProgramFiles(x86)": tmpdir}), \
             patch("steam.winreg.OpenKey", side_effect=OSError), \
             patch("steam.get_thumbnail", return_value=""), \
             patch("steam._load_name_cache", return_value={}), \
             patch("steam._save_name_cache"), \
             patch("steam._fetch_name_from_steam", side_effect=fake_fetch_name):
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
    def _patched(self, qvex_side_effect, mono_side_effect=None):
        """Return a context manager that patches the three dependencies of wait_for_game."""
        patches = [
            patch("steam._open_steam_key", return_value=MagicMock()),
            patch("steam.winreg.QueryValueEx", side_effect=qvex_side_effect),
            patch("steam._wait_registry_change"),
        ]
        if mono_side_effect is not None:
            patches.append(patch("steam.time.monotonic", side_effect=mono_side_effect))
        return patches

    def _run(self, patches, *args, **kwargs):
        """Enter all patches and call wait_for_game with the given args."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            wait_for_game(*args, **kwargs)
            return mocks

    def test_waits_until_game_starts_then_exits(self):
        # Phase 1: not running × 2 → running; Phase 2: running → exited
        self._run(
            self._patched([(0, 1), (0, 1), (100, 1), (100, 1), (0, 1)]),
            100, launch_timeout=60, poll_interval=1.0,
        )

    def test_raises_timeout_when_game_never_starts(self):
        # monotonic: start=0 → deadline=4; next check remaining=4-100=-96 → timeout
        patches = self._patched([(0, 1)], mono_side_effect=[0.0, 100.0])
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with self.assertRaises(TimeoutError) as ctx:
                wait_for_game(100, launch_timeout=4, poll_interval=2.0)
        self.assertIn("100", str(ctx.exception))

    def test_does_not_raise_when_game_starts_immediately(self):
        # Game already running on first check, exits on first exit-phase check
        patches = self._patched([(100, 1), (0, 1)])
        mocks = self._run(patches, 100, launch_timeout=1, poll_interval=1.0)
        wait_mock = mocks[2]
        wait_mock.assert_not_called()

    def test_notified_while_waiting_for_start(self):
        # Phase 1: not running → notified → running; Phase 2: running → notified → exited
        patches = self._patched([(0, 1), (100, 1), (100, 1), (0, 1)])
        mocks = self._run(patches, 100, launch_timeout=60, poll_interval=1.0)
        self.assertEqual(mocks[2].call_count, 2)

    def test_timeout_based_on_wall_time(self):
        # monotonic: start=0 → deadline=4; after first wait, remaining=4-100=-96 → timeout
        patches = self._patched([(0, 1)], mono_side_effect=[0.0, 100.0])
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with self.assertRaises(TimeoutError):
                wait_for_game(100, launch_timeout=4, poll_interval=2.0)


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
        games = _run_get_recent_games(VDF_CONTENT, steam_api_names={"440": "TF2", "730": "CS2"})
        self.assertEqual(games[0].app_id, 440)
        self.assertEqual(games[1].app_id, 730)

    def test_skips_entries_with_zero_last_played(self):
        games = _run_get_recent_games(VDF_CONTENT, steam_api_names={"440": "TF2", "730": "CS2", "100": "Game"})
        self.assertNotIn(100, {g.app_id for g in games})

    def test_respects_count_limit(self):
        games = _run_get_recent_games(VDF_CONTENT, count=1, steam_api_names={"440": "TF2", "730": "CS2"})
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].app_id, 440)

    def test_uses_steam_api_name_when_registry_unavailable(self):
        games = _run_get_recent_games(VDF_CONTENT, steam_api_names={"440": "Team Fortress 2", "730": "Counter-Strike 2"})
        self.assertEqual(games[0].name, "Team Fortress 2")

    def test_excludes_game_when_no_name_source_available(self):
        # No VDF name, no registry, no Steam API → excluded
        games = _run_get_recent_games(VDF_CONTENT)
        self.assertEqual(games, [])

    def test_returns_steam_game_objects(self):
        from steam import SteamGame
        games = _run_get_recent_games(VDF_CONTENT, steam_api_names={"440": "TF2", "730": "CS2"})
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
                 patch("steam.get_thumbnail", return_value="/cache/440.png"), \
                 patch("steam._load_name_cache", return_value={}), \
                 patch("steam._save_name_cache"), \
                 patch("steam._fetch_name_from_steam", return_value="Team Fortress 2"):
                games = get_recent_games(1)
        self.assertEqual(games[0].thumbnail, "/cache/440.png")


# ---------------------------------------------------------------------------
# Regression: apps without LastPlayed must not cause subsequent apps to be
# skipped (parser bug where current_app_id was not cleared on block exit).
# ---------------------------------------------------------------------------

# App 999 has no LastPlayed key at all; 440 and 730 come after it.
# Before the fix, the parser would get stuck on 999 and miss 440 and 730.
VDF_NO_LAST_PLAYED_FIRST = """\
"UserLocalConfigStore"
{
\t"apps"
\t{
\t\t"999"
\t\t{
\t\t\t"SomeOtherKey"\t\t"whatever"
\t\t}
\t\t"440"
\t\t{
\t\t\t"LastPlayed"\t\t"1700000002"
\t\t}
\t\t"730"
\t\t{
\t\t\t"LastPlayed"\t\t"1700000001"
\t\t}
\t}
}
"""


VDF_WITH_NAMES = """\
"UserLocalConfigStore"
{
\t"apps"
\t{
\t\t"440"
\t\t{
\t\t\t"name"\t\t"Team Fortress 2"
\t\t\t"LastPlayed"\t\t"1700000002"
\t\t}
\t\t"730"
\t\t{
\t\t\t"LastPlayed"\t\t"1700000001"
\t\t}
\t}
}
"""


class TestNameResolution(unittest.TestCase):
    def test_vdf_name_used_when_present(self):
        games = _run_get_recent_games(VDF_WITH_NAMES)
        tf2 = next(g for g in games if g.app_id == 440)
        self.assertEqual(tf2.name, "Team Fortress 2")

    def test_game_excluded_when_name_cannot_be_resolved(self):
        # App 730 has no VDF name, no registry, Steam API returns nothing → excluded
        games = _run_get_recent_games(VDF_WITH_NAMES)
        self.assertNotIn(730, {g.app_id for g in games})


class TestParserRegressions(unittest.TestCase):
    def test_apps_after_no_last_played_entry_are_not_skipped(self):
        """Apps following one with no LastPlayed must still be returned."""
        games = _run_get_recent_games(VDF_NO_LAST_PLAYED_FIRST,
                                      steam_api_names={"440": "TF2", "730": "CS2"})
        app_ids = {g.app_id for g in games}
        self.assertIn(440, app_ids)
        self.assertIn(730, app_ids)

    def test_app_with_no_last_played_is_excluded(self):
        """An app with no LastPlayed key should not appear in results."""
        games = _run_get_recent_games(VDF_NO_LAST_PLAYED_FIRST,
                                      steam_api_names={"440": "TF2", "730": "CS2"})
        self.assertNotIn(999, {g.app_id for g in games})

    def test_large_library_returns_all_played_games(self):
        """Requesting more games than exist returns all played ones, not a truncated set."""
        games = _run_get_recent_games(VDF_NO_LAST_PLAYED_FIRST, count=999,
                                      steam_api_names={"440": "TF2", "730": "CS2"})
        self.assertEqual(len(games), 2)


# ---------------------------------------------------------------------------
# Regression: CMYK JPEG thumbnails must be converted to RGB before saving as
# PNG (Pillow cannot write CMYK mode as PNG).
# ---------------------------------------------------------------------------


class TestThumbnailCmykRegression(unittest.TestCase):
    def test_cmyk_jpeg_is_saved_as_rgb_png(self):
        """get_thumbnail must not crash when the downloaded image is CMYK."""
        import steam

        def fake_urlretrieve(url, filename):
            img = Image.new("CMYK", (10, 10), (0, 128, 200, 50))
            img.save(filename, format="JPEG")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "thumbnails"
            with patch.object(steam, "_THUMBNAIL_CACHE_DIR", cache_dir), \
                 patch("steam.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
                result = steam.get_thumbnail(12345)
                self.assertTrue(result, "Expected a non-empty path back")
                with Image.open(result) as saved:
                    self.assertIn(saved.mode, ("RGB", "RGBA"), "Saved PNG must not be CMYK")

    def test_rgb_jpeg_still_works(self):
        """Normal RGB thumbnails must continue to save correctly."""
        import steam

        def fake_urlretrieve(url, filename):
            img = Image.new("RGB", (10, 10), (100, 150, 200))
            img.save(filename, format="JPEG")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "thumbnails"
            with patch.object(steam, "_THUMBNAIL_CACHE_DIR", cache_dir), \
                 patch("steam.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
                result = steam.get_thumbnail(99999)
                self.assertTrue(result)
                with Image.open(result) as saved:
                    self.assertEqual(saved.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
