import datetime
import json
import os
import pathlib
import re
import subprocess
import time

from . import parse

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
FILE_WINDOW_SECONDS = 7200
START_WINDOW_SECONDS = 5.0
CLAUDE_PROCESS = re.compile(r"(^|/)claude(\s|$)")
CONTAINER_VARS = ("ORCA_TAB_ID", "HERDR_TAB_ID")
PS_ROW = re.compile(r"\s*(\d+)\s+(\w{3} \w{3}\s+\d+ \d\d:\d\d:\d\d \d{4})\s+(.*)")
ARGV_SESSION = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f-]{36})")
UNSET = object()


def session_path(session_id, root=PROJECTS):
    hits = sorted(root.rglob(f"{session_id}.jsonl"),
                  key=lambda path: path.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def herdr_agents(timeout=5):
    try:
        finished = subprocess.run(["herdr", "agent", "list"],
                                  capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if finished.returncode != 0:
        return []
    try:
        payload = json.loads(finished.stdout)
    except ValueError:
        return []
    agents = (payload.get("result") or {}).get("agents") or []
    return [agent for agent in agents if agent.get("agent") == "claude"]


def from_herdr(root=PROJECTS):
    rows = []
    for agent in herdr_agents():
        session_id = (agent.get("agent_session") or {}).get("value")
        if not session_id:
            continue
        path = session_path(session_id, root)
        if path is None:
            continue
        rows.append({
            "id": session_id,
            "path": path,
            "title": agent.get("terminal_title_stripped") or "(無標題)",
            "status": agent.get("agent_status") or "unknown",
            "focused": bool(agent.get("focused")),
            "cwd": agent.get("cwd") or "",
            "source": "herdr",
        })
    rows.sort(key=lambda row: (not row["focused"], row["status"] != "idle"))
    return rows


def claude_processes(timeout=5):
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True,
                                 text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split(None, 1)[0] for line in listing.splitlines()
            if CLAUDE_PROCESS.search(line.split(None, 1)[-1])]


def live_cwds(timeout=10):
    pids = claude_processes()
    if not pids:
        return None
    try:
        opened = subprocess.run(["lsof", "-a", "-p", ",".join(pids), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = {line[1:] for line in opened.splitlines() if line.startswith("n")}
    return found or None


def session_summary(path):
    for tail in (400_000, 4_000_000, None):
        meta, turns, _ = parse.transcript(path, tail)
        human = parse.last_of(turns, "human")
        if human:
            reply = parse.last_of(turns, "assistant")
            return {
                "at": human[1],
                "said": human[2].splitlines()[0],
                "reply": reply[2].splitlines()[0] if reply else "",
                "cwd": meta.get("cwd") or path.parent.name,
            }
        if tail is None or path.stat().st_size <= tail:
            break
    return None


def from_files(root=PROJECTS, window=FILE_WINDOW_SECONDS, alive=UNSET):
    if alive is UNSET:
        alive = live_cwds()
    now = time.time()
    rows = []
    for path in root.rglob("*.jsonl"):
        if now - path.stat().st_mtime > window:
            continue
        summary = session_summary(path)
        if not summary:
            continue
        if alive is not None and summary["cwd"] not in alive:
            continue
        rows.append({
            "id": path.stem,
            "path": path,
            "title": (summary["reply"] or summary["said"])[:52],
            "status": "unknown",
            "focused": False,
            "cwd": summary["cwd"],
            "source": "files",
            "at": summary["at"],
        })
    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows


def container(environ=None):
    environ = os.environ if environ is None else environ
    for name in CONTAINER_VARS:
        value = environ.get(name)
        if value:
            return name, value
    return None, None


def claude_records(timeout=5):
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,lstart=,command="], capture_output=True,
                                 text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    records = []
    for line in listing.splitlines():
        matched = PS_ROW.match(line)
        if not matched or not CLAUDE_PROCESS.search(matched.group(3)):
            continue
        try:
            started = datetime.datetime.strptime(matched.group(2), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            continue
        records.append({"pid": int(matched.group(1)), "started": started,
                        "command": matched.group(3)})
    return records


def process_env(pid, timeout=5):
    try:
        listing = subprocess.run(["ps", "-E", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for token in listing.split():
        name, sep, value = token.partition("=")
        if sep and name.isupper():
            found[name] = value
    return found


def process_cwd(pid, timeout=10):
    try:
        opened = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in opened.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def first_timestamp(path):
    try:
        handle = path.open()
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                stamp = json.loads(line).get("timestamp")
            except ValueError:
                continue
            if stamp:
                moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                return moment.astimezone().replace(tzinfo=None)
    return None


def folder_for(cwd, root=PROJECTS):
    if not cwd:
        return None
    slug = "-" + cwd.strip("/").replace("/", "-").replace(".", "-")
    folder = root / slug
    return folder if folder.is_dir() else None


def session_for(record, root=PROJECTS, cwd_of=process_cwd, window=START_WINDOW_SECONDS):
    named = ARGV_SESSION.search(record["command"])
    if named:
        return named.group(1)
    folder = folder_for(cwd_of(record["pid"]), root)
    if folder is None:
        return None
    near = []
    for path in folder.glob("*.jsonl"):
        stamp = first_timestamp(path)
        if stamp is None:
            continue
        gap = abs((stamp - record["started"]).total_seconds())
        if gap <= window:
            near.append((gap, path.stem))
    if len(near) != 1:
        return None
    return near[0][1]


def pane_records(var, value, records=None, env_of=process_env):
    records = claude_records() if records is None else records
    return [record for record in records if env_of(record["pid"]).get(var) == value]


def from_pane(root=PROJECTS, environ=None, records=None, env_of=process_env,
              cwd_of=process_cwd, window=START_WINDOW_SECONDS):
    var, value = container(environ)
    if var is None:
        return []
    rows = []
    for record in pane_records(var, value, records, env_of):
        session_id = session_for(record, root, cwd_of, window)
        if session_id is None:
            continue
        path = session_path(session_id, root)
        if path is None:
            continue
        summary = session_summary(path)
        if not summary:
            continue
        rows.append({
            "id": session_id,
            "path": path,
            "title": (summary["reply"] or summary["said"])[:52],
            "status": "unknown",
            "focused": True,
            "cwd": summary["cwd"],
            "source": "pane",
            "at": summary["at"],
        })
    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows


def others(root=PROJECTS, var=None):
    if var == "HERDR_TAB_ID":
        return from_herdr(root)
    if var is None:
        return from_herdr(root) or from_files(root)
    return from_files(root)


def candidates(root=PROJECTS, environ=None):
    var, _ = container(environ)
    pane = from_pane(root, environ)
    rest = others(root, var)
    if not pane:
        return rest
    seen = {row["id"] for row in pane}
    trailing = [dict(row, focused=False) for row in rest if row["id"] not in seen]
    return pane + trailing
