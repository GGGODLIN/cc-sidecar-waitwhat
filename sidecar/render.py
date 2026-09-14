import io
import os
import re
import shutil
import sys

BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"

FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
QUOTE = re.compile(r"^>\s?(.*)$")
BOLD_SPAN = re.compile(r"\*\*(.+?)\*\*")
ITALIC_SPAN = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
PLACEHOLDER = "\x00{}\x00"


def enabled(stream=None):
    target = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(target, "isatty", lambda: False)())


def inline(text):
    spans = []

    def stash(match):
        content = match.group(2)
        if len(match.group(1)) > 1 and content.startswith(" ") and content.endswith(" "):
            content = content[1:-1]
        spans.append(content)
        return PLACEHOLDER.format(len(spans) - 1)

    text = CODE_SPAN.sub(stash, text)
    text = BOLD_SPAN.sub(lambda match: BOLD + match.group(1) + RESET, text)
    text = ITALIC_SPAN.sub(lambda match: BOLD + match.group(1) + RESET, text)
    for index, content in enumerate(spans):
        text = text.replace(PLACEHOLDER.format(index), CYAN + content + RESET)
    return text


def with_rich(markdown, width=None):
    try:
        from rich.console import Console
        from rich.markdown import Markdown
    except ImportError:
        return None
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, legacy_windows=False,
                      width=width or shutil.get_terminal_size((88, 24)).columns)
    console.print(Markdown(markdown), soft_wrap=True)
    return buffer.getvalue().rstrip("\n")


def render(markdown, styled=True, prefer_rich=True):
    if not styled:
        return markdown
    if prefer_rich:
        rendered = with_rich(markdown)
        if rendered is not None:
            return rendered
    return basic(markdown)


def basic(markdown):
    lines = []
    in_fence = False
    for line in markdown.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(f"  {DIM}{line}{RESET}" if line.strip() else "")
            continue
        heading = HEADING.match(line)
        if heading:
            lines.append(f"{BOLD}{YELLOW}{inline(heading.group(2))}{RESET}")
            continue
        quote = QUOTE.match(line)
        if quote:
            lines.append(f"{DIM}│{RESET} {inline(quote.group(1))}")
            continue
        bullet = BULLET.match(line)
        if bullet:
            lines.append(f"{bullet.group(1)}• {inline(bullet.group(2))}")
            continue
        lines.append(inline(line))
    return "\n".join(lines)
