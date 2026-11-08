"""FastMCP server and ASGI application factories."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.prompts import Prompt
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from loguru import logger
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from mcp_jira import __version__


def build_instructions(instance_name: str, site_url: str, agent_hint: str = "") -> str:
    """Server-wide facts for the model; per-tool rules live in the tool definitions."""
    site = site_url or "a Jira Cloud site"
    text = (
        f"Jira of '{instance_name}' ({site}). Use it only for work that belongs to '{instance_name}'; "
        "other Jira servers may be connected for other organisations. Every call acts as the signed-in "
        "user. Usual flow: get_issue, then get_attachment for screenshots or videos it lists, then "
        "add_comment to report back (for example with a pull-request link)."
    )
    return f"{text} {agent_hint}".strip()


def create_fastmcp_server(
    title: str,
    instructions: str,
    auth: AuthProvider | None = None,
    lifespan: Callable | None = None,
    tools: Sequence[Tool | Callable[..., Any]] | None = None,
    middlewares: Sequence[Middleware | None] | None = None,
    resources: Sequence[Resource | ResourceTemplate] | None = None,
    prompts: Sequence[Prompt] | None = None,
) -> FastMCP:
    """Build the FastMCP server. Resources and prompts are component objects, not bare functions."""
    logger.info(f"Creating FastMCP server: {title}")
    mcp = FastMCP(
        name=title,
        version=__version__,
        instructions=instructions,
        auth=auth,
        lifespan=lifespan,
        tools=list(tools or []),
        middleware=[mw for mw in (middlewares or []) if mw is not None],
    )
    for resource in resources or []:
        mcp.add_resource(resource)
    for prompt in prompts or []:
        mcp.add_prompt(prompt)
    return mcp


def create_fastmcp_asgi_app(
    title: str,
    instructions: str,
    base_api_path: str,
    public_host: str,
    lifespan: Callable | None = None,
    auth: AuthProvider | None = None,
    tools: Sequence[Tool | Callable[..., Any]] | None = None,
    middlewares: Sequence[Middleware | None] | None = None,
    resources: Sequence[Resource | ResourceTemplate] | None = None,
    prompts: Sequence[Prompt] | None = None,
) -> ASGIApp:
    mcp = create_fastmcp_server(
        title=title,
        instructions=instructions,
        auth=auth,
        lifespan=lifespan,
        tools=tools,
        middlewares=middlewares,
        resources=resources,
        prompts=prompts,
    )
    base = base_api_path.rstrip("/")
    health_path, mcp_path = f"{base}/health", f"{base}/mcp"

    @mcp.custom_route(health_path, methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "timestamp": datetime.now(UTC).isoformat(),
                "server": title,
                "version": __version__,
                "mcp_endpoint": mcp_path,
            }
        )

    logger.info(f"MCP endpoint: {mcp_path} | health: {health_path} | public host: {public_host}")
    return mcp.http_app(
        path=mcp_path,
        transport="http",
        stateless_http=True,
        # Reject requests whose Host/Origin is not this server: blocks DNS rebinding from web pages
        # on a laptop and host-header tricks behind a proxy. localhost is always allowed.
        host_origin_protection=True,
        allowed_hosts=[public_host],
    )
