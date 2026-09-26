import contextlib
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import cache, cli

SID = "1519794a-e8f5-4a29-a687-f3154a08c1b2"


def human(text):
    return {"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-09-26T06:00:00.000Z",
            "cwd": "/work/repo", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "timestamp": "2026-09-26T06:00:10.000Z", "cwd": "/work/repo",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class SessionIdJsonTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / "_cli_tmp"
        (self.root / "-work-repo").mkdir(parents=True, exist_ok=True)
        self.cache = self.root / "cache.json"
        self.path = self.root / "-work-repo" / f"{SID}.jsonl"
        self.path.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                       for r in [human("問題"), assistant("回答")]))

    def tearDown(self):
        for leftover in sorted(self.root.rglob("*"), reverse=True):
            leftover.unlink() if leftover.is_file() else leftover.rmdir()
        self.root.rmdir()

    def run_cli(self, argv):
        out = io.StringIO()
        with mock.patch.object(cli.locate, "session_path",
                               lambda sid, root=None: self.path if sid == SID else None), \
                mock.patch.object(cache, "PATH", self.cache), \
                mock.patch.object(cli.locate, "candidates", side_effect=AssertionError("list used")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, json.loads(out.getvalue())

    def test_a_non_uuid_is_refused_before_any_file_is_looked_up(self):
        code, body = self.run_cli(["1", "--session-id", "../../etc/passwd", "--json"])
        self.assertEqual(1, code)
        self.assertEqual({"ok": False, "error": "session id 不是 UUID"}, body)

    def test_an_unknown_uuid_is_a_json_failure(self):
        code, body = self.run_cli(["1", "--session-id", "0" * 8 + SID[8:], "--json"])
        self.assertEqual(1, code)
        self.assertFalse(body["ok"])

    def test_a_cache_hit_answers_without_asking_a_model(self):
        messages = [("user", "問題"), ("assistant", "回答")]
        key = cache.shared_key_for("plain", messages)
        cache.put(key, "白話答案", "白話", "http:x", self.cache)
        with mock.patch.object(cli.model, "ask", side_effect=AssertionError("model asked")):
            code, body = self.run_cli(["1", "--session-id", SID, "--json"])
        self.assertEqual(0, code)
        self.assertEqual({"ok": True, "sessionId": SID, "mode": "plain", "label": "白話",
                          "key": key, "answer": "白話答案", "source": "http:x", "cached": True},
                         body)

    def test_a_fresh_answer_is_cached_under_the_shared_key(self):
        reply = {"answer": "整段脈絡", "source": "http:y", "usage": {}, "fallback": None}
        with mock.patch.object(cli.model, "ask", return_value=reply):
            code, body = self.run_cli(["--session-id", SID, "--json", "--source", "http"])
        self.assertEqual(0, code)
        self.assertEqual(("lost", "跟丟了", False), (body["mode"], body["label"], body["cached"]))
        self.assertEqual("整段脈絡", cache.get(body["key"], self.cache)["answer"])

    def test_a_model_failure_is_a_json_failure(self):
        with mock.patch.object(cli.model, "ask", side_effect=RuntimeError("proxy down")):
            code, body = self.run_cli(["1", "--session-id", SID, "--json"])
        self.assertEqual((1, {"ok": False, "error": "proxy down"}), (code, body))


if __name__ == "__main__":
    unittest.main()
