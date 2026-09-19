import hashlib
import json
import os
import pathlib
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


def load(path=None):
    target = path or PATH
    if not target.exists():
        return {}
    try:
        entries = json.loads(target.read_text())
    except ValueError:
        return {}
    return entries if isinstance(entries, dict) else {}


def get(key, path=None):
    entry = load(path).get(key)
    if not isinstance(entry, dict) or not entry.get("answer"):
        return None
    return entry


def put(key, answer, label, source="?", path=None):
    target = path or PATH
    entries = load(target)
    entries[key] = {"answer": answer, "label": label, "source": source, "at": time.time()}
    if len(entries) > LIMIT:
        ordered = sorted(entries.items(), key=lambda pair: pair[1].get("at", 0))
        entries = dict(ordered[-LIMIT:])
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(entries, ensure_ascii=False))
    os.chmod(target, 0o600)
    return entries


def stats(path=None):
    entries = load(path)
    labels = {}
    for entry in entries.values():
        label = entry.get("label", "?")
        labels[label] = labels.get(label, 0) + 1
    return len(entries), labels
