"""BOE (Boletín Oficial del Estado) daily summary via the open data API.

Docs: https://www.boe.es/datosabiertos/api/api.php
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Any

from pydantic import BaseModel

from .http import JsonFetcher, UpstreamError, as_list

BASE_URL = "https://www.boe.es/datosabiertos/api/boe/sumario"
SOURCE = "BOE"


class BoeItem(BaseModel):
    id: str
    title: str
    section: str | None = None
    department: str | None = None
    url_html: str | None = None
    url_pdf: str | None = None


class BoeSummary(BaseModel):
    date: str
    total_items: int
    returned: int
    items: list[BoeItem]


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _url(value: Any) -> str | None:
    # url_pdf comes as {"texto": "...", "szBytes": ...}; url_html as a plain string.
    if isinstance(value, dict):
        return value.get("texto")
    return value if isinstance(value, str) else None


def _walk(node: Any, section: str | None, department: str | None, out: list[BoeItem]) -> None:
    """Collect every published item, remembering which section and department it sits under.

    The summary nests diario > seccion > departamento > epigrafe > item, but epigrafe is
    optional and any level can be a dict or a list, so we walk it generically.
    """
    if isinstance(node, list):
        for child in node:
            _walk(child, section, department, out)
        return
    if not isinstance(node, dict):
        return

    ident = node.get("identificador")
    if isinstance(ident, str) and ident.startswith("BOE-") and "titulo" in node and not ident.startswith("BOE-S"):
        out.append(
            BoeItem(
                id=ident,
                title=str(node.get("titulo", "")).strip(),
                section=section,
                department=department,
                url_html=_url(node.get("url_html")),
                url_pdf=_url(node.get("url_pdf")),
            )
        )
        return

    for key, value in node.items():
        if key == "seccion":
            for sec in as_list(value):
                _walk(sec, sec.get("nombre") if isinstance(sec, dict) else section, None, out)
        elif key == "departamento":
            for dep in as_list(value):
                _walk(dep, section, dep.get("nombre") if isinstance(dep, dict) else department, out)
        elif isinstance(value, dict | list):
            _walk(value, section, department, out)


async def daily_summary(
    fetcher: JsonFetcher,
    day: date,
    query: str | None = None,
    section: str | None = None,
    limit: int = 30,
) -> BoeSummary:
    raw = await fetcher.get_json(f"{BASE_URL}/{day:%Y%m%d}", source=SOURCE)
    data = raw.get("data") if isinstance(raw, dict) else None
    if not data:
        raise UpstreamError(f"{SOURCE}: no summary for {day.isoformat()} (no BOE is published on Sundays).")

    items: list[BoeItem] = []
    _walk(data, None, None, items)

    if section:
        s = _normalise(section)
        items = [i for i in items if i.section and s in _normalise(i.section)]
    if query:
        terms = _normalise(query).split()
        items = [i for i in items if all(t in _normalise(f"{i.title} {i.department or ''}") for t in terms)]

    return BoeSummary(
        date=day.isoformat(), total_items=len(items), returned=min(len(items), limit), items=items[:limit]
    )
