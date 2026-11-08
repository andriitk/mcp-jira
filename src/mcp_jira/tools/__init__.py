"""The single registry of everything this server exposes.

`containers.py` registers exactly these, and `tests/test_mcp.py` snapshots their schemas and
annotations, so adding a tool forces an explicit decision about whether it may write.
Tools are registered as `Tool` objects to pin annotations and to return plain content (Markdown or
images) without a duplicated structured payload.
"""

from fastmcp.tools import Tool
from mcp_types import ToolAnnotations

from .jira.tools import add_comment, create_issue, get_attachment, get_issue, search_issues, whoami

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

ALL_TOOLS = [
    Tool.from_function(whoami, annotations=READ, output_schema=None),
    Tool.from_function(get_issue, annotations=READ, output_schema=None),
    Tool.from_function(get_attachment, annotations=READ, output_schema=None),
    Tool.from_function(search_issues, annotations=READ, output_schema=None),
    Tool.from_function(add_comment, annotations=WRITE, output_schema=None),
    Tool.from_function(create_issue, annotations=WRITE, output_schema=None),
]

ALL_RESOURCES: list = []
ALL_PROMPTS: list = []
