import fcntl
import hashlib
import json
import math
import os
import pathlib
import tempfile
import time

PATH = pathlib.Path(os.environ.get(
    "SIDECAR_CACHE", pathlib.Path.home() / ".cache" / "cc-sidecar-waitwhat.json"))
LIMIT = 200


def shared_key_for(mode, messages):
    normalized = []
    for role, text in messages:
        content = text.replace("\r\n", "\n").replace("\r", "\n").strip(" \t\n\r")
        if content:
            normalized.append([role, content])
    payload = json.dumps([mode, normalized], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _strip_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    if isinstance(value, dict):
        return {k: _strip_nonfinite(v) for k, v in value.items() if isinstance(k, str)}
    if isinstance(value, list):
        return [_strip_nonfinite(item) for item in value]
    return value


def _sanitize_entries(raw):
    if not isinstance(raw, dict):
        return {}
    return {
        key: _strip_nonfinite(entry) for key, entry in raw.items()
        if isinstance(entry, dict)
        and isinstance(entry.get("answer"), str) and entry["answer"]
        and isinstance(entry.get("label", "?"), str)
        and isinstance(entry.get("source", "?"), str)
    }


def _entry_timestamp(entry):
    if not isinstance(entry, dict):
        return 0
    value = entry.get("at", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    return value


def load(path=None):
    target = pathlib.Path(path) if path is not None else PATH
    try:
        if not target.is_file():
            return {}
        entries = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _sanitize_entries(entries)


def get(key, path=None):
    entry = load(path).get(key)
    if not isinstance(entry, dict):
        return None
    answer = entry.get("answer")
    if not isinstance(answer, str) or not answer:
        return None
    return entry


def put(key, answer, label, source="?", path=None):
    target = pathlib.Path(path) if path is not None else PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # 原子替換會換掉快取檔的 inode，讀改寫需鎖在固定的檔案上。
    lock_path = target.with_name(target.name + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = load(target)
        entries[key] = {"answer": answer, "label": label, "source": source, "at": time.time()}
        if len(entries) > LIMIT:
            ordered = sorted(entries.items(), key=lambda pair: _entry_timestamp(pair[1]))
            entries = dict(ordered[-LIMIT:]) if LIMIT > 0 else {}
        _write_snapshot(target, entries)
    return entries


def _write_snapshot(target, entries):
    serialized = json.dumps(entries, ensure_ascii=False, allow_nan=False)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
        tmp_name = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def stats(path=None):
    entries = load(path)
    labels = {}
    for entry in entries.values():
        raw_label = entry.get("label", "?")
        label = raw_label if isinstance(raw_label, str) and raw_label else "?"
        labels[label] = labels.get(label, 0) + 1
    return len(entries), labels
