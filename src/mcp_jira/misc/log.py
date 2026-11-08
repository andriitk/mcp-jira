import json
import logging
import sys

from loguru import logger


class InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, FastMCP) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        caller = frame.f_back if frame else None
        while caller and caller.f_code.co_filename == logging.__file__:
            caller = caller.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _exclude_healthcheck(record) -> bool:
    return "/health" not in record["message"]


def setup_logging(log_level: str = "INFO", environment: str = "local") -> None:
    logger.remove()
    logging.root.handlers = []
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx2", "fastmcp"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = [InterceptHandler()]
        stdlib_logger.propagate = False
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    level = log_level.upper()
    if environment != "local":

        def json_sink(message) -> None:
            record = message.record
            payload = {
                "timestamp": record["time"].isoformat(),
                "level": record["level"].name,
                "message": record["message"],
                "exception": str(record["exception"]) if record["exception"] else None,
                "location": f"{record['file'].name}:{record['function']}:{record['line']}",
                "env": environment,
            }
            print(json.dumps(payload), file=sys.stderr)

        logger.configure(handlers=[{"sink": json_sink, "level": level, "filter": _exclude_healthcheck}])
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level:<8}</level> | "
            "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        )
        logger.configure(handlers=[{"sink": sys.stdout, "format": fmt, "level": level, "filter": _exclude_healthcheck}])
    logger.info(f"Logging configured (level={level}, env={environment})")
