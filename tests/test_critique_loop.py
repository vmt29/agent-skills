"""Exercise the public shell entry point without making model requests."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "skills/critique-loop/critique-loop-run.sh"
SID = "019f0000-1234-7123-8123-123456789abc"
OTHER_SID = "019f0000-1234-7123-8123-123456789def"
VERDICT = "=== VERDICT ===\nconverged: yes\nblocking: 0\nmajor: 0\nminor: 0\nnits: 0\nneeds_human: none\nrationale: checked"
FAKE_CLI = r'''
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.153.4" if name == "codex" else "2.1.263 (Claude Code)")
    sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"cli": name, "args": args, "cwd": os.getcwd(), "stdin": sys.stdin.read(),
                          "effort_env": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")}) + "\n")
scenario = os.environ.get("FAKE_SCENARIO", "success")
if scenario == "failure":
    print("reviewer connection failed", file=sys.stderr)
    sys.exit(7)
if scenario == "malformed":
    print("unparseable response")
    sys.exit(0)
sid = os.environ["FAKE_SID"]
for flag in ("resume", "--resume"):
    if flag in args:
        sid = args[args.index(flag) + 1]
if scenario == "changed-session":
    sid = os.environ["FAKE_OTHER_SID"]
if scenario == "missing-session":
    sid = ""
verdict = os.environ["FAKE_VERDICT"]
if scenario == "no-verdict":
    verdict = "The request ended without a review verdict."
print("CLI warning before structured output", file=sys.stderr)
if name == "codex":
    if args[0] == "review":
        print(verdict)
    else:
        if scenario != "empty":
            Path(args[args.index("-o") + 1]).write_text(verdict)
        print(json.dumps({"type": "thread.started", "thread_id": sid}))
else:
    model = args[args.index("--model") + 1]
    if scenario == "fallback":
        model = "claude-opus-5"
    if scenario == "opus48":
        model = "claude-opus-4-8"
    usage = {} if scenario == "missing-model" else {model: {}}
    if scenario == "mixed-models":
        usage["claude-opus-4-8"] = {}
    print(json.dumps({"type": "result", "subtype": "error_max_turns" if scenario == "error-result" else "success",
                      "is_error": scenario == "error-result", "session_id": sid,
                      "result": "" if scenario == "empty" else verdict, "modelUsage": usage}))
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="critique-loop-test-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("codex", "claude"):
            cli = self.bin / name
            cli.write_text("#!" + sys.executable + "\n" + FAKE_CLI)
            cli.chmod(0o755)
        self.buf = self.root / "buffers with spaces"
        self.buf.mkdir()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CRITIQUE_", "FAKE_")) and
                    k not in ("CODEX_THREAD_ID", "CLAUDECODE", "CODEX_MODEL", "CODEX_EFFORT",
                              "CLAUDE_MODEL", "CLAUDE_EFFORT", "REVIEW_BASE")}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CRITIQUE_LOOP_DIR=str(self.buf), FAKE_LOG=str(self.log),
                        FAKE_SID=SID, FAKE_OTHER_SID=OTHER_SID, FAKE_VERDICT=VERDICT,
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
        self.git("init", "-q")
        (self.repo / "tracked.txt").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        for iteration in (1, 2, 3, 17, 33):
            (self.buf / ("prompt-%s.txt" % iteration)).write_text("Review this task.\n" + VERDICT)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True)

    def call(self, iteration="1", mode="exec", **overrides):
        return subprocess.run(["bash", str(RUNNER), str(iteration), mode, str(self.repo)],
                              env=dict(self.env, **overrides), capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_info_defaults_and_no_model_request(self):
        for programmer, expected in [("claude", "gpt-6-astra"), ("codex", "claude-fable-5-1")]:
            with self.subTest(programmer=programmer):
                result = self.call("info", CRITIQUE_PROGRAMMER=programmer)
                self.assert_ok(result)
                self.assertIn(expected, result.stdout)
                self.assertIn("effort max", result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.buf / "run-config.json").exists())

    def test_host_detection_and_legacy_default(self):
        for env, expected in [({}, "gpt-6-astra"), ({"CLAUDECODE": "1"}, "gpt-6-astra"),
                              ({"CODEX_THREAD_ID": "thread"}, "claude-fable-5-1")]:
            with self.subTest(env=env):
                result = self.call("info", **env)
                self.assert_ok(result)
                self.assertIn(expected, result.stdout)

    def test_ambiguous_host_requires_explicit_role(self):
        markers = dict(CLAUDECODE="1", CODEX_THREAD_ID="thread")
        result = self.call("info", **markers)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous host", result.stderr)
        self.assert_ok(self.call("info", CRITIQUE_PROGRAMMER="codex", **markers))

    def test_explicit_reviewer_routes_without_host_markers(self):
        self.assert_ok(self.call(CRITIQUE_REVIEWER="claude"))
        self.assertEqual(self.calls()[0]["cli"], "claude")

    def test_rejects_self_review_and_unknown_provider(self):
        for env in [dict(CRITIQUE_PROGRAMMER="codex", CRITIQUE_REVIEWER="codex"),
                    dict(CRITIQUE_REVIEWER="unknown")]:
            self.assertNotEqual(self.call(**env).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_codex_pins_model_effort_and_resumes_plan_for_code(self):
        first = self.call(CRITIQUE_PROGRAMMER="claude", CRITIQUE_PHASE="plan")
        self.assert_ok(first)
        self.assertIn("(started)", first.stdout)
        (self.buf / "plan-sha").write_text(self.base)
        second = self.call("2", CRITIQUE_PROGRAMMER="claude", CRITIQUE_PHASE="code")
        self.assert_ok(second)
        self.assertIn("(resumed)", second.stdout)
        calls = self.calls()
        self.assertEqual(calls[1]["args"][:3], ["exec", "resume", SID])
        for call in calls:
            self.assertEqual(call["cli"], "codex")
            self.assertIn('model="gpt-6-astra"', call["args"])
            self.assertIn('model_reasoning_effort="max"', call["args"])
            self.assertIn('sandbox_mode="read-only"', call["args"])
            self.assertEqual(call["stdin"], "")
            self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual((self.buf / "session-id").read_text().strip(), SID)
        self.assertIn(VERDICT, (self.buf / "critique-2.md").read_text())

    def test_claude_resumes_read_only_and_sees_committed_and_dirty_work(self):
        self.assert_ok(self.call(CRITIQUE_PROGRAMMER="codex", CRITIQUE_PHASE="plan"))
        (self.buf / "plan-sha").write_text(self.base)
        (self.repo / "tracked.txt").write_text("implementation\n")
        self.git("commit", "-qam", "implement task")
        (self.repo / "tracked.txt").write_text("implementation\nstaged change\n")
        self.git("add", ".")
        (self.repo / "tracked.txt").write_text("implementation\nstaged change\nunstaged change\n")
        (self.repo / "new file.txt").write_text("untracked source\n")
        result = self.call("2", CRITIQUE_PROGRAMMER="codex", CRITIQUE_PHASE="code")
        self.assert_ok(result)
        self.assertIn("(resumed)", result.stdout)
        for call in self.calls():
            args = call["args"]
            self.assertEqual(call["cli"], "claude")
            self.assertEqual(args[args.index("--model") + 1], "claude-fable-5-1")
            self.assertEqual(args[args.index("--effort") + 1], "max")
            self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
            self.assertIn("--safe-mode", args)
            self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
            self.assertIn("--include-partial-messages", args)
            self.assertIn("--debug-file", args)
            self.assertIn("--strict-mcp-config", args)
            settings = json.loads(args[args.index("--settings") + 1])
            self.assertFalse(settings["switchModelsOnFlag"])
            self.assertEqual(settings["fallbackModel"], [])
            self.assertNotIn("--fallback-model", args)
            self.assertIn("mcp__*", args)
            self.assertNotIn("--dangerously-skip-permissions", args)
            self.assertEqual(call["stdin"], "")
            self.assertEqual(call["effort_env"], "max")
        args = self.calls()[1]["args"]
        self.assertEqual(args[args.index("--resume") + 1], SID)
        self.assertNotIn("--no-session-persistence", args)
        snapshot = (self.buf / "git-context-2.txt").read_text()
        for evidence in (self.base, "implement task", "+implementation", "+staged change",
                         "+unstaged change", "new file.txt"):
            self.assertIn(evidence, snapshot)
        self.assertIn("git-context-2.txt", args[-1])

    def test_claude_direct_snapshot_contains_staged_unstaged_and_untracked(self):
        (self.repo / "tracked.txt").write_text("staged\n")
        self.git("add", ".")
        (self.repo / "tracked.txt").write_text("staged\nunstaged\n")
        (self.repo / "new.txt").write_text("new\n")
        self.assert_ok(self.call(CRITIQUE_PROGRAMMER="codex", CRITIQUE_PHASE="direct"))
        snapshot = (self.buf / "git-context-1.txt").read_text()
        for evidence in ("+staged", "+unstaged", "new.txt"):
            self.assertIn(evidence, snapshot)

    def test_claude_handles_unborn_repository(self):
        self.repo = self.root / "unborn"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "first.txt").write_text("staged\n")
        self.git("add", ".")
        (self.repo / "first.txt").write_text("staged\nunstaged\n")
        self.assert_ok(self.call(CRITIQUE_PROGRAMMER="codex"))
        snapshot = (self.buf / "git-context-1.txt").read_text()
        self.assertIn("+staged", snapshot)
        self.assertIn("+unstaged", snapshot)

    def test_claude_can_review_non_git_artifact(self):
        self.repo = self.root / "documents"
        self.repo.mkdir()
        self.assert_ok(self.call(CRITIQUE_PROGRAMMER="codex"))
        self.assertFalse((self.buf / "git-context-1.txt").exists())

    def test_explicit_overrides_are_forwarded_and_locked(self):
        env = dict(CRITIQUE_REVIEWER="claude", CLAUDE_MODEL="claude-fable-5-1-custom", CLAUDE_EFFORT="high")
        self.assert_ok(self.call(**env))
        self.assertIn("claude-fable-5-1-custom", self.calls()[0]["args"])
        self.assertIn("high", self.calls()[0]["args"])
        result = self.call("2", **dict(env, CLAUDE_EFFORT="max"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_provider_and_target_cannot_change_mid_run(self):
        self.assert_ok(self.call(CRITIQUE_REVIEWER="codex"))
        self.assertNotEqual(self.call("2", CRITIQUE_REVIEWER="claude").returncode, 0)
        self.repo = self.root
        self.assertNotEqual(self.call("2", CRITIQUE_REVIEWER="codex").returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_unknown_legacy_session_requires_reset(self):
        (self.buf / "session-id").write_text(SID)
        result = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("old v2 run", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_process_failure_is_not_a_stale_success(self):
        self.assert_ok(self.call())
        (self.buf / ".body-1.txt").write_text("stale success must disappear")
        result = self.call(FAKE_SCENARIO="failure")
        self.assertEqual(result.returncode, 7)
        critique = (self.buf / "critique-1.md").read_text()
        self.assertIn("FAILED", critique)
        self.assertIn("exited with status 7", critique)
        self.assertIn("reviewer connection failed", (self.buf / "raw-1.txt").read_text())
        self.assertNotIn("reviewer connection failed", critique)
        self.assertNotIn("converged: yes", critique)
        self.assertNotIn("stale success", critique)
        self.assertFalse((self.buf / ".body-1.txt").exists())

    def test_claude_errors_do_not_create_a_session(self):
        for scenario in ("failure", "malformed", "empty", "error-result", "missing-session", "fallback",
                         "opus48", "mixed-models", "missing-model", "no-verdict"):
            with self.subTest(scenario=scenario):
                result = self.call(CRITIQUE_REVIEWER="claude", FAKE_SCENARIO=scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAILED", (self.buf / "critique-1.md").read_text())
                self.assertFalse((self.buf / "session-id").exists())

    def test_downgrade_advice_keeps_model_before_lowering_model(self):
        cases = [
            ("codex", "gpt-6-astra", "max", "CODEX_MODEL=gpt-6-astra CODEX_EFFORT=xhigh"),
            ("codex", "gpt-6-astra", "xhigh", "No further downgrade is authorized"),
            ("claude", "claude-fable-5-1", "max", "CLAUDE_MODEL=claude-fable-5-1 CLAUDE_EFFORT=xhigh"),
            ("claude", "claude-fable-5-1", "xhigh", "CLAUDE_MODEL=claude-opus-5 CLAUDE_EFFORT=xhigh"),
            ("claude", "claude-opus-5", "xhigh", "No further downgrade is authorized"),
        ]
        for index, (reviewer, model, effort, expected) in enumerate(cases):
            with self.subTest(model=model, effort=effort):
                buf = self.buf / str(index)
                buf.mkdir()
                (buf / "prompt-1.txt").write_text(VERDICT)
                result = self.call(CRITIQUE_REVIEWER=reviewer, CRITIQUE_LOOP_DIR=str(buf),
                                   FAKE_SCENARIO="failure",
                                   **{reviewer.upper() + "_MODEL": model,
                                      reviewer.upper() + "_EFFORT": effort})
                self.assertEqual(result.returncode, 7)
                self.assertIn(expected, result.stderr)
                self.assertEqual(len(self.calls()), index + 1)  # No hidden retry.

    def test_opus48_cannot_be_selected_explicitly(self):
        result = self.call("info", CRITIQUE_REVIEWER="claude", CLAUDE_MODEL="claude-opus-4-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Opus 4.8 is not an allowed reviewer", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_reported_opus5_xhigh_retry_can_start_in_fresh_directory(self):
        self.assertNotEqual(self.call(CRITIQUE_REVIEWER="claude", CLAUDE_EFFORT="xhigh",
                                     FAKE_SCENARIO="failure").returncode, 0)
        retry = self.buf / "opus5-xhigh"
        retry.mkdir()
        (retry / "prompt-2.txt").write_text("Accepted task, plan, and decisions.\n" + VERDICT)
        result = self.call("2", CRITIQUE_REVIEWER="claude", CLAUDE_MODEL="claude-opus-5",
                           CLAUDE_EFFORT="xhigh", CRITIQUE_LOOP_DIR=str(retry))
        self.assert_ok(result)
        self.assertIn("model claude-opus-5 · effort xhigh", result.stdout)
        self.assertNotIn("--resume", self.calls()[-1]["args"])
        self.assertTrue((self.buf / "raw-1.txt").exists())

    def test_codex_empty_or_missing_session_is_a_failure(self):
        for scenario in ("empty", "missing-session"):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(self.call(FAKE_SCENARIO=scenario).returncode, 0)
                self.assertFalse((self.buf / "session-id").exists())

    def test_changed_session_is_rejected_for_both_reviewers(self):
        for reviewer in ("codex", "claude"):
            with self.subTest(reviewer=reviewer):
                run_buf = self.buf / reviewer
                run_buf.mkdir()
                for i in (1, 2):
                    (run_buf / ("prompt-%s.txt" % i)).write_text(VERDICT)
                env = dict(CRITIQUE_REVIEWER=reviewer, CRITIQUE_LOOP_DIR=str(run_buf))
                self.assert_ok(self.call(**env))
                result = self.call("2", FAKE_SCENARIO="changed-session", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("different session ID", result.stderr)
                self.assertEqual((run_buf / "session-id").read_text().strip(), SID)

    def test_codex_stateless_review_keeps_custom_prompt_and_base(self):
        result = self.call(mode="review", REVIEW_BASE=self.base)
        self.assert_ok(result)
        args = self.calls()[0]["args"]
        self.assertEqual(args[0], "review")
        self.assertIn(self.base, args[-1])
        self.assertIn(VERDICT, args[-1])
        self.assertIn('review_model="gpt-6-astra"', args)
        self.assertNotIn("--uncommitted", args)
        self.assertNotIn("--base", args)
        self.assertFalse((self.buf / "session-id").exists())

    def test_claude_rejects_codex_specific_review_mode(self):
        result = self.call(mode="review", CRITIQUE_REVIEWER="claude")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("use exec", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_baseline_fails_before_request(self):
        result = self.call(CRITIQUE_REVIEWER="claude", REVIEW_BASE="missing-ref")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_round_limits_and_missing_prompt_fail_before_request(self):
        for iteration, env in [(0, {}), (33, {}), (17, {"CRITIQUE_MAX": "16"}),
                               (1, {"CRITIQUE_MAX": "33"}), (4, {})]:
            with self.subTest(iteration=iteration, env=env):
                self.assertNotEqual(self.call(iteration, **env).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_reset_clears_run_and_allows_other_reviewer(self):
        self.assert_ok(self.call())
        self.assert_ok(self.call("reset"))
        self.assertFalse(self.buf.exists())
        self.buf.mkdir()
        (self.buf / "prompt-1.txt").write_text(VERDICT)
        self.assert_ok(self.call(CRITIQUE_REVIEWER="claude"))

    def test_reset_rejects_tmp_root_traversal_and_symlinks(self):
        link = self.root / "linked-buffer"
        link.symlink_to(self.buf)
        for path in ("/tmp", "/private/tmp", "/tmp/../", str(link)):
            with self.subTest(path=path):
                self.assertNotEqual(self.call("reset", CRITIQUE_LOOP_DIR=path).returncode, 0)
        self.assertTrue(self.buf.exists())


if __name__ == "__main__":
    unittest.main()
