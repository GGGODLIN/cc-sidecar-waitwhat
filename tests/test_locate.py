import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import locate


def human(text, timestamp="2026-09-14T06:00:00.000Z", cwd="/work/repo"):
    return {"type": "user", "origin": {"kind": "human"}, "timestamp": timestamp,
            "cwd": cwd, "message": {"role": "user", "content": text}}


def headless(text, timestamp="2026-09-14T06:00:00.000Z", cwd="/work/repo"):
    return {"type": "user", "timestamp": timestamp, "cwd": cwd,
            "message": {"role": "user", "content": text}}


def assistant(text, timestamp="2026-09-14T06:00:10.000Z", cwd="/work/repo"):
    return {"type": "assistant", "timestamp": timestamp, "cwd": cwd,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class LocateTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / "_locate_tmp"
        self.root.mkdir(exist_ok=True)

    def tearDown(self):
        for leftover in sorted(self.root.rglob("*"), reverse=True):
            leftover.unlink() if leftover.is_file() else leftover.rmdir()
        self.root.rmdir()

    def write(self, name, records, folder="-work-repo"):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        path = directory / f"{name}.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records))
        return path

    def test_summary_prefers_the_real_cwd_over_the_folder_slug(self):
        path = self.write("s1", [human("問題"), assistant("回答")])
        summary = locate.session_summary(path)
        self.assertEqual("/work/repo", summary["cwd"])
        self.assertEqual("問題", summary["said"])
        self.assertEqual("回答", summary["reply"])

    def test_title_uses_the_assistant_reply_not_the_user_line(self):
        self.write("s1", [human("a"), assistant("這一輪 CC 做了什麼")])
        rows = locate.from_files(self.root, alive={"/work/repo"})
        self.assertEqual("這一輪 CC 做了什麼", rows[0]["title"])

    def test_title_falls_back_to_the_user_line_when_there_is_no_reply(self):
        self.write("s1", [human("只有我說話")])
        rows = locate.from_files(self.root, alive={"/work/repo"})
        self.assertEqual("只有我說話", rows[0]["title"])

    def test_headless_sessions_are_skipped(self):
        self.write("agent", [headless("這是 claude -p 產生的"), assistant("回答")])
        self.assertEqual([], locate.from_files(self.root, alive={"/work/repo"}))

    def test_sessions_whose_cwd_has_no_live_process_are_dropped(self):
        self.write("alive", [human("活著", cwd="/work/alive"),
                             assistant("回答", cwd="/work/alive")], folder="-work-alive")
        self.write("dead", [human("關掉了", cwd="/work/dead"),
                            assistant("回答", cwd="/work/dead")], folder="-work-dead")
        rows = locate.from_files(self.root, alive={"/work/alive"})
        self.assertEqual(["/work/alive"], [row["cwd"] for row in rows])

    def test_no_process_information_means_no_filtering(self):
        self.write("s1", [human("問題"), assistant("回答")])
        self.assertEqual(1, len(locate.from_files(self.root, alive=None)))

    def test_newest_human_message_sorts_first(self):
        self.write("old", [human("舊的", "2026-09-14T05:00:00.000Z"),
                           assistant("舊回答", "2026-09-14T05:00:10.000Z")])
        self.write("new", [human("新的", "2026-09-14T06:00:00.000Z"),
                           assistant("新回答", "2026-09-14T06:00:10.000Z")])
        rows = locate.from_files(self.root, alive={"/work/repo"})
        self.assertEqual(["新回答", "舊回答"], [row["title"] for row in rows])

    def test_claude_process_pattern_matches_the_binary_not_lookalikes(self):
        self.assertTrue(locate.CLAUDE_PROCESS.search("/Users/x/.local/bin/claude --model opus"))
        self.assertTrue(locate.CLAUDE_PROCESS.search("claude"))
        self.assertIsNone(locate.CLAUDE_PROCESS.search("/usr/bin/claude-helper --serve"))
        self.assertIsNone(locate.CLAUDE_PROCESS.search("node /opt/notclaude/index.js"))


if __name__ == "__main__":
    unittest.main()
