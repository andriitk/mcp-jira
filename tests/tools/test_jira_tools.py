"""Per-tool behaviour through the MCP client: happy paths, Jira errors, invalid input, no session."""

import base64
import io
from pathlib import Path

import httpx2
import pytest
from fastmcp.exceptions import ToolError
from PIL import Image

from tests.conftest import SITE


async def call(client, name: str, args: dict):
    return await client.call_tool(name, args, raise_on_error=False)


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


# ── get_issue ────────────────────────────────────────────────────────


async def test_get_issue_renders_the_task_context(mcp_client):
    result = await call(mcp_client, "get_issue", {"issue_key": f"{SITE}/browse/abc-123"})
    out = text_of(result)
    assert not result.is_error
    assert out.startswith("# ABC-123: Login fails with SSO")
    assert f"URL: {SITE}/browse/ABC-123" in out
    assert "Steps: open the app, click SSO." in out
    assert "User lands on the dashboard." in out  # custom field (acceptance criteria)
    assert "Sprint 42" in out
    assert "0|i0001:" not in out  # rank noise is skipped
    assert "id `501` · screen.png · image/png" in out
    assert "Still happens on staging." in out


async def test_get_issue_without_comments_skips_the_comment_call(mcp_client, jira_api):
    result = await call(mcp_client, "get_issue", {"issue_key": "ABC-123", "include_comments": False})
    assert "## Comments" not in text_of(result)
    assert not any("/comment" in str(r.url) for r in jira_api.requests)


async def test_get_issue_not_found_is_a_clean_error(mcp_client):
    result = await call(mcp_client, "get_issue", {"issue_key": "ABC-999"})
    assert result.is_error
    assert "Jira API 404" in text_of(result)
    assert "not visible to your account" in text_of(result)


async def test_get_issue_rejects_a_malformed_key(mcp_client, jira_api):
    result = await call(mcp_client, "get_issue", {"issue_key": "login bug"})
    assert result.is_error
    assert "not a Jira issue key" in text_of(result)
    assert jira_api.requests == []


async def test_rate_limit_is_retried_once(mcp_client, jira_api, monkeypatch):
    monkeypatch.setattr("mcp_jira.tools.jira.client.MAX_RETRY_AFTER_S", 0)
    responses = iter([httpx2.Response(429, headers={"Retry-After": "1"}), httpx2.Response(200, json={"issues": []})])
    jira_api.route("GET", "/search/jql", lambda r: next(responses))
    result = await call(mcp_client, "search_issues", {"jql": "project = ABC"})
    assert text_of(result) == "No issues match."


# ── get_attachment ───────────────────────────────────────────────────


async def test_image_attachment_comes_back_viewable_and_saved(mcp_client, settings):
    result = await call(mcp_client, "get_attachment", {"attachment_id": "501"})
    images = [c for c in result.content if c.type == "image"]
    assert len(images) == 1 and images[0].mime_type == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(images[0].data))) as img:
        assert max(img.size) <= 1568
    assert "saved to /host/downloads/501_screen.png" in text_of(result)
    assert (Path(settings.downloads.directory) / "501_screen.png").exists()


async def test_text_attachment_comes_back_as_text(mcp_client, jira_api):
    jira_api.route(
        "GET",
        "/attachment/502",
        lambda r: httpx2.Response(200, json={"id": 502, "filename": "app.log", "mimeType": "text/plain", "size": 12}),
    )
    jira_api.route("GET", "/attachment/content/502", lambda r: httpx2.Response(200, content=b"ERROR boom\n"))
    result = await call(mcp_client, "get_attachment", {"attachment_id": "502"})
    assert "ERROR boom" in text_of(result)


async def test_attachment_id_must_be_numeric(mcp_client):
    result = await call(mcp_client, "get_attachment", {"attachment_id": "../etc"})
    assert result.is_error


# ── search_issues ────────────────────────────────────────────────────


async def test_search_lists_issues_one_per_line(mcp_client, jira_api):
    jira_api.route(
        "GET",
        "/search/jql",
        lambda r: httpx2.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "ABC-1",
                        "fields": {
                            "summary": "One",
                            "status": {"name": "Done"},
                            "issuetype": {"name": "Task"},
                            "assignee": None,
                            "updated": "2026-11-02T10:00:00.000+0000",
                        },
                    }
                ]
            },
        ),
    )
    result = await call(mcp_client, "search_issues", {"jql": "project = ABC", "max_results": 5})
    assert "- ABC-1 [Done] One · Task · Unassigned" in text_of(result)
    assert jira_api.requests[-1].url.params["maxResults"] == "5"


async def test_search_rejects_out_of_range_limits(mcp_client):
    assert (await call(mcp_client, "search_issues", {"jql": "x", "max_results": 500})).is_error


# ── add_comment ──────────────────────────────────────────────────────


async def test_add_comment_posts_adf_and_links_to_it(mcp_client, jira_api):
    jira_api.route("POST", "/issue/ABC-123/comment", lambda r: httpx2.Response(201, json={"id": "777"}))
    body = "PR ready: [#42](https://github.com/acme/app/pull/42)\n\n- fixed SSO\n- added a test"
    result = await call(mcp_client, "add_comment", {"issue_key": "ABC-123", "body": body})
    assert text_of(result) == f"Comment 777 added: {SITE}/browse/ABC-123?focusedCommentId=777"
    sent = jira_api.last_json()["body"]
    assert sent["type"] == "doc"
    link = sent["content"][0]["content"][1]
    assert link["marks"] == [{"type": "link", "attrs": {"href": "https://github.com/acme/app/pull/42"}}]
    assert sent["content"][1]["type"] == "bulletList"


async def test_add_comment_refuses_an_empty_body(mcp_client, jira_api):
    result = await call(mcp_client, "add_comment", {"issue_key": "ABC-123", "body": "   "})
    assert result.is_error
    assert jira_api.requests == []


# ── create_issue ─────────────────────────────────────────────────────


async def test_create_issue_builds_fields(mcp_client, jira_api):
    jira_api.route("POST", "/issue", lambda r: httpx2.Response(201, json={"id": "1", "key": "ABC-124"}))
    result = await call(
        mcp_client,
        "create_issue",
        {
            "project_key": "abc",
            "summary": " Add audit log ",
            "description": "Needed for **SOC2**.",
            "issue_type": "Story",
            "labels": ["audit trail"],
            "parent_key": "ABC-100",
            "assign_to_me": True,
        },
    )
    assert text_of(result) == f"Created ABC-124: {SITE}/browse/ABC-124"
    fields = jira_api.last_json()["fields"]
    assert fields["project"] == {"key": "ABC"}
    assert fields["summary"] == "Add audit log"
    assert fields["issuetype"] == {"name": "Story"}
    assert fields["labels"] == ["audit-trail"]
    assert fields["parent"] == {"key": "ABC-100"}
    assert fields["assignee"] == {"accountId": "acc-1"}
    assert fields["description"]["content"][0]["content"][1]["marks"] == [{"type": "strong"}]


async def test_create_issue_surfaces_field_errors(mcp_client, jira_api):
    jira_api.route(
        "POST",
        "/issue",
        lambda r: httpx2.Response(
            400, json={"errorMessages": [], "errors": {"issuetype": "Specify a valid issue type"}}
        ),
    )
    result = await call(mcp_client, "create_issue", {"project_key": "ABC", "summary": "x", "issue_type": "Epik"})
    assert result.is_error
    assert "issuetype: Specify a valid issue type" in text_of(result)


# ── whoami / no session ──────────────────────────────────────────────


async def test_whoami_names_site_and_account(mcp_client):
    assert text_of(await call(mcp_client, "whoami", {})) == f"{SITE} as Dana Dev (dana@example.com)"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("whoami", {}),
        ("get_issue", {"issue_key": "ABC-1"}),
        ("add_comment", {"issue_key": "ABC-1", "body": "hi"}),
    ],
)
async def test_without_a_session_tools_ask_to_sign_in(mcp_client, stub_container, jira_api, tool, args):
    def no_session():
        raise ToolError("Not signed in to Jira. Authenticate this MCP server in your client.")

    stub_container.caller = no_session
    result = await call(mcp_client, tool, args)
    assert result.is_error
    assert "Not signed in" in text_of(result)
    assert jira_api.requests == []
