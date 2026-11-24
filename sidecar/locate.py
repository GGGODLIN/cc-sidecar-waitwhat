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
CONTAINER_VARS = ("HERDR_TAB_ID",)
PS_ROW = re.compile(r"\s*(\d+)\s+(\w{3} \w{3}\s+\d+ \d\d:\d\d:\d\d \d{4})\s+(.*)")
ARGV_SESSION = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f-]{36})")
SAFE_SESSION_NAME = re.compile(r"[A-Za-z0-9_.-]+")
UNSET = object()


def _is_safe_session_name(session_id):
    return (isinstance(session_id, str)
            and bool(SAFE_SESSION_NAME.fullmatch(session_id))
            and session_id not in (".", "..")
            and ".." not in session_id)


def _is_safe_folder_slug(slug):
    return (isinstance(slug, str)
            and bool(slug)
            and slug not in (".", "..")
            and ".." not in slug
            and "/" not in slug
            and "\\" not in slug
            and "\x00" not in slug
            and all(ord(ch) >= 32 and ord(ch) != 127 for ch in slug))


def _has_symlink_under_root(path, root):
    current = path
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


def _is_safe_child(path, root_resolved, expect_dir=False, root=None):
    try:
        if root is not None:
            if _has_symlink_under_root(path, root):
                return False
        elif path.is_symlink():
            return False
        resolved = path.resolve()
        if not resolved.is_relative_to(root_resolved):
            return False
        return resolved.is_dir() if expect_dir else resolved.is_file()
    except OSError:
        return False


def _safe_mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _first_line(text):
    lines = text.splitlines() if isinstance(text, str) else []
    return lines[0] if lines else ""


def _valid_pid(pid):
    if isinstance(pid, bool):
        return None
    if isinstance(pid, int):
        return str(pid) if pid > 0 else None
    if isinstance(pid, str) and pid.isascii() and pid.isdigit() and int(pid) > 0:
        return pid
    return None


def session_path(session_id, root=None):
    target_root = PROJECTS if root is None else root
    if not _is_safe_session_name(session_id):
        return None
    try:
        if not target_root.exists():
            return None
        root_resolved = target_root.resolve()
    except OSError:
        return None
    hits = []
    try:
        for path in target_root.rglob(f"{session_id}.jsonl"):
            if not _is_safe_child(path, root_resolved, root=target_root):
                continue
            mtime = _safe_mtime(path)
            if mtime is not None:
                hits.append((mtime, path))
    except OSError:
        pass
    hits.sort(key=lambda item: item[0], reverse=True)
    return hits[0][1] if hits else None


def herdr_agents(timeout=5):
    try:
        finished = subprocess.run(
            ["herdr", "agent", "list"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return []
    if finished.returncode != 0 or not finished.stdout:
        return []
    try:
        payload = json.loads(finished.stdout)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    agents = result.get("agents")
    if not isinstance(agents, list):
        return []
    return [agent for agent in agents if isinstance(agent, dict) and agent.get("agent") == "claude"]


def from_herdr(root=None):
    target_root = PROJECTS if root is None else root
    rows = []
    for agent in herdr_agents():
        agent_session = agent.get("agent_session")
        session_id = agent_session.get("value") if isinstance(agent_session, dict) else None
        if not isinstance(session_id, str) or not session_id:
            continue
        path = session_path(session_id, target_root)
        if path is None:
            continue
        raw_title = agent.get("terminal_title_stripped")
        raw_status = agent.get("agent_status")
        raw_cwd = agent.get("cwd")
        rows.append({
            "id": session_id,
            "path": path,
            "title": raw_title if isinstance(raw_title, str) and raw_title else "(無標題)",
            "status": raw_status if isinstance(raw_status, str) and raw_status else "unknown",
            "focused": bool(agent.get("focused")),
            "cwd": raw_cwd if isinstance(raw_cwd, str) else "",
            "source": "herdr",
        })
    rows.sort(key=lambda row: (not row["focused"], row["status"] != "idle"))
    return rows


def claude_processes(timeout=5):
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,command="], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return []
    pids = []
    for line in listing.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and CLAUDE_PROCESS.search(parts[1]):
            safe_pid = _valid_pid(parts[0])
            if safe_pid is not None:
                pids.append(safe_pid)
    return pids


def live_cwds(timeout=10):
    pids = [safe_pid for raw_pid in claude_processes() if (safe_pid := _valid_pid(raw_pid)) is not None]
    if not pids:
        return None
    try:
        opened = subprocess.run(
            ["lsof", "-a", "-p", ",".join(pids), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None
    found = {line[1:] for line in opened.splitlines() if line.startswith("n") and "\x00" not in line and len(line) > 1}
    return found or None


def session_summary(path):
    try:
        size = path.stat().st_size
    except OSError:
        return None
    for tail in (400_000, 4_000_000, None):
        meta, turns, _ = parse.transcript(path, tail)
        human = parse.last_of(turns, "human")
        if human:
            reply = parse.last_of(turns, "assistant")
            return {
                "at": human[1] if isinstance(human[1], str) else "",
                "said": _first_line(human[2]),
                "reply": _first_line(reply[2]) if reply else "",
                "cwd": meta.get("cwd") or path.parent.name,
            }
        if tail is None or size <= tail:
            break
    return None


def from_files(root=None, window=FILE_WINDOW_SECONDS, alive=UNSET):
    target_root = PROJECTS if root is None else root
    if alive is UNSET:
        alive = live_cwds()
    try:
        if not target_root.exists():
            return []
        root_resolved = target_root.resolve()
    except OSError:
        return []
    now = time.time()
    rows = []
    try:
        for path in target_root.rglob("*.jsonl"):
            if not _is_safe_child(path, root_resolved, root=target_root):
                continue
            mtime = _safe_mtime(path)
            if mtime is None or now - mtime > window:
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
    except OSError:
        pass
    rows.sort(key=lambda row: row["at"] if isinstance(row.get("at"), str) else "", reverse=True)
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
        listing = subprocess.run(
            ["ps", "-eo", "pid=,lstart=,command="], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
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
    safe_pid = _valid_pid(pid)
    if safe_pid is None:
        return {}
    try:
        listing = subprocess.run(
            ["ps", "-E", "-p", safe_pid, "-o", "command="],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return {}
    found = {}
    for token in listing.split():
        name, sep, value = token.partition("=")
        if sep and name.isupper():
            found[name] = value
    return found


def process_cwd(pid, timeout=10):
    safe_pid = _valid_pid(pid)
    if safe_pid is None:
        return ""
    try:
        opened = subprocess.run(
            ["lsof", "-a", "-p", safe_pid, "-d", "cwd", "-Fn"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return ""
    for line in opened.splitlines():
        if line.startswith("n") and "\x00" not in line and len(line) > 1:
            return line[1:]
    return ""


def first_timestamp(path):
    try:
        handle = path.open("rb")
    except OSError:
        return None
    with handle:
        for raw_line in handle:
            line = raw_line.decode("utf-8", "ignore").replace("\x00", "").strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            stamp = record.get("timestamp")
            if isinstance(stamp, str) and stamp:
                try:
                    moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    return moment.astimezone().replace(tzinfo=None)
                except (ValueError, OverflowError, OSError):
                    continue
    return None


def folder_for(cwd, root=None):
    target_root = PROJECTS if root is None else root
    if not isinstance(cwd, str) or not cwd or "\x00" in cwd:
        return None
    try:
        if not target_root.exists():
            return None
        root_resolved = target_root.resolve()
    except OSError:
        return None
    slug = "-" + cwd.strip("/").replace("/", "-").replace(".", "-")
    if not _is_safe_folder_slug(slug):
        return None
    folder = target_root / slug
    return folder if _is_safe_child(folder, root_resolved, expect_dir=True, root=target_root) else None


def session_for(record, root=None, cwd_of=process_cwd, window=START_WINDOW_SECONDS):
    target_root = PROJECTS if root is None else root
    named = ARGV_SESSION.search(record["command"])
    if named:
        return named.group(1)
    folder = folder_for(cwd_of(record["pid"]), target_root)
    if folder is None:
        return None
    try:
        root_resolved = target_root.resolve()
    except OSError:
        return None
    near = []
    try:
        for path in folder.glob("*.jsonl"):
            if not _is_safe_child(path, root_resolved, root=target_root):
                continue
            stamp = first_timestamp(path)
            if stamp is None:
                continue
            gap = abs((stamp - record["started"]).total_seconds())
            if gap <= window:
                near.append((gap, path.stem))
    except OSError:
        return None
    if len(near) != 1:
        return None
    return near[0][1]


def pane_records(var, value, records=None, env_of=process_env):
    records = claude_records() if records is None else records
    return [record for record in records if env_of(record["pid"]).get(var) == value]


def from_pane(root=None, environ=None, records=None, env_of=process_env,
              cwd_of=process_cwd, window=START_WINDOW_SECONDS):
    target_root = PROJECTS if root is None else root
    var, value = container(environ)
    if var is None:
        return []
    rows = []
    for record in pane_records(var, value, records, env_of):
        session_id = session_for(record, target_root, cwd_of, window)
        if session_id is None:
            continue
        path = session_path(session_id, target_root)
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
    rows.sort(key=lambda row: row["at"] if isinstance(row.get("at"), str) else "", reverse=True)
    return rows


def others(root=None, var=None):
    target_root = PROJECTS if root is None else root
    if var is None:
        return from_herdr(target_root) or from_files(target_root)
    return from_herdr(target_root)


def candidates(root=None, environ=None):
    target_root = PROJECTS if root is None else root
    var, _ = container(environ)
    pane = from_pane(target_root, environ)
    rest = others(target_root, var)
    if not pane:
        return rest
    seen = {row["id"] for row in pane}
    trailing = [dict(row, focused=False) for row in rest if row["id"] not in seen]
    return pane + trailing
