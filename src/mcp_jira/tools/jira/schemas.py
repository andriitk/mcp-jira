"""Input types for the Jira tools. Each `Field` is the single home of its parameter's rules."""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

_ISSUE_KEY = re.compile(r"([A-Z][A-Z0-9_]+-\d+)")


def _issue_key(value: str) -> str:
    """Accept 'ABC-123', 'abc-123' or a browse URL; return 'ABC-123'."""
    match = _ISSUE_KEY.search(value.strip().upper())
    if not match:
        raise ValueError(f"not a Jira issue key: {value!r}; expected something like ABC-123")
    return match.group(1)


IssueKey = Annotated[
    str,
    Field(description="Issue key such as 'ABC-123' (a browse URL works too).", min_length=3, max_length=300),
    AfterValidator(_issue_key),
]
OptionalIssueKey = Annotated[
    str | None,
    Field(description="Parent issue key, for a sub-task or an epic child.", max_length=300),
    AfterValidator(lambda v: None if v is None else _issue_key(v)),
]
Markdown = Annotated[str, Field(description="Markdown text.", max_length=30_000)]
NonEmptyMarkdown = Annotated[str, Field(description="Markdown text.", min_length=1, max_length=30_000)]
AttachmentId = Annotated[
    str, Field(description="Numeric attachment id from get_issue's attachment list.", pattern=r"^\d+$", max_length=20)
]
MaxFrames = Annotated[int, Field(description="Frames to sample from a video or an animated GIF.", ge=1, le=12)]
IncludeComments = Annotated[bool, Field(description="Also return all comments, oldest first.")]
Jql = Annotated[str, Field(description="JQL query.", min_length=1, max_length=2000)]
MaxResults = Annotated[int, Field(description="Number of issues to return.", ge=1, le=50)]
ProjectKey = Annotated[
    str, Field(description="Project key, e.g. 'ABC'.", pattern=r"^[A-Za-z][A-Za-z0-9_]+$", max_length=20)
]
Summary = Annotated[str, Field(description="One-line title.", min_length=1, max_length=255)]
IssueType = Annotated[str, Field(description="Issue type name as shown in Jira: Task, Bug, Story, ...", max_length=60)]
Labels = Annotated[list[str] | None, Field(description="Labels; spaces become dashes.", max_length=20)]
Priority = Annotated[str | None, Field(description="Priority name as shown in Jira, e.g. High.", max_length=60)]
AssignToMe = Annotated[bool, Field(description="Assign the new issue to the signed-in user.")]
