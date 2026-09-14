import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import render


class RenderTest(unittest.TestCase):
    def test_disabled_returns_the_original_text(self):
        source = "**粗體** 和 `程式碼`\n- 項目"
        self.assertEqual(source, render.render(source, styled=False))

    def test_bold_markers_are_replaced_by_ansi(self):
        out = render.basic("這句 **很重要** 喔")
        self.assertNotIn("**", out)
        self.assertIn(render.BOLD + "很重要" + render.RESET, out)

    def test_inline_code_keeps_content_without_backticks(self):
        out = render.basic("打 `ww 1` 就好")
        self.assertNotIn("`", out)
        self.assertIn("ww 1", out)

    def test_fence_lines_disappear_and_body_is_indented(self):
        out = render.basic("前言\n```text\n來源 web\n```\n後話")
        self.assertNotIn("```", out)
        self.assertIn("來源 web", out)
        self.assertIn("  ", out)
        self.assertIn("前言", out)
        self.assertIn("後話", out)

    def test_heading_loses_the_hashes(self):
        out = render.basic("## 什麼變了")
        self.assertNotIn("#", out)
        self.assertIn("什麼變了", out)

    def test_bullet_becomes_a_dot_and_keeps_indent(self):
        out = render.basic("  - 巢狀項目")
        self.assertIn("  • 巢狀項目", out)

    def test_quote_gets_a_bar(self):
        out = render.basic("> 引用的一句")
        self.assertIn("│", out)
        self.assertIn("引用的一句", out)

    def test_unclosed_fence_does_not_lose_the_tail(self):
        out = render.basic("開頭\n```\n沒有收尾的區塊")
        self.assertIn("沒有收尾的區塊", out)

    def test_asterisk_inside_a_word_is_left_alone(self):
        self.assertIn("2*3*4", render.basic("2*3*4"))

    def test_plain_text_survives_untouched(self):
        self.assertEqual("就是一句話", render.basic("就是一句話"))

    def test_markup_inside_inline_code_is_not_parsed(self):
        out = render.basic("`**粗體**` 會變粗")
        self.assertIn("**粗體**", out)
        self.assertNotIn(render.BOLD + "粗體", out)

    def test_double_backticks_can_wrap_a_backtick(self):
        out = render.basic("`` `--source web` `` 是行內程式碼")
        self.assertIn("`--source web`", out)
        self.assertNotIn("``", out)

    def test_lone_fence_in_a_sentence_is_left_alone(self):
        out = render.basic("程式碼區塊的 ``` 圍欄會消失")
        self.assertIn("```", out)

    def test_more_than_ten_code_spans_do_not_collide(self):
        source = " ".join(f"`片段{index}`" for index in range(12))
        out = render.basic(source)
        for index in range(12):
            self.assertIn(f"片段{index}", out)
        self.assertNotIn("\x00", out)

    def test_code_span_inside_a_bullet_keeps_the_marker_text(self):
        out = render.basic("- `- 項目` 會顯示成 `• 項目`")
        self.assertTrue(out.startswith("• "))
        self.assertIn("- 項目", out)
        self.assertIn("• 項目", out)


HAS_RICH = render.with_rich("測試") is not None


class RouteTest(unittest.TestCase):
    def test_prefer_rich_off_uses_the_builtin(self):
        out = render.render("**粗體**", prefer_rich=False)
        self.assertEqual(render.basic("**粗體**"), out)

    def test_rich_absent_falls_back_without_raising(self):
        self.assertIsInstance(render.render("**粗體**"), str)

    @unittest.skipUnless(HAS_RICH, "沒裝 rich")
    def test_rich_lays_out_a_table(self):
        out = render.render("| 來源 | 秒 |\n|---|---|\n| web | 13.7 |")
        self.assertNotIn("|---|", out)
        self.assertIn("來源", out)
        self.assertIn("13.7", out)

    @unittest.skipUnless(HAS_RICH, "沒裝 rich")
    def test_rich_does_not_break_a_cjk_line_mid_token(self):
        out = render.render("目前 28 個測試都通過，`2*3*4` 不能被誤判成斜體。")
        self.assertNotIn("2*3*\n", out)


if __name__ == "__main__":
    unittest.main()
