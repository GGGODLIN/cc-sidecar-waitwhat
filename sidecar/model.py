import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request

PROXY = os.environ.get("SIDECAR_PROXY", "http://127.0.0.1:8317/v1/chat/completions")
MODEL = os.environ.get("SIDECAR_MODEL", "gemini-3.8-flash-high")
CONFIG = pathlib.Path.home() / ".cli-proxy-api" / "config.yaml"

KEY_BLOCK = re.compile(r"^api-keys:\s*$")
KEY_ENTRY = re.compile(r"^\s*-\s*\"?([^\"\s]+)\"?\s*$")

SOURCES = ("auto", "cmd", "http")
DEFAULT_SOURCE = os.environ.get("SIDECAR_SOURCE", "auto")


def command_line():
    configured = os.environ.get("SIDECAR_CMD")
    if configured:
        return configured
    if not shutil.which("claude"):
        return None
    return "claude -p --no-session-persistence"


def api_key(config=CONFIG):
    if os.environ.get("SIDECAR_API_KEY"):
        return os.environ["SIDECAR_API_KEY"]
    if not config.exists():
        return None
    keys = []
    inside = False
    for line in config.read_text().splitlines():
        if KEY_BLOCK.match(line):
            inside = True
            continue
        if not inside:
            continue
        entry = KEY_ENTRY.match(line)
        if entry:
            keys.append(entry.group(1))
        elif line.strip() and not line.startswith(" "):
            break
    return keys[0] if keys else None


def ask_command(system, payload, command, timeout=300):
    try:
        finished = subprocess.run(
            shlex.split(command), input=system + "\n\n---\n\n" + payload,
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到指令：{command}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{command} 超過 {timeout} 秒沒有回應") from error
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip()[:200]
        raise RuntimeError(f"{command} 失敗（exit {finished.returncode}）：{detail}")
    answer = finished.stdout.strip()
    if not answer:
        raise RuntimeError(f"{command} 沒有輸出任何內容")
    return answer, {}


def ask_http(system, payload, model=MODEL, timeout=300):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ],
        "temperature": 0.3,
    }).encode()
    headers = {"Content-Type": "application/json"}
    key = api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(PROXY, data=body, headers=headers)
    try:
        raw = urllib.request.urlopen(request, timeout=timeout).read()
    except urllib.error.URLError as error:
        raise RuntimeError(f"呼叫 {PROXY} 失敗：{error}") from error
    answer = json.loads(raw)
    choices = answer.get("choices") or []
    if not choices:
        raise RuntimeError(f"模型沒有回傳內容：{json.dumps(answer)[:300]}")
    return choices[0]["message"]["content"], answer.get("usage") or {}


def candidate_models(source, http_model=MODEL):
    command = command_line()
    options = []
    if source in ("auto", "cmd") and command:
        options.append(f"cmd:{command}")
    if source in ("auto", "http"):
        options.append(http_model)
    return options


def ask(system, payload, source=DEFAULT_SOURCE, http_model=MODEL, timeout=300):
    reasons = []
    command = command_line()
    if source in ("auto", "cmd"):
        if not command:
            reasons.append("沒有設 SIDECAR_CMD，PATH 裡也沒有 claude")
            if source == "cmd":
                raise RuntimeError(reasons[-1])
        else:
            try:
                answer, usage = ask_command(system, payload, command, timeout)
                return {"answer": answer, "source": f"cmd:{command}",
                        "request_model": f"cmd:{command}", "usage": usage,
                        "fallback": reasons[0] if reasons else None}
            except RuntimeError as error:
                reasons.append(str(error))
                if source == "cmd":
                    raise

    answer, usage = ask_http(system, payload, http_model, timeout)
    return {"answer": answer, "source": f"http:{http_model}",
            "request_model": http_model, "usage": usage,
            "fallback": reasons[0] if reasons else None}
