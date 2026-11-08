"""Server contract tests: what an MCP client actually sees.

The schema snapshot is the load-bearing test. Tool schemas are the contract MCP clients depend on
and the first thing to shift under a FastMCP or Pydantic upgrade. Regenerate deliberately and read
the diff: pytest tests/test_mcp.py --inline-snapshot=fix
"""

from inline_snapshot import snapshot

from mcp_jira.tools import ALL_TOOLS


async def test_registry_is_exactly_what_clients_see(mcp_client):
    names = sorted(tool.name for tool in await mcp_client.list_tools())
    assert names == sorted(tool.name for tool in ALL_TOOLS)


async def test_instructions_name_the_instance_and_its_scope(mcp_server):
    assert "'Acme'" in mcp_server.instructions
    assert "https://acme.atlassian.net" in mcp_server.instructions
    assert "Repos under ~/work/acme." in mcp_server.instructions


async def test_write_access_is_explicit(mcp_client):
    """Adding a tool forces a decision: does it write? Nothing may be destructive."""
    tools = await mcp_client.list_tools()
    assert {t.name: t.annotations.read_only_hint for t in tools} == snapshot(
        {
            "whoami": True,
            "get_issue": True,
            "get_attachment": True,
            "search_issues": True,
            "add_comment": False,
            "create_issue": False,
        }
    )
    assert not any(t.annotations.destructive_hint for t in tools)


async def test_injected_dependencies_never_reach_the_schema(mcp_client):
    for tool in await mcp_client.list_tools():
        assert not {"jira", "settings", "http_client"} & set(tool.input_schema["properties"])
        assert tool.output_schema is None


async def test_tool_schemas_match_snapshot(mcp_client):
    tools = sorted(await mcp_client.list_tools(), key=lambda t: t.name)
    assert {t.name: t.input_schema for t in tools} == snapshot(
        {
            "add_comment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "issue_key": {
                        "description": "Issue key such as 'ABC-123' (a browse URL works too).",
                        "maxLength": 300,
                        "minLength": 3,
                        "type": "string",
                    },
                    "body": {"description": "Markdown text.", "maxLength": 30000, "minLength": 1, "type": "string"},
                },
                "required": ["issue_key", "body"],
            },
            "create_issue": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "project_key": {
                        "description": "Project key, e.g. 'ABC'.",
                        "maxLength": 20,
                        "pattern": "^[A-Za-z][A-Za-z0-9_]+$",
                        "type": "string",
                    },
                    "summary": {"description": "One-line title.", "maxLength": 255, "minLength": 1, "type": "string"},
                    "description": {
                        "default": "",
                        "description": "Markdown text.",
                        "maxLength": 30000,
                        "type": "string",
                    },
                    "issue_type": {
                        "default": "Task",
                        "description": "Issue type name as shown in Jira: Task, Bug, Story, ...",
                        "maxLength": 60,
                        "type": "string",
                    },
                    "labels": {
                        "anyOf": [{"items": {"type": "string"}, "maxItems": 20, "type": "array"}, {"type": "null"}],
                        "default": None,
                        "description": "Labels; spaces become dashes.",
                    },
                    "priority": {
                        "anyOf": [{"maxLength": 60, "type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "Priority name as shown in Jira, e.g. High.",
                    },
                    "parent_key": {
                        "anyOf": [{"maxLength": 300, "type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "Parent issue key, for a sub-task or an epic child.",
                    },
                    "assign_to_me": {
                        "default": False,
                        "description": "Assign the new issue to the signed-in user.",
                        "type": "boolean",
                    },
                },
                "required": ["project_key", "summary"],
            },
            "get_attachment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "attachment_id": {
                        "description": "Numeric attachment id from get_issue's attachment list.",
                        "maxLength": 20,
                        "pattern": "^\\d+$",
                        "type": "string",
                    },
                    "max_frames": {
                        "default": 6,
                        "description": "Frames to sample from a video or an animated GIF.",
                        "maximum": 12,
                        "minimum": 1,
                        "type": "integer",
                    },
                },
                "required": ["attachment_id"],
            },
            "get_issue": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "issue_key": {
                        "description": "Issue key such as 'ABC-123' (a browse URL works too).",
                        "maxLength": 300,
                        "minLength": 3,
                        "type": "string",
                    },
                    "include_comments": {
                        "default": True,
                        "description": "Also return all comments, oldest first.",
                        "type": "boolean",
                    },
                },
                "required": ["issue_key"],
            },
            "search_issues": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "jql": {"description": "JQL query.", "maxLength": 2000, "minLength": 1, "type": "string"},
                    "max_results": {
                        "default": 20,
                        "description": "Number of issues to return.",
                        "maximum": 50,
                        "minimum": 1,
                        "type": "integer",
                    },
                },
                "required": ["jql"],
            },
            "whoami": {"type": "object", "additionalProperties": False, "properties": {}},
        }
    )
