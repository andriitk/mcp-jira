import uvicorn
from loguru import logger

from mcp_jira.settings import get_settings


def run() -> None:
    config = get_settings()
    logger.info(f"Starting mcp-jira (env={config.misc.environment})")
    uvicorn.run(
        "mcp_jira.asgi:build_app",
        factory=True,
        host=config.api.host,
        port=config.api.port,
        log_config=None,
        log_level=config.logging.log_level,
        timeout_keep_alive=config.api.keep_alive_seconds,
        # Behind a TLS-terminating proxy, trust X-Forwarded-* only from FORWARDED_ALLOW_IPS (uvicorn env).
        proxy_headers=True,
    )


if __name__ == "__main__":
    run()
