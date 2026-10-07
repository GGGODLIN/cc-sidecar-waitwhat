import io
import json
import os
import pathlib
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import model


class CommandSourceTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("SIDECAR_CMD")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SIDECAR_CMD", None)
        else:
            os.environ["SIDECAR_CMD"] = self.saved

    def test_configured_command_wins_over_detection(self):
        os.environ["SIDECAR_CMD"] = "my-own-llm --flag"
        self.assertEqual("my-own-llm --flag", model.command_line())

    def test_prompt_goes_in_on_stdin_and_answer_comes_back_on_stdout(self):
        answer, _ = model.ask_command("系統指示", "要重講的內容", "cat")
        self.assertIn("系統指示", answer)
        self.assertIn("要重講的內容", answer)

    def test_arguments_are_split_like_a_shell_would(self):
        answer, _ = model.ask_command("s", "p", "head -n 1")
        self.assertEqual("s", answer)

    def test_nonzero_exit_is_reported(self):
        with self.assertRaises(RuntimeError) as caught:
            model.ask_command("s", "p", "false")
        self.assertIn("exit 1", str(caught.exception))

    def test_empty_output_is_reported(self):
        with self.assertRaises(RuntimeError) as caught:
            model.ask_command("s", "p", "true")
        self.assertIn("沒有輸出", str(caught.exception))

    def test_missing_binary_is_reported(self):
        with self.assertRaises(RuntimeError) as caught:
            model.ask_command("s", "p", "definitely-not-a-real-command-xyz")
        self.assertIn("找不到指令", str(caught.exception))


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("SIDECAR_CMD")
        os.environ["SIDECAR_CMD"] = "cat"

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SIDECAR_CMD", None)
        else:
            os.environ["SIDECAR_CMD"] = self.saved

    def test_single_source_yields_one_cache_slot(self):
        self.assertEqual(["cmd:cat"], model.candidate_models("cmd"))
        self.assertEqual(["some-model"], model.candidate_models("http", "some-model"))

    def test_auto_lists_cmd_before_http(self):
        options = model.candidate_models("auto", "some-model")
        self.assertIn("cmd:cat", options)
        self.assertIn("some-model", options)
        self.assertLess(options.index("cmd:cat"), options.index("some-model"))

    def test_auto_prefers_the_command_source(self):
        result = model.ask("系統指示", "內容", source="cmd")
        self.assertEqual("cmd:cat", result["source"])
        self.assertIn("內容", result["answer"])

    def test_uncertain_web_submission_stops_auto_without_an_http_request(self):
        command = "import sys; print('SIDECAR_WEB_RETRY_UNSAFE {}', file=sys.stderr); sys.exit(3)"
        os.environ["SIDECAR_CMD"] = shlex.join([sys.executable, "-c", command])
        with patch("urllib.request.urlopen", side_effect=AssertionError("不得重送 HTTP")):
            with self.assertRaises(RuntimeError) as caught:
                model.ask("系統指示", "內容", source="auto")
        self.assertIn("送出狀態不明", str(caught.exception))

    def test_unsafe_marker_after_fallback_notice_still_stops_auto(self):
        command = "import sys; print('Thinking 改用 Instant', file=sys.stderr); print('SIDECAR_WEB_RETRY_UNSAFE {}', file=sys.stderr); sys.exit(3)"
        os.environ["SIDECAR_CMD"] = shlex.join([sys.executable, "-c", command])
        with patch("urllib.request.urlopen", side_effect=AssertionError("Instant 狀態不明後不得重送")):
            with self.assertRaises(RuntimeError):
                model.ask("系統指示", "內容", source="auto")

    def test_process_timeout_stops_auto_without_an_http_request(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cat", 300)), patch(
                "urllib.request.urlopen", side_effect=AssertionError("逾時後不得重送 HTTP")):
            with self.assertRaises(RuntimeError) as caught:
                model.ask("系統指示", "內容", source="auto")
        self.assertIn("送出狀態不明", str(caught.exception))

    def test_signalled_process_stops_auto_without_an_http_request(self):
        stopped = subprocess.CompletedProcess(["cat"], -9, stdout="", stderr="")
        with patch("subprocess.run", return_value=stopped), patch(
                "urllib.request.urlopen", side_effect=AssertionError("程序被終止後不得重送")):
            with self.assertRaises(RuntimeError):
                model.ask("系統指示", "內容", source="auto")

    def test_unrelated_exit_three_keeps_the_existing_http_fallback(self):
        os.environ["SIDECAR_CMD"] = shlex.join([sys.executable, "-c", "import sys; sys.exit(3)"])
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "HTTP answer"}}]}).encode())
        with patch("urllib.request.urlopen", return_value=response), patch.dict(os.environ, {"SIDECAR_API_KEY": "fixture"}):
            result = model.ask("系統指示", "內容", source="auto")
        self.assertEqual(result["answer"], "HTTP answer")
        self.assertTrue(result["source"].startswith("http:"))


if __name__ == "__main__":
    unittest.main()
