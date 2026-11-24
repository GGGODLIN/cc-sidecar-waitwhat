import http.client
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

PROXY = os.environ.get("SIDECAR_PROXY", "http://127.0.0.1:8317/v1/chat/completions")
MODEL = os.environ.get("SIDECAR_MODEL", "gemini-3.8-flash-high")
CONFIG = pathlib.Path.home() / ".cli-proxy-api" / "config.yaml"

KEY_BLOCK = re.compile(r"^api-keys:\s*$")
KEY_ENTRY = re.compile(r"^\s*-\s*\"?([^\"\s]+)\"?\s*$")
BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
HEADER_SECRET_RE = re.compile(r"(?i)((?:authorization|cookie|set-cookie)[ \t]*:[ \t]*)[^\r\n]+")
URL_USERINFO_RE = re.compile(r"(https?://)[^/\s@]+@")
SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
MAX_HTTP_BYTES = 16 * 1024 * 1024

SOURCES = ("auto", "cmd", "http")
DEFAULT_SOURCE = os.environ.get("SIDECAR_SOURCE", "auto")


class SubmissionUncertain(RuntimeError):
    pass


def _sanitize_url(url):
    if not isinstance(url, str):
        return "<invalid-url>"
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "<invalid-url>"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def _redact_text(text, secret=None):
    cleaned = BEARER_RE.sub(r"\1[REDACTED]", str(text))
    cleaned = HEADER_SECRET_RE.sub(r"\1[REDACTED]", cleaned)
    cleaned = URL_USERINFO_RE.sub(r"\1[REDACTED]@", cleaned)
    cleaned = SK_KEY_RE.sub("[REDACTED_KEY]", cleaned)
    if secret:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _safe_command_label(command):
    return _redact_text(command, os.environ.get("SIDECAR_API_KEY"))


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API 金鑰和逐字稿只授權給使用者設定的端點。
        return None


def command_line():
    configured = os.environ.get("SIDECAR_CMD")
    if configured:
        return configured
    if not shutil.which("claude"):
        return None
    return "claude -p --no-session-persistence"


def api_key(config=None):
    if os.environ.get("SIDECAR_API_KEY"):
        return os.environ["SIDECAR_API_KEY"]
    target = CONFIG if config is None else config
    try:
        if not target.is_file():
            return None
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    keys = []
    inside = False
    for line in content.splitlines():
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
    secret = os.environ.get("SIDECAR_API_KEY")
    safe_cmd = _redact_text(command, secret) if isinstance(command, str) else ""
    try:
        argv = shlex.split(command) if isinstance(command, str) else []
    except ValueError as error:
        raise RuntimeError(f"無效的指令格式：{safe_cmd}") from error
    if not argv:
        raise RuntimeError("未指定有效指令")
    if any("\x00" in arg for arg in argv):
        raise RuntimeError(f"無效的指令格式：{safe_cmd}")
    try:
        finished = subprocess.run(
            argv, input=system + "\n\n---\n\n" + payload,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到指令：{safe_cmd}") from error
    except subprocess.TimeoutExpired as error:
        raise SubmissionUncertain(f"送出狀態不明：{safe_cmd} 超過 {timeout} 秒沒有回應，停止回退") from error
    except (OSError, ValueError) as error:
        raise RuntimeError(f"無法執行指令：{safe_cmd}（{_redact_text(error, secret)}）") from error
    if finished.returncode < 0:
        raise SubmissionUncertain("送出狀態不明：程序被訊號終止，停止回退")
    if finished.returncode == 3 and any(line.startswith("SIDECAR_WEB_RETRY_UNSAFE ") for line in finished.stderr.splitlines()):
        raise SubmissionUncertain("送出狀態不明，停止回退以免重複請求")
    if finished.returncode != 0:
        detail = _redact_text((finished.stderr or finished.stdout or "").strip()[:200], secret)
        raise RuntimeError(f"{safe_cmd} 失敗（exit {finished.returncode}）：{detail}")
    answer = finished.stdout.strip()
    if not answer:
        raise RuntimeError(f"{safe_cmd} 沒有輸出任何內容")
    return answer, {}


def ask_http(system, payload, model=MODEL, timeout=300):
    if (not isinstance(PROXY, str)
            or not PROXY.strip()
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in PROXY)):
        raise RuntimeError("無效的 PROXY 位址：<invalid-url>")
    try:
        parsed_proxy = urllib.parse.urlparse(PROXY)
        proxy_port = parsed_proxy.port
    except ValueError as error:
        raise RuntimeError(f"無效的 PROXY 位址：{_sanitize_url(PROXY)}") from error
    if (parsed_proxy.scheme.lower() not in ("http", "https")
            or not parsed_proxy.hostname
            or parsed_proxy.username is not None
            or (proxy_port is not None and not 1 <= proxy_port <= 65535)):
        raise RuntimeError(f"無效的 PROXY 位址：{_sanitize_url(PROXY)}")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ],
        "temperature": 0.3,
    }).encode()
    headers = {"Content-Type": "application/json"}
    safe_proxy = _sanitize_url(PROXY)
    key = api_key()
    if key:
        if any(ch in key for ch in ("\r", "\n", "\x00")):
            raise RuntimeError(f"呼叫 {safe_proxy} 失敗：API 金鑰格式無效")
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(PROXY, data=body, headers=headers)
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        response = opener.open(request, timeout=timeout)
        try:
            raw = response.read(MAX_HTTP_BYTES + 1)
        finally:
            close_fn = getattr(response, "close", None)
            if callable(close_fn):
                close_fn()
    except urllib.error.HTTPError as error:
        retry_after = ""
        if error.code == 429 and error.headers is not None:
            retry_val = error.headers.get("Retry-After")
            if retry_val and re.fullmatch(
                r"[0-9]+|[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} (?:[0-9]{2}:){2}[0-9]{2} GMT",
                retry_val,
            ):
                retry_after = f" (Retry-After: {_redact_text(retry_val, key)})"
        try:
            error.close()
        except OSError:
            pass
        raise RuntimeError(f"呼叫 {safe_proxy} 失敗：HTTP {error.code}{retry_after}") from None
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
        if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
            raise RuntimeError(f"呼叫 {safe_proxy} 逾時") from error
        detail = _redact_text(error, key)
        raise RuntimeError(f"呼叫 {safe_proxy} 失敗：{detail}") from error
    if len(raw) > MAX_HTTP_BYTES:
        raise RuntimeError(f"呼叫 {safe_proxy} 失敗：回應大小超過上限")
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(f"模型回傳非有效 JSON：{_redact_text(error, key)}") from error
    if not isinstance(answer, dict):
        raise RuntimeError("模型回傳格式異常（非 JSON 物件）")
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"模型沒有回傳內容：{_redact_text(json.dumps(answer, ensure_ascii=False)[:300], key)}")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型沒有回傳有效文字內容")
    usage = answer.get("usage")
    return content, usage if isinstance(usage, dict) else {}


def candidate_models(source, http_model=MODEL):
    command = command_line()
    options = []
    if source in ("auto", "cmd") and command:
        options.append(f"cmd:{_safe_command_label(command)}")
    if source in ("auto", "http"):
        options.append(http_model)
    return options


def ask(system, payload, source=DEFAULT_SOURCE, http_model=MODEL, timeout=300):
    if source not in SOURCES:
        raise RuntimeError(f"不支援的來源：{source}")
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
                label = _safe_command_label(command)
                return {"answer": answer, "source": f"cmd:{label}",
                        "request_model": f"cmd:{label}", "usage": usage,
                        "fallback": reasons[0] if reasons else None}
            except SubmissionUncertain:
                raise
            except RuntimeError as error:
                reasons.append(str(error))
                if source == "cmd":
                    raise

    answer, usage = ask_http(system, payload, http_model, timeout)
    return {"answer": answer, "source": f"http:{http_model}",
            "request_model": http_model, "usage": usage,
            "fallback": reasons[0] if reasons else None}
