import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import cache


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "shared-cache-key.json"


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(__file__).resolve().parent / "_cache_tmp"
        self.tmp.mkdir(exist_ok=True)
        self.path = self.tmp / "cache.json"

    def tearDown(self):
        for leftover in self.tmp.glob("*"):
            leftover.unlink()
        self.tmp.rmdir()

    def test_shared_key_matches_contract_fixture(self):
        fixture = json.loads(FIXTURE.read_text())
        messages = [(item["role"], item["text"]) for item in fixture["messages"]]
        self.assertEqual(fixture["expected"], cache.shared_key_for(fixture["mode"], messages))

    def test_shared_key_separates_modes(self):
        messages = [("user", "同一段"), ("assistant", "同一個回答")]
        self.assertNotEqual(
            cache.shared_key_for("plain", messages),
            cache.shared_key_for("lost", messages))

    def test_shared_key_separates_changed_text(self):
        original = [("user", "問題"), ("assistant", "原答案")]
        changed = [("user", "問題"), ("assistant", "新答案")]
        self.assertNotEqual(
            cache.shared_key_for("plain", original),
            cache.shared_key_for("plain", changed))

    def test_source_is_remembered_with_the_answer(self):
        key = cache.shared_key_for("plain", [("user", "問題"), ("assistant", "答案")])
        cache.put(key, "私有來源給的答案", "白話", "cmd:private-llm", self.path)
        self.assertEqual("cmd:private-llm", cache.get(key, self.path)["source"])

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
        key = "key"
        cache.put(key, "還是寫得進去", "白話", "gemini:flash", self.path)
        self.assertEqual("還是寫得進去", cache.get(key, self.path)["answer"])

    def test_stats_counts_by_label(self):
        cache.put("a", "x", "白話", "gemini:flash", self.path)
        cache.put("b", "y", "白話", "gemini:flash", self.path)
        cache.put("c", "z", "跟丟了", "gemini:flash", self.path)
        total, labels = cache.stats(self.path)
        self.assertEqual(3, total)
        self.assertEqual({"白話": 2, "跟丟了": 1}, labels)

    def test_written_file_is_valid_json(self):
        cache.put("key", "內容", "白話", "gemini:flash", self.path)
        self.assertIsInstance(json.loads(self.path.read_text()), dict)


if __name__ == "__main__":
    unittest.main()
