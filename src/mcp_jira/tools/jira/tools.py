"""Jira tools. Read a task, look at its attachments, report back; create issues when asked.

There are deliberately no update, transition or delete tools: the agent cannot destroy anything.
Each tool is one plain function; the Jira client arrives through `Depends()`, bound to the signed-in
caller, and is stripped from the tool schema.
"""

import asyncio

from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from mcp_jira.misc.di import provide_jira_client, provide_settings
from mcp_jira.settings import Settings

from . import adf, attachments, render
from .attachments import Content
from .client import JiraClient, JiraError
from .schemas import (
    AssignToMe,
    AttachmentId,
    IncludeComments,
    IssueKey,
    IssueType,
    Jql,
    Labels,
    Markdown,
    MaxFrames,
    MaxResults,
    NonEmptyMarkdown,
    OptionalIssueKey,
    Priority,
    ProjectKey,
    Summary,
)


async def whoami(jira: JiraClient = Depends(provide_jira_client)) -> str:
    """Show the Jira site and the account this server acts as.

    Use it when unsure whether this server belongs to the organisation of the current task.
    """
    caller = jira.caller
    return f"{caller.site_url} as {caller.name} ({caller.email or caller.account_id})"


async def get_issue(
    issue_key: IssueKey,
    include_comments: IncludeComments = True,
    jira: JiraClient = Depends(provide_jira_client),
) -> str:
    """Read one issue as Markdown: summary, status, people, parent, description, non-empty custom
    fields (acceptance criteria, sprint, ...), subtasks, linked issues, attachments with ids, comments.

    ## Examples
    Good: get_issue("ABC-123") before planning work on that task.
    Bad: get_issue on every search hit; read the search result first.
    """
    try:
        issue = await jira.get_issue(issue_key)
        comments = await jira.get_comments(issue_key) if include_comments else None
    except JiraError as exc:
        raise ToolError(str(exc)) from exc
    return render.render_issue(issue, comments, jira.caller.site_url)


async def get_attachment(
    attachment_id: AttachmentId,
    max_frames: MaxFrames = 6,
    jira: JiraClient = Depends(provide_jira_client),
    settings: Settings = Depends(provide_settings),
) -> list[Content]:
    """Open one issue attachment so you can see it.

    Images come back inline. Videos and animated GIFs come back as evenly spaced frames with
    timestamps. Text-like files (logs, JSON, CSV, code) come back as text. When a local path is
    returned, the full file is there for local tools (PDFs, archives, the whole video).
    """
    try:
        meta = attachments.AttachmentMeta.from_api(await jira.attachment_meta(attachment_id))
        data = await jira.download_attachment(meta.id, attachments.MAX_DOWNLOAD_BYTES)
    except JiraError as exc:
        raise ToolError(str(exc)) from exc
    saved_at = None
    if settings.downloads.directory is not None:
        path = attachments.save(meta, data, settings.downloads.directory)
        host_dir = settings.downloads.host_directory.rstrip("/")
        saved_at = f"{host_dir}/{path.name}" if host_dir else str(path)
    # Decoding is CPU-bound: keep it off the event loop so other requests are served meanwhile.
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(attachments.describe, meta, data, saved_at, max_frames),
            timeout=attachments.PREVIEW_TIMEOUT_S,
        )
    except TimeoutError as exc:
        where = f"; the file is saved at {saved_at}" if saved_at else "; open it in Jira"
        raise ToolError(f"Preview of {meta.filename} took too long{where}.") from exc


async def search_issues(
    jql: Jql,
    max_results: MaxResults = 20,
    jira: JiraClient = Depends(provide_jira_client),
) -> str:
    """Find issues with JQL. One line per issue: key, status, summary, type, assignee, last update.

    ## Examples
    - My open work: assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC
    - Recent in a project: project = ABC AND created >= -7d ORDER BY created DESC
    - Full text: text ~ "billing timeout" ORDER BY updated DESC
    """
    try:
        issues = await jira.search(jql, max_results)
    except JiraError as exc:
        raise ToolError(str(exc)) from exc
    return render.render_search(issues, jira.caller.site_url)


async def add_comment(
    issue_key: IssueKey,
    body: NonEmptyMarkdown,
    jira: JiraClient = Depends(provide_jira_client),
) -> str:
    """Post a comment on an issue, visible to everyone who can see the issue. It cannot be edited or
    deleted through this server, so post the final text once.

    Markdown becomes Jira formatting: paragraphs, headings, lists, code blocks, quotes, **bold**,
    *italic*, `code`, [links](https://example.com) and bare URLs.

    ## Returns
    The comment id and a direct link to it.
    """
    if not body.strip():
        raise ToolError("Comment body is empty.")
    try:
        comment = await jira.add_comment(issue_key, adf.from_markdown(body))
    except JiraError as exc:
        raise ToolError(str(exc)) from exc
    site = jira.caller.site_url
    return f"Comment {comment['id']} added: {site}/browse/{issue_key}?focusedCommentId={comment['id']}"


async def create_issue(
    project_key: ProjectKey,
    summary: Summary,
    description: Markdown = "",
    issue_type: IssueType = "Task",
    labels: Labels = None,
    priority: Priority = None,
    parent_key: OptionalIssueKey = None,
    assign_to_me: AssignToMe = False,
    jira: JiraClient = Depends(provide_jira_client),
) -> str:
    """Create a new issue. It cannot be deleted through this server, so create it only when the user
    asked for it.

    ## Returns
    The new key and its link. When Jira rejects a field (unknown type, a required custom field), the
    error names that field.
    """
    fields: dict = {
        "project": {"key": project_key.upper()},
        "summary": summary.strip(),
        "issuetype": {"name": issue_type},
    }
    if description.strip():
        fields["description"] = adf.from_markdown(description)
    if labels:
        fields["labels"] = [label.strip().replace(" ", "-") for label in labels if label.strip()]
    if priority:
        fields["priority"] = {"name": priority}
    if parent_key:
        fields["parent"] = {"key": parent_key}
    if assign_to_me:
        fields["assignee"] = {"accountId": jira.caller.account_id}
    try:
        created = await jira.create_issue(fields)
    except JiraError as exc:
        raise ToolError(str(exc)) from exc
    return f"Created {created['key']}: {jira.caller.site_url}/browse/{created['key']}"
