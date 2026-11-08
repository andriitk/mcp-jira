from collections.abc import AsyncIterator

import httpx2
from dependency_injector.containers import DeclarativeContainer
from dependency_injector.providers import Callable, Configuration, Factory, List, Object, Resource, Self, Singleton
from loguru import logger

from mcp_jira.app import build_instructions, create_fastmcp_asgi_app
from mcp_jira.auth.atlassian import build_atlassian_provider, current_caller
from mcp_jira.misc.di import set_container
from mcp_jira.misc.lifespan import Lifespan
from mcp_jira.misc.log import setup_logging
from mcp_jira.settings import Settings, get_settings
from mcp_jira.tools import ALL_PROMPTS, ALL_RESOURCES, ALL_TOOLS

# ---------------------------------------------------------------------------
# Async resource factories (lifecycle managed by Lifespan)
# ---------------------------------------------------------------------------


async def init_http_client(request_timeout: float, connect_retries: int) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.AsyncHTTPTransport(retries=connect_retries)
    client = httpx2.AsyncClient(timeout=httpx2.Timeout(request_timeout, connect=10), transport=transport)
    try:
        yield client
    finally:
        await client.aclose()


def _no_auth_provider() -> None:
    """Placeholder until `bootstrap()` attaches the Atlassian provider."""
    return None


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class MainContainer(DeclarativeContainer):
    __self__ = Self()
    config: Configuration = Configuration()

    # -- Lifespan (init_resources / shutdown_resources) ---------------------
    lifespan: Singleton[Lifespan] = Singleton(Lifespan, __self__)

    # -- Settings, HTTP, caller ----------------------------------------------
    settings: Singleton[Settings] = Singleton(get_settings)
    http_client: Resource[httpx2.AsyncClient] = Resource(
        init_http_client,
        request_timeout=config.http.timeout,
        connect_retries=config.http.connect_retries,
    )
    # Resolved per tool call from the verified OAuth token.
    caller = Callable(current_caller)

    # -- Auth (placeholder; bootstrap() overrides with the real provider) ----
    auth_provider: Factory = Factory(_no_auth_provider)

    # -- MCP app ------------------------------------------------------------
    mcp_app = Factory(
        create_fastmcp_asgi_app,
        title=config.misc.title,
        instructions=Callable(
            build_instructions,
            instance_name=config.misc.instance_name,
            site_url=config.atlassian.site_url,
            agent_hint=config.misc.agent_hint,
        ),
        base_api_path=config.misc.base_api_path,
        public_host=config.misc.public_host,
        auth=auth_provider,
        lifespan=lifespan,
        middlewares=List(),
        tools=List(*ALL_TOOLS),
        resources=List(*ALL_RESOURCES),
        prompts=List(*ALL_PROMPTS),
    )


def _configure_auth(container: MainContainer, settings: Settings) -> None:
    """Attach the Atlassian provider, or fail loudly.

    No credentials at all is a supported local mode: the server boots so `tools/list` and `/health`
    work, and every tool call answers "not signed in". Credentials present but incomplete or invalid
    is a misconfigured deployment and must stop the boot.
    """
    atlassian = settings.atlassian
    if atlassian.is_partially_configured:
        raise RuntimeError("Set all of ATLASSIAN_CLIENT_ID, ATLASSIAN_CLIENT_SECRET and JIRA_SITE_URL, or none")
    if not atlassian.is_configured:
        logger.warning("Atlassian OAuth is not configured: tools will answer 'not signed in'.")
        return
    container.auth_provider.override(Object(build_atlassian_provider(settings)))
    storage = {"file": "encrypted local files", "redis": "Redis (encrypted)", "dynamodb": "DynamoDB (encrypted)"}[
        settings.storage.backend
    ]
    logger.info(f"Per-user Atlassian OAuth enabled for {atlassian.site_url}; OAuth state in {storage}")


def bootstrap() -> MainContainer:
    container = MainContainer()
    settings = get_settings()
    container.config.from_dict(settings.model_dump())
    setup_logging(log_level=settings.logging.log_level, environment=settings.misc.environment)
    # Make the container reachable from FastMCP's Depends() factories.
    set_container(container)
    _configure_auth(container, settings)
    return container
