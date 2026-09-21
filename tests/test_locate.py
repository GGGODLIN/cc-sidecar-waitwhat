import datetime
import json
import pathlib
import sys
import unittest
from unittest import mock

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


STAMP = "2026-09-14T06:00:00.000Z"


def local(stamp):
    moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return moment.astimezone().replace(tzinfo=None)


def record(pid=1, command="/usr/local/bin/claude", offset=0.0, stamp=STAMP):
    return {"pid": pid, "command": command,
            "started": local(stamp) + datetime.timedelta(seconds=offset)}


class ContainerTest(unittest.TestCase):
    def test_orca_wins_when_both_multiplexers_are_present(self):
        found = locate.container({"ORCA_TAB_ID": "tab-o", "HERDR_TAB_ID": "tab-h"})
        self.assertEqual(("ORCA_TAB_ID", "tab-o"), found)

    def test_herdr_is_used_on_its_own(self):
        self.assertEqual(("HERDR_TAB_ID", "w4:tP7"),
                         locate.container({"HERDR_TAB_ID": "w4:tP7"}))

    def test_a_plain_terminal_has_no_container(self):
        self.assertEqual((None, None), locate.container({"TERM_PROGRAM": "ghostty"}))

    def test_an_empty_value_does_not_count_as_a_container(self):
        self.assertEqual((None, None), locate.container({"ORCA_TAB_ID": ""}))


class PaneTest(LocateTest):
    def env_of(self, mapping):
        return lambda pid: mapping.get(pid, {})

    def cwd_of(self, mapping, default="/work/repo"):
        return lambda pid: mapping.get(pid, default)

    def test_session_id_in_the_command_line_wins_over_timing(self):
        resumed = "e0cd476e-9b28-4459-a71f-4e4759fedfad"
        self.write(resumed, [human("舊的", "2026-09-14T01:00:00.000Z"),
                             assistant("舊回答", "2026-09-14T01:00:10.000Z")])
        self.write("decoy", [human("同時開的"), assistant("回答")])
        found = locate.session_for(record(command=f"claude --resume {resumed}"),
                                   self.root, self.cwd_of({}))
        self.assertEqual(resumed, found)

    def test_a_name_that_is_not_a_session_id_is_ignored_in_the_command_line(self):
        self.write("mine", [human("問題"), assistant("回答")])
        found = locate.session_for(record(command="claude --resume nope", offset=1.0),
                                   self.root, self.cwd_of({}))
        self.assertEqual("mine", found)

    def test_the_session_written_at_startup_is_the_match(self):
        self.write("mine", [human("問題"), assistant("回答")])
        self.write("older", [human("早就開了", "2026-09-14T05:00:00.000Z"),
                             assistant("回答", "2026-09-14T05:00:10.000Z")])
        found = locate.session_for(record(offset=1.3), self.root, self.cwd_of({}))
        self.assertEqual("mine", found)

    def test_two_sessions_starting_together_resolve_to_nothing(self):
        self.write("twin_a", [human("問題"), assistant("回答")])
        self.write("twin_b", [human("問題"), assistant("回答")])
        self.assertIsNone(locate.session_for(record(), self.root, self.cwd_of({})))

    def test_a_session_that_has_not_been_written_yet_resolves_to_nothing(self):
        self.write("other", [human("別人的", "2026-09-14T05:00:00.000Z"),
                             assistant("回答", "2026-09-14T05:00:10.000Z")])
        self.assertIsNone(locate.session_for(record(), self.root, self.cwd_of({})))

    def test_a_cwd_with_no_project_folder_resolves_to_nothing(self):
        self.write("mine", [human("問題"), assistant("回答")])
        self.assertIsNone(locate.session_for(record(), self.root,
                                             self.cwd_of({}, default="/nowhere")))

    def test_only_processes_sharing_the_container_value_are_taken(self):
        self.write("mine", [human("問題"), assistant("鄰居這格說的話")])
        rows = locate.from_pane(
            self.root,
            environ={"ORCA_TAB_ID": "tab-1"},
            records=[record(pid=11, offset=1.0), record(pid=22, offset=1.0)],
            env_of=self.env_of({11: {"ORCA_TAB_ID": "tab-1"}, 22: {"ORCA_TAB_ID": "tab-2"}}),
            cwd_of=self.cwd_of({}))
        self.assertEqual(["mine"], [row["id"] for row in rows])
        self.assertEqual("鄰居這格說的話", rows[0]["title"])
        self.assertTrue(rows[0]["focused"])
        self.assertEqual("pane", rows[0]["source"])

    def test_a_brand_new_neighbour_is_skipped_rather_than_guessed(self):
        self.write("mine", [human("問題"), assistant("回答")])
        rows = locate.from_pane(
            self.root,
            environ={"ORCA_TAB_ID": "tab-1"},
            records=[record(pid=11, offset=1.0), record(pid=22, offset=9000.0)],
            env_of=self.env_of({11: {"ORCA_TAB_ID": "tab-1"}, 22: {"ORCA_TAB_ID": "tab-1"}}),
            cwd_of=self.cwd_of({}))
        self.assertEqual(["mine"], [row["id"] for row in rows])

    def test_no_container_variable_means_no_pane_rows(self):
        self.write("mine", [human("問題"), assistant("回答")])
        self.assertEqual([], locate.from_pane(self.root, environ={"TERM_PROGRAM": "ghostty"}))


class CandidatesTest(unittest.TestCase):
    def rows(self, ids, source, focused=False):
        return [{"id": name, "path": pathlib.Path(f"/tmp/{name}.jsonl"), "title": name,
                 "status": "unknown", "focused": focused, "cwd": "/work/repo",
                 "source": source, "at": 0} for name in ids]

    def test_the_pane_session_leads_and_is_not_listed_twice(self):
        with mock.patch.object(locate, "from_pane", return_value=self.rows(["mine"], "pane", True)), \
             mock.patch.object(locate, "others", return_value=self.rows(["mine", "other"], "files")):
            rows = locate.candidates(environ={"ORCA_TAB_ID": "tab-1"})
        self.assertEqual(["mine", "other"], [row["id"] for row in rows])
        self.assertEqual([True, False], [row["focused"] for row in rows])
        self.assertEqual("pane", rows[0]["source"])

    def test_without_a_pane_match_the_old_listing_is_untouched(self):
        with mock.patch.object(locate, "from_pane", return_value=[]), \
             mock.patch.object(locate, "others", return_value=self.rows(["a"], "herdr", True)):
            rows = locate.candidates(environ={"HERDR_TAB_ID": "w4:tP7"})
        self.assertEqual(["a"], [row["id"] for row in rows])
        self.assertTrue(rows[0]["focused"])

    def test_a_herdr_pane_keeps_asking_herdr_for_the_rest(self):
        with mock.patch.object(locate, "from_herdr", return_value=self.rows(["h"], "herdr")) as asked, \
             mock.patch.object(locate, "from_files", return_value=self.rows(["f"], "files")):
            rows = locate.others(var="HERDR_TAB_ID")
        asked.assert_called_once()
        self.assertEqual(["h"], [row["id"] for row in rows])

    def test_an_orca_pane_lists_the_rest_from_files_not_from_herdr(self):
        with mock.patch.object(locate, "from_herdr", return_value=self.rows(["h"], "herdr")) as skipped, \
             mock.patch.object(locate, "from_files", return_value=self.rows(["f"], "files")):
            rows = locate.others(var="ORCA_TAB_ID")
        skipped.assert_not_called()
        self.assertEqual(["f"], [row["id"] for row in rows])


if __name__ == "__main__":
    unittest.main()
