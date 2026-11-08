"""Bridge between the `dependency-injector` container and FastMCP's `Depends()`.

The container owns lifecycle (the shared HTTP client is built at startup and closed at shutdown).
FastMCP owns injection: a tool parameter declared as `Depends(factory)` is resolved per request and
stripped from the tool's JSON schema, so MCP clients never see it. FastMCP calls each factory with
no arguments, so the factories read from the process-wide container that `bootstrap()` installs.

Adding a dependency: register it on `MainContainer`, add a `provide_<thing>()` here, declare it in
the tool signature as `thing: Thing = Depends(provide_thing)`.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import httpx2

from mcp_jira.settings import Settings

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from mcp_jira.containers import MainContainer
    from mcp_jira.tools.jira.client import JiraClient

_container: MainContainer | None = None


def set_container(container: MainContainer) -> None:
    """Install the process-wide container. Called once by `bootstrap()`."""
    global _container
    _container = container


def get_container() -> MainContainer:
    if _container is None:
        raise RuntimeError("DI container is not initialised; call bootstrap() before resolving dependencies")
    return _container


async def resolve(provider_name: str) -> Any:
    """Resolve a container provider by name, awaiting async resources."""
    value = getattr(get_container(), provider_name)()
    if inspect.isawaitable(value):
        value = await value
    return value


async def provide_http_client() -> httpx2.AsyncClient:
    return await resolve("http_client")


async def provide_settings() -> Settings:
    return await resolve("settings")


async def provide_jira_client() -> JiraClient:
    """A Jira client acting as the signed-in caller, on the site resolved from their token."""
    from mcp_jira.tools.jira.client import JiraClient  # tools import this module

    return JiraClient(await provide_http_client(), await resolve("caller"))
