import argparse
import pathlib
import sys
import time

from . import cache, locate, model, parse, prompts, render

GLOSSARY = pathlib.Path.home() / ".claude" / "glossary.md"
STATUS_LABEL = {"idle": "等你回", "working": "還在跑", "blocked": "卡住了", "done": "做完了"}


def glossary_block():
    if not GLOSSARY.exists():
        return None
    return "# 使用者的個人語彙表（重講時優先用這裡的說法）\n" + GLOSSARY.read_text()


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


def build_full(row, tail=None):
    meta, turns, tools = parse.transcript(row["path"], tail)
    if not turns:
        return None
    blocks = [f"# 這個 session 的基本資料\n"
              f"視窗標題：{row['title']}\n"
              f"工作目錄：{meta.get('cwd')}\n"
              f"開始時間：{meta.get('started')}"]
    for block in (glossary_block(), repo_glossary_block(meta.get("cwd"))):
        if block:
            blocks.append(block)
    if tools:
        unique = list(dict.fromkeys(tools))
        blocks.append(f"# 這個 session 做過的事（工具呼叫 {len(tools)} 次，去重後取最近 60 筆）\n"
                      + "\n".join("- " + item for item in unique[-60:]))
    body = "\n\n".join(render_turn(group) for group in parse.split_turns(turns))
    blocks.append("# 對話紀錄\n" + body)
    return "\n\n---\n\n".join(blocks)


def build_recent(row, count):
    groups = parse.recent_turns(row["path"], count)
    if not groups:
        return None
    blocks = []
    block = glossary_block()
    if block:
        blocks.append(block)
    heading = ("# 要重講的內容（最後一個 turn）" if count == 1
               else f"# 要重講的內容（最後 {len(groups)} 個 turn）")
    blocks.append(heading + "\n\n" + "\n\n".join(render_turn(group) for group in groups))
    return "\n\n---\n\n".join(blocks)


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
    return parser


def main(argv):
    options = build_parser().parse_args(argv)

    if options.cache_stats:
        total, labels = cache.stats()
        detail = "  ".join(f"{name} {count}" for name, count in sorted(labels.items()))
        print(f"{cache.PATH}\n{total} 筆  {detail}")
        return 0

    styled = render.enabled() and not options.raw
    rows = locate.candidates()
    if not rows:
        print("找不到活著的 CC session", file=sys.stderr)
        return 1
    if options.list:
        print(render_list(rows))
        return 0

    position = options.session - 1
    if not 0 <= position < len(rows):
        print(f"沒有第 {options.session} 支，目前有 {len(rows)} 支（打 -l 看清單）", file=sys.stderr)
        return 1
    row = rows[position]

    if options.turns is None:
        payload, system, label = build_full(row, options.tail), prompts.WAIT_WHAT, "跟丟了"
    elif options.turns < 1:
        print("turn 數要大於 0", file=sys.stderr)
        return 1
    else:
        payload, system = build_recent(row, options.turns), prompts.PLAIN
        label = "白話" if options.turns == 1 else f"白話 x{options.turns}"
    if not payload:
        print(f"「{row['title']}」還沒有可重講的內容", file=sys.stderr)
        return 1

    if not options.no_cache:
        for candidate in model.candidate_models(options.source, options.model):
            entry = cache.get(cache.key_for(candidate, system, payload))
            if entry:
                print(f"── {label}：{row['title']}  "
                      f"(快取命中 · 來源 {entry.get('source', candidate)})",
                      file=sys.stderr, flush=True)
                print(render.render(entry["answer"], styled))
                return 0

    planned = model.candidate_models(options.source, options.model)
    heading = planned[0] if planned else "沒有可用的來源"
    print(f"── {label}：{row['title']}  ({row['id'][:8]}，送出 {len(payload):,} 字 → {heading})",
          file=sys.stderr, flush=True)
    started = time.time()
    try:
        result = model.ask(system, payload, options.source, options.model)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    cache.put(cache.key_for(result["request_model"], system, payload),
              result["answer"], label, result["source"])
    print(render.render(result["answer"], styled))

    usage = result["usage"]
    tokens = ""
    if usage.get("prompt_tokens"):
        tokens = f"  輸入 {usage['prompt_tokens']} / 輸出 {usage.get('completion_tokens')} token"
    note = f"  ← 退回原因：{result['fallback']}" if result["fallback"] else ""
    print(f"── {time.time() - started:.1f}s  來源 {result['source']}{tokens}{note}",
          file=sys.stderr)
    return 0
