"""Atlassian token verification and the OAuth wiring, against a fake Atlassian."""

from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from cryptography.fernet import Fernet
from fastmcp.exceptions import ToolError
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from mcp_jira.auth.atlassian import (
    RESOURCES_URL,
    AtlassianTokenVerifier,
    build_atlassian_provider,
    build_client_storage,
    current_caller,
)
from mcp_jira.settings import Settings
from tests.conftest import CLOUD_ID, SITE

JIRA = ["read:jira-work", "write:jira-work", "read:jira-user"]


def fake_atlassian(sites: list[dict], counter: list | None = None) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if counter is not None:
            counter.append(str(request.url))
        if request.headers["Authorization"] != "Bearer good":
            return httpx2.Response(401)
        if str(request.url) == RESOURCES_URL:
            return httpx2.Response(200, json=sites)
        if str(request.url).endswith("/rest/api/3/myself"):
            return httpx2.Response(
                200, json={"accountId": "acc-1", "displayName": "Dana Dev", "emailAddress": "dana@example.com"}
            )
        return httpx2.Response(404)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


SITE_ENTRY = {"id": CLOUD_ID, "url": SITE, "name": "acme", "scopes": JIRA}


async def test_valid_token_resolves_site_and_account():
    verifier = AtlassianTokenVerifier(site_url=SITE, http=fake_atlassian([SITE_ENTRY]))
    token = await verifier.verify_token("good")
    assert token is not None
    assert token.subject == "acc-1"
    assert token.claims["cloud_id"] == CLOUD_ID
    assert token.claims["site_url"] == SITE


async def test_successful_verification_is_cached():
    calls: list = []
    verifier = AtlassianTokenVerifier(site_url=SITE, http=fake_atlassian([SITE_ENTRY], calls))
    await verifier.verify_token("good")
    await verifier.verify_token("good")
    assert len(calls) == 2  # resources + myself, once


@pytest.mark.parametrize(
    "token,sites",
    [
        ("bad", [SITE_ENTRY]),  # Atlassian rejects the token
        ("good", [{**SITE_ENTRY, "url": "https://other.atlassian.net"}]),  # token is for another site
        ("good", [{**SITE_ENTRY, "scopes": ["read:jira-work"]}]),  # missing write scope
    ],
)
async def test_token_is_rejected(token, sites):
    verifier = AtlassianTokenVerifier(site_url=SITE, http=fake_atlassian(sites))
    assert await verifier.verify_token(token) is None


def test_no_access_token_means_not_signed_in():
    with pytest.raises(ToolError, match="Not signed in"):
        current_caller()


def configured(**extra) -> Settings:
    return Settings.model_validate(
        {
            "misc": {"BASE_URL": "http://localhost:8711"},
            "atlassian": {
                "ATLASSIAN_CLIENT_ID": "cid",
                "ATLASSIAN_CLIENT_SECRET": "secret",
                "JIRA_SITE_URL": "acme.atlassian.net/",
                **extra,
            },
        }
    )


async def test_provider_sends_atlassian_specific_authorize_parameters():
    provider = build_atlassian_provider(configured())
    url = provider._build_upstream_authorize_url("txn", {"scopes": ["read:jira-work"]})
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://auth.atlassian.com/authorize?")
    assert query["audience"] == ["api.atlassian.com"]
    assert query["redirect_uri"] == ["http://localhost:8711/auth/callback"]
    assert set(query["scope"][0].split()) == {*JIRA, "offline_access"}
    assert "code_challenge" not in query  # PKCE stays between the MCP client and this server


def test_file_storage_is_fastmcps_default():
    assert build_client_storage(configured()) is None


@pytest.mark.parametrize(
    "storage,store_class",
    [
        ({"OAUTH_STORAGE": "redis", "OAUTH_REDIS_URL": "redis://r:6379/0"}, "RedisStore"),
        (
            {"OAUTH_STORAGE": "dynamodb", "OAUTH_DYNAMODB_TABLE": "t", "OAUTH_DYNAMODB_REGION": "eu-west-1"},
            "DynamoDBStore",
        ),
    ],
)
def test_shared_storage_is_always_encrypted(storage, store_class):
    key = Fernet.generate_key().decode()
    settings = Settings.model_validate({"storage": {**storage, "OAUTH_STORAGE_ENCRYPTION_KEY": key}})
    wrapper = build_client_storage(settings)
    assert isinstance(wrapper, FernetEncryptionWrapper)
    assert type(wrapper.key_value).__name__ == store_class
