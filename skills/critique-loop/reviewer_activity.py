"""Observe reviewer processes without printing reasoning, prompts, or credentials."""

import json
import os
from pathlib import Path
import re
import subprocess
import signal
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def error_kind(value, status=None):
    """Return only a safe category/status, never an arbitrary API error body."""
    if isinstance(value, dict):
        status = status or value.get("status") or value.get("status_code")
        text = str(value.get("type", "")) + " " + str(value.get("message", ""))
    else:
        text = str(value or "")
    if status is None:
        match = re.search(
            r"(?:API [Ee]rror(?: \(attempt \d+/\d+\))?:|API request failed:|Error in streaming:|HTTP(?: status)?|"
            r"status(?:_code| code)?[\"']?\s*[=:]?)\s*[\"']?(\d{3})", text, re.I)
        status = int(match.group(1)) if match else None
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    lower = text.lower()
    if status == 429 or "rate_limit" in lower or "ratelimiterror" in lower:
        kind = "rate_limit"
    elif status in (401, 403) or "authentication_error" in lower or "authenticationerror" in lower:
        kind = "authentication_or_permission"
    elif status == 408 or "timeout" in lower or "timed out" in lower:
        kind = "timeout"
    elif status is not None and status >= 500 or "overloaded" in lower:
        kind = "server_or_overload"
    elif "connection" in lower or "failed to fetch" in lower:
        kind = "connection"
    elif status is not None and status >= 400:
        kind = "request_error"
    else:
        kind = "api_error"
    return {"kind": kind, "http_status": status}


class Tail:
    def __init__(self, path):
        self.path = Path(path) if path is not None else None
        self.offset = 0
        self.pending = b""

    def lines(self):
        if self.path is None or not self.path.exists():
            return []
        if self.path.stat().st_size < self.offset:
            self.offset = 0
            self.pending = b""
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        parts = (self.pending + data).split(b"\n")
        self.pending = parts.pop()
        return [part.decode("utf-8", errors="replace") for part in parts]


class ActivityMonitor:
    def __init__(self, raw_path, diagnostic_path=None, now=time.monotonic, utc=utc_now):
        self.raw = Tail(raw_path)
        self.diagnostic = Tail(diagnostic_path)
        self.now = now
        self.utc = utc
        self.started = now()
        self.last_activity = "no output yet"
        self.last_activity_mono = None
        self.last_activity_at = None
        self.last_api_error = None
        self.counts = dict(events=0, thinking_chars=0, text_chars=0, tool_calls=0,
                           tool_errors=0, api_error_events=0, retries=0)
        self.seen_tools = set()
        self.session_id = None
        self.observed_models = set()
        self.completed = False
        self.result_is_error = False

    def activity(self, label):
        self.last_activity = label
        self.last_activity_mono = self.now()
        self.last_activity_at = self.utc()

    def api_error(self, value, status=None):
        self.counts["api_error_events"] += 1
        self.last_api_error = dict(error_kind(value, status), observed_at=self.utc())
        self.activity("API error")

    def tool(self, block):
        identity = block.get("id")
        if identity and identity not in self.seen_tools:
            self.seen_tools.add(identity)
            self.counts["tool_calls"] += 1
        name = block.get("name")
        self.activity("tool: " + (name if name in ("Read", "Glob", "Grep") else "other"))

    def consume(self, record):
        if not isinstance(record, dict):
            return
        self.counts["events"] += 1
        if record.get("session_id"):
            self.session_id = record["session_id"]
        kind, subtype = record.get("type"), record.get("subtype")
        if kind == "stream_event":
            event = record.get("event", {})
            event_kind = event.get("type")
            if event_kind == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "thinking_delta":
                    self.counts["thinking_chars"] += len(delta.get("thinking", ""))
                    self.activity("thinking")
                elif delta.get("type") == "text_delta":
                    self.counts["text_chars"] += len(delta.get("text", ""))
                    self.activity("writing")
                elif delta.get("type") == "input_json_delta":
                    self.activity("preparing a tool call")
            elif event_kind == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") in ("thinking", "redacted_thinking"):
                    self.activity("thinking")
                elif block.get("type") == "tool_use":
                    self.tool(block)
            elif event_kind == "message_start":
                model = event.get("message", {}).get("model")
                if model:
                    self.observed_models.add(model)
                self.activity("API response started")
            elif event_kind == "error":
                self.api_error(event.get("error"))
        elif kind == "assistant":
            message = record.get("message", {})
            if message.get("model"):
                self.observed_models.add(message["model"])
            for block in message.get("content", []):
                if block.get("type") == "tool_use":
                    self.tool(block)
        elif kind == "user":
            content = record.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self.counts["tool_errors"] += bool(block.get("is_error"))
                    self.activity("tool finished; awaiting API response")
        elif subtype in ("api_retry", "api_error"):
            self.api_error(record.get("error"), record.get("error_status") or record.get("status_code"))
            if subtype == "api_retry":
                self.counts["retries"] += 1
                self.activity("API retry")
        elif kind == "error":
            self.api_error(record.get("error") or record.get("message"))
        elif kind == "result":
            self.completed = True
            self.result_is_error = bool(record.get("is_error"))
            self.observed_models.update(record.get("modelUsage", {}))
            self.activity("result received" if not self.result_is_error else "error result received")
            if self.result_is_error:
                for error in record.get("errors", []):
                    if re.search(r"API Error:|APIConnectionError|APITimeoutError|rate_limit_error|overloaded_error", str(error), re.I):
                        self.api_error(error)
        elif kind == "system" and subtype == "init":
            self.activity("reviewer initialized; awaiting API response")
        elif kind == "thread.started":  # Codex JSON event stream.
            self.session_id = record.get("thread_id")
            self.activity("reviewer initialized")
        elif kind in ("turn.failed", "turn.completed"):
            self.completed = True
            self.result_is_error = kind == "turn.failed"
            if self.result_is_error:
                self.api_error(record.get("error"))
            else:
                self.activity("turn completed")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = record.get("item", {})
            item_kind = item.get("type")
            self.activity("thinking" if item_kind == "reasoning" else "reviewer event")
            if item_kind == "command_execution" and kind == "item.completed":
                self.counts["tool_calls"] += 1
                self.counts["tool_errors"] += bool(item.get("exit_code"))

    def diagnostic_line(self, line):
        # Only recognize diagnostic error/retry markers, never quote the raw line.
        if re.search(r"API Error:|API error|API request failed|APIConnectionError|APITimeoutError|Error in streaming|overloaded_error|rate_limit_error", line, re.I):
            self.api_error(line)
        if re.search(r"\bretrying\b|\bretry attempt\b", line, re.I):
            self.counts["retries"] += 1
            self.activity("API retry")

    def poll(self, final=False):
        raw_lines = self.raw.lines()
        diagnostic_lines = self.diagnostic.lines()
        if final:
            for tail, lines in ((self.raw, raw_lines), (self.diagnostic, diagnostic_lines)):
                if tail.pending:
                    lines.append(tail.pending.decode("utf-8", errors="replace"))
                    tail.pending = b""
        for line in raw_lines:
            try:
                self.consume(json.loads(line))
            except json.JSONDecodeError:
                self.diagnostic_line(line)
        for line in diagnostic_lines:
            self.diagnostic_line(line)

    def snapshot(self, process_alive=True, elapsed_seconds=None, checked_at=None, exit_code=None):
        now = self.now()
        return dict(self.counts, checked_at=checked_at or self.utc(), checked_monotonic=now,
                    started_monotonic=self.started,
                    elapsed_seconds=max(0, now - self.started) if elapsed_seconds is None else elapsed_seconds,
                    process_alive=process_alive, exit_code=exit_code, completed=self.completed,
                    result_is_error=self.result_is_error, last_activity=self.last_activity,
                    last_activity_at=self.last_activity_at,
                    last_activity_monotonic=self.last_activity_mono,
                    activity_age_seconds=None if self.last_activity_mono is None else max(0, now - self.last_activity_mono),
                    last_api_error=self.last_api_error, session_id=self.session_id,
                    observed_models=sorted(self.observed_models))


def refresh_status(snapshot, now=None):
    """Recompute age/elapsed at query time; never relabel a stale snapshot fresh."""
    result = dict(snapshot)
    now = time.monotonic() if now is None else now
    age = now - result["checked_monotonic"]
    running = result.get("exit_code") is None
    result["snapshot_age_seconds"] = max(0, age)
    result["stale"] = running and (age < 0 or age > 60)
    if running:
        result["elapsed_seconds"] = max(0, now - result["started_monotonic"])
        activity = result.get("last_activity_monotonic")
        result["activity_age_seconds"] = None if activity is None else max(0, now - activity)
    return result


def format_status(status):
    elapsed = int(status["elapsed_seconds"])
    age = status.get("activity_age_seconds")
    last = "%s (%s)" % (status["last_activity"], "no event" if age is None else "%ds ago" % int(age))
    process = "STALE monitor" if status.get("stale") else (
        "alive" if status["process_alive"] else "exited %s" % status.get("exit_code"))
    error = status.get("last_api_error")
    details = "" if error is None else " · last API error %s%s at %s" % (
        error["kind"], " HTTP %s" % error["http_status"] if error["http_status"] else "", error["observed_at"])
    identity = "%s/%s · " % (status["model"], status["effort"]) if status.get("model") else ""
    return ("[%s] %s%s · elapsed %dm%02ds · last: %s · thinking chars %s · tools %s (errors %s) · API error events %s · retries %s%s"
            % (status["checked_at"], identity, process, elapsed // 60, elapsed % 60, last,
               status["thinking_chars"], status["tool_calls"], status["tool_errors"],
               status["api_error_events"], status["retries"], details))


def write_json(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


@contextmanager
def termination_as_exception():
    """An interrupted supervisor must clean up its owned reviewer before exiting."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_monitored(command, target, env, raw_file, diagnostic_file, status_file,
                  metadata=None, heartbeat_seconds=60, poll_seconds=1,
                  output=print):
    if not 0 < heartbeat_seconds <= 60:
        raise ValueError("heartbeat must be positive and at most 60 seconds")
    monitor = ActivityMonitor(raw_file, diagnostic_file)
    metadata = metadata or {}
    next_report = monitor.started
    errors_reported = 0
    with termination_as_exception(), Path(raw_file).open("wb") as raw:
        process = subprocess.Popen(command, cwd=target, stdin=subprocess.DEVNULL,
                                   env=env, stdout=raw, stderr=subprocess.STDOUT)
        try:
            while True:
                code = process.poll()
                monitor.poll(final=code is not None)
                snapshot = dict(monitor.snapshot(process_alive=code is None, exit_code=code), **metadata, pid=process.pid)
                write_json(status_file, snapshot)
                now = time.monotonic()
                if now >= next_report or code is not None or monitor.counts["api_error_events"] > errors_reported:
                    output(format_status(snapshot), flush=True)
                    errors_reported = monitor.counts["api_error_events"]
                    next_report = now + heartbeat_seconds
                if code is not None:
                    return subprocess.CompletedProcess(command, code)
                try:
                    process.wait(timeout=min(poll_seconds, max(0.001, next_report - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            # Do not orphan a reviewer if its supervising helper is interrupted.
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            monitor.poll(final=True)
            write_json(status_file, dict(monitor.snapshot(False, exit_code=process.returncode), **metadata, pid=process.pid))
            raise
