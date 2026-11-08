"""Atlassian Document Format <-> Markdown.

`to_markdown` renders every ADF node Jira commonly produces; unknown nodes degrade to their text.
`from_markdown` covers the Markdown subset an agent writes in comments: paragraphs, headings,
lists (nested), code blocks, quotes, rules, **bold**, *italic*, ~~strike~~, `code`, links and bare URLs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

Node = dict[str, Any]

_LISTS = ("bulletList", "orderedList", "taskList")


# ---------------------------------------------------------------------------------------------------
# ADF -> Markdown
# ---------------------------------------------------------------------------------------------------
def to_markdown(doc: Node | None) -> str:
    if not doc:
        return ""
    nodes = doc.get("content", []) if doc.get("type") == "doc" else [doc]
    return _blocks(nodes).strip()


def _blocks(nodes: list[Node], indent: str = "") -> str:
    return "\n\n".join(part for part in (_block(n, indent) for n in nodes) if part)


def _block(node: Node, indent: str = "") -> str:
    kind, content, attrs = node.get("type"), node.get("content", []), node.get("attrs", {})
    match kind:
        case "paragraph":
            return indent + _inline(content).replace("\n", "\n" + indent)
        case "heading":
            return "#" * int(attrs.get("level", 1)) + " " + _inline(content)
        case "bulletList" | "orderedList" | "taskList":
            start = int(attrs.get("order", 1) or 1)
            return "\n".join(_list_item(item, kind, start + i, indent) for i, item in enumerate(content))
        case "codeBlock":
            text = "".join(c.get("text", "") for c in content)
            return f"```{attrs.get('language', '') or ''}\n{text}\n```"
        case "blockquote":
            return _quote(_blocks(content))
        case "panel":
            return _quote(f"**{attrs.get('panelType', 'note').capitalize()}:** {_blocks(content)}")
        case "rule":
            return "---"
        case "mediaSingle" | "mediaGroup":
            return " ".join(_media(m) for m in content)
        case "media":
            return _media(node)
        case "table":
            return _table(node)
        case "expand" | "nestedExpand":
            title = attrs.get("title") or "Details"
            return f"**{title}**\n\n{_blocks(content)}"
        case "blockCard" | "embedCard":
            return attrs.get("url", "")
        case "decisionList":
            return "\n".join(f"- Decision: {_inline(item.get('content', []))}" for item in content)
        case _:
            if content:
                inline = all(c.get("type") in _INLINE_TYPES for c in content)
                return _inline(content) if inline else _blocks(content, indent)
            return node.get("text", "")


def _list_item(item: Node, kind: str, number: int, indent: str) -> str:
    if kind == "taskList":
        marker = "- [x] " if item.get("attrs", {}).get("state") == "DONE" else "- [ ] "
    else:
        marker = f"{number}. " if kind == "orderedList" else "- "
    children = item.get("content", [])
    if children and children[0].get("type") not in ("paragraph", *_LISTS):
        # taskItem holds inline nodes directly.
        return indent + marker + _inline(children)
    lines: list[str] = []
    for i, child in enumerate(children):
        if child.get("type") in _LISTS:
            lines.append(_block(child, indent + "  "))
        else:
            text = _block(child).replace("\n", "\n" + indent + "  ")
            lines.append(indent + (marker if i == 0 else "  ") + text)
    return "\n".join(lines)


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _table(node: Node) -> str:
    rows = []
    for row in node.get("content", []):
        cells = [_blocks(c.get("content", [])).replace("\n", " ").replace("|", "\\|") for c in row.get("content", [])]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    width = rows[0].count(" | ") + 1
    return "\n".join([rows[0], "|" + " --- |" * width, *rows[1:]])


def _media(node: Node) -> str:
    attrs = node.get("attrs", {})
    name = attrs.get("alt") or attrs.get("fileName")
    return f"[attachment: {name}]" if name else "[embedded attachment]"


_INLINE_TYPES = {"text", "hardBreak", "mention", "emoji", "inlineCard", "status", "date", "mediaInline"}


def _inline(nodes: list[Node]) -> str:
    out: list[str] = []
    for node in nodes:
        kind, attrs = node.get("type"), node.get("attrs", {})
        match kind:
            case "text":
                out.append(_apply_marks(node.get("text", ""), node.get("marks", [])))
            case "hardBreak":
                out.append("\n")
            case "mention":
                text = attrs.get("text") or "user"
                out.append(text if text.startswith("@") else f"@{text}")
            case "emoji":
                out.append(attrs.get("text") or attrs.get("shortName", ""))
            case "inlineCard":
                out.append(attrs.get("url", ""))
            case "status":
                out.append(f"[{attrs.get('text', '')}]")
            case "date":
                out.append(_date(attrs.get("timestamp")))
            case "mediaInline" | "media":
                out.append(_media(node))
            case _:
                out.append(_inline(node["content"]) if node.get("content") else node.get("text", ""))
    return "".join(out)


def _apply_marks(text: str, marks: list[Node]) -> str:
    href = None
    for mark in marks:
        match mark.get("type"):
            case "code":
                text = f"`{text}`"
            case "strong":
                text = f"**{text}**"
            case "em":
                text = f"*{text}*"
            case "strike":
                text = f"~~{text}~~"
            case "link":
                href = mark.get("attrs", {}).get("href")
    return f"[{text}]({href})" if href and href != text else text


def _date(timestamp: Any) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC).strftime("%Y-%m-%d")
    except TypeError, ValueError:
        return ""


# ---------------------------------------------------------------------------------------------------
# Markdown -> ADF
# ---------------------------------------------------------------------------------------------------
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_INLINE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<link>\[(?P<ltext>[^\]\n]+)\]\((?P<lurl>[^)\s]+)\))"
    r"|(?P<bold>\*\*(?P<btext>.+?)\*\*)"
    r"|(?P<strike>~~(?P<stext>.+?)~~)"
    r"|(?P<em>(?<![\w*])\*(?P<etext>[^*\n]+?)\*(?![\w*])|(?<!\w)_(?P<utext>[^_\n]+?)_(?!\w))"
    r"|(?P<url>https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"])"
)


def from_markdown(markdown: str) -> Node:
    return {"type": "doc", "version": 1, "content": _parse_blocks(markdown.replace("\r\n", "\n").split("\n"))}


def _parse_blocks(lines: list[str]) -> list[Node]:
    blocks: list[Node] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
        elif stripped.startswith("```"):
            language, body = stripped[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            node: Node = {"type": "codeBlock"}
            if language:
                node["attrs"] = {"language": language}
            if body:
                node["content"] = [{"type": "text", "text": "\n".join(body)}]
            blocks.append(node)
        elif m := _HEADING.match(stripped):
            blocks.append({"type": "heading", "attrs": {"level": len(m[1])}, "content": _parse_inline(m[2])})
            i += 1
        elif _RULE.match(stripped):
            blocks.append({"type": "rule"})
            i += 1
        elif stripped.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = [
                b for b in _parse_blocks(quoted) if b["type"] in ("paragraph", "bulletList", "orderedList", "codeBlock")
            ]
            if inner:
                blocks.append({"type": "blockquote", "content": inner})
        elif _LIST_ITEM.match(lines[i]):
            items = []
            while i < len(lines) and (m := _LIST_ITEM.match(lines[i])):
                items.append((len(m[1].expandtabs(4)), m[2][0].isdigit(), m[3]))
                i += 1
            blocks.append(_build_list(items))
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                para.append(lines[i].strip())
                i += 1
            content: list[Node] = []
            for n, line in enumerate(para):
                if n:
                    content.append({"type": "hardBreak"})
                content.extend(_parse_inline(line))
            if content:
                blocks.append({"type": "paragraph", "content": content})
    return blocks


def _starts_block(line: str) -> bool:
    s = line.strip()
    return bool(s.startswith(("```", ">")) or _HEADING.match(s) or _RULE.match(s) or _LIST_ITEM.match(line))


def _list_node(ordered: bool) -> Node:
    return {"type": "orderedList" if ordered else "bulletList", "content": []}


def _build_list(items: list[tuple[int, bool, str]]) -> Node:
    root = _list_node(items[0][1])
    stack: list[tuple[int, Node]] = [(items[0][0], root)]
    for indent, ordered, text in items:
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        current_indent, current = stack[-1]
        if indent > current_indent and current["content"]:
            nested = _list_node(ordered)
            current["content"][-1]["content"].append(nested)
            stack.append((indent, nested))
            current = nested
        paragraph = {"type": "paragraph", "content": _parse_inline(text)} if text else {"type": "paragraph"}
        current["content"].append({"type": "listItem", "content": [paragraph]})
    return root


def _text(text: str, marks: tuple[Node, ...]) -> Node:
    node: Node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _parse_inline(text: str, marks: tuple[Node, ...] = ()) -> list[Node]:
    out: list[Node] = []
    pos = 0
    in_link = any(m["type"] == "link" for m in marks)
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(_text(text[pos : m.start()], marks))
        if m["code"]:
            # ADF allows `code` to combine only with `link`.
            out.append(_text(m["code"][1:-1], (*[k for k in marks if k["type"] == "link"], {"type": "code"})))
        elif m["link"]:
            link = () if in_link else ({"type": "link", "attrs": {"href": m["lurl"]}},)
            out.extend(_parse_inline(m["ltext"], (*marks, *link)))
        elif m["bold"]:
            out.extend(_parse_inline(m["btext"], (*marks, {"type": "strong"})))
        elif m["strike"]:
            out.extend(_parse_inline(m["stext"], (*marks, {"type": "strike"})))
        elif m["em"]:
            out.extend(_parse_inline(m["etext"] or m["utext"], (*marks, {"type": "em"})))
        else:
            link = () if in_link else ({"type": "link", "attrs": {"href": m["url"]}},)
            out.append(_text(m["url"], (*marks, *link)))
        pos = m.end()
    if pos < len(text):
        out.append(_text(text[pos:], marks))
    return [n for n in out if n["text"]]
