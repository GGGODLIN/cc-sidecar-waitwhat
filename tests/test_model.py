import os
import pathlib
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
