"""Tests for server.py — streaming detection, auto-sync logic, and file watcher."""
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
import sync_engine
from models import SyncState
from server import Settings, _do_auto_sync, _is_streaming_active, _try_auto_sync, _SyncEventHandler, _schedule_sync, _set_sync_state, _get_sync_state
from steam import SteamGame


FAKE_GAMES = [
    SteamGame(app_id=100, name="Half-Life", thumbnail="/t/100.png"),
    SteamGame(app_id=200, name="Portal", thumbnail="/t/200.png"),
]


# ---------------------------------------------------------------------------
# _is_streaming_active (log-file-based detection)
# ---------------------------------------------------------------------------


class TestIsStreamingActive(unittest.TestCase):
    """Tests for Apollo session detection via log file parsing."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._log_dir = Path(self._tmpdir.name)
        self._log_file = self._log_dir / "sunshine.log"
        self._log_backup = self._log_dir / "sunshine.log.backup"
        self._config_path = self._log_dir / "apps.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _check(self, log_content="", backup_content=None):
        """Write log content and call _is_streaming_active."""
        if log_content:
            self._log_file.write_text(log_content, encoding="utf-8")
        if backup_content is not None:
            self._log_backup.write_text(backup_content, encoding="utf-8")
        with patch("sync_engine._load_config_path", return_value=self._config_path):
            return _is_streaming_active()

    def test_returns_true_when_app_launched(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: Launching app [440] with UUID [abc-123]\n'
        ))

    def test_returns_true_when_session_paused(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: Session pausing for app [Half-Life].\n'
        ))

    def test_returns_true_when_session_resumed(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: Session resuming for app [Half-Life].\n'
        ))

    def test_returns_true_when_app_auto_detached(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: Treating the app as a detached command.\n'
        ))

    def test_returns_false_when_app_exited(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Launching app [440]\n'
            '[2026-03-26 10:01:00]: Info: All app processes have successfully exited.\n'
        ))

    def test_returns_false_when_process_terminated(self):
        self.assertFalse(self._check(
            '[2026-03-26 09:59:24]: Info: Session resuming for app [The Binding of Isaac: Rebirth].\n'
            '[2026-03-26 09:59:55]: Info: Process terminated\n'
        ))

    def test_returns_false_when_app_forcefully_terminated(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Launching app [440]\n'
            '[2026-03-26 10:01:00]: Info: Forcefully terminating the app\n'
        ))

    def test_returns_false_when_session_already_stopped(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Session already stopped, do not run pause commands.\n'
        ))

    def test_returns_false_when_no_graceful_exit_timeout(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: No graceful exit timeout was specified\n'
        ))

    def test_returns_false_when_terminate_on_pause(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Terminating app [Half-Life] when all clients are disconnected.\n'
        ))

    def test_returns_false_when_log_empty(self):
        self.assertFalse(self._check(""))

    def test_returns_false_when_no_log_files_exist(self):
        with patch("sync_engine._load_config_path", return_value=self._config_path):
            self.assertFalse(_is_streaming_active())

    def test_returns_false_when_only_unrelated_log_lines(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Configuration UI available at [https://localhost:47990]\n'
            '[2026-03-26 10:00:01]: Info: Registered Apollo mDNS service\n'
        ))

    def test_most_recent_event_wins_running_after_stopped(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: All app processes have successfully exited.\n'
            '[2026-03-26 10:01:00]: Info: Launching app [440]\n'
        ))

    def test_most_recent_event_wins_stopped_after_running(self):
        self.assertFalse(self._check(
            '[2026-03-26 10:00:00]: Info: Launching app [440]\n'
            '[2026-03-26 10:01:00]: Info: All app processes have successfully exited.\n'
        ))

    def test_pause_after_launch_means_still_running(self):
        self.assertTrue(self._check(
            '[2026-03-26 10:00:00]: Info: Launching app [440]\n'
            '[2026-03-26 10:00:30]: Info: New streaming session started\n'
            '[2026-03-26 10:05:00]: Info: Session pausing for app [Half-Life].\n'
        ))

    def test_falls_back_to_backup_log_when_current_has_no_events(self):
        self.assertTrue(self._check(
            log_content='[2026-03-26 10:00:00]: Info: Configuration UI available\n',
            backup_content='[2026-03-26 09:00:00]: Info: Launching app [440]\n',
        ))

    def test_current_log_takes_precedence_over_backup(self):
        self.assertFalse(self._check(
            log_content='[2026-03-26 10:01:00]: Info: All app processes have successfully exited.\n',
            backup_content='[2026-03-26 09:00:00]: Info: Launching app [440]\n',
        ))

    def test_handles_read_error_gracefully(self):
        with patch("sync_engine._load_config_path", return_value=self._config_path):
            self._log_file.write_text("Launching app [440]", encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                self.assertFalse(_is_streaming_active())


# ---------------------------------------------------------------------------
# _try_auto_sync
# ---------------------------------------------------------------------------


class TestTryAutoSync(unittest.TestCase):
    def _run(self, auto_sync=True, streaming=False, sync_returns=False, sync_raises=None, config_path=r"C:\fake\apps.json"):
        """Call _try_auto_sync with mocks; return (sync_called, log_mock, schedule_mock)."""
        settings = Settings(auto_sync=auto_sync, config_path=config_path)
        side = sync_raises if sync_raises else MagicMock(return_value=sync_returns)
        mock_sync = MagicMock(side_effect=side)
        mock_log = MagicMock()
        mock_schedule = MagicMock()

        with patch("sync_engine._load_settings", return_value=settings), \
             patch("sync_engine._is_streaming_active", return_value=streaming), \
             patch("sync_engine._do_auto_sync", mock_sync), \
             patch("sync_engine._append_log", mock_log), \
             patch("sync_engine._schedule_sync", mock_schedule):
            _try_auto_sync()

        return mock_sync.called, mock_log, mock_schedule

    def test_does_not_sync_when_disabled(self):
        synced, _, _ = self._run(auto_sync=False)
        self.assertFalse(synced)

    def test_calls_sync_when_enabled(self):
        synced, _, _ = self._run(auto_sync=True)
        self.assertTrue(synced)

    def test_defers_when_streaming_active(self):
        synced, _, mock_schedule = self._run(auto_sync=True, streaming=True)
        self.assertFalse(synced)
        mock_schedule.assert_called_once()

    def test_logs_success_when_sync_returns_true(self):
        _, mock_log, _ = self._run(sync_returns=True)
        mock_log.assert_called_once_with("auto", True, "Synced games")

    def test_does_not_log_when_sync_returns_false(self):
        _, mock_log, _ = self._run(sync_returns=False)
        mock_log.assert_not_called()

    def test_does_not_crash_when_sync_raises(self):
        self._run(sync_raises=RuntimeError("UAC cancelled"))

    def test_logs_error_when_sync_raises(self):
        _, mock_log, _ = self._run(sync_raises=RuntimeError("UAC cancelled"))
        mock_log.assert_called_once()
        args = mock_log.call_args[0]
        self.assertEqual(args[0], "auto")
        self.assertFalse(args[1])

    def test_does_not_log_when_disabled(self):
        _, mock_log, _ = self._run(auto_sync=False)
        mock_log.assert_not_called()

    def test_does_not_sync_when_config_path_not_set(self):
        synced, _, _ = self._run(auto_sync=True, config_path=None)
        self.assertFalse(synced)

    def test_resets_to_idle_when_config_path_not_set(self):
        _set_sync_state(SyncState.PENDING)
        synced, _, _ = self._run(auto_sync=True, config_path=None)
        self.assertFalse(synced)
        self.assertEqual(_get_sync_state(), "idle")


# ---------------------------------------------------------------------------
# sync state transitions
# ---------------------------------------------------------------------------


class TestSyncState(unittest.TestCase):
    def setUp(self):
        _set_sync_state(SyncState.IDLE)
        with sync_engine._sync_timer_lock:
            if sync_engine._sync_timer is not None:
                sync_engine._sync_timer.cancel()
                sync_engine._sync_timer = None

    def test_schedule_sync_sets_pending(self):
        with patch("sync_engine._try_auto_sync"):
            _schedule_sync(delay=60)
        self.assertEqual(_get_sync_state(), "pending")

    def test_try_auto_sync_resets_to_idle_after_success(self):
        with patch("sync_engine._load_settings", return_value=Settings(auto_sync=True, config_path=r"C:\fake")), \
             patch("sync_engine._is_streaming_active", return_value=False), \
             patch("sync_engine._do_auto_sync", return_value=True), \
             patch("sync_engine._append_log"):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_resets_to_idle_when_sync_raises(self):
        with patch("sync_engine._load_settings", return_value=Settings(auto_sync=True, config_path=r"C:\fake")), \
             patch("sync_engine._is_streaming_active", return_value=False), \
             patch("sync_engine._do_auto_sync", side_effect=RuntimeError("fail")), \
             patch("sync_engine._append_log"):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_resets_to_idle_when_disabled(self):
        _set_sync_state(SyncState.PENDING)
        with patch("sync_engine._load_settings", return_value=Settings(auto_sync=False)):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_stays_pending_when_streaming(self):
        _set_sync_state(SyncState.PENDING)
        with patch("sync_engine._load_settings", return_value=Settings(auto_sync=True, config_path=r"C:\fake")), \
             patch("sync_engine._is_streaming_active", return_value=True), \
             patch("sync_engine._schedule_sync"):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "pending")

    def test_api_sync_status_returns_idle(self):
        _set_sync_state(SyncState.IDLE)
        with server.app.test_client() as client:
            resp = client.get("/api/sync-status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["state"], "idle")

    def test_api_sync_status_returns_pending(self):
        _set_sync_state(SyncState.PENDING)
        with server.app.test_client() as client:
            resp = client.get("/api/sync-status")
        self.assertEqual(resp.get_json()["state"], "pending")

    def test_api_sync_status_returns_syncing(self):
        _set_sync_state(SyncState.SYNCING)
        with server.app.test_client() as client:
            resp = client.get("/api/sync-status")
        self.assertEqual(resp.get_json()["state"], "syncing")


# ---------------------------------------------------------------------------
# _SyncEventHandler
# ---------------------------------------------------------------------------


class TestSyncEventHandler(unittest.TestCase):
    def test_triggers_on_matching_filename(self):
        handler = _SyncEventHandler({"localconfig.vdf"})
        event = MagicMock(is_directory=False, src_path=r"C:\Steam\userdata\123\config\localconfig.vdf")
        with patch("sync_engine._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_called_once()

    def test_ignores_non_matching_filename(self):
        handler = _SyncEventHandler({"localconfig.vdf"})
        event = MagicMock(is_directory=False, src_path=r"C:\Steam\userdata\123\config\other.vdf")
        with patch("sync_engine._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_not_called()

    def test_ignores_directory_events(self):
        handler = _SyncEventHandler({"localconfig.vdf"})
        event = MagicMock(is_directory=True, src_path=r"C:\Steam\userdata\123\config")
        with patch("sync_engine._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_not_called()

    def test_matching_is_case_insensitive(self):
        handler = _SyncEventHandler({"LocalConfig.VDF"})
        event = MagicMock(is_directory=False, src_path=r"C:\Steam\localconfig.vdf")
        with patch("sync_engine._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_called_once()


# ---------------------------------------------------------------------------
# _do_auto_sync
# ---------------------------------------------------------------------------


class TestDoAutoSync(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_path = Path(self._tmpdir.name) / "apps.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, settings_data, games=None):
        """Run _do_auto_sync with the given settings and games; return (mock_write, mock_restart, result)."""
        if games is None:
            games = FAKE_GAMES
        fake_config = MagicMock()
        fake_config.model_dump_json.return_value = '{"apps": []}'
        mock_write = MagicMock()
        mock_restart = MagicMock()
        settings = Settings(**settings_data) if isinstance(settings_data, dict) else settings_data

        with patch("sync_engine._load_settings", return_value=settings), \
             patch("sync_engine.get_recent_games", return_value=games), \
             patch("sync_engine._load_config_path", return_value=self._config_path), \
             patch("sync_engine.load_sunshine_config", return_value=MagicMock()), \
             patch("sync_engine.build_sunshine_config", return_value=fake_config), \
             patch("sync_engine.get_thumbnail", return_value="/t/fake.png"), \
             patch("sync_engine._write_elevated", mock_write), \
             patch("sync_engine._restart_elevated", mock_restart):
            result = _do_auto_sync()

        return mock_write, mock_restart, result

    def test_writes_and_restarts_when_games_available(self):
        mock_write, mock_restart, result = self._run({"count": 10, "excluded_games": []})
        mock_write.assert_called_once()
        mock_restart.assert_called_once()
        self.assertTrue(result)

    def test_returns_false_when_no_games_returned(self):
        _, _, result = self._run({"count": 10, "excluded_games": []}, games=[])
        self.assertFalse(result)

    def test_does_nothing_when_all_games_unchecked(self):
        mock_write, mock_restart, result = self._run({"count": 10, "excluded_games": [100, 200]})
        mock_write.assert_not_called()
        mock_restart.assert_not_called()
        self.assertFalse(result)

    def test_filters_excluded_games_before_building_config(self):
        captured = {}

        def fake_build(existing, games, **kwargs):
            captured["games"] = games
            m = MagicMock()
            m.model_dump_json.return_value = "{}"
            return m

        with patch("sync_engine._load_settings", return_value=Settings(count=10, excluded_games=[100])), \
             patch("sync_engine.get_recent_games", return_value=FAKE_GAMES), \
             patch("sync_engine._load_config_path", return_value=self._config_path), \
             patch("sync_engine.load_sunshine_config", return_value=MagicMock()), \
             patch("sync_engine.build_sunshine_config", side_effect=fake_build), \
             patch("sync_engine.get_thumbnail", return_value="/t/200.png"), \
             patch("sync_engine._write_elevated"), \
             patch("sync_engine._restart_elevated"):
            _do_auto_sync()

        ids = {g.app_id for g in captured["games"]}
        self.assertNotIn(100, ids)
        self.assertIn(200, ids)

    def test_fetches_all_games_without_thumbnails(self):
        with patch("sync_engine._load_settings", return_value=Settings(count=5)), \
             patch("sync_engine.get_recent_games", return_value=[]) as mock_get, \
             patch("sync_engine._load_config_path", return_value=self._config_path), \
             patch("sync_engine.load_sunshine_config", return_value=MagicMock()), \
             patch("sync_engine.build_sunshine_config", return_value=MagicMock()), \
             patch("sync_engine._write_elevated"), \
             patch("sync_engine._restart_elevated"):
            _do_auto_sync()

        mock_get.assert_called_once_with(count=None, fetch_thumbnails=False)

    def test_write_targets_configured_path(self):
        expected = self._config_path
        mock_write = MagicMock()
        fake_config = MagicMock()
        fake_config.model_dump_json.return_value = "{}"

        with patch("sync_engine._load_settings", return_value=Settings(count=10)), \
             patch("sync_engine.get_recent_games", return_value=FAKE_GAMES), \
             patch("sync_engine._load_config_path", return_value=expected), \
             patch("sync_engine.load_sunshine_config", return_value=MagicMock()), \
             patch("sync_engine.build_sunshine_config", return_value=fake_config), \
             patch("sync_engine.get_thumbnail", return_value="/t/fake.png"), \
             patch("sync_engine._write_elevated", mock_write), \
             patch("sync_engine._restart_elevated"):
            _do_auto_sync()

        self.assertEqual(mock_write.call_args[0][0], expected)

    def test_partially_excluded_games_still_syncs_remainder(self):
        mock_write, mock_restart, _ = self._run({"count": 10, "excluded_games": [100]})
        mock_write.assert_called_once()
        mock_restart.assert_called_once()

    def _run_noop(self):
        """Run _do_auto_sync where existing and new configs are identical."""
        same = {"apps": []}
        existing = MagicMock()
        existing.model_dump.return_value = same
        new_config = MagicMock()
        new_config.model_dump.return_value = same
        mock_write = MagicMock()
        mock_restart = MagicMock()

        with patch("sync_engine._load_settings", return_value=Settings(count=10)), \
             patch("sync_engine.get_recent_games", return_value=FAKE_GAMES), \
             patch("sync_engine._load_config_path", return_value=self._config_path), \
             patch("sync_engine.load_sunshine_config", return_value=existing), \
             patch("sync_engine.build_sunshine_config", return_value=new_config), \
             patch("sync_engine.get_thumbnail", return_value="/t/fake.png"), \
             patch("sync_engine._write_elevated", mock_write), \
             patch("sync_engine._restart_elevated", mock_restart):
            result = _do_auto_sync()

        return mock_write, mock_restart, result

    def test_skips_write_when_config_unchanged(self):
        mock_write, _, _ = self._run_noop()
        mock_write.assert_not_called()

    def test_skips_restart_when_config_unchanged(self):
        _, mock_restart, _ = self._run_noop()
        mock_restart.assert_not_called()

    def test_returns_false_when_config_unchanged(self):
        _, _, result = self._run_noop()
        self.assertFalse(result)

    def test_raises_when_config_dir_missing(self):
        bad_path = Path(self._tmpdir.name) / "nonexistent" / "apps.json"
        with patch("sync_engine._load_settings", return_value=Settings(count=10)), \
             patch("sync_engine.get_recent_games", return_value=FAKE_GAMES), \
             patch("sync_engine._load_config_path", return_value=bad_path), \
             patch("sync_engine.get_thumbnail", return_value="/t/fake.png"):
            with self.assertRaises(RuntimeError) as ctx:
                _do_auto_sync()
        self.assertIn("Config path not found", str(ctx.exception))


# ---------------------------------------------------------------------------
# _schedule_sync — debouncing
# ---------------------------------------------------------------------------


class TestScheduleSync(unittest.TestCase):
    def setUp(self):
        with sync_engine._sync_timer_lock:
            if sync_engine._sync_timer is not None:
                sync_engine._sync_timer.cancel()
                sync_engine._sync_timer = None

    def test_debounces_rapid_calls(self):
        """Multiple rapid calls should result in only one _try_auto_sync invocation."""
        with patch("sync_engine._try_auto_sync") as mock_sync:
            for _ in range(5):
                _schedule_sync(delay=0.05)
            time.sleep(0.2)
        mock_sync.assert_called_once()

    def test_fires_after_delay(self):
        """A single call should fire _try_auto_sync after the delay."""
        with patch("sync_engine._try_auto_sync") as mock_sync:
            _schedule_sync(delay=0.05)
            mock_sync.assert_not_called()   # not yet
            time.sleep(0.2)
        mock_sync.assert_called_once()


# ---------------------------------------------------------------------------
# api_update_settings — sync triggering
# ---------------------------------------------------------------------------


class TestUpdateSettingsTrigger(unittest.TestCase):
    """Verify that api_update_settings calls _schedule_sync for sync-relevant fields."""

    def _post(self, payload, mock_schedule):
        with server.app.test_client() as client:
            return client.post("/api/settings", json=payload)

    def test_triggers_sync_on_excluded_games(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"excluded_games": [100]}, mock_schedule)
        mock_schedule.assert_called_once()

    def test_triggers_sync_on_included_games(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"included_games": [100]}, mock_schedule)
        mock_schedule.assert_called_once()

    def test_triggers_sync_on_count(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"count": 5}, mock_schedule)
        mock_schedule.assert_called_once()

    def test_triggers_sync_on_auto_sync(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"auto_sync": True}, mock_schedule)
        mock_schedule.assert_called_once()

    def test_triggers_sync_on_config_path(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"config_path": r"C:\foo\apps.json"}, mock_schedule)
        mock_schedule.assert_called_once()

    def test_does_not_trigger_on_show_debug(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"show_debug": True}, mock_schedule)
        mock_schedule.assert_not_called()


# ---------------------------------------------------------------------------
# SSE — _sse_push
# ---------------------------------------------------------------------------


class TestSsePush(unittest.TestCase):
    def setUp(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()

    def tearDown(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()

    def test_delivers_message_to_subscriber(self):
        q = queue.SimpleQueue()
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.add(q)
        sync_engine._sse_push("my_event", '{"x": 1}')
        msg = q.get_nowait()
        self.assertIn("event: my_event", msg)
        self.assertIn('"x": 1', msg)

    def test_delivers_to_all_subscribers(self):
        q1, q2 = queue.SimpleQueue(), queue.SimpleQueue()
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.update({q1, q2})
        sync_engine._sse_push("ev", "data")
        self.assertFalse(q1.empty())
        self.assertFalse(q2.empty())

    def test_no_error_with_no_subscribers(self):
        sync_engine._sse_push("ev", "data")  # must not raise

    def test_removes_dead_subscriber(self):
        dead = MagicMock()
        dead.put_nowait.side_effect = Exception("broken pipe")
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.add(dead)
        sync_engine._sse_push("ev", "data")
        self.assertNotIn(dead, sync_engine._sse_subscribers)

    def test_message_ends_with_double_newline(self):
        q = queue.SimpleQueue()
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.add(q)
        sync_engine._sse_push("ev", "payload")
        msg = q.get_nowait()
        self.assertTrue(msg.endswith("\n\n"))


# ---------------------------------------------------------------------------
# SSE — state-change side effects
# ---------------------------------------------------------------------------


class TestSseStateChangeSideEffects(unittest.TestCase):
    def setUp(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()
        _set_sync_state(SyncState.IDLE)

    def tearDown(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()

    def _subscribe(self):
        q = queue.SimpleQueue()
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.add(q)
        return q

    def test_set_sync_state_pushes_sync_status_event(self):
        q = self._subscribe()
        _set_sync_state(SyncState.SYNCING)
        msg = q.get_nowait()
        self.assertIn("event: sync_status", msg)
        data = json.loads(msg.split("data: ")[1].strip())
        self.assertEqual(data["state"], "syncing")

    def test_bump_games_version_pushes_sync_status_event(self):
        q = self._subscribe()
        sync_engine._bump_games_version()
        msg = q.get_nowait()
        self.assertIn("event: sync_status", msg)
        data = json.loads(msg.split("data: ")[1].strip())
        self.assertIn("games_version", data)

    def test_append_log_pushes_log_updated_event(self):
        q = self._subscribe()
        with patch("sync_engine._save_log"):
            sync_engine._append_log("auto", True, "Synced games")
        msg = q.get_nowait()
        self.assertIn("event: log_updated", msg)


# ---------------------------------------------------------------------------
# SSE — /api/events route
# ---------------------------------------------------------------------------


class TestApiEventsRoute(unittest.TestCase):
    def setUp(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()
        _set_sync_state(SyncState.IDLE)

    def tearDown(self):
        with sync_engine._sse_lock:
            sync_engine._sse_subscribers.clear()

    def _open_stream(self):
        """Return (gen, first_chunk) — caller must call gen.close() when done."""
        with server.app.test_request_context("/api/events"):
            resp = server.api_events()
        gen = resp.response
        first = next(gen)
        if isinstance(first, bytes):
            first = first.decode()
        return gen, first, resp

    def test_content_type_is_event_stream(self):
        gen, _, resp = self._open_stream()
        gen.close()
        self.assertEqual(resp.mimetype, "text/event-stream")

    def test_initial_chunk_is_sync_status_event(self):
        gen, first, _ = self._open_stream()
        gen.close()
        self.assertIn("event: sync_status", first)

    def test_initial_chunk_contains_state_and_version(self):
        _set_sync_state(SyncState.PENDING)
        gen, first, _ = self._open_stream()
        gen.close()
        data = json.loads(first.split("data: ")[1].strip())
        self.assertEqual(data["state"], "pending")
        self.assertIn("games_version", data)

    def test_subscriber_registered_after_first_yield(self):
        initial = len(sync_engine._sse_subscribers)
        gen, _, _ = self._open_stream()
        self.assertEqual(len(sync_engine._sse_subscribers), initial + 1)
        gen.close()

    def test_subscriber_removed_on_close(self):
        gen, _, _ = self._open_stream()
        count_open = len(sync_engine._sse_subscribers)
        gen.close()
        self.assertEqual(len(sync_engine._sse_subscribers), count_open - 1)

    def test_pushed_event_appears_in_stream(self):
        gen, _, _ = self._open_stream()
        sync_engine._sse_push("custom_event", '{"hello": "world"}')
        chunk = next(gen)
        gen.close()
        if isinstance(chunk, bytes):
            chunk = chunk.decode()
        self.assertIn("event: custom_event", chunk)
        self.assertIn('"hello": "world"', chunk)


# ---------------------------------------------------------------------------
# api_get_settings — needs_setup flag
# ---------------------------------------------------------------------------


class TestNeedsSetup(unittest.TestCase):
    """Verify api_get_settings returns needs_setup flag based on config_path."""

    def test_needs_setup_true_when_config_path_not_set(self):
        with patch("server._load_settings", return_value=Settings()), \
             patch("server._load_config_path", return_value=Path(r"C:\Program Files\Apollo\config\apps.json")):
            with server.app.test_client() as client:
                resp = client.get("/api/settings")
        data = resp.get_json()
        self.assertTrue(data["needs_setup"])

    def test_needs_setup_false_when_config_path_set(self):
        with patch("server._load_settings", return_value=Settings(config_path=r"C:\custom\apps.json")), \
             patch("server._load_config_path", return_value=Path(r"C:\custom\apps.json")):
            with server.app.test_client() as client:
                resp = client.get("/api/settings")
        data = resp.get_json()
        self.assertFalse(data["needs_setup"])

    def test_returns_resolved_config_path(self):
        resolved = Path(r"C:\Program Files\Apollo\config\apps.json")
        with patch("server._load_settings", return_value=Settings()), \
             patch("server._load_config_path", return_value=resolved):
            with server.app.test_client() as client:
                resp = client.get("/api/settings")
        data = resp.get_json()
        self.assertEqual(data["config_path"], str(resolved))


# ---------------------------------------------------------------------------
# startup.py — registry helpers
# ---------------------------------------------------------------------------


import startup
from startup import set_run_at_startup, get_run_at_startup


class TestStartupRegistry(unittest.TestCase):
    def _mock_open_key(self):
        """Return (context_manager_mock, inner_key_mock) for winreg.OpenKey."""
        key = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=key)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx, key

    def test_enable_writes_registry_value(self):
        ctx, key = self._mock_open_key()
        with patch("startup.winreg.OpenKey", return_value=ctx), \
             patch("startup.winreg.SetValueEx") as mock_set, \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001), \
             patch("startup.winreg.KEY_SET_VALUE", 0x0002), \
             patch("startup.winreg.REG_SZ", 1):
            set_run_at_startup(True)
        mock_set.assert_called_once_with(key, "SunDeck", 0, 1, sys.executable)

    def test_disable_deletes_registry_value(self):
        ctx, key = self._mock_open_key()
        with patch("startup.winreg.OpenKey", return_value=ctx), \
             patch("startup.winreg.DeleteValue") as mock_del, \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001), \
             patch("startup.winreg.KEY_SET_VALUE", 0x0002):
            set_run_at_startup(False)
        mock_del.assert_called_once_with(key, "SunDeck")

    def test_disable_silently_ignores_already_absent_value(self):
        ctx, _ = self._mock_open_key()
        with patch("startup.winreg.OpenKey", return_value=ctx), \
             patch("startup.winreg.DeleteValue", side_effect=FileNotFoundError), \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001), \
             patch("startup.winreg.KEY_SET_VALUE", 0x0002):
            set_run_at_startup(False)   # must not raise

    def test_get_returns_true_when_value_matches_executable(self):
        ctx, _ = self._mock_open_key()
        with patch("startup.winreg.OpenKey", return_value=ctx), \
             patch("startup.winreg.QueryValueEx", return_value=(sys.executable, 1)), \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001):
            self.assertTrue(get_run_at_startup())

    def test_get_returns_false_when_value_points_to_different_exe(self):
        ctx, _ = self._mock_open_key()
        with patch("startup.winreg.OpenKey", return_value=ctx), \
             patch("startup.winreg.QueryValueEx", return_value=(r"C:\other\app.exe", 1)), \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001):
            self.assertFalse(get_run_at_startup())

    def test_get_returns_false_when_registry_key_absent(self):
        with patch("startup.winreg.OpenKey", side_effect=FileNotFoundError), \
             patch("startup.winreg.HKEY_CURRENT_USER", 0x80000001):
            self.assertFalse(get_run_at_startup())


# ---------------------------------------------------------------------------
# api_update_settings — run_at_startup handling
# ---------------------------------------------------------------------------


class TestRunAtStartupSetting(unittest.TestCase):
    def test_enabling_calls_set_run_at_startup_true(self):
        with patch("server._patch_settings"), \
             patch("server.set_run_at_startup") as mock_startup:
            with server.app.test_client() as client:
                client.post("/api/settings", json={"run_at_startup": True})
        mock_startup.assert_called_once_with(True)

    def test_disabling_calls_set_run_at_startup_false(self):
        with patch("server._patch_settings"), \
             patch("server.set_run_at_startup") as mock_startup:
            with server.app.test_client() as client:
                client.post("/api/settings", json={"run_at_startup": False})
        mock_startup.assert_called_once_with(False)

    def test_does_not_trigger_schedule_sync(self):
        with patch("server._patch_settings"), \
             patch("server.set_run_at_startup"), \
             patch("server._schedule_sync") as mock_schedule:
            with server.app.test_client() as client:
                client.post("/api/settings", json={"run_at_startup": True})
        mock_schedule.assert_not_called()

    def test_get_settings_includes_run_at_startup_field(self):
        with patch("server._load_settings", return_value=Settings(run_at_startup=True)), \
             patch("server._load_config_path", return_value=Path(r"C:\fake\apps.json")):
            with server.app.test_client() as client:
                resp = client.get("/api/settings")
        data = resp.get_json()
        self.assertIn("run_at_startup", data)
        self.assertTrue(data["run_at_startup"])

    def test_get_settings_run_at_startup_defaults_true(self):
        with patch("server._load_settings", return_value=Settings()), \
             patch("server._load_config_path", return_value=Path(r"C:\fake\apps.json")):
            with server.app.test_client() as client:
                resp = client.get("/api/settings")
        self.assertTrue(resp.get_json()["run_at_startup"])


if __name__ == "__main__":
    unittest.main()
