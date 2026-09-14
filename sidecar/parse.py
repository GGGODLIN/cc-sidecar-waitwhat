import json
import re

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_TAG = re.compile(r"<command-(name|message|args)>.*?</command-\1>", re.S)


def iter_records(path, tail=None):
    size = path.stat().st_size
    if tail is None or size <= tail:
        lines = path.read_text(errors="ignore").splitlines()
    else:
        with path.open("rb") as handle:
            handle.seek(size - tail)
            lines = handle.read().decode("utf-8", "ignore").splitlines()[1:]
    for line in lines:
        try:
            yield json.loads(line)
        except ValueError:
            continue


def clean(text):
    return COMMAND_TAG.sub("", REMINDER.sub("", text)).strip()


def text_of(message):
    content = (message or {}).get("content")
    if isinstance(content, str):
        return clean(content)
    if not isinstance(content, list):
        return ""
    parts = [block.get("text", "") for block in content
             if isinstance(block, dict) and block.get("type") == "text"]
    return clean("\n".join(part for part in parts if part))


def is_human(record):
    return (record.get("type") == "user"
            and (record.get("origin") or {}).get("kind") == "human")


def tool_call(record):
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)
            return f"{block.get('name')}: {arguments[:160]}"
    return None


def transcript(path, tail=None):
    meta = {}
    turns = []
    tools = []
    for record in iter_records(path, tail):
        if record.get("isSidechain"):
            continue
        if "cwd" not in meta and record.get("cwd"):
            meta["cwd"] = record["cwd"]
        if "started" not in meta and record.get("timestamp"):
            meta["started"] = record["timestamp"]
        if is_human(record):
            text = text_of(record.get("message"))
            if text:
                turns.append(("human", record.get("timestamp"), text))
        elif record.get("type") == "assistant":
            text = text_of(record.get("message"))
            if text:
                turns.append(("assistant", record.get("timestamp"), text))
            called = tool_call(record)
            if called:
                tools.append(called)
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
    _, turns, _ = transcript(path, tail)
    groups = split_turns(turns)
    if len(groups) < count and path.stat().st_size > tail:
        _, turns, _ = transcript(path, None)
        groups = split_turns(turns)
    return groups[-count:]
