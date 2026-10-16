"""Shared HTTP layer: one client, sane timeouts, a small TTL cache and clear errors.

Public APIs are slow and occasionally flaky. Agents call the same endpoint many
times in one conversation, so a short in-memory cache saves latency and avoids
hammering services we don't own.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from . import __version__

USER_AGENT = f"spain-open-data-mcp/{__version__} (+https://github.com/gabicuesta/spain-open-data-mcp)"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class UpstreamError(RuntimeError):
    """A public API failed or returned something we could not use."""


def build_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


class JsonFetcher:
    """GET + JSON with a per-URL TTL cache."""

    def __init__(self, client: httpx.AsyncClient, ttl_seconds: float = 600.0, max_entries: int = 256):
        self._client = client
        self._ttl = ttl_seconds
        self._max = max_entries
        self._cache: dict[str, tuple[float, Any]] = {}

    async def get_json(self, url: str, params: dict[str, Any] | None = None, source: str = "upstream") -> Any:
        key = str(httpx.URL(url, params=params or {}))
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < self._ttl:
            return hit[1]

        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"{source}: timed out. The service may be slow; try again.") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{source}: network error ({exc.__class__.__name__}).") from exc

        if response.status_code == 404:
            raise UpstreamError(f"{source}: not found (404). Check the identifier or date.")
        if response.status_code >= 400:
            raise UpstreamError(f"{source}: HTTP {response.status_code}.")

        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamError(f"{source}: response was not valid JSON.") from exc

        if len(self._cache) >= self._max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (now, data)
        return data


def find_key(obj: Any, key: str) -> Any:
    """Depth-first, case-insensitive lookup of the first value stored under `key`.

    Some Spanish public APIs change key casing between endpoints and versions
    (e.g. `Consulta_DNPRCResult` vs `consulta_dnprcResult`), so parsers use this
    instead of brittle fixed paths.
    """
    target = key.lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == target:
                return v
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_key(item, key)
            if found is not None:
                return found
    return None


def as_list(value: Any) -> list[Any]:
    """APIs that serialise XML to JSON return a dict for one element and a list for many."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
