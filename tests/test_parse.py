import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import parse


def human(text, timestamp="2026-09-14T06:00:00.000Z"):
    return {"type": "user", "origin": {"kind": "human"}, "timestamp": timestamp,
            "cwd": "/tmp/demo", "message": {"role": "user", "content": text}}


def assistant(text, timestamp="2026-09-14T06:00:10.000Z"):
    return {"type": "assistant", "timestamp": timestamp,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def tool_result(text="tool output"):
    return {"type": "user", "timestamp": "2026-09-14T06:00:05.000Z",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "content": text}]}}


def tool_use(name, payload):
    return {"type": "assistant", "timestamp": "2026-09-14T06:00:04.000Z",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": payload}]}}


def write(tmp, records):
    path = tmp / "session.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records))
    return path


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(__file__).resolve().parent / "_tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        for leftover in self.tmp.glob("*"):
            leftover.unlink()
        self.tmp.rmdir()

    def test_only_human_origin_counts_as_user_turn(self):
        path = write(self.tmp, [human("真的是我打的"), tool_result("這是工具回傳")])
        _, turns, _ = parse.transcript(path)
        self.assertEqual([("human", "2026-09-14T06:00:00.000Z", "真的是我打的")], turns)

    def test_system_reminder_is_stripped(self):
        path = write(self.tmp, [human("問題<system-reminder>不該出現</system-reminder>")])
        _, turns, _ = parse.transcript(path)
        self.assertEqual("問題", turns[0][2])

    def test_sidechain_records_are_skipped(self):
        record = assistant("subagent 說的話")
        record["isSidechain"] = True
        path = write(self.tmp, [human("主線問題"), record, assistant("主線回答")])
        _, turns, _ = parse.transcript(path)
        self.assertEqual(["主線問題", "主線回答"], [text for _, _, text in turns])

    def test_tool_calls_are_collected_separately(self):
        path = write(self.tmp, [human("跑一下"), tool_use("Bash", {"command": "ls"}),
                                assistant("跑完了")])
        _, turns, tools = parse.transcript(path)
        self.assertEqual(2, len(turns))
        self.assertEqual(1, len(tools))
        self.assertTrue(tools[0].startswith("Bash: "))

    def test_recent_turns_takes_one_turn(self):
        path = write(self.tmp, [human("舊問題", "2026-09-14T05:00:00.000Z"),
                                assistant("舊回答", "2026-09-14T05:00:10.000Z"),
                                human("新問題", "2026-09-14T06:00:00.000Z"),
                                assistant("新回答", "2026-09-14T06:00:10.000Z")])
        groups = parse.recent_turns(path, 1)
        self.assertEqual(1, len(groups))
        self.assertEqual("新問題", groups[0]["prompt"])
        self.assertEqual(["新回答"], groups[0]["replies"])

    def test_one_turn_keeps_every_reply_around_tool_calls(self):
        path = write(self.tmp, [human("先前問題", "2026-09-14T05:00:00.000Z"),
                                assistant("先前回答", "2026-09-14T05:00:10.000Z"),
                                human("這一題", "2026-09-14T06:00:00.000Z"),
                                assistant("長篇分析" * 20, "2026-09-14T06:00:10.000Z"),
                                tool_use("Bash", {"command": "ls"}),
                                assistant("開工。", "2026-09-14T06:00:30.000Z")])
        groups = parse.recent_turns(path, 1)
        self.assertEqual("這一題", groups[0]["prompt"])
        self.assertEqual(2, len(groups[0]["replies"]))
        self.assertTrue(groups[0]["replies"][0].startswith("長篇分析"))
        self.assertEqual("開工。", groups[0]["replies"][1])

    def test_recent_turns_counts_backwards(self):
        records = []
        for index in range(5):
            records.append(human(f"問題 {index}", f"2026-09-14T06:{index:02d}:00.000Z"))
            records.append(assistant(f"回答 {index}", f"2026-09-14T06:{index:02d}:30.000Z"))
        path = write(self.tmp, records)
        groups = parse.recent_turns(path, 3)
        self.assertEqual(["問題 2", "問題 3", "問題 4"],
                         [group["prompt"] for group in groups])

    def test_recent_turns_clamps_when_asked_for_too_many(self):
        path = write(self.tmp, [human("只有一輪"), assistant("回答")])
        self.assertEqual(1, len(parse.recent_turns(path, 99)))

    def test_turn_without_any_human_message(self):
        path = write(self.tmp, [assistant("孤兒回應", "2026-09-14T06:00:10.000Z")])
        groups = parse.recent_turns(path, 1)
        self.assertIsNone(groups[0]["prompt"])
        self.assertEqual(["孤兒回應"], groups[0]["replies"])

    def test_tail_read_drops_partial_first_line(self):
        records = [human(f"訊息 {i}", f"2026-09-14T06:{i:02d}:00.000Z") for i in range(40)]
        path = write(self.tmp, records)
        _, turns, _ = parse.transcript(path, tail=400)
        self.assertTrue(turns)
        self.assertEqual("訊息 39", turns[-1][2])
        self.assertLess(len(turns), 40)


if __name__ == "__main__":
    unittest.main()
