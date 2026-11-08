"""Shared fixtures.

`stub_container` replaces the DI container, so tools run end-to-end through a real in-memory MCP
`Client` against a fake Jira (an `httpx2.MockTransport` with documented response shapes), with no
network and no OAuth. `jira_api` lets a test add or override routes and inspect sent requests.
"""

import io
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import fastmcp
import httpx2
import pytest
from fastmcp import Client
from PIL import Image

from mcp_jira.app import build_instructions, create_fastmcp_server
from mcp_jira.misc import di
from mcp_jira.settings import Settings
from mcp_jira.tools import ALL_TOOLS
from mcp_jira.tools.jira.client import API_GATEWAY, Caller

SITE = "https://acme.atlassian.net"
CLOUD_ID = "11111111-2222-3333-4444-555555555555"
BASE = f"{API_GATEWAY}/{CLOUD_ID}/rest/api/3"

Handler = Callable[[httpx2.Request], httpx2.Response]


def adf(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def png_bytes(size: tuple[int, int] = (3000, 1500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 128)).save(buf, format="PNG")
    return buf.getvalue()


ISSUE = {
    "id": "10001",
    "key": "ABC-123",
    "names": {"customfield_10010": "Acceptance criteria", "customfield_10019": "Rank", "customfield_10020": "Sprint"},
    "fields": {
        "summary": "Login fails with SSO",
        "project": {"key": "ABC", "name": "Alpha"},
        "issuetype": {"name": "Bug"},
        "status": {"name": "To Do"},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Dana Dev"},
        "reporter": {"displayName": "Quinn QA"},
        "labels": ["auth"],
        "created": "2026-11-01T10:00:00.000+0000",
        "updated": "2026-11-02T11:30:00.000+0000",
        "description": adf("Steps: open the app, click SSO."),
        "customfield_10010": adf("User lands on the dashboard."),
        "customfield_10019": "0|i0001:",
        "customfield_10020": [{"id": 7, "name": "Sprint 42", "state": "active"}],
        "attachment": [
            {
                "id": "501",
                "filename": "screen.png",
                "mimeType": "image/png",
                "size": 2048,
                "author": {"displayName": "Quinn QA"},
                "created": "2026-11-01T10:05:00.000+0000",
            },
        ],
        "subtasks": [],
        "issuelinks": [],
    },
}

COMMENTS = {
    "startAt": 0,
    "maxResults": 100,
    "total": 1,
    "comments": [
        {
            "id": "9001",
            "author": {"displayName": "Quinn QA"},
            "created": "2026-11-01T12:00:00.000+0000",
            "body": adf("Still happens on staging."),
        }
    ],
}


class FakeJira:
    """Routes keyed by (method, path); records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.routes: dict[tuple[str, str], Handler] = {
            ("GET", f"{BASE}/issue/ABC-123"): lambda r: httpx2.Response(200, json=ISSUE),
            ("GET", f"{BASE}/issue/ABC-123/comment"): lambda r: httpx2.Response(200, json=COMMENTS),
            ("GET", f"{BASE}/attachment/501"): lambda r: httpx2.Response(
                200, json={"id": 501, "filename": "screen.png", "mimeType": "image/png", "size": 2048}
            ),
            ("GET", f"{BASE}/attachment/content/501"): lambda r: httpx2.Response(200, content=png_bytes()),
        }

    def route(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method, f"{BASE}{path}")] = handler

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        handler = self.routes.get((request.method, url))
        if handler is None:
            return httpx2.Response(404, json={"errorMessages": ["Issue does not exist or you do not have permission"]})
        return handler(request)

    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def jira_api() -> FakeJira:
    return FakeJira()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {"downloads": {"DOWNLOADS_DIR": str(tmp_path), "HOST_DOWNLOADS_DIR": "/host/downloads"}}
    )


@pytest.fixture
def caller() -> Caller:
    return Caller(
        token="t0k", cloud_id=CLOUD_ID, site_url=SITE, account_id="acc-1", name="Dana Dev", email="dana@example.com"
    )


@pytest.fixture(autouse=True)
def fastmcp_home(tmp_path: Path, monkeypatch) -> None:
    """OAuth state written by FastMCP during a test goes to a temp dir, never the user's home."""
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path / "fastmcp")


@pytest.fixture(autouse=True)
def stub_container(jira_api: FakeJira, settings: Settings, caller: Caller) -> Iterator[SimpleNamespace]:
    """Install a container stand-in so no test can reach a real network or OAuth."""
    previous = di._container
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(jira_api))
    stub = SimpleNamespace(http_client=lambda: http, settings=lambda: settings, caller=lambda: caller)
    di.set_container(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        di._container = previous


@pytest.fixture
def mcp_server():
    return create_fastmcp_server(
        title="mcp-jira-test",
        instructions=build_instructions("Acme", SITE, "Repos under ~/work/acme."),
        tools=ALL_TOOLS,
    )


@pytest.fixture
async def mcp_client(mcp_server):
    async with Client(mcp_server) as client:
        yield client
