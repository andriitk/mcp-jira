from mcp_jira.tools.jira import adf


def p(*content):
    return {"type": "paragraph", "content": list(content)}


def t(text, *marks):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def doc(*blocks):
    return {"type": "doc", "version": 1, "content": list(blocks)}


# ── ADF -> Markdown ──────────────────────────────────────────────────


def test_inline_marks_and_links():
    node = doc(
        p(
            t("Fix "),
            t("now", {"type": "strong"}),
            t(" see "),
            t("PR", {"type": "link", "attrs": {"href": "https://x.test/pr/1"}}),
            t(" and "),
            t("cfg.yaml", {"type": "code"}),
        )
    )
    assert adf.to_markdown(node) == "Fix **now** see [PR](https://x.test/pr/1) and `cfg.yaml`"


def test_nested_lists_code_tables_and_media():
    node = doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [t("Repro")]},
        {
            "type": "orderedList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        p(t("open")),
                        {"type": "bulletList", "content": [{"type": "listItem", "content": [p(t("as admin"))]}]},
                    ],
                },
                {"type": "listItem", "content": [p(t("click"))]},
            ],
        },
        {"type": "codeBlock", "attrs": {"language": "json"}, "content": [t('{"a": 1}')]},
        {
            "type": "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableHeader", "content": [p(t("k"))]},
                        {"type": "tableHeader", "content": [p(t("v"))]},
                    ],
                },
                {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableCell", "content": [p(t("a|b"))]},
                        {"type": "tableCell", "content": [p(t("1"))]},
                    ],
                },
            ],
        },
        {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"alt": "shot.png"}}]},
        {"type": "taskList", "content": [{"type": "taskItem", "attrs": {"state": "DONE"}, "content": [t("done")]}]},
    )
    assert adf.to_markdown(node) == (
        "## Repro\n\n"
        "1. open\n  - as admin\n2. click\n\n"
        '```json\n{"a": 1}\n```\n\n'
        "| k | v |\n| --- | --- |\n| a\\|b | 1 |\n\n"
        "[attachment: shot.png]\n\n"
        "- [x] done"
    )


def test_mentions_status_panels_and_unknown_nodes_degrade_to_text():
    node = doc(
        p({"type": "mention", "attrs": {"text": "@Dana"}}, t(" "), {"type": "status", "attrs": {"text": "BLOCKED"}}),
        {"type": "panel", "attrs": {"panelType": "warning"}, "content": [p(t("careful"))]},
        {"type": "someFutureNode", "content": [p(t("still readable"))]},
    )
    assert adf.to_markdown(node) == "@Dana [BLOCKED]\n\n> **Warning:** careful\n\nstill readable"


def test_empty_description():
    assert adf.to_markdown(None) == ""


# ── Markdown -> ADF ──────────────────────────────────────────────────


def test_comment_with_link_list_and_code():
    md = "Done in [PR #42](https://github.com/a/b/pull/42).\n\n- one\n  - nested\n- two\n\n```py\nprint(1)\n```"
    out = adf.from_markdown(md)
    first, bullets, code = out["content"]
    assert first["content"][1] == t("PR #42", {"type": "link", "attrs": {"href": "https://github.com/a/b/pull/42"}})
    assert bullets["type"] == "bulletList"
    assert bullets["content"][0]["content"][1]["type"] == "bulletList"  # nested list under "one"
    assert code == {"type": "codeBlock", "attrs": {"language": "py"}, "content": [t("print(1)")]}


def test_bare_urls_bold_italic_and_line_breaks():
    out = adf.from_markdown("**Ready** for *review*\nhttps://ci.test/run/9.")
    para = out["content"][0]["content"]
    assert para[0] == t("Ready", {"type": "strong"})
    assert para[2] == t("review", {"type": "em"})
    assert para[3] == {"type": "hardBreak"}
    assert para[4] == t("https://ci.test/run/9", {"type": "link", "attrs": {"href": "https://ci.test/run/9"}})
    assert para[5] == t(".")


def test_code_mark_combines_only_with_link():
    out = adf.from_markdown("**see `x`**")
    assert out["content"][0]["content"][-1] == t("x", {"type": "code"})


def test_headings_quotes_rules_and_ordered_lists():
    out = adf.from_markdown("# Title\n\n> quoted\n\n---\n\n1. a\n2. b")
    assert [b["type"] for b in out["content"]] == ["heading", "blockquote", "rule", "orderedList"]


def test_round_trip_keeps_meaning():
    md = "## Result\n\n- **fixed** login\n- see [PR](https://x.test/1)\n\n`done`"
    assert adf.to_markdown(adf.from_markdown(md)) == md
