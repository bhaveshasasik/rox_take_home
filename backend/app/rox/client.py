"""Thin async client for the Rox API.

Response shapes are verified against the live API (2026-07-27); the tolerant
extraction helpers at the bottom absorb the envelope differences documented in
`.claude/skills/rox-api/SKILL.md`.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger(__name__)


class RoxError(RuntimeError):
    """Non-retryable Rox API failure."""


class RoxTransientError(RoxError):
    """Retryable failure (5xx, timeout, connection reset)."""


class RoxClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.rox_base_url).rstrip("/")
        self.token = token or settings.rox_api_token
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> RoxClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying httpx client, created on first use.

        `async with` is still preferred (it closes the connection pool), but
        lazily creating here lets the client be constructed and used in places
        that aren't structured around a context manager — e.g. a prospecting
        provider built inside a background task.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type(RoxTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        try:
            resp = await self.client.request(method, path, json=json, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RoxTransientError(f"{method} {path}: {exc}") from exc

        if resp.status_code >= 500:
            raise RoxTransientError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
        if resp.status_code == 429:
            raise RoxTransientError(f"{method} {path} -> rate limited")
        if resp.status_code >= 400:
            raise RoxError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ------------------------------------------------------------------
    # Endpoints 
    # ------------------------------------------------------------------

    async def get_me(self) -> Any:
        """Current user from the auth token."""
        return await self._request("GET", "/user/me")

    async def get_customer_hierarchy(self) -> Any:
        """All accounts, organized as a hierarchy tree."""
        return await self._request("GET", "/hierarchy/customers")

    async def list_account_research_sections(self) -> Any:
        """Metadata for every account research section in the org."""
        return await self._request(
            "GET", "/account_research/account_research_section"
        )

    async def refresh_by_tab(self, column_id: str, tab_id: str) -> Any:
        """Trigger regeneration of a column's cells for every account in a tab.

        Pass `rox_org_id` as `tab_id` to cover all accounts.

        The 201 is NOT confirmation: this returns "Successfully triggered
        refresh" for any UUID, including all-zeros and columns where nothing
        happens. Verify via `list_priority_jobs`.
        """
        return await self._request(
            "POST", f"/research/clever_column/{column_id}/refresh_by_tab/{tab_id}", json={}
        )

    async def list_priority_jobs(self, **params: Any) -> list[dict]:
        """The async task queue behind research.

        Params: task_type, task_state, rox_company_id, num_lookback_days,
        num_lookback_hours.
        """
        data = await self._request(
            "GET", "/priority_jobs", params={k: v for k, v in params.items() if v is not None}
        )
        payload = unwrap(data)
        return [j for j in payload if isinstance(j, dict)] if isinstance(payload, list) else []

    async def get_customers_paginated(self, column_id: str) -> list[dict]:
        """Bulk-read every account's value for one column.

        Replaces N per-entity cell calls with one request. Despite the name,
        no pagination params are honoured — it returns every account.
        """
        data = await self._request("GET", f"/agents/customers_paginated/{column_id}")
        payload = unwrap(data)
        return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []

    async def list_people(self, rox_company_id: str) -> list[dict]:
        """Contacts for one account. 422 without rox_company_id."""
        data = await self._request(
            "GET", "/people", params={"rox_company_id": rox_company_id}
        )
        if isinstance(data, dict):
            rows = data.get("data")
            return [p for p in rows if isinstance(p, dict)] if isinstance(rows, list) else []
        return []

    async def create_sequence(self, payload: dict) -> Any:
        """Create a DRAFT sequence. One sequence targets one person."""
        return await self._request("POST", "/sequences", json=payload)

    async def get_priorities(self) -> list[dict]:
        """LOW/MED/HIGH definitions. Easiest source of rox_org_id / rox_user_id,
        which /user/me does not return."""
        data = await self._request("GET", "/priorities")
        payload = unwrap(data)
        return [p for p in payload if isinstance(p, dict)] if isinstance(payload, list) else []

    async def discover_org_id(self) -> str | None:
        """rox_org_id, used as the `tab_id` that means 'all accounts'.

        /priorities returns it underscore-separated
        (`b2a7ec35_8d8e_4e35_9355_8c29a5220a3f`) but refresh_by_tab was verified
        with the dashed form, so normalize. (Rox's own UUID pattern accepts
        `[-_]`, but only the dashed form is confirmed working end to end.)
        """
        for row in await self.get_priorities():
            org_id = row.get("rox_org_id")
            if org_id:
                return normalize_uuid(str(org_id))
        return None

    async def get_clever_column_cell(self, column_id: str, entity_id: str) -> Any:
        """Fetch one generated cell value for one account."""
        return await self._request(
            "GET", f"/research/clever_column/{column_id}/cell/{entity_id}"
        )


# ----------------------------------------------------------------------
# Tolerant extraction helpers
#
# These absorb envelope differences (`{"data": ...}` vs bare payload) so the
# services above them don't care. Shapes verified live 2026-07-27.
# ----------------------------------------------------------------------


def normalize_uuid(value: str) -> str:
    """Rox emits some ids underscore-separated; endpoints want dashes."""
    return value.replace("_", "-") if value else value


def unwrap(data: Any) -> Any:
    """Strip a common `{"data": ...}` / `{"result": ...}` envelope."""
    if isinstance(data, dict):
        for key in ("data", "result", "results", "items"):
            if key in data and isinstance(data[key], (dict, list)):
                return data[key]
    return data


def first_present(data: Any, *keys: str) -> Any | None:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def flatten_hierarchy(data: Any) -> list[dict]:
    """Walk the customer hierarchy tree into a flat list of account dicts.

    Handles arbitrary nesting and the usual child-key spellings, tagging each
    node with its parent id so the tree can be rebuilt later.
    """
    out: list[dict] = []

    def visit(node: Any, parent_id: str | None) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, parent_id)
            return
        if not isinstance(node, dict):
            return

        node_id = first_present(node, "id", "entity_id", "customer_id", "uuid")
        name = first_present(node, "name", "customer_name", "display_name", "title")

        # Only treat it as an account if it looks like one; otherwise keep
        # descending (some trees wrap children under a container object).
        if node_id is not None and name is not None:
            record = dict(node)
            record["_id"] = str(node_id)
            record["_name"] = str(name)
            record["_parent_id"] = parent_id
            out.append(record)
            parent_id = str(node_id)

        for key in ("children", "customers", "accounts", "nodes", "sub_customers"):
            if key in node:
                visit(node[key], parent_id)

    visit(unwrap(data), None)
    return out
