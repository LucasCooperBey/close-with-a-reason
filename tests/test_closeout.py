"""Tests for closeout.py."""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _remove_bytecode():
    # Import writes bytecode that stores the absolute path of this checkout.
    root = Path(__file__).resolve().parents[1]
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


atexit.register(_remove_bytecode)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import closeout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FROZEN = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="closeout-")
        self.projects = Path(self.tmp) / "projects"
        self.codex = Path(self.tmp) / "codex"
        self.out = Path(self.tmp) / "out"
        self.work = Path(self.tmp) / "work"
        self.hooks = Path(self.tmp) / "hooks"
        self.projects.mkdir()
        self.codex.mkdir()
        self.work.mkdir()
        self.hooks.mkdir()
        self._now = closeout.now
        closeout.now = lambda: FROZEN

    def tearDown(self):
        closeout.now = self._now
        shutil.rmtree(self.tmp)

    def patch(self, **kwargs):
        return _Patched(closeout, kwargs)

    def run_cli(self, argv):
        from io import StringIO
        stdout, stderr = StringIO(), StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout, stderr
        try:
            try:
                code = closeout.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return code, stdout.getvalue(), stderr.getvalue()

    def cli(self, *argv):
        return self.run_cli(list(argv) + [
            "--projects-dir", str(self.projects),
            "--codex-dir", str(self.codex),
        ])

    def git_options(self):
        return [
            "-c", "user.email=closeout@invalid",
            "-c", "user.name=Closeout",
            "-c", "commit.gpgsign=false",
        ]

    def git_call(self, args):
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        env["GIT_TEMPLATE_DIR"] = str(self.hooks)
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError("git failed %s\n%s" % (args, proc.stderr))

    def git(self, repo, *args):
        self.git_call(["git", "-C", str(repo)] + self.git_options() + list(args))

    def make_repo(self, name="repo"):
        path = Path(self.tmp) / name
        path.mkdir(parents=True)
        self.git_call(["git"] + self.git_options() + ["init", "-b", "main", str(path)])
        return path

    def make_bare(self, name="bare.git"):
        path = Path(self.tmp) / name
        self.git_call(["git"] + self.git_options() + ["init", "--bare", "-b", "main", str(path)])
        return path

    def commit_file(self, repo, name, content):
        dest = Path(repo) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        self.git(repo, "add", name)
        self.git(repo, "commit", "-m", "add %s" % name)

    def stage(self, fixture, folder, filename, branch="main", cwd=None, edit_path=None, root="projects"):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        text = text.replace("__CWD__", cwd if cwd is not None else str(self.work))
        text = text.replace("__BRANCH__", branch)
        if edit_path is not None:
            text = text.replace("__FILE__", edit_path)
        for token in ("__CWD__", "__BRANCH__", "__FILE__"):
            if token in text:
                raise AssertionError("unreplaced %s in %s" % (token, fixture))
        base = self.projects if root == "projects" else self.codex
        dest_dir = base / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        dest.write_text(text, encoding="utf-8")
        return dest

    def notes(self):
        return sorted(path for path in self.out.glob("*.md") if path.name != "INDEX.md")

    def only_note(self):
        found = self.notes()
        self.assertEqual(len(found), 1, [path.name for path in self.out.glob("*")])
        return found[0].read_text(encoding="utf-8")

    def assert_note(self, text):
        lines = text.splitlines()
        self.assertEqual(len(lines), 7, text)
        self.assertTrue(lines[0].startswith("# "), lines[0])
        self.assertTrue(lines[1].startswith("WINDOW: "), lines[1])
        self.assertTrue(lines[2].startswith("VERDICT: "), lines[2])
        self.assertTrue(lines[3].startswith("REASON: "), lines[3])
        self.assertTrue(lines[4].startswith("CARRY: "), lines[4])
        self.assertTrue(lines[5].startswith("NEXT: "), lines[5])
        self.assertTrue(lines[6].startswith("RESUME: "), lines[6])
        return lines

    def write_dirty_repo(self):
        repo = self.make_repo("dirty-repo")
        self.commit_file(repo, "buffer.py", "one\n")
        (repo / "buffer.py").write_text("one\ntwo\n", encoding="utf-8")
        return repo

    def stage_dirty(self, repo=None, folder="dirty-app"):
        if repo is None:
            repo = self.write_dirty_repo()
        edit = str(repo / "buffer.py")
        self.stage(
            "uncommitted.jsonl",
            folder,
            "sess-dirty.jsonl",
            cwd=str(repo),
            edit_path=edit,
        )
        return repo, edit

    def stage_all(self):
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        self.stage_dirty()
        self.stage("unanswered.jsonl", "open-app", "sess-open.jsonl")
        self.stage("garbage.jsonl", "junk-app", "sess-garbage.jsonl")
        self.stage("tool-result.jsonl", "tool-app", "sess-tool.jsonl")
        self.stage("merge-a.jsonl", "pair-app", "sess-merge-a.jsonl", branch="notes")
        self.stage("merge-b.jsonl", "pair-app", "sess-merge-b.jsonl", branch="notes")
        self.stage("merge-far.jsonl", "pair-app", "sess-far.jsonl", branch="notes")
        self.stage("old.jsonl", "pair-app", "sess-old.jsonl", branch="notes")
        self.stage("rollout-demo.jsonl", "demo-project", "rollout-demo.jsonl", root="codex")
        self.stage("rollout-weird.jsonl", "demo-project", "rollout-weird.jsonl", root="codex")

    def test_clean_finished_chat_is_close(self):
        # source: Door 27, 11-12: the gift was a five-line note while the real closeout stayed private
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        code, out, err = self.cli(
            "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        files = list(self.out.glob("*.md"))
        self.assertEqual([path.name for path in files], ["2026-11-12-sessclea-add-a-retry.md"])
        lines = self.assert_note(files[0].read_text(encoding="utf-8"))
        self.assertEqual(lines[0], "# Add a retry")
        self.assertEqual(
            lines[1],
            "WINDOW: clean-app on main, 2026-11-12 16:00 UTC to 2026-11-12 16:05 UTC",
        )
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertEqual(
            lines[3],
            "REASON: Closed because the last request was answered and the chat edited no files.",
        )
        self.assertEqual(
            lines[4],
            "CARRY: Add a retry around the upload.; Next: run the upload once.",
        )
        # The request was answered, so NEXT is the assistant's own Next line, not the finished request.
        self.assertEqual(lines[5], "NEXT: Run the upload once.")
        self.assertTrue(lines[6].startswith("RESUME: cd "))
        self.assertIn("claude --resume sess-clean", lines[6])
        self.assertIn(str(self.work), lines[6])
        self.assertNotIn("Wrong summary", "\n".join(lines))
        self.assertNotIn("Windows", "\n".join(lines))
        self.assertNotIn("INDEX.md", out)

    def test_uncommitted_file_is_keep_rule_2(self):
        # source: Door 27, 11-12: an edited file left uncommitted keeps the chat open
        repo, edit = self.stage_dirty()
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("is uncommitted", lines[3])
        self.assertIn("buffer.py", lines[3])
        self.assertNotIn("no assistant answer", lines[3])
        self.assertTrue(lines[4].startswith("CARRY: uncommitted "))
        self.assertIn(edit, lines[4])
        self.assertIn("Save the buffer helper.", lines[4])
        self.assertEqual(lines[5], "NEXT: Commit or discard the uncommitted work in buffer.py.")

    def test_unanswered_request_is_keep_rule_3(self):
        # source: Door 27, 11-12: a request with no assistant answer keeps the chat open
        self.stage("unanswered.jsonl", "open-app", "sess-open.jsonl")
        code, out, err = self.cli(
            "note", "--session", "sess-open", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        long_text = ""
        for line in (FIXTURES / "unanswered.jsonl").read_text(encoding="utf-8").splitlines():
            text = closeout.person_text(json.loads(line))
            if text:
                long_text = text
                break
        self.assertGreater(len(long_text), 80)
        self.assertEqual(lines[0], "# " + closeout.one_line(long_text)[:80])
        self.assertEqual(len(lines[0][2:]), 80)
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertEqual(
            lines[3],
            "REASON: Kept because the last request has no assistant answer after it.",
        )
        self.assertEqual(lines[4], "CARRY: Add the count line at the bottom.")
        self.assertNotIn("Sketch the index layout", lines[4])
        self.assertIn("14:00 UTC to 2026-11-12 14:20 UTC", lines[1])

    def test_same_folder_and_branch_within_24h_merges(self):
        # source: Door 27, 11-12: two chats on the same folder and branch within 24 hours merge
        self.stage("merge-a.jsonl", "pair-app", "sess-merge-a.jsonl", branch="notes")
        self.stage("merge-b.jsonl", "pair-app", "sess-merge-b.jsonl", branch="notes")
        self.stage("merge-far.jsonl", "pair-app", "sess-far.jsonl", branch="notes")
        self.stage("old.jsonl", "pair-app", "sess-old.jsonl", branch="notes")
        code, out, err = self.cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("Merge with Split the index", out)
        self.assertIn("Merge with Split the sections", out)
        self.assertNotIn("Ancient import", out)
        code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        by_title = {}
        for path in self.notes():
            lines = self.assert_note(path.read_text(encoding="utf-8"))
            by_title[lines[0][2:]] = lines
        self.assertEqual(by_title["Split the index"][2], "VERDICT: MERGE with Split the sections")
        self.assertEqual(by_title["Split the sections"][2], "VERDICT: MERGE with Split the index")
        self.assertEqual(by_title["Split the header"][2], "VERDICT: CLOSE")
        self.assertNotIn("Ancient import", by_title)
        self.assertIn("pair-app on notes", by_title["Split the index"][1])

    def test_parse_failures_over_five_percent_keep_rule_1(self):
        # source: Door 27, 11-12: more than 5 percent unreadable lines keeps the chat open
        self.stage("garbage.jsonl", "junk-app", "sess-garbage.jsonl")
        code, out, err = self.cli(
            "note", "--session", "sess-garbage", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[0], "# Broken transcript")
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertEqual(
            lines[3],
            "REASON: Kept because 1 of 4 lines failed to parse, which is over 5 percent.",
        )
        self.assertNotIn("Not read", lines[3])
        self.assertNotIn("Ignore this title source.", lines[0])

    def test_tool_result_only_user_record_is_not_the_request(self):
        # source: Door 27, 11-12: a tool result is not the person typing
        self.stage("tool-result.jsonl", "tool-app", "sess-tool.jsonl")
        code, out, err = self.cli(
            "note", "--session", "sess-tool", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        text = self.only_note()
        lines = self.assert_note(text)
        self.assertEqual(lines[0], "# Rename the flag to days.")
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertNotIn("no assistant answer", text)
        self.assertNotIn("TOOL_OUTPUT_BLOB_SHOULD_NOT_COUNT", text)
        self.assertEqual(lines[4], "CARRY: Rename the flag to days.")
        self.assertEqual(lines[5], "NEXT: Nothing left.")

    def test_chat_older_than_days_is_left_out(self):
        # source: Door 27, 11-12: a chat older than the window is left out
        self.stage("old.jsonl", "pair-app", "sess-old.jsonl", branch="notes")
        code, out, err = self.cli(
            "note", "--session", "sess-old", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        self.assertIn("# Ancient import", self.only_note())
        shutil.rmtree(self.out)
        code, out, err = self.cli("list")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Ancient import", out)
        self.assertIn("0 safe to close, 0 keep open, 0 merge.", out)
        code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        index = (self.out / "INDEX.md").read_text(encoding="utf-8")
        self.assertNotIn("Ancient import", index)
        self.assertIn("0 safe to close, 0 keep open, 0 merge.", index)
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        code, out, err = self.cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("Add a retry", out)
        self.assertNotIn("Ancient import", out)
        code, out, err = self.cli("list", "--days", "400")
        self.assertEqual(code, 0, err)
        self.assertIn("Ancient import", out)
        self.assertIn("Add a retry", out)

    def _spy_loads(self):
        opened = []
        real = closeout.load_chat

        def spy(path, source, folder):
            opened.append(Path(str(path)).name)
            return real(path, source, folder)

        return opened, spy

    def _age(self, path, days):
        stamp = (FROZEN - timedelta(days=days)).timestamp()
        os.utime(str(path), (stamp, stamp))

    def test_list_and_sweep_never_open_a_file_modified_before_the_window(self):
        # source: Door 27 review, 11-12: list --days 1 on a real 17 GB projects folder (7,127
        # transcripts) was killed at 6 min 40 s with no output, because every file was read before the date check
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        stale = self.stage("old.jsonl", "pair-app", "sess-old.jsonl", branch="notes")
        self._age(stale, 30)
        opened, spy = self._spy_loads()
        with self.patch(load_chat=spy):
            code, out, err = self.cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("Add a retry", out)
        self.assertEqual(opened, ["sess-clean.jsonl"])
        del opened[:]
        with self.patch(load_chat=spy):
            code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertEqual(opened, ["sess-clean.jsonl"])
        del opened[:]
        with self.patch(load_chat=spy):
            code, out, err = self.cli("list", "--days", "400")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(opened), ["sess-clean.jsonl", "sess-old.jsonl"])
        self.assertIn("Ancient import", out)

    def test_note_opens_only_the_chat_and_its_possible_merge_partners(self):
        # source: Door 27 review, 11-12: note read every transcript on the machine to judge one chat
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        stale = self.stage("old.jsonl", "pair-app", "sess-old.jsonl", branch="notes")
        self._age(stale, 30)
        opened, spy = self._spy_loads()
        with self.patch(load_chat=spy):
            code, out, err = self.cli(
                "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(opened, ["sess-clean.jsonl"])
        shutil.rmtree(str(self.out))
        del opened[:]
        with self.patch(load_chat=spy):
            code, out, err = self.cli("note", "--latest", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertNotIn("sess-old.jsonl", opened)
        self.assertIn("# Add a retry", self.only_note())
        shutil.rmtree(str(self.out))
        with self.patch(load_chat=spy):
            code, out, err = self.cli(
                "note", "--session", "sess-old", "--provider", "none", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        self.assertIn("# Ancient import", self.only_note())

    def test_next_after_an_answered_request_is_the_pending_work(self):
        # source: Door 27 review, 11-12: a closed chat's NEXT repeated the request it had already finished
        repo = self.make_repo("pushed-repo")
        self.commit_file(repo, "buffer.py", "one\n")
        self.stage(
            "uncommitted.jsonl", "clean-commit", "sess-dirty.jsonl",
            cwd=str(repo), edit_path=str(repo / "buffer.py"),
        )
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertEqual(lines[5], "NEXT: Nothing left.")

    def test_codex_bad_shape_is_not_read(self):
        # source: Door 27, 11-12: a transcript whose shape does not match is not read and not guessed
        self.stage("rollout-weird.jsonl", "demo-project", "rollout-weird.jsonl", root="codex")
        code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        text = self.only_note()
        lines = self.assert_note(text)
        self.assertEqual(lines[0], "# Not read (rollout-weird.jsonl)")
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("did not match the transcript shape", lines[3])
        self.assertNotIn("failed to parse", lines[3])
        self.assertEqual(lines[4], "CARRY: Nothing")
        self.assertEqual(lines[5], "NEXT: Nothing left.")
        self.assertEqual(lines[6], "RESUME: not read")
        self.assertIn("unknown to unknown", lines[1])
        self.assertNotIn("THIS_SHAPE_IS_NOT_A_CHAT", text)
        self.assertNotIn("secret-branch", text)
        self.assertNotIn("should-not-be-used", text)
        index = (self.out / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Not read (rollout-weird.jsonl)", index.split("## Keep open", 1)[1])

    def test_codex_session_with_the_same_fields_is_read(self):
        # source: Door 27, 11-12: a Codex session with the same fields gets a resume command
        self.stage("rollout-demo.jsonl", "demo-project", "rollout-demo.jsonl", root="codex")
        code, out, err = self.cli(
            "note", "--session", "codex-sess-1", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[0], "# Check the codex log")
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertIn("WINDOW: demo-project on main", lines[1])
        self.assertIn("codex resume codex-sess-1", lines[6])
        self.assertNotIn("claude --resume", lines[6])

    def test_missing_cwd_is_keep_rule_1(self):
        # source: Door 27, 11-12: a missing working directory keeps the chat open
        missing = "/tmp/closeout-missing-cwd-does-not-exist"
        self.assertFalse(os.path.exists(missing))
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl", cwd=missing)
        code, out, err = self.cli(
            "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("does not exist", lines[3])
        self.assertIn(missing, lines[3])
        self.assertNotIn("failed to parse", lines[3])

    def test_rule1_parse_wins_over_uncommitted_file(self):
        # source: Door 27, 11-12: verdict rules run in order, parse failure before an uncommitted file
        repo, edit = self.stage_dirty()
        path = self.projects / "dirty-app" / "sess-dirty.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "this is not json\n", encoding="utf-8")
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("failed to parse", lines[3])
        self.assertNotIn("uncommitted", lines[3])

    def test_rule2_wins_over_unanswered(self):
        # source: Door 27, 11-12: verdict rules run in order, uncommitted file before an unanswered request
        repo, edit = self.stage_dirty()
        extra = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "Also rename the helper."}]},
            "timestamp": "2026-11-12T15:20:00Z",
            "sessionId": "sess-dirty",
            "cwd": str(repo),
            "gitBranch": "main",
        }
        path = self.projects / "dirty-app" / "sess-dirty.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + json.dumps(extra) + "\n", encoding="utf-8")
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("is uncommitted", lines[3])
        self.assertNotIn("no assistant answer", lines[3])

    def test_rule3_wins_over_merge(self):
        # source: Door 27, 11-12: verdict rules run in order, an unanswered request before a merge
        self.stage("unanswered.jsonl", "pair-app", "sess-open.jsonl", branch="notes")
        self.stage("merge-b.jsonl", "pair-app", "sess-merge-b.jsonl", branch="notes")
        code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        found = ""
        for path in self.notes():
            text = path.read_text(encoding="utf-8")
            if "no assistant answer" in text:
                found = text
        self.assertTrue(found)
        lines = self.assert_note(found)
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertNotIn("MERGE", lines[2])

    def test_exactly_five_percent_does_not_keep(self):
        # source: Door 27, 11-12: exactly 5 percent unreadable is not over the keep line
        lines = []
        common = {
            "cwd": str(self.work),
            "gitBranch": "main",
            "sessionId": "sess-five",
        }
        lines.append(json.dumps(dict(common, **{
            "type": "user",
            "message": {"role": "user", "content": "Please ship the index."},
            "timestamp": "2026-11-12T08:01:00Z",
        })))
        lines.append(json.dumps(dict(common, **{
            "type": "assistant",
            "message": {"role": "assistant", "content": "Shipped."},
            "timestamp": "2026-11-12T08:05:00Z",
        })))
        for i in range(17):
            lines.append(json.dumps(dict(common, **{
                "type": "progress",
                "n": i,
                "timestamp": "2026-11-12T08:00:00Z",
            })))
        lines.append("this line is not json")
        self.assertEqual(len(lines), 20)
        dest = self.projects / "five-app"
        dest.mkdir()
        (dest / "sess-five.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out, err = self.cli(
            "note", "--session", "sess-five", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        note_lines = self.assert_note(self.only_note())
        self.assertEqual(note_lines[2], "VERDICT: CLOSE")
        self.assertNotIn("failed to parse", note_lines[3])

    def test_failed_over_five_percent_boundary(self):
        # source: Door 27, 11-12: more than 5 percent is strictly greater than one line in twenty
        self.assertFalse(closeout.failed_over_five_percent(1, 20))
        self.assertTrue(closeout.failed_over_five_percent(2, 20))
        self.assertTrue(closeout.failed_over_five_percent(1, 19))
        self.assertFalse(closeout.failed_over_five_percent(0, 10))
        self.assertFalse(closeout.failed_over_five_percent(0, 0))

    def test_active_within_24h_includes_the_boundary(self):
        # source: Door 27, 11-12: within 24 hours includes the boundary and not one minute past it
        end = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
        start = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
        earlier = closeout.Chat()
        earlier.first = end
        earlier.last = end
        later = closeout.Chat()
        later.first = start
        later.last = start
        self.assertTrue(closeout.active_within_24h(earlier, later))
        earlier.first = end - timedelta(minutes=1)
        earlier.last = earlier.first
        self.assertFalse(closeout.active_within_24h(earlier, later))
        blank = closeout.Chat()
        self.assertFalse(closeout.active_within_24h(blank, later))

    def test_unpushed_commits_keep_rule_2(self):
        # source: Door 27, 11-12: commits that are not pushed keep the chat open
        bare = self.make_bare()
        repo = self.make_repo("pushed-repo")
        self.commit_file(repo, "readme.txt", "a\n")
        self.git(repo, "remote", "add", "origin", str(bare))
        self.git(repo, "push", "-u", "origin", "HEAD")
        self.commit_file(repo, "readme.txt", "a\nb\n")
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl", cwd=str(repo))
        code, out, err = self.cli(
            "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertEqual(lines[3], "REASON: Kept because 1 commit is not pushed.")

    def test_committed_file_with_no_upstream_can_close(self):
        # source: Door 27, 11-12: a committed edit with no upstream can close
        repo = self.make_repo("clean-repo")
        self.commit_file(repo, "buffer.py", "done\n")
        self.stage(
            "uncommitted.jsonl",
            "dirty-app",
            "sess-dirty.jsonl",
            cwd=str(repo),
            edit_path=str(repo / "buffer.py"),
        )
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertIn("edited files are committed", lines[3])
        self.assertNotIn("not pushed", lines[3])
        self.assertNotIn("uncommitted", lines[4])

    def test_local_commits_without_upstream_do_not_keep(self):
        # source: Door 27, 11-12: local commits keep the chat open only when an upstream exists
        repo = self.make_repo("local-repo")
        self.commit_file(repo, "readme.txt", "a\n")
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl", cwd=str(repo))
        code, out, err = self.cli(
            "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertNotIn("not pushed", lines[3])

    def test_edited_file_outside_a_repo_is_keep(self):
        # source: Door 27, 11-12: an edited file git cannot check keeps the chat open
        edit = self.work / "buffer.py"
        edit.write_text("x\n", encoding="utf-8")
        self.stage(
            "uncommitted.jsonl",
            "dirty-app",
            "sess-dirty.jsonl",
            cwd=str(self.work),
            edit_path=str(edit),
        )
        code, out, err = self.cli(
            "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("git could not check", lines[3])
        self.assertIn("buffer.py", lines[3])

    def write_records(self, folder, session_id, records, cwd):
        lines = []
        for minute, (kind, content) in enumerate(records):
            lines.append(json.dumps({
                "type": kind,
                "message": {"role": kind, "content": content},
                "timestamp": "2026-11-12T09:%02d:00Z" % minute,
                "sessionId": session_id,
                "cwd": str(cwd),
                "gitBranch": "main",
            }))
        dest = self.projects / folder
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ("%s.jsonl" % session_id)).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def edit_records(self, path, closing="Done."):
        return [
            ("user", [{"type": "text", "text": "Add the helper."}]),
            ("assistant", [{"type": "tool_use", "name": "Write", "input": {"file_path": str(path)}}]),
            ("user", [{"type": "tool_result", "content": "ok"}]),
            ("assistant", [{"type": "text", "text": closing}]),
        ]

    def note_lines(self, session_id):
        code, out, err = self.cli(
            "note", "--session", session_id, "--provider", "none", "--out", str(self.out),
        )
        self.assertEqual(code, 0, err)
        return self.assert_note(self.only_note())

    def test_file_in_a_new_untracked_folder_keeps_the_chat(self):
        # source: Door 27 review-gate REJECT, 11-12: git status lists only "?? folder/" for a new
        # folder, so a file the chat created inside it read as committed and the chat as safe to close
        repo = self.make_repo("fresh-repo")
        self.commit_file(repo, "README.md", "x\n")
        made = repo / "newpkg" / "helper.py"
        made.parent.mkdir()
        made.write_text("print(1)\n", encoding="utf-8")
        self.write_records("fresh-app", "sess-newdir", self.edit_records(made), repo)
        lines = self.note_lines("sess-newdir")
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("helper.py is untracked", lines[3])

    def test_edited_file_in_another_repo_is_checked_in_that_repo(self):
        # source: Door 27 review-gate REJECT, 11-12: a file edited outside the chat's own repo read as committed
        home_repo = self.make_repo("home-repo")
        self.commit_file(home_repo, "a.txt", "a\n")
        other = self.make_repo("other-repo")
        self.commit_file(other, "b.py", "one\n")
        (other / "b.py").write_text("one\ntwo\n", encoding="utf-8")
        self.write_records("home-app", "sess-other", self.edit_records(other / "b.py"), home_repo)
        lines = self.note_lines("sess-other")
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("b.py is uncommitted", lines[3])

    def test_chat_that_stopped_on_a_tool_call_is_unanswered(self):
        # source: Door 27 review-gate REJECT, 11-12: a chat cut off mid-tool counted as answered
        self.write_records("cut-app", "sess-cut", [
            ("user", [{"type": "text", "text": "Run the migration."}]),
            ("assistant", [{"type": "text", "text": "Running it now."}]),
            ("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "make migrate"}}]),
        ], self.work)
        lines = self.note_lines("sess-cut")
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertIn("no assistant answer", lines[3])

    def test_marker_lines_need_a_word_boundary_and_still_is_never_next(self):
        # source: Door 27 review-gate REJECT, 11-12: "Stillwater" and "Nextjs" were read as markers
        self.write_records("mark-app", "sess-mark", [
            ("user", [{"type": "text", "text": "Check the build."}]),
            ("assistant", [{"type": "text", "text": "Stillwater is the test town.\nNextjs builds.\nStill failing on CI."}]),
        ], self.work)
        lines = self.note_lines("sess-mark")
        self.assertNotIn("Stillwater", lines[4])
        self.assertNotIn("Nextjs", lines[4])
        self.assertIn("Still failing on CI.", lines[4])
        self.assertEqual(lines[5], "NEXT: Nothing left.")

    def test_git_never_runs_a_repo_fsmonitor_command(self):
        # source: security review, 11-12: git status runs core.fsmonitor from the repo's own config,
        # and closeout runs status in every repo a chat edited
        seen = {}

        class Proc(object):
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return Proc()

        with _Attr(closeout.subprocess, "run", fake_run):
            closeout.run_git(str(self.work), ["status"])
        self.assertEqual(seen["argv"][:3], ["git", "-c", "core.fsmonitor=false"])

    def test_git_status_does_not_take_the_index_lock(self):
        # source: Door 27 review-gate REJECT, 11-12: GIT_OPTIONAL_LOCKS=1 is git's default and lets status
        # refresh the index, so a git command the person runs at the same time can fail
        self.assertEqual(closeout.git_env()["GIT_OPTIONAL_LOCKS"], "0")

    def test_ollama_skips_embedding_models(self):
        # source: Door 27 review-gate REJECT, 11-12: the first listed model can be an embedding model
        class Resp(object):
            def __init__(self, raw):
                self.raw = raw

            def read(self):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=None):
            return Resp(b'{"models":[{"name":"nomic-embed-text"},{"name":"chat-model"}]}')

        with _Attr(closeout.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(closeout.fetch_ollama_model(2), "chat-model")

    def test_no_transcripts_exits_3(self):
        # source: Door 27, 11-12: no transcripts exits 3 and names the folder
        code, out, err = self.cli("list")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("No transcripts found", err)
        self.assertIn(closeout.expand(str(self.projects)), err)
        self.assertIn(closeout.expand(str(self.codex)), err)
        missing = Path(self.tmp) / "missing-projects"
        code, out, err = self.run_cli([
            "list",
            "--projects-dir", str(missing),
            "--codex-dir", str(self.codex),
        ])
        self.assertEqual(code, 3)
        self.assertIn(str(missing), err)

    def test_bad_arguments_exit_2(self):
        # source: Door 27, 11-12: bad arguments exit 2
        code, out, err = self.run_cli([])
        self.assertEqual(code, 2)
        code, out, err = self.cli("note")
        self.assertEqual(code, 2)
        code, out, err = self.cli("list", "--days", "-1")
        self.assertEqual(code, 2)
        self.assertIn("--days", err)
        code, out, err = self.cli("sweep", "--provider", "maybe")
        self.assertEqual(code, 2)
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        code, out, err = self.cli("note", "--session", "missing-session", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("missing-session", err)

    def test_list_writes_nothing(self):
        # source: Door 27, 11-12: list prints the groups and does not write notes
        self.stage_all()
        home = Path(self.tmp) / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            code, out, err = self.cli("list")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
        self.assertEqual(code, 0, err)
        self.assertIn("Safe to close\n", out)
        self.assertIn("Keep open\n", out)
        self.assertIn("Merge\n", out)
        self.assertIn("4 safe to close, 4 keep open, 2 merge.", out)
        self.assertNotIn("Ancient import", out)
        self.assertNotIn("](", out)
        self.assertFalse((home / "closeouts").exists())
        self.assertFalse(self.out.exists())

    def test_sweep_writes_index_groups_and_links(self):
        # source: Door 27, 11-12: sweep writes an index with safe, keep, and merge
        self.stage_all()
        code, out, err = self.cli("sweep", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        index_path = self.out / "INDEX.md"
        self.assertTrue(index_path.is_file())
        index = index_path.read_text(encoding="utf-8")
        self.assertTrue(index.startswith("# Closeout index\n"))
        self.assertLess(index.index("## Safe to close"), index.index("## Keep open"))
        self.assertLess(index.index("## Keep open"), index.index("## Merge"))
        self.assertTrue(index.strip().endswith("4 safe to close, 4 keep open, 2 merge."))
        safe, rest = index.split("## Keep open", 1)
        keep, merge = rest.split("## Merge", 1)
        self.assertIn("Add a retry", safe)
        self.assertIn("Rename the flag to days.", safe)
        self.assertIn("Split the header", safe)
        self.assertIn("Check the codex log", safe)
        self.assertIn("Save the buffer helper", keep)
        self.assertIn("Broken transcript", keep)
        self.assertIn("Sketch the index layout", keep)
        self.assertIn("Not read (rollout-weird.jsonl)", keep)
        self.assertIn("Split the index", merge)
        self.assertIn("Split the sections", merge)
        self.assertNotIn("Ancient import", index)
        self.assertNotIn("TOOL_OUTPUT_BLOB_SHOULD_NOT_COUNT", index)
        self.assertNotIn("THIS_SHAPE_IS_NOT_A_CHAT", index)
        for link in __import__("re").findall(r"\]\(([^)]+)\)", index):
            self.assertTrue((self.out / link).is_file(), link)
        for path in self.notes():
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 7, path.name)
            blob = path.read_text(encoding="utf-8")
            self.assertNotIn("TOOL_OUTPUT_BLOB_SHOULD_NOT_COUNT", blob)
            self.assertNotIn("THIS_SHAPE_IS_NOT_A_CHAT", blob)
            self.assertNotIn("secret-branch", blob)

    def test_sweep_default_out_dir_is_under_home_closeouts(self):
        # source: Door 27, 11-12: sweep writes notes under ~/closeouts/YYYY-MM-DD
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        home = Path(self.tmp) / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            code, out, err = self.cli("sweep", "--provider", "none")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
        self.assertEqual(code, 0, err)
        indexes = list(home.glob("closeouts/*/INDEX.md"))
        self.assertEqual(len(indexes), 1)
        self.assertTrue(any(path.suffix == ".md" and path.name != "INDEX.md" for path in indexes[0].parent.iterdir()))

    def test_note_latest_picks_the_newest_session(self):
        # source: Door 27, 11-12: note --latest writes the newest chat
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        self.stage("unanswered.jsonl", "open-app", "sess-open.jsonl")
        code, out, err = self.cli("note", "--latest", "--provider", "none", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        text = self.only_note()
        self.assertIn("# Add a retry", text)
        self.assertNotIn("Sketch the index layout", text.splitlines()[0])
        self.assertFalse((self.out / "INDEX.md").exists())

    def test_provider_none_does_not_call_out(self):
        # source: Door 27, 11-12: provider none keeps the last 12 turns on the machine
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")

        def boom(*args, **kwargs):
            raise AssertionError("provider called")

        with self.patch(call_claude=boom, call_ollama=boom, probe_ollama=boom, fetch_ollama_model=boom):
            code, out, err = self.cli("note", "--latest", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertIn("NEXT: Run the upload once.", self.only_note())
        self.assertNotIn("provider failed", self.only_note())

    def test_provider_error_falls_back_and_the_note_says_so(self):
        # source: Door 27, 11-12: a provider error falls back and the note says so
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")

        def boom(prompt):
            raise RuntimeError("connection refused")

        with self.patch(call_claude=boom):
            code, out, err = self.cli(
                "note", "--session", "sess-clean", "--provider", "claude", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertEqual(
            lines[5],
            "NEXT: The model provider failed, so the next step is: Run the upload once.",
        )

    def test_provider_writes_only_the_next_line(self):
        # source: Door 27, 11-12: a provider writes the NEXT line and does not choose the verdict
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")

        def fake(prompt):
            self.assertIn(closeout.INSTRUCTION, prompt)
            return "Ship the notes."

        with self.patch(call_claude=fake):
            code, out, err = self.cli(
                "note", "--session", "sess-clean", "--provider", "claude", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertEqual(lines[5], "NEXT: Ship the notes.")

    def test_provider_prompt_is_last_12_turns_without_tool_output(self):
        # source: Door 27, 11-12: only the last 12 typed turns go to a provider, never tool output
        lines = []
        for i in range(14):
            lines.append(json.dumps({
                "type": "user",
                "message": {"content": [{"type": "text", "text": "UNIQUE_TURN_%02d" % i}]},
                "timestamp": "2026-11-12T07:%02d:00Z" % i,
                "sessionId": "sess-turns",
                "cwd": str(self.work),
                "gitBranch": "main",
            }))
            content = [{"type": "text", "text": "REPLY_%02d" % i}]
            if i == 3:
                content.append({
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "SECRET_PATH_NOT_TEXT.dat"},
                })
            lines.append(json.dumps({
                "type": "assistant",
                "message": {"content": content},
                "timestamp": "2026-11-12T07:%02d:30Z" % i,
                "sessionId": "sess-turns",
                "cwd": str(self.work),
                "gitBranch": "main",
            }))
            if i == 3:
                lines.append(json.dumps({
                    "type": "user",
                    "message": {"content": [{
                        "type": "tool_result",
                        "content": "TOOL_OUTPUT_SECRET",
                    }]},
                    "timestamp": "2026-11-12T07:03:40Z",
                    "sessionId": "sess-turns",
                    "cwd": str(self.work),
                    "gitBranch": "main",
                }))
        dest = self.projects / "turns-app"
        dest.mkdir()
        (dest / "sess-turns.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        seen = {}

        def fake(prompt):
            seen["prompt"] = prompt
            return "Ship the notes."

        with self.patch(call_claude=fake):
            code, out, err = self.cli(
                "note", "--session", "sess-turns", "--provider", "claude", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        prompt = seen["prompt"]
        self.assertIn(closeout.INSTRUCTION, prompt)
        self.assertNotIn("TOOL_OUTPUT_SECRET", prompt)
        self.assertNotIn("SECRET_PATH_NOT_TEXT.dat", prompt)
        self.assertNotIn("UNIQUE_TURN_00", prompt)
        self.assertNotIn("UNIQUE_TURN_07", prompt)
        self.assertIn("UNIQUE_TURN_08", prompt)
        self.assertIn("UNIQUE_TURN_13", prompt)
        self.assertIn("REPLY_13", prompt)
        labels = [
            line for line in prompt.splitlines()
            if line.startswith("Person: ") or line.startswith("Assistant: ")
        ]
        self.assertEqual(len(labels), 12)

    def test_auto_uses_claude_then_ollama_then_none(self):
        # source: Door 27, 11-12: auto picks claude, then ollama within two seconds, then none
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")
        calls = []

        def call_claude(prompt):
            calls.append("claude")
            return "From claude."

        def call_ollama(prompt):
            calls.append("ollama")
            return "From ollama."

        with self.patch(call_claude=call_claude, call_ollama=call_ollama):
            with _Attr(closeout.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None):
                code, out, err = self.cli(
                    "note", "--latest", "--provider", "auto", "--out", str(self.out),
                )
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, ["claude"])
        self.assertIn("NEXT: From claude.", self.only_note())

        shutil.rmtree(self.out)
        calls[:] = []
        with self.patch(call_claude=call_claude, call_ollama=call_ollama, probe_ollama=lambda: True):
            with _Attr(closeout.shutil, "which", lambda name: None):
                code, out, err = self.cli(
                    "note", "--latest", "--provider", "auto", "--out", str(self.out),
                )
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, ["ollama"])
        self.assertIn("NEXT: From ollama.", self.only_note())

        shutil.rmtree(self.out)
        calls[:] = []
        with self.patch(call_claude=call_claude, call_ollama=call_ollama, probe_ollama=lambda: False):
            with _Attr(closeout.shutil, "which", lambda name: None):
                code, out, err = self.cli(
                    "note", "--latest", "--provider", "auto", "--out", str(self.out),
                )
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [])
        text = self.only_note()
        self.assertIn("NEXT: Run the upload once.", text)
        self.assertNotIn("provider failed", text)

    def test_auto_ollama_probe_timeout_is_two_seconds(self):
        # source: Door 27, 11-12: auto treats ollama as down when it does not answer within two seconds
        seen = {}

        def fetch(timeout):
            seen["timeout"] = timeout
            raise OSError("down")

        with self.patch(fetch_ollama_model=fetch):
            with _Attr(closeout.shutil, "which", lambda name: None):
                self.assertEqual(closeout.select_provider("auto"), "none")
        self.assertEqual(seen["timeout"], 2)

    def test_claude_provider_passes_the_prompt_on_stdin(self):
        # source: Door 27, 11-12: claude -p receives the prompt on stdin

        class Proc(object):
            returncode = 0
            stdout = "Ship the notes.\nSecond line ignored.\n"
            stderr = ""

        seen = {}

        def fake_run(*args, **kwargs):
            seen["args"] = args[0] if args else kwargs.get("args")
            seen["input"] = kwargs.get("input")
            seen["text"] = kwargs.get("text")
            return Proc()

        with _Attr(closeout.subprocess, "run", fake_run):
            result = closeout.call_claude("hello from stdin")
        self.assertEqual(result, "Ship the notes.")
        # source: Door 27 review-gate REJECT, 11-12: each claude -p call wrote a new transcript that
        # later list and --latest runs picked up, so the call now asks for no saved session
        # source: security review, 11-12: transcript text reached a claude -p that had tools, so text
        # inside a chat could steer an agent; the call now runs with no tools and no MCP servers
        self.assertEqual(
            seen["args"],
            ["claude", "-p", "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
        )
        self.assertEqual(seen["input"], "hello from stdin")
        self.assertTrue(seen["text"])

    def test_ollama_posts_chat_with_stream_false(self):
        # source: Door 27, 11-12: ollama chat is posted with stream false and the first listed model
        calls = []

        class Resp(object):
            def __init__(self, raw):
                self.raw = raw

            def read(self):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, timeout, request.data))
            if request.full_url.endswith("/api/tags"):
                return Resp(b'{"models":[{"name":"test-model"},{"name":"other"}]}')
            return Resp(b'{"message":{"role":"assistant","content":"Ship the notes."}}')

        with _Attr(closeout.urllib.request, "urlopen", fake_urlopen):
            result = closeout.call_ollama("hello prompt")
        self.assertEqual(result, "Ship the notes.")
        self.assertEqual(calls[0][0], "http://localhost:11434/api/tags")
        self.assertEqual(calls[1][0], "http://localhost:11434/api/chat")
        body = json.loads(calls[1][2].decode("utf-8"))
        self.assertIs(body["stream"], False)
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello prompt"}])

    def test_windows_skips_git_and_the_note_says_so(self):
        # source: Door 27, 11-12: Windows may skip git checks if the note says so
        # source: Door 27 review-gate REJECT, 11-12: with the checks skipped, a chat with edits went to
        # "Safe to close"; failure-closed means an edit nobody could check keeps the chat open
        repo, edit = self.stage_dirty()
        self.stage("clean.jsonl", "clean-app", "sess-clean.jsonl")

        def fail_git(*args, **kwargs):
            raise AssertionError("git was called")

        with self.patch(git_checks_enabled=lambda: False, run_git=fail_git):
            code, out, err = self.cli(
                "note", "--session", "sess-dirty", "--provider", "none", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: KEEP")
        self.assertEqual(
            lines[3],
            "REASON: Kept because %s could not be checked, and git checks were skipped on Windows." % edit,
        )
        shutil.rmtree(str(self.out))
        with self.patch(git_checks_enabled=lambda: False, run_git=fail_git):
            code, out, err = self.cli(
                "note", "--session", "sess-clean", "--provider", "none", "--out", str(self.out),
            )
        self.assertEqual(code, 0, err)
        lines = self.assert_note(self.only_note())
        self.assertEqual(lines[2], "VERDICT: CLOSE")
        self.assertEqual(
            lines[3],
            "REASON: Closed because the last request was answered, and git checks were skipped on Windows.",
        )

    def test_edit_tools_collect_file_paths(self):
        # source: Door 27, 11-12: Edit, Write, MultiEdit, and NotebookEdit mark the files that changed
        records = [{
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": "b.py"}},
                {"type": "tool_use", "name": "MultiEdit", "input": {"file_path": "c.py"}},
                {"type": "tool_use", "name": "NotebookEdit", "input": {"notebook_path": "d.ipynb"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "e.py"}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
            ]},
        }]
        self.assertEqual(closeout.edited_files(records), ["a.py", "b.py", "c.py", "d.ipynb"])

    def test_cwd_inside_rejects_an_unrelated_directory(self):
        # source: Door 27, 11-12: git status is trusted only for a repo that contains the working directory
        other = Path(self.tmp) / "other"
        other.mkdir()
        self.assertTrue(closeout.cwd_inside(str(self.work), str(self.work)))
        self.assertFalse(closeout.cwd_inside(str(self.work), str(other)))

    def test_skill_tells_the_model_the_format_and_command(self):
        # source: Door 27, 11-12: the skill writes the same note and runs closeout on the current chat
        # source: Door 27 review, 11-12: Claude Code 2.1.322 does not export CLAUDE_SESSION_ID to the
        # shell, so a skill keyed on it never ran the tool; the chat being closed is the newest transcript
        text = (ROOT / "skill" / "closeout" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: closeout\n", text)
        self.assertIn("description: Write the closing note for this chat and say whether it is safe to close.\n", text)
        self.assertIn("VERDICT: KEEP | MERGE with <other title> | CLOSE", text)
        self.assertIn("python3 ~/.claude/skills/closeout/closeout.py note --latest", text)
        self.assertNotIn("CLAUDE_SESSION_ID", text)
        self.assertIn("~/closeouts/", text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)

    def test_readme_ends_with_the_door_lines(self):
        # source: Door 27, 11-12: the README ends with the Door 27 lines
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines[-2],
            "Made by Lucas Cooper-Bey as Door 27 of 100 Doors, one free tool a day: https://sandiegoaistudio.com/season",
        )
        self.assertEqual(
            lines[-1],
            "If you like seeing how other people keep track of their AI work, San Diego AI Studio runs Sharathon, where builders show their own setups: https://www.skool.com/sandiegoaistudio",
        )
        text = "\n".join(lines)
        self.assertIn("python3 closeout.py list", text)
        self.assertIn("python3 closeout.py sweep", text)
        self.assertIn("~/.claude/skills/", text)
        self.assertIn("Nothing leaves the machine", text)

    def test_license_names_the_copyright_holder(self):
        # source: Door 27, 11-12: the license is MIT and names the copyright holder
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 Lucas Cooper-Bey", text)

    def test_no_em_dash_or_en_dash_in_the_project(self):
        # source: Door 27, 11-12: the project text uses plain hyphens
        names = {"LICENSE", ".gitignore"}
        suffixes = {".py", ".md", ".jsonl"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix not in suffixes and path.name not in names:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, str(path))
            self.assertNotIn("\u2013", text, str(path))


class _Patched(object):
    def __init__(self, module, values):
        self.module = module
        self.values = values
        self.old = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = getattr(self.module, key)
            setattr(self.module, key, value)
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            setattr(self.module, key, value)
        return False


class _Attr(object):
    def __init__(self, obj, name, value):
        self.obj = obj
        self.name = name
        self.value = value
        self.old = None

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, exc_type, exc, tb):
        setattr(self.obj, self.name, self.old)
        return False


if __name__ == "__main__":
    unittest.main()
