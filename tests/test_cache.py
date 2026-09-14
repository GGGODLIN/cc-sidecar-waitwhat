import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import cache


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(__file__).resolve().parent / "_cache_tmp"
        self.tmp.mkdir(exist_ok=True)
        self.path = self.tmp / "cache.json"

    def tearDown(self):
        for leftover in self.tmp.glob("*"):
            leftover.unlink()
        self.tmp.rmdir()

    def test_same_input_hits_and_changed_model_misses(self):
        key = cache.key_for("flash", "system", "payload")
        cache.put(key, "重講結果", "白話", "gemini:flash", self.path)
        self.assertEqual("重講結果", cache.get(key, self.path)["answer"])
        other = cache.key_for("pro", "system", "payload")
        self.assertIsNone(cache.get(other, self.path))

    def test_changed_payload_misses(self):
        key = cache.key_for("flash", "system", "payload")
        cache.put(key, "舊答案", "白話", "gemini:flash", self.path)
        moved = cache.key_for("flash", "system", "payload 多了一句")
        self.assertIsNone(cache.get(moved, self.path))

    def test_source_is_remembered_with_the_answer(self):
        key = cache.key_for("cmd:private-llm", "system", "payload")
        cache.put(key, "私有來源給的答案", "白話", "cmd:private-llm", self.path)
        self.assertEqual("cmd:private-llm", cache.get(key, self.path)["source"])

    def test_each_source_keeps_its_own_slot(self):
        system, payload = "system", "payload"
        cache.put(cache.key_for("cmd:private-llm", system, payload),
                  "cmd 版", "白話", "cmd:private-llm", self.path)
        cache.put(cache.key_for("some-http-model", system, payload),
                  "http 版", "白話", "http:some-http-model", self.path)
        self.assertEqual(
            "cmd 版",
            cache.get(cache.key_for("cmd:private-llm", system, payload), self.path)["answer"])
        self.assertEqual(
            "http 版",
            cache.get(cache.key_for("some-http-model", system, payload), self.path)["answer"])

    def test_eviction_keeps_newest_entries(self):
        original = cache.LIMIT
        cache.LIMIT = 3
        try:
            for index in range(5):
                cache.put(f"key{index}", f"答案{index}", "白話", "gemini:flash", self.path)
        finally:
            cache.LIMIT = original
        entries = cache.load(self.path)
        self.assertEqual(3, len(entries))
        self.assertIn("key4", entries)
        self.assertNotIn("key0", entries)

    def test_corrupt_file_is_treated_as_empty(self):
        self.path.write_text("{ 壞掉的 json")
        self.assertEqual({}, cache.load(self.path))
        key = cache.key_for("flash", "system", "payload")
        cache.put(key, "還是寫得進去", "白話", "gemini:flash", self.path)
        self.assertEqual("還是寫得進去", cache.get(key, self.path)["answer"])

    def test_stats_counts_by_label(self):
        cache.put(cache.key_for("m", "s", "a"), "x", "白話", "gemini:flash", self.path)
        cache.put(cache.key_for("m", "s", "b"), "y", "白話", "gemini:flash", self.path)
        cache.put(cache.key_for("m", "s", "c"), "z", "跟丟了", "gemini:flash", self.path)
        total, labels = cache.stats(self.path)
        self.assertEqual(3, total)
        self.assertEqual({"白話": 2, "跟丟了": 1}, labels)

    def test_written_file_is_valid_json(self):
        cache.put(cache.key_for("m", "s", "p"), "內容", "白話", "gemini:flash", self.path)
        self.assertIsInstance(json.loads(self.path.read_text()), dict)


if __name__ == "__main__":
    unittest.main()
