"""The ASGI app as a deployment sees it: health, OAuth discovery, auth gate, Host protection, config."""

import httpx2
import pytest
from pydantic import ValidationError

from mcp_jira.containers import bootstrap
from mcp_jira.settings import Settings, get_settings

OAUTH_ENV = {
    "ATLASSIAN_CLIENT_ID": "cid",
    "ATLASSIAN_CLIENT_SECRET": "secret",
    "JIRA_SITE_URL": "https://acme.atlassian.net",
    "BASE_URL": "http://localhost:8711",
    "JIRA_INSTANCE_NAME": "acme",
}


@pytest.fixture
def app_with(monkeypatch):
    def build(env: dict):
        for key in (*OAUTH_ENV, "OAUTH_STORAGE", "OAUTH_REDIS_URL", "OAUTH_STORAGE_ENCRYPTION_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return bootstrap().mcp_app()

    yield build
    get_settings.cache_clear()


async def request(app, method: str, path: str, host: str = "localhost:8711", **kwargs) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url=f"http://{host}") as client,
    ):
        return await client.request(method, path, **kwargs)


async def test_health(app_with):
    resp = await request(app_with(OAUTH_ENV), "GET", "/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_mcp_requires_oauth_and_advertises_discovery(app_with):
    app = app_with(OAUTH_ENV)
    resp = await request(
        app,
        "POST",
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 401
    assert "resource_metadata" in resp.headers["www-authenticate"]

    meta = await request(app, "GET", "/.well-known/oauth-authorization-server")
    assert meta.status_code == 200
    assert meta.json()["authorization_endpoint"] == "http://localhost:8711/authorize"
    # Clients register dynamically; URL client IDs (CIMD) are off unless enabled.
    assert meta.json()["registration_endpoint"] == "http://localhost:8711/register"
    assert not meta.json().get("client_id_metadata_document_supported")


async def test_cimd_can_be_enabled(app_with):
    app = app_with({**OAUTH_ENV, "OAUTH_ENABLE_CIMD": "true"})
    meta = await request(app, "GET", "/.well-known/oauth-authorization-server")
    assert meta.json()["client_id_metadata_document_supported"] is True


async def raw_status(app, host: str) -> int:
    """POST /mcp the way uvicorn in a container delivers it: scope["server"] is the socket address.

    (httpx's ASGITransport puts the URL host there, which the guard trusts, so it cannot test this.)
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", host.encode()), (b"content-type", b"application/json")],
        "server": ("172.17.0.2", 8000),
        "client": ("172.17.0.1", 50000),
    }
    messages = iter([{"type": "http.request", "body": b"{}", "more_body": False}])
    status: list[int] = []

    async def receive():
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    async with app.router.lifespan_context(app):
        await app(scope, receive, send)
    return status[0]


async def test_foreign_host_header_is_rejected(app_with):
    assert await raw_status(app_with(OAUTH_ENV), "evil.example:8711") == 421


async def test_localhost_reaches_the_auth_gate(app_with):
    assert await raw_status(app_with(OAUTH_ENV), "localhost:8711") == 401


async def test_public_host_is_accepted_behind_a_proxy(app_with):
    app = app_with({**OAUTH_ENV, "BASE_URL": "https://jira-mcp.example.com"})
    assert await raw_status(app, "jira-mcp.example.com") == 401


async def test_boots_without_credentials_for_local_tool_listing(app_with):
    resp = await request(app_with({}), "GET", "/health")
    assert resp.status_code == 200


async def test_partial_credentials_stop_the_boot(app_with):
    with pytest.raises(RuntimeError, match="or none"):
        app_with({"ATLASSIAN_CLIENT_ID": "cid"})


@pytest.mark.parametrize(
    "storage,error",
    [
        ({"OAUTH_STORAGE": "redis", "OAUTH_REDIS_URL": "redis://r:6379/0"}, "OAUTH_STORAGE_ENCRYPTION_KEY"),
        ({"OAUTH_STORAGE": "dynamodb"}, "OAUTH_STORAGE_ENCRYPTION_KEY"),
        ({"OAUTH_STORAGE": "redis", "OAUTH_STORAGE_ENCRYPTION_KEY": "k"}, "OAUTH_REDIS_URL"),
    ],
)
def test_shared_storage_must_be_complete(storage, error):
    with pytest.raises(ValidationError, match=error):
        Settings.model_validate({"storage": storage})


def test_site_url_is_normalised():
    settings = Settings.model_validate({"atlassian": {"JIRA_SITE_URL": "acme.atlassian.net/"}})
    assert settings.atlassian.site_url == "https://acme.atlassian.net"
