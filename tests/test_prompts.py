import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import prompts


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(__file__).resolve().parent / "_prompt_tmp"
        self.tmp.mkdir(exist_ok=True)
        self.saved = prompts.PROMPT_DIR
        prompts.PROMPT_DIR = self.tmp

    def tearDown(self):
        prompts.PROMPT_DIR = self.saved
        for leftover in self.tmp.glob("*"):
            leftover.unlink()
        self.tmp.rmdir()

    def test_builtin_default_is_used_when_no_file_exists(self):
        self.assertEqual(prompts.DEFAULT_WAIT_WHAT, prompts.wait_what())
        self.assertEqual(prompts.DEFAULT_PLAIN, prompts.plain())
        self.assertEqual("內建預設", prompts.source_of("wait-what"))

    def test_a_file_overrides_the_default(self):
        (self.tmp / "plain.md").write_text("我自己的重講規則", encoding="utf-8")
        self.assertEqual("我自己的重講規則", prompts.plain())
        self.assertIn("plain.md", prompts.source_of("plain"))

    def test_an_empty_file_falls_back_instead_of_sending_nothing(self):
        (self.tmp / "plain.md").write_text("   \n\n", encoding="utf-8")
        self.assertEqual(prompts.DEFAULT_PLAIN, prompts.plain())
        self.assertEqual("內建預設", prompts.source_of("plain"))

    def test_the_two_prompts_override_independently(self):
        (self.tmp / "plain.md").write_text("只換白話那套", encoding="utf-8")
        self.assertEqual("只換白話那套", prompts.plain())
        self.assertEqual(prompts.DEFAULT_WAIT_WHAT, prompts.wait_what())

    def test_defaults_do_not_pin_an_output_language(self):
        for text in (prompts.DEFAULT_WAIT_WHAT, prompts.DEFAULT_PLAIN):
            self.assertNotIn("繁體中文", text)
            self.assertIn("相同的語言", text)


if __name__ == "__main__":
    unittest.main()
