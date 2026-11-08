"""Settings, read from the environment (and `.env`) once per process via `get_settings()`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings as _BaseSettings


class BaseSettings(_BaseSettings, env_file=".env", env_file_encoding="utf-8", extra="ignore"):
    """Dotenv-aware settings."""


def _normalize_url(value: str) -> str:
    """'acme.atlassian.net/' -> 'https://acme.atlassian.net'; empty stays empty."""
    value = value.strip()
    if not value:
        return ""
    url = value if "://" in value else f"https://{value}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not a URL: {value!r}")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


class MiscSettings(BaseSettings):
    environment: str = Field(alias="ENVIRONMENT", default="local")
    """'local' = human-readable logs; anything else = JSON logs."""
    title: str = "mcp-jira"
    base_api_path: str = Field(alias="BASE_API_PATH", default="")
    """Mount prefix, e.g. '/api/jira' behind a shared ingress. Empty = /mcp and /health at the root."""
    base_url: str = Field(alias="BASE_URL", default="http://localhost:8000")
    """Public URL that MCP clients and browsers use to reach this server (OAuth redirects use it)."""
    instance_name: str = Field(alias="JIRA_INSTANCE_NAME", default="Jira", min_length=1, max_length=60)
    """Label the agent sees, e.g. the organisation that owns the Jira site."""
    agent_hint: str = Field(alias="JIRA_AGENT_HINT", default="", max_length=1000)
    """Appended to the server instructions: when the agent should pick this instance."""

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _normalize_url(value)

    @computed_field(return_type=str)
    @property
    def public_host(self) -> str:
        return urlparse(self.base_url).hostname or "localhost"


class LoggingSettings(BaseSettings):
    log_level: str = Field(alias="LOG_LEVEL", default="info")


class ApiSettings(BaseSettings):
    host: str = Field(alias="HOST", default="0.0.0.0")  # noqa: S104 - container port is published by the operator
    port: int = Field(alias="PORT", default=8000, ge=1, le=65535)
    keep_alive_seconds: int = 10


class HTTPClientSettings(BaseSettings):
    timeout: float = 60.0
    connect_retries: int = 2


class AtlassianSettings(BaseSettings):
    """Atlassian OAuth 2.0 (3LO) app and the one Jira Cloud site this instance serves."""

    client_id: str = Field(alias="ATLASSIAN_CLIENT_ID", default="")
    client_secret: SecretStr = Field(alias="ATLASSIAN_CLIENT_SECRET", default=SecretStr(""))
    site_url: str = Field(alias="JIRA_SITE_URL", default="")
    """https://<your-site>.atlassian.net"""
    consent: Literal["always", "remember"] = Field(alias="OAUTH_CONSENT", default="always")
    """Show this server's consent screen on every client authorization (safest), or remember approved
    clients."""
    allowed_client_redirect_uris: str = Field(alias="ALLOWED_CLIENT_REDIRECT_URIS", default="")
    """Comma-separated patterns for MCP client callbacks, e.g. 'http://localhost:*,https://claude.ai/*'.
    Empty = any (fine on localhost; set it for a shared deployment)."""
    jwt_signing_key: SecretStr = Field(alias="JWT_SIGNING_KEY", default=SecretStr(""))
    """Signs tokens issued to MCP clients. Empty = derived from the client secret."""
    enable_cimd: bool = Field(alias="OAUTH_ENABLE_CIMD", default=False)
    """Accept URL client IDs (Client ID Metadata Documents). Off by default: the server must then fetch
    the client's metadata from the internet (e.g. claude.ai), which bot protection may block. With it
    off, clients use dynamic client registration, which every MCP client supports."""

    @field_validator("site_url")
    @classmethod
    def _site_url(cls, value: str) -> str:
        return _normalize_url(value)

    @computed_field(return_type=bool)
    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret.get_secret_value() and self.site_url)

    @computed_field(return_type=bool)
    @property
    def is_partially_configured(self) -> bool:
        provided = [self.client_id, self.client_secret.get_secret_value(), self.site_url]
        return any(provided) and not all(provided)

    @computed_field(return_type=list[str] | None)
    @property
    def redirect_patterns(self) -> list[str] | None:
        patterns = [p.strip() for p in self.allowed_client_redirect_uris.split(",") if p.strip()]
        return patterns or None


class StorageSettings(BaseSettings):
    """Where OAuth state lives: client registrations and the users' Atlassian tokens.

    - file: encrypted files under FASTMCP_HOME. One container with a persistent volume (local use, one VM).
    - redis / dynamodb: shared state for several replicas or serverless; every value is encrypted with
      OAUTH_STORAGE_ENCRYPTION_KEY before it leaves the process.
    """

    backend: Literal["file", "redis", "dynamodb"] = Field(alias="OAUTH_STORAGE", default="file")
    encryption_key: SecretStr = Field(alias="OAUTH_STORAGE_ENCRYPTION_KEY", default=SecretStr(""))
    """Fernet key, required for redis and dynamodb. Generate one with:
    python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"""
    redis_url: SecretStr = Field(alias="OAUTH_REDIS_URL", default=SecretStr(""))
    """redis://host:6379/0 or rediss:// for TLS."""
    dynamodb_table: str = Field(alias="OAUTH_DYNAMODB_TABLE", default="mcp-jira-oauth")
    """Created on first use if missing (needs dynamodb:CreateTable); otherwise create it beforehand."""
    dynamodb_region: str = Field(alias="OAUTH_DYNAMODB_REGION", default="")
    """Empty = the AWS SDK default (AWS_REGION, instance metadata, ...)."""
    dynamodb_endpoint_url: str = Field(alias="OAUTH_DYNAMODB_ENDPOINT_URL", default="")
    """Only for DynamoDB Local or another compatible endpoint."""

    @model_validator(mode="after")
    def _shared_backends_are_complete(self) -> Self:
        if self.backend == "file":
            return self
        if not self.encryption_key.get_secret_value():
            raise ValueError(f"OAUTH_STORAGE_ENCRYPTION_KEY is required for OAUTH_STORAGE={self.backend}")
        if self.backend == "redis" and not self.redis_url.get_secret_value():
            raise ValueError("OAUTH_REDIS_URL is required for OAUTH_STORAGE=redis")
        return self


class DownloadsSettings(BaseSettings):
    """Attachments are saved only when the agent shares a filesystem with the server (local use)."""

    directory: Path | None = Field(alias="DOWNLOADS_DIR", default=None)
    host_directory: str = Field(alias="HOST_DOWNLOADS_DIR", default="")
    """DOWNLOADS_DIR as the agent sees it, i.e. the host side of the container mount."""


class Settings(BaseSettings):
    misc: MiscSettings = Field(default_factory=MiscSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    http: HTTPClientSettings = Field(default_factory=HTTPClientSettings)
    atlassian: AtlassianSettings = Field(default_factory=AtlassianSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    downloads: DownloadsSettings = Field(default_factory=DownloadsSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton; importing this module does not read the environment."""
    return Settings()
