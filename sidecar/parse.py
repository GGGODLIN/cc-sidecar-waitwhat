import json
import re

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_TAG = re.compile(r"<command-(name|message|args)>.*?</command-\1>", re.S)


def iter_records(path, tail=None):
    if tail is not None and tail <= 0:
        return
    try:
        size = path.stat().st_size
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        if tail is not None and size > tail:
            handle.seek(size - tail - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        for raw_line in handle:
            line = raw_line.decode("utf-8", "ignore").replace("\x00", "").strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def clean(text):
    if not isinstance(text, str):
        return ""
    cleaned = text.replace("\x00", "")
    return COMMAND_TAG.sub("", REMINDER.sub("", cleaned)).strip()


def text_of(message):
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return clean(content)
    if not isinstance(content, list):
        return ""
    parts = [block.get("text") for block in content
             if isinstance(block, dict) and block.get("type") == "text"
             and isinstance(block.get("text"), str)]
    return clean("\n".join(part for part in parts if part))


def is_human(record):
    if not isinstance(record, dict):
        return False
    origin = record.get("origin")
    return record.get("type") == "user" and isinstance(origin, dict) and origin.get("kind") == "human"


def tool_calls(record):
    if not isinstance(record, dict):
        return []
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    found = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            raw_input = block.get("input")
            try:
                arguments = json.dumps(raw_input if raw_input is not None else {}, ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = "{}"
            name = block.get("name")
            label = name.replace("\x00", "") if isinstance(name, str) and name else "unknown"
            found.append(f"{label}: {arguments.replace(chr(0), '')[:160]}")
    return found


def tool_call(record):
    calls = tool_calls(record)
    return calls[0] if calls else None


def transcript(path, tail=None):
    meta = {}
    turns = []
    tools = []
    for record in iter_records(path, tail):
        if record.get("isSidechain"):
            continue
        cwd = record.get("cwd")
        if "cwd" not in meta and isinstance(cwd, str):
            clean_cwd = cwd.replace("\x00", "")
            if clean_cwd:
                meta["cwd"] = clean_cwd
        raw_stamp = record.get("timestamp")
        stamp = raw_stamp.replace("\x00", "") if isinstance(raw_stamp, str) else ""
        if "started" not in meta and stamp:
            meta["started"] = stamp
        if is_human(record):
            text = text_of(record.get("message"))
            if text:
                turns.append(("human", stamp, text))
        elif record.get("type") == "assistant":
            text = text_of(record.get("message"))
            if text:
                turns.append(("assistant", stamp, text))
            called = tool_calls(record)
            if called:
                tools.extend(called)
    return meta, turns, tools


def last_of(turns, role):
    for entry in reversed(turns):
        if entry[0] == role:
            return entry
    return None


def split_turns(turns):
    groups = []
    current = None
    for role, timestamp, text in turns:
        if role == "human":
            if current is not None:
                groups.append(current)
            current = {"prompt": text, "at": timestamp, "replies": []}
        elif current is not None:
            current["replies"].append(text)
        else:
            current = {"prompt": None, "at": timestamp, "replies": [text]}
    if current is not None:
        groups.append(current)
    return groups


def recent_turns(path, count, tail=3_000_000):
    if count <= 0 or (tail is not None and tail <= 0):
        return []
    _, turns, _ = transcript(path, tail)
    groups = split_turns(turns)
    if tail is not None and len(groups) < count:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > tail:
            _, turns, _ = transcript(path, None)
            groups = split_turns(turns)
    return groups[-count:]
