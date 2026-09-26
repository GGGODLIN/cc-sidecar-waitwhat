import argparse
import json
import pathlib
import re
import sys
import time

from . import cache, locate, model, parse, prompts, render

GLOSSARY = pathlib.Path.home() / ".claude" / "glossary.md"
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STATUS_LABEL = {"idle": "等你回", "working": "還在跑", "blocked": "卡住了", "done": "做完了"}


def glossary_block(content_length=None):
    if not GLOSSARY.exists():
        return None
    text = GLOSSARY.read_text().strip()
    if not text or (content_length is not None and len(text) >= content_length):
        return None
    return "# 使用者的個人語彙表（重講時優先用這裡的說法）\n" + text


def repo_glossary_block(cwd):
    path = pathlib.Path(cwd or "/nonexistent") / "openspec" / "glossary.md"
    if not path.exists():
        return None
    return "# 這個 repo 的領域詞彙\n" + path.read_text()[:4000]


def render_turn(group):
    lines = []
    if group["prompt"]:
        lines.append("【使用者】\n" + group["prompt"])
    for reply in group["replies"]:
        lines.append("【助手】\n" + reply)
    return "\n\n".join(lines)


def cache_messages(groups):
    messages = []
    for group in groups:
        if group["prompt"]:
            messages.append(("user", group["prompt"]))
        messages.extend(("assistant", reply) for reply in group["replies"])
    return messages


def build_full(row, tail=None):
    meta, turns, tools = parse.transcript(row["path"], tail)
    if not turns:
        return None, []
    groups = parse.split_turns(turns)
    body = "\n\n".join(render_turn(group) for group in groups)
    blocks = [f"# 這個 session 的基本資料\n"
              f"視窗標題：{row['title']}\n"
              f"工作目錄：{meta.get('cwd')}\n"
              f"開始時間：{meta.get('started')}"]
    for block in (glossary_block(len(body)), repo_glossary_block(meta.get("cwd"))):
        if block:
            blocks.append(block)
    if tools:
        unique = list(dict.fromkeys(tools))
        blocks.append(f"# 這個 session 做過的事（工具呼叫 {len(tools)} 次，去重後取最近 60 筆）\n"
                      + "\n".join("- " + item for item in unique[-60:]))
    blocks.append("# 對話紀錄\n" + body)
    return "\n\n---\n\n".join(blocks), cache_messages(groups)


def build_recent(row, count):
    groups = parse.recent_turns(row["path"], count)
    if not groups:
        return None, []
    heading = ("# 要重講的內容（最後一個 turn）" if count == 1
               else f"# 要重講的內容（最後 {len(groups)} 個 turn）")
    body = heading + "\n\n" + "\n\n".join(render_turn(group) for group in groups)
    blocks = []
    block = glossary_block(len(body))
    if block:
        blocks.append(block)
    blocks.append(body)
    return "\n\n---\n\n".join(blocks), cache_messages(groups)


def render_list(rows):
    lines = []
    for index, row in enumerate(rows, 1):
        marker = "→" if row["focused"] else " "
        status = STATUS_LABEL.get(row["status"], row["status"])
        lines.append(f"{marker} [{index}] {row['title']}   ({status})")
        lines.append(f"       {row['cwd']}")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ww",
        description="在 CC 之外重講它剛剛說的話。不帶數字＝整段脈絡，帶數字＝最後幾個 turn 白話重講。")
    parser.add_argument("turns", nargs="?", type=int,
                        help="往回幾個 turn，1 就是最後一輪；不給就是整段脈絡")
    parser.add_argument("-s", "--session", type=int, default=1,
                        help="選第幾支 session，預設是你正在看的那支")
    parser.add_argument("-l", "--list", action="store_true", help="列出活著的 session")
    parser.add_argument("-m", "--model", default=model.MODEL, help="http 來源要用哪顆模型")
    parser.add_argument("--source", choices=model.SOURCES, default=model.DEFAULT_SOURCE,
                        help="auto 依序試 web → cmd → http；指定單一來源則失敗就直接報錯")
    parser.add_argument("--no-cache", action="store_true", help="跳過快取，重新問一次")
    parser.add_argument("--raw", action="store_true", help="輸出原始 markdown，不套終端機樣式")
    parser.add_argument("--cache-stats", action="store_true", help="看快取現況")
    parser.add_argument("--tail", type=int, default=None, help="只讀 session 檔末尾 N bytes")
    parser.add_argument("--session-id", default=None,
                        help="用 session UUID 指定要重講的 session，給不經清單選取的程式呼叫")
    parser.add_argument("--json", action="store_true",
                        help="stdout 只印一個 JSON 物件（成功或失敗都是），給程式讀")
    return parser


def row_for_id(session_id, root=locate.PROJECTS):
    # The ordinal -s is a position in a list that reorders as sessions come and go, so a caller that
    # already knows which session it means passes the UUID and never goes through the list.
    if not SESSION_ID.fullmatch(session_id or ""):
        return None, "session id 不是 UUID"
    path = locate.session_path(session_id, root)
    if path is None:
        return None, f"找不到 session {session_id}"
    summary = locate.session_summary(path) or {}
    return {"id": session_id, "path": path,
            "title": (summary.get("reply") or summary.get("said") or "(無標題)")[:52],
            "status": "unknown", "focused": False, "cwd": summary.get("cwd") or "",
            "source": "id"}, None


def emit(options, payload, text=None, error=None):
    if options.json:
        print(json.dumps(payload, ensure_ascii=False))
    elif error is not None:
        print(error, file=sys.stderr)
    elif text is not None:
        print(text)
    return 0 if payload.get("ok") else 1


def main(argv):
    options = build_parser().parse_args(argv)

    if options.cache_stats:
        total, labels = cache.stats()
        detail = "  ".join(f"{name} {count}" for name, count in sorted(labels.items()))
        print(f"{cache.PATH}\n{total} 筆  {detail}")
        return 0

    def fail(message):
        return emit(options, {"ok": False, "error": message}, error=message)

    def note(message):
        if not options.json:
            print(message, file=sys.stderr, flush=True)

    styled = render.enabled() and not options.raw and not options.json
    if options.session_id is not None:
        row, problem = row_for_id(options.session_id)
        if row is None:
            return fail(problem)
    else:
        rows = locate.candidates()
        if not rows:
            return fail("找不到活著的 CC session")
        if options.list:
            print(render_list(rows))
            return 0

        position = options.session - 1
        if not 0 <= position < len(rows):
            return fail(f"沒有第 {options.session} 支，目前有 {len(rows)} 支（打 -l 看清單）")
        row = rows[position]

    if options.turns is None:
        payload, messages = build_full(row, options.tail)
        system, label, mode = prompts.wait_what(), "跟丟了", "lost"
    elif options.turns < 1:
        return fail("turn 數要大於 0")
    else:
        payload, messages = build_recent(row, options.turns)
        system, mode = prompts.plain(), "plain"
        label = "白話" if options.turns == 1 else f"白話 x{options.turns}"
    if not payload:
        return fail(f"「{row['title']}」還沒有可重講的內容")
    if row["status"] == "working":
        note("⚠ 這支 agent 還在跑，最後一輪可能只有半截（打 -l 看狀態）")

    key = cache.shared_key_for(mode, messages)
    def result_of(answer, source, cached):
        return {"ok": True, "sessionId": row["id"], "mode": mode, "label": label, "key": key,
                "answer": answer, "source": source, "cached": cached}

    if not options.no_cache:
        entry = cache.get(key)
        if entry:
            note(f"── {label}：{row['title']}  (快取命中 · 來源 {entry.get('source', '?')})")
            return emit(options, result_of(entry["answer"], entry.get("source", "?"), True),
                        text=render.render(entry["answer"], styled))

    planned = model.candidate_models(options.source, options.model)
    heading = planned[0] if planned else "沒有可用的來源"
    note(f"── {label}：{row['title']}  ({row['id'][:8]}，送出 {len(payload):,} 字 → {heading})")
    started = time.time()
    try:
        result = model.ask(system, payload, options.source, options.model)
    except RuntimeError as error:
        return fail(str(error))
    cache.put(key, result["answer"], label, result["source"])
    if options.json:
        return emit(options, result_of(result["answer"], result["source"], False))
    print(render.render(result["answer"], styled))

    usage = result["usage"]
    tokens = ""
    if usage.get("prompt_tokens"):
        tokens = f"  輸入 {usage['prompt_tokens']} / 輸出 {usage.get('completion_tokens')} token"
    note = f"  ← 退回原因：{result['fallback']}" if result["fallback"] else ""
    print(f"── {time.time() - started:.1f}s  來源 {result['source']}{tokens}{note}",
          file=sys.stderr)
    return 0
