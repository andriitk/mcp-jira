"""Render Jira API payloads as compact Markdown for an agent."""

from __future__ import annotations

from typing import Any

from . import adf

CUSTOM_FIELD_LIMIT = 3000
SKIPPED_CUSTOM_FIELDS = {"rank", "development"}


def _when(value: str | None) -> str:
    return value[:16].replace("T", " ") if value else ""


def _name(user: dict | None) -> str:
    return user.get("displayName", "unknown") if user else "Unassigned"


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _plain(value: Any) -> str | None:
    """Best-effort text for a custom field value; None when there is nothing readable."""
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, str):
        return None if value.startswith("{") else value
    if isinstance(value, dict):
        if value.get("type") == "doc":
            return adf.to_markdown(value) or None
        for key in ("displayName", "value", "name", "key"):
            if key in value:
                child = value.get("child")
                text = str(value[key])
                return f"{text} / {_plain(child)}" if isinstance(child, dict) else text
        return None
    if isinstance(value, list):
        parts = [p for p in (_plain(v) for v in value) if p]
        return ", ".join(parts) or None
    return None


def render_issue(issue: dict, comments: list[dict] | None, site_url: str) -> str:
    key, fields, names = issue["key"], issue["fields"], issue.get("names", {})
    out = [f"# {key}: {fields.get('summary', '')}", f"URL: {site_url}/browse/{key}", ""]

    meta = {
        "Project": (fields.get("project") or {}).get("name"),
        "Type": (fields.get("issuetype") or {}).get("name"),
        "Status": (fields.get("status") or {}).get("name"),
        "Priority": (fields.get("priority") or {}).get("name"),
        "Resolution": (fields.get("resolution") or {}).get("name"),
        "Assignee": _name(fields.get("assignee")),
        "Reporter": _name(fields.get("reporter")),
        "Parent": " ".join(
            filter(
                None,
                [
                    (fields.get("parent") or {}).get("key"),
                    ((fields.get("parent") or {}).get("fields") or {}).get("summary"),
                ],
            )
        ),
        "Labels": ", ".join(fields.get("labels") or []),
        "Components": ", ".join(c.get("name", "") for c in fields.get("components") or []),
        "Fix versions": ", ".join(v.get("name", "") for v in fields.get("fixVersions") or []),
        "Due": fields.get("duedate"),
        "Created": _when(fields.get("created")),
        "Updated": _when(fields.get("updated")),
    }
    out += [f"- **{k}:** {v}" for k, v in meta.items() if v]

    out += ["", "## Description", "", adf.to_markdown(fields.get("description")) or "_No description._"]

    custom = []
    for field_id, value in fields.items():
        if not field_id.startswith("customfield_"):
            continue
        label = names.get(field_id, field_id)
        if label.lower() in SKIPPED_CUSTOM_FIELDS:
            continue
        text = _plain(value)
        if text:
            if len(text) > CUSTOM_FIELD_LIMIT:
                text = text[:CUSTOM_FIELD_LIMIT] + " …(truncated)"
            custom.append(f"### {label}\n\n{text}" if "\n" in text else f"- **{label}:** {text}")
    if custom:
        out += ["", "## Other fields", "", *custom]

    subtasks = fields.get("subtasks") or []
    if subtasks:
        out += ["", "## Subtasks", ""]
        out += [f"- {s['key']} [{s['fields']['status']['name']}] {s['fields']['summary']}" for s in subtasks]

    links = fields.get("issuelinks") or []
    if links:
        out += ["", "## Linked issues", ""]
        for link in links:
            if "outwardIssue" in link:
                other, verb = link["outwardIssue"], link["type"].get("outward", "relates to")
            else:
                other, verb = link["inwardIssue"], link["type"].get("inward", "relates to")
            f = other.get("fields", {})
            out.append(f"- {verb} {other['key']} [{(f.get('status') or {}).get('name', '')}] {f.get('summary', '')}")

    attachments = fields.get("attachment") or []
    if attachments:
        out += ["", f"## Attachments ({len(attachments)})", ""]
        for a in attachments:
            out.append(
                f"- id `{a['id']}` · {a.get('filename')} · {a.get('mimeType')} · {_size(int(a.get('size', 0)))}"
                f" · {_name(a.get('author'))} {_when(a.get('created'))}"
            )
        out.append("\nUse `get_attachment` with an id to see screenshots, video frames or file contents.")

    if comments is not None:
        out += ["", f"## Comments ({len(comments)})"]
        for c in comments:
            out += [
                "",
                f"### {_name(c.get('author'))} · {_when(c.get('created'))} · id {c['id']}",
                "",
                adf.to_markdown(c.get("body")),
            ]
    return "\n".join(out).strip() + "\n"


def render_search(issues: list[dict], site_url: str) -> str:
    if not issues:
        return "No issues match."
    lines = []
    for issue in issues:
        f = issue["fields"]
        lines.append(
            f"- {issue['key']} [{(f.get('status') or {}).get('name', '')}] {f.get('summary', '')}"
            f" · {(f.get('issuetype') or {}).get('name', '')} · {_name(f.get('assignee'))}"
            f" · updated {_when(f.get('updated'))}"
        )
    return f"{len(issues)} issue(s) on {site_url}:\n" + "\n".join(lines)
