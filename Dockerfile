# syntax=docker/dockerfile:1.10

# ---------- 1. Build: resolve the locked environment with uv ----------
FROM python:3.15-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first (cached until the lock changes), then the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --extra cloud

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra cloud


# ---------- 2. Test: the same environment plus dev tools ----------
FROM build AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra cloud

COPY tests ./tests
COPY docker ./docker
ENV PATH="/opt/venv/bin:$PATH"
CMD ["docker/test.sh"]


# ---------- 3. Runtime: no build tools, non-root, read-only friendly ----------
FROM python:3.15-slim-trixie AS runtime

ARG UID=10001
RUN useradd --uid "$UID" --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /downloads \
    && chown app /data /downloads

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTMCP_HOME=/data \
    HOST=0.0.0.0 \
    PORT=8000

USER app
WORKDIR /home/app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}{os.environ.get('BASE_API_PATH', '')}/health\", timeout=2)"]

CMD ["mcp-jira"]
