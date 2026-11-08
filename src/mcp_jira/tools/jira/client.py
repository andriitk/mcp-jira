"""Thin async client for the Jira Cloud REST API v3, bound to one user's token and one site."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx2

API_GATEWAY = "https://api.atlassian.com/ex/jira"
ISSUE_LIST_FIELDS = "summary,status,issuetype,priority,assignee,updated"
MAX_COMMENTS = 500
MAX_RETRY_AFTER_S = 10.0


class JiraError(Exception):
    pass


@dataclass(frozen=True)
class Caller:
    """Who is calling and where: built per request from the verified access token."""

    token: str
    cloud_id: str
    site_url: str
    account_id: str
    name: str | None = None
    email: str | None = None


def _error_text(resp: httpx2.Response) -> str:
    try:
        body = resp.json()
        messages = list(body.get("errorMessages", [])) + [f"{k}: {v}" for k, v in body.get("errors", {}).items()]
        detail = "; ".join(messages) or resp.text[:500]
    except ValueError:
        detail = resp.text[:500]
    hint = {
        401: " (session expired; reconnect the MCP server to log in again)",
        403: " (your Jira account lacks permission)",
        404: " (does not exist, or is not visible to your account)",
    }.get(resp.status_code, "")
    return f"Jira API {resp.status_code}{hint}: {detail}"


def _retry_after(resp: httpx2.Response) -> float:
    """One bounded wait on 429; a longer limit surfaces as an error instead of a hung tool call."""
    try:
        return min(float(resp.headers.get("Retry-After", "2")), MAX_RETRY_AFTER_S)
    except ValueError:
        return 2.0


class JiraClient:
    def __init__(self, http: httpx2.AsyncClient, caller: Caller):
        self._http = http
        self.caller = caller
        self._base = f"{API_GATEWAY}/{caller.cloud_id}"
        self._auth = {"Authorization": f"Bearer {caller.token}"}

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {**self._auth, "Accept": "application/json"}
        resp = await self._http.request(method, f"{self._base}{path}", headers=headers, **kwargs)
        if resp.status_code == 429:
            await asyncio.sleep(_retry_after(resp))
            resp = await self._http.request(method, f"{self._base}{path}", headers=headers, **kwargs)
        if resp.is_error:
            raise JiraError(_error_text(resp))
        return resp.json() if resp.content else None

    # -- reads --------------------------------------------------------------------------------------
    async def myself(self) -> dict:
        return await self.request("GET", "/rest/api/3/myself")

    async def get_issue(self, key: str) -> dict:
        return await self.request("GET", f"/rest/api/3/issue/{key}", params={"fields": "*all", "expand": "names"})

    async def get_comments(self, key: str) -> list[dict]:
        comments: list[dict] = []
        while len(comments) < MAX_COMMENTS:
            page = await self.request(
                "GET",
                f"/rest/api/3/issue/{key}/comment",
                params={"startAt": len(comments), "maxResults": 100, "orderBy": "created"},
            )
            batch = page.get("comments", [])
            comments.extend(batch)
            if not batch or len(comments) >= page.get("total", 0):
                break
        return comments

    async def search(self, jql: str, limit: int) -> list[dict]:
        page = await self.request(
            "GET", "/rest/api/3/search/jql", params={"jql": jql, "maxResults": limit, "fields": ISSUE_LIST_FIELDS}
        )
        return page.get("issues", [])

    async def attachment_meta(self, attachment_id: str) -> dict:
        return await self.request("GET", f"/rest/api/3/attachment/{attachment_id}")

    async def download_attachment(self, attachment_id: str, max_bytes: int) -> bytes:
        url = f"{self._base}/rest/api/3/attachment/content/{attachment_id}"
        async with self._http.stream(
            "GET", url, params={"redirect": "false"}, headers={**self._auth, "Accept": "*/*"}
        ) as resp:
            if resp.is_error:
                await resp.aread()
                raise JiraError(_error_text(resp))
            chunks, total = [], 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise JiraError(f"Attachment is larger than {max_bytes // 1_000_000} MB; open it in Jira.")
                chunks.append(chunk)
        return b"".join(chunks)

    # -- writes (no update or delete, by design) ------------------------------------------------------
    async def add_comment(self, key: str, body_adf: dict) -> dict:
        return await self.request("POST", f"/rest/api/3/issue/{key}/comment", json={"body": body_adf})

    async def create_issue(self, fields: dict) -> dict:
        return await self.request("POST", "/rest/api/3/issue", json={"fields": fields})
