import json
import pathlib
import re
import subprocess
import time

from . import parse

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
FILE_WINDOW_SECONDS = 7200
CLAUDE_PROCESS = re.compile(r"(^|/)claude(\s|$)")
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


def candidates(root=PROJECTS):
    rows = from_herdr(root)
    return rows if rows else from_files(root)
