"""Tests for server.py — streaming detection, auto-sync scheduling, and sync execution."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
from server import Settings, _do_auto_sync, _is_streaming_active, _sync_worker
from steam import SteamGame


FAKE_GAMES = [
    SteamGame(app_id=100, name="Half-Life", thumbnail="/t/100.png"),
    SteamGame(app_id=200, name="Portal", thumbnail="/t/200.png"),
]


# ---------------------------------------------------------------------------
# _is_streaming_active
# ---------------------------------------------------------------------------


class TestIsStreamingActive(unittest.TestCase):
    def _netstat(self, output):
        with patch("server.subprocess.check_output", return_value=output):
            return _is_streaming_active()

    def test_returns_false_with_no_streaming_connections(self):
        self.assertFalse(self._netstat(
            "  TCP  0.0.0.0:47990   0.0.0.0:0   LISTENING\n"
        ))

    def test_returns_true_when_rtsp_port_established(self):
        self.assertTrue(self._netstat(
            "  TCP  192.168.1.2:48010  192.168.1.5:54321  ESTABLISHED\n"
        ))

    def test_returns_true_when_video_port_established(self):
        self.assertTrue(self._netstat(
            "  TCP  192.168.1.2:47998  192.168.1.5:54321  ESTABLISHED\n"
        ))

    def test_returns_true_when_control_port_established(self):
        self.assertTrue(self._netstat(
            "  TCP  192.168.1.2:47999  192.168.1.5:54321  ESTABLISHED\n"
        ))

    def test_returns_true_when_audio_port_established(self):
        self.assertTrue(self._netstat(
            "  TCP  192.168.1.2:48000  192.168.1.5:54321  ESTABLISHED\n"
        ))

    def test_returns_false_when_streaming_port_in_time_wait(self):
        self.assertFalse(self._netstat(
            "  TCP  192.168.1.2:48010  192.168.1.5:54321  TIME_WAIT\n"
        ))

    def test_returns_false_when_streaming_port_only_listening(self):
        self.assertFalse(self._netstat(
            "  TCP  0.0.0.0:48010  0.0.0.0:0  LISTENING\n"
        ))

    def test_returns_false_on_subprocess_error(self):
        with patch("server.subprocess.check_output", side_effect=OSError):
            self.assertFalse(_is_streaming_active())

    def test_returns_false_with_empty_output(self):
        self.assertFalse(self._netstat(""))

    def test_established_on_unrelated_port_does_not_trigger(self):
        self.assertFalse(self._netstat(
            "  TCP  192.168.1.2:5000  192.168.1.5:54321  ESTABLISHED\n"
        ))


# ---------------------------------------------------------------------------
# _sync_worker — scheduling logic
# ---------------------------------------------------------------------------


def _run_worker_tick(settings_data, now, streaming=False, sync_raises=None):
    """Run exactly one tick of _sync_worker and return (sync_called, save_called)."""
    ticks = [0]

    def fake_wait(timeout):
        ticks[0] += 1
        return ticks[0] > 1  # first call → False (run tick); second → True (stop)

    mock_sync = MagicMock(side_effect=sync_raises)
    mock_save = MagicMock()
    settings = Settings(**settings_data) if isinstance(settings_data, dict) else settings_data

    with patch.object(server._sync_stop, "wait", side_effect=fake_wait), \
         patch("server._load_settings", return_value=settings), \
         patch("server.time.time", return_value=now), \
         patch("server._is_streaming_active", return_value=streaming), \
         patch("server._do_auto_sync", mock_sync), \
         patch("server._patch_settings", mock_save), \
         patch("server._append_log"):
        _sync_worker()

    return mock_sync.called, mock_save.called


class TestSyncWorkerScheduling(unittest.TestCase):
    _BASE = {"auto_sync_hours": 6.0, "last_sync_time": 0.0}

    def test_does_not_sync_when_disabled(self):
        synced, _ = _run_worker_tick({**self._BASE, "auto_sync_hours": 0}, now=99999.0)
        self.assertFalse(synced)

    def test_does_not_sync_when_interval_not_elapsed(self):
        now = 3600.0
        synced, _ = _run_worker_tick(
            {**self._BASE, "auto_sync_hours": 6.0, "last_sync_time": now - 3000},
            now=now,
        )
        self.assertFalse(synced)

    def test_syncs_when_interval_has_elapsed(self):
        synced, _ = _run_worker_tick({**self._BASE, "last_sync_time": 0.0}, now=7 * 3600.0)
        self.assertTrue(synced)

    def test_does_not_sync_when_streaming_active(self):
        synced, _ = _run_worker_tick(
            {**self._BASE, "last_sync_time": 0.0}, now=7 * 3600.0, streaming=True
        )
        self.assertFalse(synced)

    def test_saves_last_sync_time_after_successful_sync(self):
        _, saved = _run_worker_tick({**self._BASE, "last_sync_time": 0.0}, now=7 * 3600.0)
        self.assertTrue(saved)

    def test_does_not_save_when_sync_is_skipped(self):
        _, saved = _run_worker_tick({**self._BASE, "auto_sync_hours": 0}, now=99999.0)
        self.assertFalse(saved)

    def test_does_not_save_when_streaming_blocks_sync(self):
        _, saved = _run_worker_tick(
            {**self._BASE, "last_sync_time": 0.0}, now=7 * 3600.0, streaming=True
        )
        self.assertFalse(saved)

    def test_does_not_crash_when_sync_raises(self):
        # Must complete without propagating the exception
        _run_worker_tick(
            {**self._BASE, "last_sync_time": 0.0},
            now=7 * 3600.0,
            sync_raises=RuntimeError("UAC cancelled"),
        )

    def test_does_not_save_when_sync_raises(self):
        _, saved = _run_worker_tick(
            {**self._BASE, "last_sync_time": 0.0},
            now=7 * 3600.0,
            sync_raises=RuntimeError("UAC cancelled"),
        )
        self.assertFalse(saved)

    def test_syncs_exactly_at_interval_boundary(self):
        interval, last = 6.0, 1000.0
        synced, _ = _run_worker_tick(
            {**self._BASE, "auto_sync_hours": interval, "last_sync_time": last},
            now=last + interval * 3600,
        )
        self.assertTrue(synced)

    def test_does_not_sync_one_second_before_interval(self):
        interval, last = 6.0, 1000.0
        synced, _ = _run_worker_tick(
            {**self._BASE, "auto_sync_hours": interval, "last_sync_time": last},
            now=last + interval * 3600 - 1,
        )
        self.assertFalse(synced)

    def test_settings_read_fresh_each_tick(self):
        """_load_settings_data must be called on every tick so live changes take effect."""
        ticks = [0]

        def fake_wait(timeout):
            ticks[0] += 1
            return ticks[0] > 2  # two ticks

        mock_load = MagicMock(return_value=Settings(auto_sync_hours=0))

        with patch.object(server._sync_stop, "wait", side_effect=fake_wait), \
             patch("server._load_settings", mock_load), \
             patch("server.time.time", return_value=0.0), \
             patch("server._is_streaming_active", return_value=False), \
             patch("server._do_auto_sync"), \
             patch("server._patch_settings"), \
             patch("server._append_log"):
            _sync_worker()

        self.assertEqual(mock_load.call_count, 2)


# ---------------------------------------------------------------------------
# _do_auto_sync
# ---------------------------------------------------------------------------


class TestDoAutoSync(unittest.TestCase):
    def _run(self, settings_data, games=None):
        """Run _do_auto_sync with the given settings and games; return (mock_write, mock_restart)."""
        if games is None:
            games = FAKE_GAMES
        fake_config = MagicMock()
        fake_config.model_dump_json.return_value = '{"apps": []}'
        mock_write = MagicMock()
        mock_restart = MagicMock()
        settings = Settings(**settings_data) if isinstance(settings_data, dict) else settings_data

        with patch("server._load_settings", return_value=settings), \
             patch("server.get_recent_games", return_value=games), \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", return_value=fake_config), \
             patch("server._write_elevated", mock_write), \
             patch("server._restart_elevated", mock_restart):
            _do_auto_sync()

        return mock_write, mock_restart

    def test_writes_and_restarts_when_games_available(self):
        mock_write, mock_restart = self._run({"count": 10, "unchecked_games": []})
        mock_write.assert_called_once()
        mock_restart.assert_called_once()

    def test_does_nothing_when_no_games_returned(self):
        mock_write, mock_restart = self._run({"count": 10, "unchecked_games": []}, games=[])
        mock_write.assert_not_called()
        mock_restart.assert_not_called()

    def test_does_nothing_when_all_games_unchecked(self):
        mock_write, mock_restart = self._run({"count": 10, "unchecked_games": [100, 200]})
        mock_write.assert_not_called()
        mock_restart.assert_not_called()

    def test_filters_unchecked_games_before_building_config(self):
        captured = {}

        def fake_build(existing, games, **kwargs):
            captured["games"] = games
            m = MagicMock()
            m.model_dump_json.return_value = "{}"
            return m

        with patch("server._load_settings", return_value=Settings(count=10, unchecked_games=[100])), \
             patch("server.get_recent_games", return_value=FAKE_GAMES), \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", side_effect=fake_build), \
             patch("server._write_elevated"), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        ids = {g.app_id for g in captured["games"]}
        self.assertNotIn(100, ids)
        self.assertIn(200, ids)

    def test_requests_correct_game_count_from_steam(self):
        with patch("server._load_settings", return_value=Settings(count=5)), \
             patch("server.get_recent_games", return_value=[]) as mock_get, \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", return_value=MagicMock()), \
             patch("server._write_elevated"), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        mock_get.assert_called_once_with(5)

    def test_defaults_game_count_to_10(self):
        with patch("server._load_settings", return_value=Settings()), \
             patch("server.get_recent_games", return_value=[]) as mock_get, \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", return_value=MagicMock()), \
             patch("server._write_elevated"), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        mock_get.assert_called_once_with(10)

    def test_write_targets_configured_path(self):
        expected = Path("/custom/apps.json")
        mock_write = MagicMock()
        fake_config = MagicMock()
        fake_config.model_dump_json.return_value = "{}"

        with patch("server._load_settings", return_value=Settings(count=10)), \
             patch("server.get_recent_games", return_value=FAKE_GAMES), \
             patch("server._load_config_path", return_value=expected), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", return_value=fake_config), \
             patch("server._write_elevated", mock_write), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        self.assertEqual(mock_write.call_args[0][0], expected)

    def test_partially_unchecked_games_still_syncs_remainder(self):
        mock_write, mock_restart = self._run({"count": 10, "unchecked_games": [100]})
        mock_write.assert_called_once()
        mock_restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()
