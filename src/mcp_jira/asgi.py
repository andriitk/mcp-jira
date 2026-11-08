from mcp_jira.containers import bootstrap


def build_app():
    """ASGI factory: `uvicorn --factory mcp_jira.asgi:build_app` (or any ASGI host)."""
    container = bootstrap()
    return container.mcp_app()
