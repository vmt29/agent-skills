"""Offline checks for reviewer activity reporting; never invoke a real model."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "critique-loop"
sys.path.insert(0, str(SKILL_DIR))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def delta(kind, **payload):
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": kind, **payload},
        },
    }


def success_result():
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "e30a11da-0c1f-4eb0-af65-0ed3f665c239",
        "modelUsage": {"claude-fable-5-1": {"inputTokens": 1, "outputTokens": 1}},
        "result": "=== VERDICT ===\nblocking: 0\nmajor: 0\nneeds_human: none\n",
    }


class ActivityMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("reviewer_activity", SKILL_DIR / "reviewer_activity.py")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.raw = self.directory / "raw.txt"
        self.diagnostics = self.directory / "diagnostics.txt"
        self.raw.touch()
        self.diagnostics.touch()
        self.clock = FakeClock()
        self.monitor = self.module.ActivityMonitor(
            self.raw, self.diagnostics, now=self.clock
        )

    def emit(self, *events):
        with self.raw.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")
        self.monitor.poll()

    def debug(self, line):
        with self.diagnostics.open("a", encoding="utf-8") as stream:
            stream.write(line)
        self.monitor.poll()

    def snapshot(self, alive=True, stamp="2026-09-08T20:00:00Z"):
        return self.monitor.snapshot(
            process_alive=alive,
            elapsed_seconds=self.clock.value - 100.0,
            checked_at=stamp,
        )

    def test_partial_thinking_events_do_not_expose_their_content(self):
        private_text = "private reviewer reasoning 🧪 must not appear in status"
        event = (json.dumps(delta("thinking_delta", thinking=private_text), ensure_ascii=False) + "\n").encode()
        split = event.index("🧪".encode()) + 2
        with self.raw.open("ab") as stream:
            stream.write(event[:split])
        self.monitor.poll()
        partial = self.snapshot()
        self.assertEqual(0, partial["thinking_chars"])
        self.assertEqual(0, partial["events"])
        self.assertNotIn(private_text, json.dumps(partial))
        with self.raw.open("ab") as stream:
            stream.write(event[split:])
        self.monitor.poll()
        complete = self.snapshot()
        self.assertEqual(len(private_text), complete["thinking_chars"])
        self.assertEqual(1, complete["events"])
        self.assertEqual("thinking", complete["last_activity"])
        self.assertNotIn(private_text, json.dumps(complete, ensure_ascii=False))
        self.assertNotIn(private_text, self.module.format_status(complete))

    def test_polling_without_appended_events_does_not_count_them_twice(self):
        self.emit(delta("thinking_delta", thinking="private text"))
        first = self.snapshot()
        self.monitor.poll()
        self.monitor.poll()
        self.assertEqual(first, self.snapshot())

    def test_fresh_snapshots_advance_even_when_the_reviewer_is_silent(self):
        self.emit(delta("thinking_delta", thinking="private text"))
        first = self.snapshot()
        self.clock.advance(60)
        second = self.snapshot(stamp="2026-09-08T20:01:00Z")
        self.assertEqual("2026-09-08T20:00:00Z", first["checked_at"])
        self.assertEqual("2026-09-08T20:01:00Z", second["checked_at"])
        self.assertEqual(0, first["elapsed_seconds"])
        self.assertEqual(60, second["elapsed_seconds"])
        self.assertEqual(0, first["activity_age_seconds"])
        self.assertEqual(60, second["activity_age_seconds"])

    def test_text_and_tool_preparation_are_distinct_from_thinking(self):
        self.emit(delta("text_delta", text="private answer draft"))
        writing = self.snapshot()
        self.assertEqual("writing", writing["last_activity"])
        self.assertEqual(len("private answer draft"), writing["text_chars"])
        self.assertEqual(0, writing["thinking_chars"])
        self.emit(delta("input_json_delta", partial_json='{"file_path":"private"'))
        preparing = self.snapshot()
        self.assertEqual("preparing a tool call", preparing["last_activity"])
        self.assertEqual(0, preparing["api_error_events"])
        self.assertNotIn("private", json.dumps(preparing))

    def test_read_failure_is_not_an_api_failure_even_if_its_text_mentions_http(self):
        block = {"type": "tool_use", "id": "read-1", "name": "Read", "input": {"file_path": "/missing"}}
        self.emit(
            {"type": "stream_event", "event": {"type": "content_block_start", "content_block": block}},
            {"type": "assistant", "message": {"content": [block]}},
            {"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": "read-1", "is_error": True,
                "content": "File not found: /tmp/API Error: 429.txt",
            }]}},
        )
        snapshot = self.snapshot()
        self.assertEqual(1, snapshot["tool_calls"], "stream and full message describe the same tool call")
        self.assertEqual(1, snapshot["tool_errors"])
        self.assertEqual(0, snapshot["api_error_events"])
        self.assertIsNone(snapshot["last_api_error"])
        self.assertEqual("tool finished; awaiting API response", snapshot["last_activity"])

    def test_structured_api_retries_record_safe_status_and_category(self):
        for status, category in ((429, "rate_limit"), (503, "server_or_overload")):
            with self.subTest(status=status):
                self.emit({
                    "type": "system", "subtype": "api_retry", "attempt": 1,
                    "error": {"status": status, "message": "credential-bearing API body must remain private"},
                })
                snapshot = self.snapshot()
                self.assertEqual(status, snapshot["last_api_error"]["http_status"])
                self.assertEqual(category, snapshot["last_api_error"]["kind"])
                self.assertEqual("API retry", snapshot["last_activity"])
                self.assertNotIn("credential-bearing", json.dumps(snapshot))
        self.assertEqual(2, snapshot["api_error_events"])
        self.assertEqual(2, snapshot["retries"])
        self.assertEqual(0, snapshot["tool_errors"])

    def test_stream_api_error_is_counted_without_requiring_cli_exit(self):
        self.emit({"type": "stream_event", "event": {
            "type": "error", "error": {"type": "overloaded_error", "message": "private provider body"},
        }})
        snapshot = self.snapshot()
        self.assertTrue(snapshot["process_alive"])
        self.assertFalse(snapshot["completed"])
        self.assertEqual(1, snapshot["api_error_events"])
        self.assertEqual("server_or_overload", snapshot["last_api_error"]["kind"])
        self.assertNotIn("private provider body", json.dumps(snapshot))

    def test_empty_stream_and_nonstreaming_fallback_are_visible(self):
        self.monitor.diagnostic_line('[ERROR] Stream completed with message_start but no content blocks completed - triggering non-streaming fallback')
        self.monitor.diagnostic_line('[ERROR] Error streaming, falling back to non-streaming mode: Stream ended without receiving any events')
        state = self.snapshot()
        self.assertEqual(2, state["api_error_events"])
        self.assertEqual(1, state["retries"], "two log lines describe one fallback")
        self.assertEqual("stream_failure", state["last_api_error"]["kind"])
        self.assertEqual("nonstreaming fallback", state["last_activity"])

    def test_debug_api_error_and_retry_are_consumed_once(self):
        self.debug("[DEBUG] API request failed: API Error: 429 private provider response\n")
        self.debug("[DEBUG] Retrying request in 3 seconds\n")
        first = self.snapshot()
        self.assertEqual(1, first["api_error_events"])
        self.assertEqual(1, first["retries"])
        self.assertEqual(429, first["last_api_error"]["http_status"])
        self.assertEqual("rate_limit", first["last_api_error"]["kind"])
        self.assertNotIn("private provider", json.dumps(first))
        self.monitor.poll()
        self.assertEqual(first, self.snapshot())

    def test_error_in_streaming_diagnostic_preserves_http_status(self):
        self.debug('[ERROR] Error in streaming: 503 {"message":"private response"}\n')
        snapshot = self.snapshot()
        self.assertEqual(1, snapshot["api_error_events"])
        self.assertEqual(503, snapshot["last_api_error"]["http_status"])
        self.assertEqual("server_or_overload", snapshot["last_api_error"]["kind"])
        self.assertNotIn("private response", json.dumps(snapshot))

    def test_claude_debug_attempt_prefix_preserves_http_400(self):
        self.debug('[DEBUG] API error (attempt 1/11): 400 400 {"message":"private request body"}\n')
        snapshot = self.snapshot()
        self.assertEqual(1, snapshot["api_error_events"])
        self.assertEqual(400, snapshot["last_api_error"]["http_status"])
        self.assertEqual("request_error", snapshot["last_api_error"]["kind"])
        self.assertNotIn("private request body", json.dumps(snapshot))

    def test_absent_output_for_an_hour_is_not_invented_as_an_api_failure(self):
        self.clock.advance(3600)
        self.monitor.poll()
        snapshot = self.snapshot()
        self.assertTrue(snapshot["process_alive"])
        self.assertFalse(snapshot["completed"])
        self.assertEqual(3600, snapshot["elapsed_seconds"])
        self.assertEqual("no output yet", snapshot["last_activity"])
        self.assertIsNone(snapshot["activity_age_seconds"])
        self.assertEqual(0, snapshot["api_error_events"])
        self.assertEqual(0, snapshot["retries"])

    def test_long_thinking_retains_activity_age_without_inventing_failure(self):
        self.emit(delta("thinking_delta", thinking="private thought"))
        self.clock.advance(3600)
        self.monitor.poll()
        snapshot = self.snapshot()
        self.assertTrue(snapshot["process_alive"])
        self.assertFalse(snapshot["completed"])
        self.assertEqual("thinking", snapshot["last_activity"])
        self.assertEqual(3600, snapshot["activity_age_seconds"])
        self.assertEqual(0, snapshot["api_error_events"])
        self.assertEqual(0, snapshot["retries"])

    def test_success_marks_completion_and_keeps_model_provenance(self):
        self.emit(success_result())
        snapshot = self.monitor.snapshot(process_alive=False, exit_code=0)
        self.assertTrue(snapshot["completed"])
        self.assertFalse(snapshot["result_is_error"])
        self.assertFalse(snapshot["process_alive"])
        self.assertEqual(0, snapshot["exit_code"])
        self.assertEqual(["claude-fable-5-1"], snapshot["observed_models"])
        self.assertEqual(success_result()["session_id"], snapshot["session_id"])
        self.assertNotIn("=== VERDICT ===", json.dumps(snapshot))

    def test_error_result_is_not_a_successful_completion(self):
        self.emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                   "errors": ["API Error: 401 private authentication response"]})
        snapshot = self.snapshot(alive=False)
        self.assertTrue(snapshot["completed"])
        self.assertTrue(snapshot["result_is_error"])
        self.assertEqual(1, snapshot["api_error_events"])
        self.assertEqual(401, snapshot["last_api_error"]["http_status"])
        self.assertEqual("authentication_or_permission", snapshot["last_api_error"]["kind"])

    def test_codex_failed_turn_records_a_terminal_api_failure(self):
        session_id = success_result()["session_id"]
        self.emit(
            {"type": "thread.started", "thread_id": session_id},
            {"type": "turn.failed", "error": {"message": "HTTP 429 private rate limit response"}},
        )
        snapshot = self.snapshot(alive=False)
        self.assertTrue(snapshot["completed"])
        self.assertTrue(snapshot["result_is_error"])
        self.assertEqual(session_id, snapshot["session_id"])
        self.assertEqual(1, snapshot["api_error_events"])
        self.assertEqual(429, snapshot["last_api_error"]["http_status"])
        self.assertEqual("rate_limit", snapshot["last_api_error"]["kind"])
        self.assertNotIn("private rate limit response", json.dumps(snapshot))

    def test_codex_completed_turn_is_a_successful_terminal_event(self):
        self.emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        snapshot = self.snapshot(alive=False)
        self.assertTrue(snapshot["completed"])
        self.assertFalse(snapshot["result_is_error"])
        self.assertEqual("turn completed", snapshot["last_activity"])
        self.assertEqual(0, snapshot["api_error_events"])

    def test_final_poll_consumes_a_result_without_a_trailing_newline_once(self):
        self.raw.write_text(json.dumps(success_result()), encoding="utf-8")
        self.monitor.poll()
        self.assertFalse(self.snapshot()["completed"])
        self.assertEqual(0, self.snapshot()["events"])
        self.monitor.poll(final=True)
        finished = self.snapshot(alive=False)
        self.assertTrue(finished["completed"])
        self.assertFalse(finished["result_is_error"])
        self.assertEqual(1, finished["events"])
        self.monitor.poll(final=True)
        self.assertEqual(finished, self.snapshot(alive=False))

    def test_final_poll_consumes_an_error_without_a_trailing_newline_once(self):
        self.raw.write_text(json.dumps({"type": "error", "message": "HTTP 503 private server response"}))
        self.monitor.poll()
        self.assertEqual(0, self.snapshot()["api_error_events"])
        self.monitor.poll(final=True)
        failed = self.snapshot(alive=False)
        self.assertEqual(1, failed["api_error_events"])
        self.assertEqual(503, failed["last_api_error"]["http_status"])
        self.assertEqual("server_or_overload", failed["last_api_error"]["kind"])
        self.assertNotIn("private server response", json.dumps(failed))
        self.monitor.poll(final=True)
        self.assertEqual(failed, self.snapshot(alive=False))

    def test_refresh_recomputes_ages_and_marks_a_stopped_monitor_stale(self):
        self.emit(delta("thinking_delta", thinking="private thought"))
        original = self.snapshot()
        fresh = self.module.refresh_status(original, now=160)
        stale = self.module.refresh_status(original, now=161)
        self.assertFalse(fresh["stale"])
        self.assertTrue(stale["stale"])
        self.assertEqual(61, stale["elapsed_seconds"])
        self.assertEqual(61, stale["activity_age_seconds"])
        self.assertEqual(61, stale["snapshot_age_seconds"])
        self.assertEqual(original["checked_at"], stale["checked_at"], "reading an old status must not renew its timestamp")
        self.assertEqual(0, original["elapsed_seconds"], "refresh must not mutate stored evidence")
        self.assertIn("STALE monitor", self.module.format_status(stale))

    def test_clock_reset_cannot_make_an_old_snapshot_look_fresh(self):
        stale = self.module.refresh_status(self.snapshot(), now=99)
        self.assertTrue(stale["stale"])

    def test_completed_review_status_does_not_keep_aging_as_running(self):
        self.emit(success_result())
        self.clock.advance(25)
        finished = self.monitor.snapshot(process_alive=False, exit_code=0)
        later = self.module.refresh_status(finished, now=1000)
        self.assertFalse(later["stale"])
        self.assertEqual(25, later["elapsed_seconds"])
        self.assertEqual(25, later["activity_age_seconds"])


class MonitoredSubprocessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("reviewer_activity", SKILL_DIR / "reviewer_activity.py")

    def test_fake_child_reports_heartbeats_while_thinking_and_exits_cleanly(self):
        private_thought = "never print the contents of reviewer thinking"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = textwrap.dedent("""\
                from pathlib import Path
                import time
                print(%r, flush=True)
                deadline = time.monotonic() + 5
                while not Path("heartbeats-seen").exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("monitor did not report intermediate heartbeats")
                    time.sleep(0.005)
                print(%r, flush=True)
            """) % (json.dumps(delta("thinking_delta", thinking=private_thought)), json.dumps(success_result()))
            snapshots, reports = [], []
            status_path = root / "status.json"
            def capture(message, **kwargs):
                reports.append(message)
                snapshots.append(json.loads(status_path.read_text()))
                if sum(s["process_alive"] and s["thinking_chars"] > 0 for s in snapshots) >= 2:
                    (root / "heartbeats-seen").touch()
            result = self.module.run_monitored(
                [sys.executable, "-c", script], target=root, env=dict(os.environ),
                raw_file=root / "raw.txt", diagnostic_file=root / "debug.txt",
                status_file=status_path,
                heartbeat_seconds=0.025, poll_seconds=0.005, output=capture,
            )
            self.assertEqual(0, result.returncode)
            self.assertGreaterEqual(len(reports), 3, "a silent, live child needs intermediate heartbeats")
            thinking_reports = [s for s in snapshots if s["process_alive"] and s["thinking_chars"] > 0]
            self.assertGreaterEqual(len(thinking_reports), 2)
            self.assertLess(thinking_reports[0]["elapsed_seconds"], thinking_reports[-1]["elapsed_seconds"])
            self.assertLess(thinking_reports[0]["activity_age_seconds"], thinking_reports[-1]["activity_age_seconds"])
            self.assertTrue(all(s["api_error_events"] == 0 for s in snapshots))
            self.assertNotIn(private_thought, "\n".join(reports))
            final = json.loads(status_path.read_text())
            self.assertTrue(final["completed"])
            self.assertFalse(final["process_alive"])
            self.assertEqual(0, final["exit_code"])
            self.assertEqual(success_result()["session_id"], final["session_id"])
            self.assertFalse((root / "session-id").exists(), "only a validated runner verdict may persist the accepted session")

    def test_fake_child_api_error_is_reported_before_the_next_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = {"type": "system", "subtype": "api_retry", "error_status": 429,
                     "error": {"message": "private rate limit response"}}
            script = textwrap.dedent("""\
                from pathlib import Path
                import time
                def wait_for(name):
                    deadline = time.monotonic() + 5
                    while not Path(name).exists():
                        if time.monotonic() > deadline:
                            raise RuntimeError("monitor did not report " + name)
                        time.sleep(0.005)
                wait_for("monitor-started")
                print(%r, flush=True)
                wait_for("api-seen")
            """) % json.dumps(event)
            reports = []
            def capture(message, **kwargs):
                reports.append(json.loads((root / "status.json").read_text()))
                if reports[-1]["process_alive"] and reports[-1]["api_error_events"] > 0:
                    (root / "api-seen").touch()
                else:
                    (root / "monitor-started").touch()
            result = self.module.run_monitored(
                [sys.executable, "-c", script], target=root, env=dict(os.environ),
                raw_file=root / "raw.txt", diagnostic_file=root / "debug.txt",
                status_file=root / "status.json", heartbeat_seconds=60,
                poll_seconds=0.005, output=capture,
            )
            self.assertEqual(0, result.returncode)
            failures_while_alive = [s for s in reports if s["process_alive"] and s["api_error_events"] > 0]
            self.assertTrue(failures_while_alive, "an API failure must surface without waiting for exit or the next minute")
            self.assertEqual(429, failures_while_alive[0]["last_api_error"]["http_status"])

    def test_heartbeat_cannot_be_disabled_or_made_longer_than_one_minute(self):
        for interval in (0, -1, 61):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.module.run_monitored(
                    ["must-not-execute"], target=SKILL_DIR, env={},
                    raw_file=None, diagnostic_file=None, status_file=None,
                    heartbeat_seconds=interval,
                )

    @unittest.skipUnless(os.name == "posix", "POSIX supervisor signal semantics")
    def test_sigterm_cleans_up_the_child_and_persists_final_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = textwrap.dedent("""\
                import os
                from pathlib import Path
                import sys
                sys.path.insert(0, %r)
                from reviewer_activity import run_monitored
                run_monitored(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    target=Path.cwd(), env=dict(os.environ),
                    raw_file=Path("raw.txt"), diagnostic_file=Path("debug.txt"),
                    status_file=Path("status.json"),
                    heartbeat_seconds=0.025, poll_seconds=0.005,
                )
            """) % str(SKILL_DIR)
            supervisor = subprocess.Popen(
                [sys.executable, "-c", script], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            try:
                status_path = root / "status.json"
                deadline = time.monotonic() + 5
                while not status_path.exists():
                    if supervisor.poll() is not None:
                        stdout, stderr = supervisor.communicate()
                        self.fail("supervisor exited before becoming ready: " + stdout + stderr)
                    if time.monotonic() > deadline:
                        self.fail("supervisor did not publish its initial status")
                    time.sleep(0.005)
                initial = json.loads(status_path.read_text())
                self.assertTrue(initial["process_alive"])
                child_pid = initial["pid"]

                supervisor.terminate()
                stdout, stderr = supervisor.communicate(timeout=5)
                self.assertEqual(128 + signal.SIGTERM, supervisor.returncode, stdout + stderr)
                final = json.loads(status_path.read_text())
                self.assertEqual(child_pid, final["pid"])
                self.assertFalse(final["process_alive"])
                self.assertFalse(final["completed"], "interruption is not a completed review")
                self.assertEqual(-signal.SIGTERM, final["exit_code"])
                self.assertGreaterEqual(final["checked_monotonic"], initial["checked_monotonic"])
                with self.assertRaises(ProcessLookupError, msg="the supervisor must reap its child"):
                    os.kill(child_pid, 0)
            finally:
                # The isolated process group contains only this test's supervisor
                # and fake child, including a child orphaned by a regression.
                try:
                    os.killpg(supervisor.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                supervisor.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
