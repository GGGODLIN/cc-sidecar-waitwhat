import concurrent.futures
import contextlib
import http.client
import io
import json
import os
import pathlib
import selectors
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sidecar import cache, cli, locate, model, parse, prompts, render


SID = "1519794a-e8f5-4a29-a687-f3154a08c1b2"
HTTP_FIXTURE_URL = "http://fixture.invalid/v1/chat/completions"


def response_socket(status, body=b"", headers=None, reason="Fixture"):
    headers = {"Content-Length": str(len(body)), **(headers or {})}
    head = f"HTTP/1.1 {status} {reason}\r\n"
    head += "".join(f"{name}: {value}\r\n" for name, value in headers.items()) + "\r\n"
    conn = mock.Mock()
    conn.makefile.return_value = io.BytesIO(head.encode("ascii") + body)
    return conn


@contextlib.contextmanager
def http_connections(*responses):
    with mock.patch("urllib.request.getproxies", return_value={}), \
         mock.patch("socket.create_connection", side_effect=responses) as connect:
        yield connect


class ParseHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_non_dict_json_and_malformed_fields_do_not_crash_transcript(self):
        path = self.tmp / "corrupt.jsonl"
        lines = [
            "null",
            "[]",
            "123",
            '"just a string"',
            '{"type": "user", "origin": "human", "message": "not-a-dict"}',
            json.dumps({
                "type": "user",
                "origin": {"kind": "human"},
                "timestamp": 99999,
                "cwd": 12345,
                "message": {"role": "user", "content": [{"type": "text", "text": 42}, {"type": "text", "text": None}, {"type": "text", "text": "有效問題"}]},
            }, ensure_ascii=False),
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-09-14T06:00:10.000Z",
                "cwd": "/work/ok",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "內部推理"},
                        {"type": "text", "text": "   \n  "},
                        {"type": "tool_use", "name": "Read", "input": {"file": "a.py"}},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        {"type": "text", "text": "有效回答"},
                    ],
                },
            }, ensure_ascii=False),
            '{"truncated": true',
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

        meta, turns, tools = parse.transcript(path)
        self.assertEqual("/work/ok", meta.get("cwd"))
        self.assertEqual("2026-09-14T06:00:10.000Z", meta.get("started"))
        self.assertEqual([("human", "", "有效問題"), ("assistant", "2026-09-14T06:00:10.000Z", "有效回答")], turns)
        self.assertEqual(2, len(tools))
        self.assertTrue(tools[0].startswith("Read: "))
        self.assertTrue(tools[1].startswith("Bash: "))

    def test_nul_bytes_invalid_utf8_and_unicode_line_separators_are_handled(self):
        path = self.tmp / "binary.jsonl"
        record = {
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00.000Z",
            "cwd": "/work/demo",
            "message": {"role": "user", "content": "前段\x00後段\u2028同一行"},
        }
        raw_line = json.dumps(record, ensure_ascii=False).encode("utf-8")
        path.write_bytes(b"\xff\xfeinvalid\n" + raw_line + b"\n\x00\x00corrupt")

        _, turns, _ = parse.transcript(path)
        self.assertEqual(1, len(turns))
        self.assertNotIn("\x00", turns[0][2])
        self.assertIn("前段", turns[0][2])
        self.assertIn("同一行", turns[0][2])

    def test_missing_file_and_non_positive_tail_or_count_are_safe(self):
        missing = self.tmp / "missing.jsonl"
        meta, turns, tools = parse.transcript(missing)
        self.assertEqual(({}, [], []), (meta, turns, tools))

        path = self.tmp / "valid.jsonl"
        path.write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00.000Z",
            "message": {"role": "user", "content": "問"},
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual([], list(parse.iter_records(path, tail=0)))
        self.assertEqual([], list(parse.iter_records(path, tail=-50)))
        self.assertEqual([], parse.recent_turns(path, 0))
        self.assertEqual([], parse.recent_turns(path, -3))
        self.assertEqual(1, len(parse.recent_turns(path, 1, tail=None)))

    def test_tail_keeps_first_whole_record_at_exact_byte_boundary(self):
        path = self.tmp / "session.jsonl"
        last = '{"type":"assistant","message":{"content":"答案"}}'.encode("utf-8")
        path.write_bytes(b'{"type":"other"}\r\n' + last)
        self.assertEqual([("assistant", "", "答案")], parse.transcript(path, tail=len(last))[1])
        self.assertEqual([], parse.transcript(path, tail=len(last) - 1)[1])

    def test_very_long_line_and_nested_tool_blocks_parse_without_error(self):
        path = self.tmp / "long.jsonl"
        big_text = "x" * 1_500_000
        records = [
            {
                "type": "user",
                "origin": {"kind": "human"},
                "timestamp": "2026-09-14T06:00:00.000Z",
                "cwd": "/work/long",
                "message": {"role": "user", "content": big_text},
            },
            {
                "type": "user",
                "timestamp": "2026-09-14T06:00:05.000Z",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "content": [{"type": "tool_use", "name": "Nested", "input": {"k": "v"}}],
                    }],
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-14T06:00:10.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "   "},
                        {"type": "text", "text": "ok"},
                    ],
                },
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        _, turns, tools = parse.transcript(path)
        self.assertEqual(2, len(turns))
        self.assertEqual(1_500_000, len(turns[0][2]))
        self.assertEqual("ok", turns[1][2])
        self.assertEqual([], tools)


class LocateHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp_dir.name)
        self.root = self.base / "projects"
        self.root.mkdir()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_session_path_blocks_traversal_glob_and_symlink_escape(self):
        outside = self.base / "secret.jsonl"
        outside.write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00.000Z",
            "cwd": "/work/demo",
            "message": {"role": "user", "content": "secret"},
        }), encoding="utf-8")
        proj = self.root / "-work-demo"
        proj.mkdir()
        escaped_link = proj / f"{SID}.jsonl"
        escaped_link.symlink_to(outside)

        self.assertIsNone(locate.session_path("../secret", self.root))
        self.assertIsNone(locate.session_path("*", self.root))
        self.assertIsNone(locate.session_path(SID, self.root))

    def test_from_files_skips_symlinks_broken_links_and_missing_timestamps(self):
        outside = self.base / "outside.jsonl"
        outside.write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00.000Z",
            "cwd": "/work/demo",
            "message": {"role": "user", "content": "outside"},
        }), encoding="utf-8")
        proj = self.root / "-work-demo"
        proj.mkdir()
        (proj / "escape.jsonl").symlink_to(outside)
        (proj / "broken.jsonl").symlink_to(self.base / "does-not-exist.jsonl")
        (proj / "no_ts.jsonl").write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "cwd": "/work/demo",
            "message": {"role": "user", "content": "沒有時間戳"},
        }, ensure_ascii=False), encoding="utf-8")
        (proj / "with_ts.jsonl").write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00.000Z",
            "cwd": "/work/demo",
            "message": {"role": "user", "content": "有時間戳"},
        }, ensure_ascii=False), encoding="utf-8")

        rows = locate.from_files(self.root, alive=None)
        ids = [row["id"] for row in rows]
        self.assertEqual(["with_ts", "no_ts"], ids)

    def test_first_timestamp_survives_invalid_utf8_nondict_and_bad_iso(self):
        path = self.root / "bad.jsonl"
        path.write_bytes(
            b"\xff\xfe\nnull\n"
            + b'{"timestamp": 12345}\n'
            + b'{"timestamp": "not-a-timestamp"}\n'
            + b'{"timestamp": "2026-09-14T06:00:00.000Z"}\n'
        )
        stamp = locate.first_timestamp(path)
        self.assertIsNotNone(stamp)

    def test_herdr_agents_and_claude_processes_handle_malformed_outputs(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["herdr"], 0, stdout='["not-dict"]', stderr="")):
            self.assertEqual([], locate.herdr_agents())
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["herdr"], 0, stdout='{"result": {"agents": [null, "bad", {"agent": "claude", "agent_session": "bad"}]}}', stderr="")):
            agents = locate.herdr_agents()
            self.assertEqual(1, len(agents))
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["ps"], 0, stdout="\n   \n123 /usr/bin/claude\n", stderr="")):
            self.assertEqual(["123"], locate.claude_processes())

    def test_empty_or_missing_root_and_symlinked_folder_return_empty_or_none(self):
        missing_root = self.base / "does-not-exist"
        self.assertIsNone(locate.session_path(SID, missing_root))
        self.assertEqual([], locate.from_files(missing_root, alive=None))
        self.assertIsNone(locate.folder_for("/work/demo", missing_root))

        outside_dir = self.base / "outside_dir"
        outside_dir.mkdir()
        (self.root / "-work-escape").symlink_to(outside_dir)
        self.assertIsNone(locate.folder_for("/work/escape", self.root))


class CacheHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmp_dir.name)
        self.path = self.tmp / "nested" / "cache.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_load_eviction_and_stats_survive_corrupt_entries_and_non_file_path(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "bad_str": "not-a-dict",
            "bad_at": {"answer": "a", "label": "白話", "at": "not-a-float"},
            "bad_ans": {"answer": 123, "label": "白話", "at": 1.0},
            "good": {"answer": "ok", "label": "白話", "at": 2.0},
        }, ensure_ascii=False), encoding="utf-8")

        self.assertIsNone(cache.get("bad_ans", self.path))
        total, labels = cache.stats(self.path)
        self.assertGreaterEqual(total, 1)
        self.assertIsInstance(labels, dict)

        saved_limit = cache.LIMIT
        cache.LIMIT = 2
        try:
            cache.put("fresh", "新答案", "白話", "http:x", self.path)
        finally:
            cache.LIMIT = saved_limit
        self.assertEqual("新答案", cache.get("fresh", self.path)["answer"])

        dir_path = self.tmp / "as_dir"
        dir_path.mkdir()
        self.assertEqual({}, cache.load(dir_path))

    def test_cache_ignores_corrupt_entries_inside_a_valid_snapshot(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "bad": None,
            "other": {"answer": "x", "label": []},
            "good": {"answer": "keep", "label": "plain"},
        }), encoding="utf-8")
        self.assertEqual((1, {"plain": 1}), cache.stats(self.path))
        with mock.patch.object(cache, "LIMIT", 2):
            cache.put("new", "another", "plain", path=self.path)
        self.assertEqual({"good", "new"}, set(cache.load(self.path)))

    def test_put_is_atomic_has_0600_mode_and_does_not_follow_symlink_target(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("DO NOT TOUCH", encoding="utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.symlink_to(victim)

        cache.put("k1", "v1", "白話", "http:x", self.path)
        self.assertEqual("DO NOT TOUCH", victim.read_text(encoding="utf-8"))
        self.assertFalse(self.path.is_symlink())
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(0o600, mode)

    def test_concurrent_puts_never_produce_corrupt_json_or_leftover_temps(self):
        def worker(idx):
            for step in range(10):
                cache.put(f"k-{idx}-{step}", f"val-{idx}-{step}", "白話", "http:x", self.path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(worker, i) for i in range(6)]
            for fut in futures:
                fut.result()

        parsed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsInstance(parsed, dict)
        self.assertGreater(len(parsed), 0)
        leftovers = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(["cache.json", "cache.json.lock"], leftovers)

    def test_multiprocess_concurrent_puts_preserve_valid_json(self):
        repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
        script = "\n".join([
            "import pathlib, sys",
            f"sys.path.insert(0, {repo_root!r})",
            "from sidecar import cache",
            "p = pathlib.Path(sys.argv[1])",
            "idx = sys.argv[2]",
            "for i in range(8):",
            "    cache.put(f'mp-{idx}-{i}', 'v' * 400, '白話', 'http:x', p)",
        ])
        procs = [
            subprocess.Popen([sys.executable, "-c", script, str(self.path), str(i)])
            for i in range(4)
        ]
        codes = []
        try:
            for proc in procs:
                codes.append(proc.wait(timeout=15))
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        self.assertEqual([0, 0, 0, 0], codes)
        parsed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsInstance(parsed, dict)
        self.assertEqual({f"mp-{worker}-{step}" for worker in range(4) for step in range(8)}, set(parsed))
        self.assertEqual(["cache.json", "cache.json.lock"], sorted(p.name for p in self.path.parent.iterdir()))

    def test_cache_concurrent_processes_preserve_both_updates(self):
        worker = """
import pathlib
import sys
from sidecar import cache

path, key = pathlib.Path(sys.argv[1]), sys.argv[2]
original = cache.load

def delayed_load(target):
    entries = original(target)
    print('loaded', flush=True)
    sys.stdin.readline()
    return entries

cache.load = delayed_load
print('started', flush=True)
cache.put(key, key, 'plain', path=path)
"""
        processes = []
        try:
            for key in ("one", "two"):
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", worker, str(self.path), key],
                    cwd=pathlib.Path(__file__).resolve().parent.parent,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                )
                processes.append(process)
            # 兩個程序先啟動，讓未上鎖的實作有機會讀到同一份快照。
            with selectors.DefaultSelector() as ready:
                for process in processes:
                    ready.register(process.stdout, selectors.EVENT_READ)
                for _ in processes:
                    started = ready.select(5)
                    self.assertTrue(started, "cache workers did not start")
                    for descriptor, _ in started:
                        self.assertEqual(b"started\n", descriptor.fileobj.readline())
                        ready.unregister(descriptor.fileobj)
                    if not ready.get_map():
                        break
                for process in processes:
                    ready.register(process.stdout, selectors.EVENT_READ)
                loaded = ready.select(5)
                self.assertTrue(loaded, "neither cache worker read a snapshot")
                for descriptor, _ in loaded:
                    self.assertEqual(b"loaded\n", descriptor.fileobj.readline())
                    ready.unregister(descriptor.fileobj)
                if ready.get_map():
                    for descriptor, _ in ready.select(0.3):
                        self.assertEqual(b"loaded\n", descriptor.fileobj.readline())
            for process in processes:
                process.stdin.write(b"continue\n")
                process.stdin.flush()
            for process in processes:
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(0, process.returncode, stderr)
            self.assertEqual({"one", "two"}, set(cache.load(self.path)))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()


class ModelHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmp_dir.name)
        self.missing_config = self.tmp / "nonexistent-config.yaml"
        self.config_patcher = mock.patch.object(model, "CONFIG", self.missing_config)
        self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.tmp_dir.cleanup()

    def test_cross_origin_redirect_blocks_auth_header_leak(self):
        redirect = response_socket(302, headers={"Location": "http://other.invalid/next"})
        target = response_socket(200, b'{"choices":[{"message":{"content":"unexpected"}}]}')
        with mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), \
             mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "super-secret-token-xyz"}), \
             http_connections(redirect, target) as connect:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302") as caught:
                model.ask_http("sys", "user prompt", timeout=5)
        connect.assert_called_once()
        self.assertIn(b"Authorization: Bearer super-secret-token-xyz", redirect.sendall.call_args_list[0].args[0])
        target.sendall.assert_not_called()
        self.assertNotIn("super-secret-token-xyz", str(caught.exception))

    def test_http_refuses_redirect_before_opening_the_next_url(self):
        for status in (300, 301, 302, 303, 304, 305, 307, 308, 399):
            with self.subTest(status=status):
                redirect = response_socket(status, headers={"Location": "/other"})
                target = response_socket(200, b'{"choices":[{"message":{"content":"unexpected"}}]}')
                with mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), \
                     mock.patch.object(model, "api_key", return_value=None), \
                     http_connections(redirect, target) as connect:
                    with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                        model.ask_http("system", "question")
                connect.assert_called_once()
                self.assertIn(b"POST /v1/chat/completions ", redirect.sendall.call_args_list[0].args[0])
                target.sendall.assert_not_called()

    def test_http_4xx_5xx_429_timeout_truncated_and_bad_json_raise_runtime_error(self):
        cases = [
            (429, b'{"error":"too many"}', {"Retry-After": "12"}),
            (500, b"internal server error", {}),
            (200, b"<html>not json</html>", {}),
            (200, b'{"choices": [{"message": {"content": ""}}]}', {}),
            (200, b'{"choices": "not-a-list"}', {}),
            (200, b"[]", {}),
        ]
        for status_code, payload_bytes, extra_headers in cases:
            response = response_socket(status_code, payload_bytes, extra_headers)
            with self.subTest(status=status_code, body=payload_bytes), \
                 mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), \
                 mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "secret-key-999"}), \
                 http_connections(response) as connect:
                with self.assertRaises(RuntimeError) as caught:
                    model.ask_http("sys", "prompt", timeout=5)
                connect.assert_called_once()
                self.assertNotIn("secret-key-999", str(caught.exception))
                if status_code == 429:
                    self.assertIn("Retry-After: 12", str(caught.exception))

        with mock.patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("timed out")), \
             mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "secret-key-999"}):
            with self.assertRaisesRegex(RuntimeError, "逾時"):
                model.ask_http("sys", "prompt", timeout=1)

        with mock.patch("urllib.request.OpenerDirector.open", side_effect=http.client.IncompleteRead(b"partial")), \
             mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "secret-key-999"}):
            with self.assertRaises(RuntimeError):
                model.ask_http("sys", "prompt", timeout=1)

        with mock.patch.object(model, "PROXY", "file:///etc/passwd"), http_connections() as connect:
            with self.assertRaises(RuntimeError):
                model.ask_http("sys", "prompt", timeout=1)
        connect.assert_not_called()

        with mock.patch.object(model, "PROXY", "http://alice:superpass@fixture.invalid/v1/chat/completions"), \
             http_connections() as connect:
            with self.assertRaisesRegex(RuntimeError, "無效的 PROXY 位址") as caught:
                model.ask_http("sys", "prompt", timeout=1)
        connect.assert_not_called()
        self.assertNotIn("superpass", str(caught.exception))

        truncated = response_socket(200, b'{"choices":[{"message":{"content":"half', {"Content-Length": "500"})
        with mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), http_connections(truncated) as connect:
            with self.assertRaises(RuntimeError):
                model.ask_http("sys", "prompt", timeout=5)
        connect.assert_called_once()

    def test_http_timeout_and_status_errors_use_safe_diagnostics(self):
        for failure in (TimeoutError(), urllib.error.URLError(TimeoutError())):
            with self.subTest(failure=failure), mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), \
                 mock.patch.object(model, "api_key", return_value=None), \
                 mock.patch("urllib.request.OpenerDirector.open", side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, "逾時"):
                    model.ask_http("system", "question")

        for status in (429, 503):
            response = response_socket(
                status, b"fixture-body", {"Retry-After": "fixture-header"}, reason="fixture-detail",
            )
            with self.subTest(status=status), mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), \
                 mock.patch.object(model, "api_key", return_value=None), http_connections(response):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}") as caught:
                    model.ask_http("system", "question")
                self.assertNotIn("fixture-detail", str(caught.exception))
                self.assertNotIn("fixture-body", str(caught.exception))
                self.assertNotIn("fixture-header", str(caught.exception))

    def test_invalid_command_quotes_empty_command_and_invalid_source_raise_runtime_error(self):
        with self.assertRaises(RuntimeError):
            model.ask_command("sys", "prompt", 'unclosed "quote')
        with self.assertRaises(RuntimeError):
            model.ask_command("sys", "prompt", "   ")
        with self.assertRaises(RuntimeError):
            model.ask("sys", "prompt", source="bogus")

    def test_api_key_handles_unreadable_or_directory_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_cfg = pathlib.Path(tmp) / "cfg_dir"
            dir_cfg.mkdir()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SIDECAR_API_KEY", None)
                self.assertIsNone(model.api_key(dir_cfg))


class RenderAndCliHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp_dir.name)
        self.config_patcher = mock.patch.object(model, "CONFIG", self.root / "nonexistent-config.yaml")
        self.prompt_patcher = mock.patch.object(prompts, "PROMPT_DIR", self.root / "no-prompts")
        self.glossary_patcher = mock.patch.object(cli, "GLOSSARY", self.root / "no-glossary.md")
        self.projects_patcher = mock.patch.object(locate, "PROJECTS", self.root / "no-projects")
        self.cache_patcher = mock.patch.object(cache, "PATH", self.root / "isolated-cache.json")
        self.config_patcher.start()
        self.prompt_patcher.start()
        self.glossary_patcher.start()
        self.projects_patcher.start()
        self.cache_patcher.start()
        (self.root / "-work-repo").mkdir(parents=True)
        self.cache = self.root / "cache.json"
        self.path = self.root / "-work-repo" / f"{SID}.jsonl"
        self.path.write_text("\n".join([
            json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-09-26T06:00:00.000Z", "cwd": "/work/repo", "message": {"role": "user", "content": "問題"}}, ensure_ascii=False),
            json.dumps({"type": "assistant", "timestamp": "2026-09-26T06:00:10.000Z", "cwd": "/work/repo", "message": {"role": "assistant", "content": [{"type": "text", "text": "回答"}]}}, ensure_ascii=False),
        ]), encoding="utf-8")

    def tearDown(self):
        self.cache_patcher.stop()
        self.projects_patcher.stop()
        self.glossary_patcher.stop()
        self.prompt_patcher.stop()
        self.config_patcher.stop()
        self.tmp_dir.cleanup()

    def test_render_strips_terminal_ansi_osc_and_control_injections(self):
        malicious = "安全\x1b]52;c;cHduZWQ=\x07文字\x1b[31m紅字\x1b[0m\r覆蓋\x08\x000\x00 `code`"
        out_basic = render.render(malicious, styled=True, prefer_rich=False)
        self.assertNotIn("\x1b]52", out_basic)
        self.assertNotIn("\x1b[31m", out_basic)
        self.assertNotIn("\r", out_basic)
        self.assertNotIn("\x08", out_basic)
        self.assertNotIn("\x00", out_basic)
        self.assertIn("安全", out_basic)
        self.assertIn("code", out_basic)

        out_inline = render.inline("前 \x000\x00 後 `real`")
        self.assertIn(render.CYAN + "real" + render.RESET, out_inline)
        self.assertEqual(1, out_inline.count(render.CYAN + "real" + render.RESET))

    def test_prompts_and_glossary_survive_unreadable_or_directory_paths(self):
        bad_dir = self.root / "prompts"
        bad_dir.mkdir()
        (bad_dir / "plain.md").mkdir()
        with mock.patch.object(prompts, "PROMPT_DIR", bad_dir):
            self.assertEqual(prompts.DEFAULT_PLAIN, prompts.plain())
            self.assertEqual("內建預設", prompts.source_of("plain"))

        bad_glossary = self.root / "glossary_dir"
        bad_glossary.mkdir()
        with mock.patch.object(cli, "GLOSSARY", bad_glossary):
            self.assertIsNone(cli.glossary_block())

    def test_cli_rejects_non_positive_tail_and_survives_readonly_cache_put(self):
        out = io.StringIO()
        with mock.patch.object(cli.locate, "session_path", lambda sid, root=None: self.path if sid == SID else None), \
             mock.patch.object(cli, "GLOSSARY", self.root / "no-glossary.md"), \
             mock.patch.object(cache, "PATH", self.cache), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--session-id", SID, "--tail", "0", "--json"])
        self.assertEqual(1, code)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])

        out2 = io.StringIO()
        reply = {"answer": "成功回答", "source": "http:y", "usage": "bad-usage", "fallback": None}
        with mock.patch.object(cli.locate, "session_path", lambda sid, root=None: self.path if sid == SID else None), \
             mock.patch.object(cli, "GLOSSARY", self.root / "no-glossary.md"), \
             mock.patch.object(cache, "PATH", self.cache), \
             mock.patch.object(cache, "put", side_effect=PermissionError("read-only")), \
             mock.patch.object(cli.model, "ask", return_value=reply), \
             contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
            code2 = cli.main(["1", "--session-id", SID, "--json"])
        self.assertEqual(0, code2)
        body2 = json.loads(out2.getvalue())
        self.assertTrue(body2["ok"])
        self.assertEqual("成功回答", body2["answer"])

    def test_cli_render_list_sanitizes_terminal_escape_sequences(self):
        rendered = cli.render_list([{
            "title": "標題\x1b]0;hacked\x07\x1b[2J",
            "status": "idle",
            "focused": True,
            "cwd": "/work/\x1b[31mrepo",
        }])
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertIn("標題", rendered)

    def test_cli_rejects_invalid_default_source_and_empty_transcript(self):
        out = io.StringIO()
        with mock.patch.object(cli.model, "DEFAULT_SOURCE", "bogus"), \
             mock.patch.object(cli, "GLOSSARY", self.root / "no-glossary.md"), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["1", "--session-id", SID, "--json"])
        self.assertEqual(1, code)
        self.assertFalse(json.loads(out.getvalue())["ok"])

        empty_path = self.root / "-work-repo" / "empty.jsonl"
        empty_path.write_text("{corrupt only\n", encoding="utf-8")
        out2 = io.StringIO()
        with mock.patch.object(cli.locate, "session_path", lambda sid, root=None: empty_path if sid == SID else None), \
             mock.patch.object(cli, "GLOSSARY", self.root / "no-glossary.md"), \
             contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
            code2 = cli.main(["1", "--session-id", SID, "--json"])
        self.assertEqual(1, code2)
        self.assertFalse(json.loads(out2.getvalue())["ok"])


class DeepEdgeCaseHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmp_dir.name)
        self.missing_config = self.tmp / "nonexistent-config.yaml"
        self.config_patcher = mock.patch.object(model, "CONFIG", self.missing_config)
        self.prompt_patcher = mock.patch.object(prompts, "PROMPT_DIR", self.tmp / "no-prompts")
        self.glossary_patcher = mock.patch.object(cli, "GLOSSARY", self.tmp / "no-glossary.md")
        self.projects_patcher = mock.patch.object(locate, "PROJECTS", self.tmp / "no-projects")
        self.cache_patcher = mock.patch.object(cache, "PATH", self.tmp / "isolated-cache.json")
        self.config_patcher.start()
        self.prompt_patcher.start()
        self.glossary_patcher.start()
        self.projects_patcher.start()
        self.cache_patcher.start()

    def tearDown(self):
        self.cache_patcher.stop()
        self.projects_patcher.stop()
        self.glossary_patcher.stop()
        self.prompt_patcher.stop()
        self.config_patcher.stop()
        self.tmp_dir.cleanup()

    def test_folder_for_supports_spaces_and_unicode_and_blocks_intermediate_symlinks(self):
        root = self.tmp / "projects"
        spaced = root / "-work-My Projects-中文專案"
        spaced.mkdir(parents=True)
        self.assertEqual(spaced, locate.folder_for("/work/My Projects/中文專案", root))
        self.assertIsNone(locate.folder_for("/work/bad\x07dir", root))

        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "s1.jsonl").write_text(json.dumps({
            "type": "user", "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00Z", "cwd": "/work/a",
            "message": {"role": "user", "content": "secret"},
        }), encoding="utf-8")
        (root / "linkdir").symlink_to(outside)
        self.assertIsNone(locate.session_path("s1", root))

    def test_first_timestamp_handles_extreme_overflow_years_and_recent_turns_honors_non_positive_tail(self):
        p = self.tmp / "extreme.jsonl"
        p.write_text("\n".join([
            json.dumps({"timestamp": "9999-12-31T23:59:59Z"}),
            json.dumps({"timestamp": "2026-09-14T06:00:00Z"}),
        ]) + "\n", encoding="utf-8")
        self.assertIsNotNone(locate.first_timestamp(p))

        tpath = self.tmp / "turns.jsonl"
        tpath.write_text(json.dumps({
            "type": "user",
            "origin": {"kind": "human"},
            "timestamp": "2026-09-14T06:00:00Z",
            "cwd": "/work/\u0000demo",
            "message": {"role": "user", "content": "hello"},
        }) + "\n", encoding="utf-8")
        self.assertEqual([], parse.recent_turns(tpath, 1, tail=0))
        self.assertEqual([], parse.recent_turns(tpath, 1, tail=-10))
        meta, _, _ = parse.transcript(tpath)
        self.assertNotIn("\x00", meta.get("cwd", ""))

    def test_cache_evicts_infinity_and_nan_timestamps_and_writes_strict_json(self):
        cp = self.tmp / "cache.json"
        cp.write_text(
            '{"inf_entry":{"answer":"forever","label":"old","at":Infinity},'
            '"nan_entry":{"answer":"bad","label":"old","at":NaN},'
            '"ok_old":{"answer":"a","label":"x","at":100}}',
            encoding="utf-8",
        )
        orig = cache.LIMIT
        cache.LIMIT = 2
        try:
            cache.put("fresh", "new", "白話", "http:m", cp)
        finally:
            cache.LIMIT = orig
        raw = cp.read_text(encoding="utf-8")
        self.assertNotIn("Infinity", raw)
        self.assertNotIn("NaN", raw)
        loaded = json.loads(raw)
        self.assertIn("fresh", loaded)
        self.assertNotIn("inf_entry", loaded)

    def test_model_rejects_invalid_proxy_port_invalid_redirect_port_chunked_truncation_and_redacts_command_secrets(self):
        for bad_proxy in ["http://127.0.0.1:99999/v1/chat/completions", "http://127.0.0.1:abc/v1/chat/completions", ""]:
            with mock.patch.object(model, "PROXY", bad_proxy):
                with self.assertRaises(RuntimeError):
                    model.ask_http("sys", "prompt", timeout=1)

        redirect = response_socket(302, headers={"Location": "http://fixture.invalid:99999/other"})
        with mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), http_connections(redirect) as connect:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                model.ask_http("sys", "prompt", timeout=2)
        connect.assert_called_once()

        truncated = response_socket(
            200, b'10\r\n{"choices":[{"me\r\n', {"Transfer-Encoding": "chunked", "Content-Type": "application/json"},
        )
        with mock.patch.object(model, "PROXY", HTTP_FIXTURE_URL), http_connections(truncated) as connect:
            with self.assertRaises(RuntimeError) as caught:
                model.ask_http("sys", "prompt", timeout=2)
        self.assertIsInstance(caught.exception.__cause__, http.client.IncompleteRead)
        connect.assert_called_once()

        cmd = f'{sys.executable} -c "import sys; sys.stderr.write(\'Bearer secretCmdToken777 sk-secretApiKey1234567890\'); sys.exit(1)"'
        with mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "secretEnvKey888"}):
            with self.assertRaises(RuntimeError) as caught:
                model.ask_command("sys", "prompt", cmd)
        self.assertNotIn("secretCmdToken777", str(caught.exception))
        self.assertNotIn("sk-secretApiKey1234567890", str(caught.exception))

    def test_sanitize_terminal_strips_c1_osc_charset_escapes_emit_stderr_cache_stats_and_no_color_output(self):
        self.assertEqual("oktail", render.sanitize_terminal("ok\x9d52;c;cHduZWQ=\x9ctail"))
        self.assertEqual("oktail", render.sanitize_terminal("ok\x1b(Btail"))

        err_buf = io.StringIO()
        opts = cli.build_parser().parse_args([])
        with contextlib.redirect_stderr(err_buf):
            cli.emit(opts, {"ok": False, "error": "bad"}, error="err \x1b]52;c;cHduZWQ=\x07 msg")
        self.assertNotIn("\x1b", err_buf.getvalue())
        self.assertNotIn("52;c;", err_buf.getvalue())

        cp = self.tmp / "poison-stats.json"
        cp.write_text(json.dumps({
            "k1": {"answer": "ok", "label": "lbl\x1b]52;c;bad\x07", "at": 1.0},
        }), encoding="utf-8")
        stats_out = io.StringIO()
        with mock.patch.object(cache, "PATH", cp), contextlib.redirect_stdout(stats_out):
            code = cli.main(["--cache-stats"])
        self.assertEqual(0, code)
        self.assertNotIn("\x1b", stats_out.getvalue())
        self.assertNotIn("52;c;", stats_out.getvalue())

        sess_dir = self.tmp / "-work-repo"
        sess_dir.mkdir(parents=True)
        sess_file = sess_dir / f"{SID}.jsonl"
        sess_file.write_text("\n".join([
            json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-09-26T06:00:00Z", "cwd": "/work/repo", "message": {"role": "user", "content": "q"}}),
            json.dumps({"type": "assistant", "timestamp": "2026-09-26T06:00:10Z", "cwd": "/work/repo", "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]}}),
        ]), encoding="utf-8")
        out_no_color = io.StringIO()
        reply = {"answer": "safe\x1b]52;c;cHduZWQ=\x07text", "source": "http:m", "usage": {}, "fallback": None}
        with mock.patch.object(cli.locate, "session_path", lambda sid, root=None: sess_file if sid == SID else None), \
             mock.patch.object(cli, "GLOSSARY", self.tmp / "none.md"), \
             mock.patch.object(cache, "PATH", self.tmp / "c2.json"), \
             mock.patch.object(cli.model, "ask", return_value=reply), \
             mock.patch.dict(os.environ, {"NO_COLOR": "1"}), \
             contextlib.redirect_stdout(out_no_color), contextlib.redirect_stderr(io.StringIO()):
            code2 = cli.main(["1", "--session-id", SID])
        self.assertEqual(0, code2)
        self.assertNotIn("\x1b", out_no_color.getvalue())
        self.assertIn("safetext", out_no_color.getvalue())

    def test_ask_command_never_reads_config_and_still_redacts_secrets(self):
        cfg_file = self.tmp / "dynamic-config.yaml"
        cfg_file.write_text('api-keys:\n  - "cfg-secret-key-123"\n', encoding="utf-8")
        clean_env = dict(os.environ)
        clean_env.pop("SIDECAR_API_KEY", None)
        with mock.patch.dict(os.environ, clean_env, clear=True), \
             mock.patch.object(model, "CONFIG", cfg_file):
            self.assertEqual("cfg-secret-key-123", model.api_key())

        class ForbiddenConfig:
            def is_file(self):
                raise AssertionError("ask_command must not read model.CONFIG")

            def read_text(self, *args, **kwargs):
                raise AssertionError("ask_command must not read model.CONFIG")

        with mock.patch.dict(os.environ, clean_env, clear=True), \
             mock.patch.object(model, "CONFIG", ForbiddenConfig()):
            ok_cmd = f'{sys.executable} -c "print(\'isolated-ok\')"'
            answer, usage = model.ask_command("sys", "payload", ok_cmd)
            self.assertEqual("isolated-ok", answer)
            self.assertEqual({}, usage)

            fail_cmd = (
                f'{sys.executable} -c "import sys; '
                "sys.stderr.write('Bearer tokSecret111 https://user:pwSecret222@example.test/api sk-keySecret333444555'); "
                'sys.exit(1)"'
            )
            with self.assertRaises(RuntimeError) as caught:
                model.ask_command("sys", "payload", fail_cmd)
            msg = str(caught.exception)
            self.assertNotIn("tokSecret111", msg)
            self.assertNotIn("pwSecret222", msg)
            self.assertNotIn("sk-keySecret333444555", msg)

        env_with_key = dict(clean_env)
        env_with_key["SIDECAR_API_KEY"] = "envOnlySecret777"
        with mock.patch.dict(os.environ, env_with_key, clear=True), \
             mock.patch.object(model, "CONFIG", ForbiddenConfig()):
            env_fail_cmd = f'{sys.executable} -c "import sys; sys.stderr.write(\'leaked envOnlySecret777\'); sys.exit(1)"'
            with self.assertRaises(RuntimeError) as caught_env:
                model.ask_command("sys", "payload", env_fail_cmd)
            self.assertNotIn("envOnlySecret777", str(caught_env.exception))

    def test_locate_subprocess_helpers_survive_non_utf8_bytes_and_unicode_errors(self):
        real_run = subprocess.run

        def run_raw_bytes(raw_bytes):
            def _fake(_argv, *args, **kwargs):
                script = f"import sys; sys.stdout.buffer.write({raw_bytes!r})"
                return real_run([sys.executable, "-c", script], *args, **kwargs)
            return _fake

        herdr_raw = b'{"result":{"agents":[{"agent":"claude","terminal_title_stripped":"bad\xff\xfetitle"}]}}'
        with mock.patch("subprocess.run", side_effect=run_raw_bytes(herdr_raw)):
            agents = locate.herdr_agents()
            self.assertEqual(1, len(agents))
            self.assertEqual("claude", agents[0]["agent"])

        with mock.patch("subprocess.run", side_effect=run_raw_bytes(b"4321 /usr/bin/claude \xff\xfe\n")):
            self.assertEqual(["4321"], locate.claude_processes())

        with mock.patch.object(locate, "claude_processes", return_value=["4321"]), \
             mock.patch("subprocess.run", side_effect=run_raw_bytes(b"p4321\nn/work/dir_\xff\xfe\n")):
            cwds = locate.live_cwds()
            self.assertEqual({"/work/dir_\ufffd\ufffd"}, cwds)

        rec_raw = b"4321 Mon Sep 14 06:00:00 2026 /usr/bin/claude \xff\xfe\n"
        with mock.patch("subprocess.run", side_effect=run_raw_bytes(rec_raw)):
            recs = locate.claude_records()
            self.assertEqual(1, len(recs))
            self.assertEqual(4321, recs[0]["pid"])

        env_raw = b"/usr/bin/claude HERDR_TAB_ID=tab-1 BAD=\xff\xfe\n"
        with mock.patch("subprocess.run", side_effect=run_raw_bytes(env_raw)):
            env_map = locate.process_env(4321)
            self.assertEqual("tab-1", env_map.get("HERDR_TAB_ID"))

        with mock.patch("subprocess.run", side_effect=run_raw_bytes(b"p4321\nn/work/dir_\xff\xfe\n")):
            self.assertEqual("/work/dir_\ufffd\ufffd", locate.process_cwd(4321))

        decode_err = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with mock.patch("subprocess.run", side_effect=decode_err):
            self.assertEqual([], locate.herdr_agents())
            self.assertEqual([], locate.claude_processes())
            self.assertEqual([], locate.claude_records())
            self.assertEqual({}, locate.process_env(4321))
            self.assertEqual("", locate.process_cwd(4321))
        with mock.patch.object(locate, "claude_processes", return_value=["4321"]), \
             mock.patch("subprocess.run", side_effect=decode_err):
            self.assertIsNone(locate.live_cwds())

    def test_ask_command_nul_ask_http_header_injection_and_command_label_redaction(self):
        with self.assertRaises(RuntimeError):
            model.ask_command("sys", "payload", "echo hello\x00world")

        with mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "badKeySecret\r\nX-Injected: 1"}), \
             mock.patch.object(model, "PROXY", "http://127.0.0.1:1/v1/chat/completions"):
            with self.assertRaises(RuntimeError) as caught_hdr:
                model.ask_http("sys", "payload", timeout=1)
        self.assertNotIn("badKeySecret", str(caught_hdr.exception))

        for bad_proxy in ["http://127.0.0.1:1/v1\r\nEvil: 1", "http://127.0.0.1:1/v1\x00x"]:
            with mock.patch.object(model, "PROXY", bad_proxy), \
                 mock.patch.dict(os.environ, {"SIDECAR_API_KEY": "cleanKey"}):
                with self.assertRaises(RuntimeError):
                    model.ask_http("sys", "payload", timeout=1)

        secret_cmd = (
            f'{sys.executable} -c "print(\'ok-redacted\')" '
            "--auth 'Bearer tokSecretLabel111' "
            "--url https://u:pwLabel222@example.test/api "
            "--key sk-labelSecretKey1234567890 "
            "--cookie 'Cookie: a=1; sid=cookieLabel333'"
        )
        with mock.patch.dict(os.environ, {"SIDECAR_CMD": secret_cmd, "SIDECAR_API_KEY": "envLabelSecret444"}):
            candidates = model.candidate_models("cmd")
            self.assertEqual(1, len(candidates))
            for leaked in ("tokSecretLabel111", "pwLabel222", "sk-labelSecretKey1234567890", "cookieLabel333"):
                self.assertNotIn(leaked, candidates[0])
            res = model.ask("sys", "payload", source="cmd")
            self.assertEqual("ok-redacted", res["answer"])
            for leaked in ("tokSecretLabel111", "pwLabel222", "sk-labelSecretKey1234567890", "cookieLabel333"):
                self.assertNotIn(leaked, res["source"])
                self.assertNotIn(leaked, res["request_model"])

    def test_locate_rejects_bad_pids_sanitizes_herdr_types_and_resolves_patched_projects(self):
        proj_root = self.tmp / "dyn_projects"
        proj_dir = proj_root / "-work-demo"
        proj_dir.mkdir(parents=True)
        sess_file = proj_dir / f"{SID}.jsonl"
        sess_file.write_text("\n".join([
            json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-09-26T06:00:00Z", "cwd": "/work/demo", "message": {"role": "user", "content": "q"}}),
            json.dumps({"type": "assistant", "timestamp": "2026-09-26T06:00:10Z", "cwd": "/work/demo", "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]}}),
        ]), encoding="utf-8")

        with mock.patch.object(locate, "PROJECTS", proj_root):
            self.assertEqual(sess_file, locate.session_path(SID))
            row, err = cli.row_for_id(SID)
            self.assertIsNone(err)
            self.assertEqual(sess_file, row["path"])

        bad_herdr = json.dumps({
            "result": {
                "agents": [{
                    "agent": "claude",
                    "agent_session": {"value": SID},
                    "agent_status": {"nested": "dict"},
                    "terminal_title_stripped": ["not", "str"],
                    "cwd": 12345,
                }],
            },
        })
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["herdr"], 0, stdout=bad_herdr, stderr="")):
            rows = locate.from_herdr(proj_root)
        self.assertEqual(1, len(rows))
        self.assertEqual("unknown", rows[0]["status"])
        self.assertEqual("(無標題)", rows[0]["title"])
        self.assertEqual("", rows[0]["cwd"])
        rendered = cli.render_list(rows)
        self.assertIn("(無標題)", rendered)
        self.assertIn("(unknown)", rendered)
        rendered_direct = cli.render_list([{"title": "t", "status": {"bad": 1}, "focused": False, "cwd": "/w"}])
        self.assertIn("(unknown)", rendered_direct)

        ps_bad = "123\x004 /usr/bin/claude\n-flag /usr/bin/claude\n0 /usr/bin/claude\n4321 /usr/bin/claude\n"
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["ps"], 0, stdout=ps_bad, stderr="")):
            self.assertEqual(["4321"], locate.claude_processes())

        with mock.patch.object(locate, "claude_processes", return_value=["123\x004", "-x"]), \
             mock.patch("subprocess.run", side_effect=AssertionError("must not invoke lsof for invalid pids")):
            self.assertIsNone(locate.live_cwds())

        with mock.patch("subprocess.run", side_effect=AssertionError("must not invoke subprocess for invalid pid")):
            self.assertEqual({}, locate.process_env("123\x004"))
            self.assertEqual({}, locate.process_env("-flag"))
            self.assertEqual("", locate.process_cwd("123\x004"))
            self.assertEqual("", locate.process_cwd("-flag"))

        with mock.patch.object(locate, "claude_processes", return_value=["4321"]), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["lsof"], 0, stdout="p4321\nn/work/bad\x00dir\nn/work/good\n", stderr="")):
            self.assertEqual({"/work/good"}, locate.live_cwds())
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["lsof"], 0, stdout="p4321\nn/work/bad\x00dir\nn/work/good\n", stderr="")):
            self.assertEqual("/work/good", locate.process_cwd(4321))


if __name__ == "__main__":
    unittest.main()
