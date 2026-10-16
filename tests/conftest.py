"""Offline test harness: a fake transport that serves recorded fixtures by URL path."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from spain_open_data_mcp.http import JsonFetcher, build_client

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Router:
    """Maps a URL path suffix to a fixture file (or status code) and records the requests made."""

    def __init__(self) -> None:
        self.routes: dict[str, str | int] = {}
        self.requests: list[httpx.Request] = []

    def add(self, path_suffix: str, fixture_or_status: str | int) -> Router:
        self.routes[path_suffix] = fixture_or_status
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for suffix, target in self.routes.items():
            if request.url.path.endswith(suffix):
                if isinstance(target, int):
                    return httpx.Response(target)
                return httpx.Response(200, json=load(target))
        return httpx.Response(404)


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def make_fetcher(router: Router) -> Callable[[], JsonFetcher]:
    def factory() -> JsonFetcher:
        return JsonFetcher(build_client(httpx.MockTransport(router.handler)))

    return factory
