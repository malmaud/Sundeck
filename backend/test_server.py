"""Tests for server.py — streaming detection, auto-sync logic, and file watcher."""
import json
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
from server import Settings, _do_auto_sync, _is_streaming_active, _try_auto_sync, _SyncEventHandler, _schedule_sync, _set_sync_state, _get_sync_state
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

    def test_returns_false_with_no_streaming_ports_bound(self):
        self.assertFalse(self._netstat(
            "  UDP    0.0.0.0:47990          *:*\n"
        ))

    def test_returns_true_when_video_port_bound(self):
        self.assertTrue(self._netstat(
            "  UDP    0.0.0.0:47998          *:*\n"
        ))

    def test_returns_true_when_control_port_bound(self):
        self.assertTrue(self._netstat(
            "  UDP    0.0.0.0:47999          *:*\n"
        ))

    def test_returns_true_when_audio_port_bound(self):
        self.assertTrue(self._netstat(
            "  UDP    0.0.0.0:48000          *:*\n"
        ))

    def test_returns_true_when_streaming_port_among_multiple_lines(self):
        self.assertTrue(self._netstat(
            "  UDP    0.0.0.0:5353           *:*\n"
            "  UDP    0.0.0.0:47998          *:*\n"
            "  UDP    0.0.0.0:1900           *:*\n"
        ))

    def test_returns_false_when_only_tcp_lines_present(self):
        # The function only inspects UDP output; TCP lines are ignored
        self.assertFalse(self._netstat(
            "  TCP    0.0.0.0:47998          0.0.0.0:0   LISTENING\n"
        ))

    def test_returns_false_when_only_non_streaming_udp_ports_bound(self):
        self.assertFalse(self._netstat(
            "  UDP    0.0.0.0:5353           *:*\n"
            "  UDP    0.0.0.0:1900           *:*\n"
        ))

    def test_returns_false_on_subprocess_error(self):
        with patch("server.subprocess.check_output", side_effect=OSError):
            self.assertFalse(_is_streaming_active())

    def test_returns_false_with_empty_output(self):
        self.assertFalse(self._netstat(""))

    def test_unrelated_udp_port_does_not_trigger(self):
        self.assertFalse(self._netstat(
            "  UDP    0.0.0.0:5000           *:*\n"
        ))


# ---------------------------------------------------------------------------
# _try_auto_sync
# ---------------------------------------------------------------------------


class TestTryAutoSync(unittest.TestCase):
    def _run(self, auto_sync=True, streaming=False, sync_returns=False, sync_raises=None):
        """Call _try_auto_sync with mocks; return (sync_called, log_mock, schedule_mock)."""
        settings = Settings(auto_sync=auto_sync)
        side = sync_raises if sync_raises else MagicMock(return_value=sync_returns)
        mock_sync = MagicMock(side_effect=side)
        mock_log = MagicMock()
        mock_schedule = MagicMock()

        with patch("server._load_settings", return_value=settings), \
             patch("server._is_streaming_active", return_value=streaming), \
             patch("server._do_auto_sync", mock_sync), \
             patch("server._append_log", mock_log), \
             patch("server._schedule_sync", mock_schedule):
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


# ---------------------------------------------------------------------------
# sync state transitions
# ---------------------------------------------------------------------------


class TestSyncState(unittest.TestCase):
    def setUp(self):
        _set_sync_state("idle")
        with server._sync_timer_lock:
            if server._sync_timer is not None:
                server._sync_timer.cancel()
                server._sync_timer = None

    def test_schedule_sync_sets_pending(self):
        with patch("server._try_auto_sync"):
            _schedule_sync(delay=60)
        self.assertEqual(_get_sync_state(), "pending")

    def test_try_auto_sync_resets_to_idle_after_success(self):
        with patch("server._load_settings", return_value=Settings(auto_sync=True)), \
             patch("server._is_streaming_active", return_value=False), \
             patch("server._do_auto_sync", return_value=True), \
             patch("server._append_log"):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_resets_to_idle_when_sync_raises(self):
        with patch("server._load_settings", return_value=Settings(auto_sync=True)), \
             patch("server._is_streaming_active", return_value=False), \
             patch("server._do_auto_sync", side_effect=RuntimeError("fail")), \
             patch("server._append_log"):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_resets_to_idle_when_disabled(self):
        _set_sync_state("pending")
        with patch("server._load_settings", return_value=Settings(auto_sync=False)):
            _try_auto_sync()

        self.assertEqual(_get_sync_state(), "idle")

    def test_try_auto_sync_stays_pending_when_streaming(self):
        _set_sync_state("pending")
        with patch("server._load_settings", return_value=Settings(auto_sync=True)), \
             patch("server._is_streaming_active", return_value=True), \
             patch("server._schedule_sync"):
            _try_auto_sync()

        # State remains pending (re-scheduled), not bumped to syncing or reset to idle
        self.assertEqual(_get_sync_state(), "pending")

    def test_api_sync_status_returns_idle(self):
        _set_sync_state("idle")
        with server.app.test_client() as client:
            resp = client.get("/api/sync-status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["state"], "idle")

    def test_api_sync_status_returns_pending(self):
        _set_sync_state("pending")
        with server.app.test_client() as client:
            resp = client.get("/api/sync-status")
        self.assertEqual(resp.get_json()["state"], "pending")

    def test_api_sync_status_returns_syncing(self):
        _set_sync_state("syncing")
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
        with patch("server._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_called_once()

    def test_ignores_non_matching_filename(self):
        handler = _SyncEventHandler({"localconfig.vdf"})
        event = MagicMock(is_directory=False, src_path=r"C:\Steam\userdata\123\config\other.vdf")
        with patch("server._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_not_called()

    def test_ignores_directory_events(self):
        handler = _SyncEventHandler({"localconfig.vdf"})
        event = MagicMock(is_directory=True, src_path=r"C:\Steam\userdata\123\config")
        with patch("server._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_not_called()

    def test_matching_is_case_insensitive(self):
        handler = _SyncEventHandler({"LocalConfig.VDF"})
        event = MagicMock(is_directory=False, src_path=r"C:\Steam\localconfig.vdf")
        with patch("server._schedule_sync") as mock_schedule:
            handler.on_modified(event)
        mock_schedule.assert_called_once()


# ---------------------------------------------------------------------------
# _do_auto_sync
# ---------------------------------------------------------------------------


class TestDoAutoSync(unittest.TestCase):
    def _run(self, settings_data, games=None):
        """Run _do_auto_sync with the given settings and games; return (mock_write, mock_restart, result)."""
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
             patch("server.get_thumbnail", return_value="/t/fake.png"), \
             patch("server._write_elevated", mock_write), \
             patch("server._restart_elevated", mock_restart):
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

        with patch("server._load_settings", return_value=Settings(count=10, excluded_games=[100])), \
             patch("server.get_recent_games", return_value=FAKE_GAMES), \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", side_effect=fake_build), \
             patch("server.get_thumbnail", return_value="/t/200.png"), \
             patch("server._write_elevated"), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        ids = {g.app_id for g in captured["games"]}
        self.assertNotIn(100, ids)
        self.assertIn(200, ids)

    def test_fetches_all_games_without_thumbnails(self):
        with patch("server._load_settings", return_value=Settings(count=5)), \
             patch("server.get_recent_games", return_value=[]) as mock_get, \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=MagicMock()), \
             patch("server.build_sunshine_config", return_value=MagicMock()), \
             patch("server._write_elevated"), \
             patch("server._restart_elevated"):
            _do_auto_sync()

        mock_get.assert_called_once_with(count=None, fetch_thumbnails=False)

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
             patch("server.get_thumbnail", return_value="/t/fake.png"), \
             patch("server._write_elevated", mock_write), \
             patch("server._restart_elevated"):
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

        with patch("server._load_settings", return_value=Settings(count=10)), \
             patch("server.get_recent_games", return_value=FAKE_GAMES), \
             patch("server._load_config_path", return_value=Path("/fake/apps.json")), \
             patch("server.load_sunshine_config", return_value=existing), \
             patch("server.build_sunshine_config", return_value=new_config), \
             patch("server.get_thumbnail", return_value="/t/fake.png"), \
             patch("server._write_elevated", mock_write), \
             patch("server._restart_elevated", mock_restart):
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


# ---------------------------------------------------------------------------
# _schedule_sync — debouncing
# ---------------------------------------------------------------------------


class TestScheduleSync(unittest.TestCase):
    def setUp(self):
        # Reset global timer state between tests.
        with server._sync_timer_lock:
            if server._sync_timer is not None:
                server._sync_timer.cancel()
                server._sync_timer = None

    def test_debounces_rapid_calls(self):
        """Multiple rapid calls should result in only one _try_auto_sync invocation."""
        with patch("server._try_auto_sync") as mock_sync:
            for _ in range(5):
                _schedule_sync(delay=0.05)
            time.sleep(0.2)
        mock_sync.assert_called_once()

    def test_fires_after_delay(self):
        """A single call should fire _try_auto_sync after the delay."""
        with patch("server._try_auto_sync") as mock_sync:
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

    def test_does_not_trigger_on_config_path(self):
        with patch("server._patch_settings"), \
             patch("server._schedule_sync") as mock_schedule:
            self._post({"config_path": r"C:\foo\apps.json"}, mock_schedule)
        mock_schedule.assert_not_called()

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
        with server._sse_lock:
            server._sse_subscribers.clear()

    def tearDown(self):
        with server._sse_lock:
            server._sse_subscribers.clear()

    def test_delivers_message_to_subscriber(self):
        q = queue.SimpleQueue()
        with server._sse_lock:
            server._sse_subscribers.add(q)
        server._sse_push("my_event", '{"x": 1}')
        msg = q.get_nowait()
        self.assertIn("event: my_event", msg)
        self.assertIn('"x": 1', msg)

    def test_delivers_to_all_subscribers(self):
        q1, q2 = queue.SimpleQueue(), queue.SimpleQueue()
        with server._sse_lock:
            server._sse_subscribers.update({q1, q2})
        server._sse_push("ev", "data")
        self.assertFalse(q1.empty())
        self.assertFalse(q2.empty())

    def test_no_error_with_no_subscribers(self):
        server._sse_push("ev", "data")  # must not raise

    def test_removes_dead_subscriber(self):
        dead = MagicMock()
        dead.put_nowait.side_effect = Exception("broken pipe")
        with server._sse_lock:
            server._sse_subscribers.add(dead)
        server._sse_push("ev", "data")
        self.assertNotIn(dead, server._sse_subscribers)

    def test_message_ends_with_double_newline(self):
        q = queue.SimpleQueue()
        with server._sse_lock:
            server._sse_subscribers.add(q)
        server._sse_push("ev", "payload")
        msg = q.get_nowait()
        self.assertTrue(msg.endswith("\n\n"))


# ---------------------------------------------------------------------------
# SSE — state-change side effects
# ---------------------------------------------------------------------------


class TestSseStateChangeSideEffects(unittest.TestCase):
    def setUp(self):
        with server._sse_lock:
            server._sse_subscribers.clear()
        _set_sync_state("idle")

    def tearDown(self):
        with server._sse_lock:
            server._sse_subscribers.clear()

    def _subscribe(self):
        q = queue.SimpleQueue()
        with server._sse_lock:
            server._sse_subscribers.add(q)
        return q

    def test_set_sync_state_pushes_sync_status_event(self):
        q = self._subscribe()
        _set_sync_state("syncing")
        msg = q.get_nowait()
        self.assertIn("event: sync_status", msg)
        data = json.loads(msg.split("data: ")[1].strip())
        self.assertEqual(data["state"], "syncing")

    def test_bump_games_version_pushes_sync_status_event(self):
        q = self._subscribe()
        server._bump_games_version()
        msg = q.get_nowait()
        self.assertIn("event: sync_status", msg)
        data = json.loads(msg.split("data: ")[1].strip())
        self.assertIn("games_version", data)

    def test_append_log_pushes_log_updated_event(self):
        q = self._subscribe()
        with patch("server._save_log"):
            server._append_log("auto", True, "Synced games")
        msg = q.get_nowait()
        self.assertIn("event: log_updated", msg)


# ---------------------------------------------------------------------------
# SSE — /api/events route
# ---------------------------------------------------------------------------


class TestApiEventsRoute(unittest.TestCase):
    def setUp(self):
        with server._sse_lock:
            server._sse_subscribers.clear()
        _set_sync_state("idle")

    def tearDown(self):
        with server._sse_lock:
            server._sse_subscribers.clear()

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
        _set_sync_state("pending")
        gen, first, _ = self._open_stream()
        gen.close()
        data = json.loads(first.split("data: ")[1].strip())
        self.assertEqual(data["state"], "pending")
        self.assertIn("games_version", data)

    def test_subscriber_registered_after_first_yield(self):
        initial = len(server._sse_subscribers)
        gen, _, _ = self._open_stream()
        self.assertEqual(len(server._sse_subscribers), initial + 1)
        gen.close()

    def test_subscriber_removed_on_close(self):
        gen, _, _ = self._open_stream()
        count_open = len(server._sse_subscribers)
        gen.close()
        self.assertEqual(len(server._sse_subscribers), count_open - 1)

    def test_pushed_event_appears_in_stream(self):
        gen, _, _ = self._open_stream()
        server._sse_push("custom_event", '{"hello": "world"}')
        chunk = next(gen)
        gen.close()
        if isinstance(chunk, bytes):
            chunk = chunk.decode()
        self.assertIn("event: custom_event", chunk)
        self.assertIn('"hello": "world"', chunk)


if __name__ == "__main__":
    unittest.main()
