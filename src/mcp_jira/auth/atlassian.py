"""Per-user Atlassian OAuth 2.0 (3LO) for MCP clients.

Towards MCP clients this server is an OAuth authorization server (dynamic client registration,
PKCE, consent screen); towards Atlassian it is an OAuth client. FastMCP's `OAuthProxy` does the
protocol work, including encrypted state storage and transparent refresh of Atlassian's rotating
refresh tokens. This module adds what is Atlassian-specific: endpoints, the `audience` parameter,
and a token verifier that also resolves the configured Jira site and the caller's account.
"""

from __future__ import annotations

import httpx2
from cryptography.fernet import Fernet
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.token_cache import TokenCache
from loguru import logger

from mcp_jira.settings import Settings
from mcp_jira.tools.jira.client import API_GATEWAY, Caller

AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"  # noqa: S105 - endpoint URL, not a secret
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
JIRA_SCOPES = ("read:jira-work", "write:jira-work", "read:jira-user")
REQUESTED_SCOPES = (*JIRA_SCOPES, "offline_access")
VERIFY_CACHE_TTL_S = 300


class AtlassianTokenVerifier(TokenVerifier):
    """Accept an Atlassian access token only if it grants the Jira scopes on the configured site.

    Atlassian access tokens cannot be verified offline, so verification asks Atlassian which sites
    the token reaches and who the user is there. Successful results are cached briefly.
    """

    def __init__(self, *, site_url: str, http: httpx2.AsyncClient | None = None):
        super().__init__(required_scopes=list(JIRA_SCOPES))
        self._site_url = site_url.lower()
        self._http = http or httpx2.AsyncClient(timeout=10)
        self._cache = TokenCache(ttl_seconds=VERIFY_CACHE_TTL_S, max_size=10_000)

    async def verify_token(self, token: str) -> AccessToken | None:
        hit, cached = self._cache.get(token)
        if hit:
            return cached
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resources = await self._http.get(RESOURCES_URL, headers=headers)
            if resources.status_code != 200:
                return None
            sites = resources.json()
            site = next((s for s in sites if s.get("url", "").rstrip("/").lower() == self._site_url), None)
            if site is None:
                granted = ", ".join(s.get("url", "?") for s in sites) or "none"
                logger.warning(f"Token does not grant {self._site_url} (granted: {granted})")
                return None
            if not set(JIRA_SCOPES) <= set(site.get("scopes", [])):
                logger.warning(f"Token lacks Jira scopes on {self._site_url}: {site.get('scopes')}")
                return None
            me = await self._http.get(f"{API_GATEWAY}/{site['id']}/rest/api/3/myself", headers=headers)
            if me.status_code != 200:
                return None
            user = me.json()
            result = AccessToken(
                token=token,
                client_id=user["accountId"],
                scopes=list(site["scopes"]),
                expires_at=None,
                subject=user["accountId"],
                claims={
                    "cloud_id": site["id"],
                    "site_url": site["url"].rstrip("/"),
                    "account_id": user["accountId"],
                    "name": user.get("displayName"),
                    "email": user.get("emailAddress"),
                },
            )
        except (httpx2.HTTPError, ValueError, KeyError) as exc:
            logger.warning(f"Atlassian token verification failed: {exc!r}")
            return None
        self._cache.set(token, result)
        return result


def current_caller() -> Caller:
    """The signed-in user of the current MCP request, from the verified access token."""
    token = get_access_token()
    if token is None or "cloud_id" not in (token.claims or {}):
        raise ToolError(
            "Not signed in to Jira. Authenticate this MCP server in your client (in Claude Code: /mcp), then retry."
        )
    claims = token.claims
    return Caller(
        token=token.token,
        cloud_id=claims["cloud_id"],
        site_url=claims["site_url"],
        account_id=claims["account_id"],
        name=claims.get("name"),
        email=claims.get("email"),
    )


def _client_storage(settings: Settings):
    """Shared encrypted OAuth state for several replicas; None = FastMCP's encrypted local files."""
    storage = settings.storage
    if not storage.redis_url.get_secret_value():
        return None
    from key_value.aio.stores.redis import RedisStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    return FernetEncryptionWrapper(
        key_value=RedisStore(url=storage.redis_url.get_secret_value()),
        fernet=Fernet(storage.encryption_key.get_secret_value().encode()),
        raise_on_decryption_error=False,
    )


def build_atlassian_provider(settings: Settings) -> OAuthProxy:
    atlassian = settings.atlassian
    return OAuthProxy(
        upstream_authorization_endpoint=AUTHORIZE_URL,
        upstream_token_endpoint=TOKEN_URL,
        upstream_client_id=atlassian.client_id,
        upstream_client_secret=atlassian.client_secret.get_secret_value(),
        token_verifier=AtlassianTokenVerifier(site_url=atlassian.site_url),
        base_url=settings.misc.base_url,
        token_endpoint_auth_method="client_secret_post",  # noqa: S106 - a method name, not a secret
        # Atlassian needs `audience`, and `offline_access` for refresh tokens. PKCE and the RFC 8707
        # `resource` parameter stay between the MCP client and this server.
        extra_authorize_params={
            "audience": "api.atlassian.com",
            "prompt": "consent",
            "scope": " ".join(REQUESTED_SCOPES),
        },
        forward_pkce=False,
        forward_resource=False,
        allowed_client_redirect_uris=atlassian.redirect_patterns,
        client_storage=_client_storage(settings),
        jwt_signing_key=atlassian.jwt_signing_key.get_secret_value() or None,
        require_authorization_consent=True if atlassian.consent == "always" else "remember",
        # Refresh Atlassian's one-hour access token before it expires instead of failing a call first.
        token_expiry_threshold_seconds=VERIFY_CACHE_TTL_S,
    )
