"""INE (Instituto Nacional de Estadística) via the public Tempus3 JSON API.

Docs: https://www.ine.es/dyngs/DataLab/manual.html?cid=45
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .http import JsonFetcher, UpstreamError

BASE_URL = "https://servicios.ine.es/wstempus/js/ES"
SOURCE = "INE"


class Operation(BaseModel):
    id: int
    code: str = Field(description="Short code, e.g. 'IPC'")
    name: str
    url: str | None = None


class Table(BaseModel):
    id: int
    name: str
    code: str | None = None


class DataPoint(BaseModel):
    date: str = Field(description="ISO date of the period start")
    year: int | None = None
    value: float | None = Field(description="None when INE marks the value as secret or missing")


class Series(BaseModel):
    code: str
    name: str
    points: list[DataPoint]


def _normalise(text: str) -> str:
    """Lowercase and strip accents so 'construccion' matches 'Construcción'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _to_iso(epoch_ms: int | float | None) -> str:
    if epoch_ms is None:
        return ""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).date().isoformat()


def _parse_series(raw: dict) -> Series:
    points = [
        DataPoint(
            date=_to_iso(p.get("Fecha")),
            year=p.get("Anyo"),
            value=None if p.get("Secreto") else p.get("Valor"),
        )
        for p in raw.get("Data") or []
    ]
    points.sort(key=lambda p: p.date)
    return Series(code=raw.get("COD", ""), name=(raw.get("Nombre") or "").strip(), points=points)


async def search_operations(fetcher: JsonFetcher, query: str | None = None, limit: int = 25) -> list[Operation]:
    raw = await fetcher.get_json(f"{BASE_URL}/OPERACIONES_DISPONIBLES", source=SOURCE)
    if not isinstance(raw, list):
        raise UpstreamError(f"{SOURCE}: unexpected response for the operations list.")

    ops = [
        Operation(id=o["Id"], code=o.get("Codigo", ""), name=o.get("Nombre", ""), url=o.get("Url"))
        for o in raw
        if "Id" in o
    ]
    if query:
        terms = _normalise(query).split()
        ops = [o for o in ops if all(t in _normalise(f"{o.code} {o.name}") for t in terms)]
    return ops[:limit]


async def list_tables(fetcher: JsonFetcher, operation: str, query: str | None = None, limit: int = 50) -> list[Table]:
    raw = await fetcher.get_json(f"{BASE_URL}/TABLAS_OPERACION/{operation}", source=SOURCE)
    if not isinstance(raw, list):
        raise UpstreamError(f"{SOURCE}: unexpected response listing tables for '{operation}'.")

    tables = [Table(id=t["Id"], name=t.get("Nombre", ""), code=t.get("Codigo")) for t in raw if "Id" in t]
    if query:
        terms = _normalise(query).split()
        tables = [t for t in tables if all(term in _normalise(t.name) for term in terms)]
    return tables[:limit]


async def get_table(
    fetcher: JsonFetcher, table_id: int, last_n: int = 12, query: str | None = None, max_series: int = 20
) -> list[Series]:
    raw = await fetcher.get_json(f"{BASE_URL}/DATOS_TABLA/{table_id}", params={"nult": last_n}, source=SOURCE)
    if not isinstance(raw, list):
        raise UpstreamError(f"{SOURCE}: unexpected response for table {table_id}.")

    series = [_parse_series(s) for s in raw]
    if query:
        terms = _normalise(query).split()
        series = [s for s in series if all(t in _normalise(s.name) for t in terms)]
    return series[:max_series]


async def get_series(fetcher: JsonFetcher, series_code: str, last_n: int = 12) -> Series:
    raw = await fetcher.get_json(f"{BASE_URL}/DATOS_SERIE/{series_code}", params={"nult": last_n}, source=SOURCE)
    if not isinstance(raw, dict) or "COD" not in raw:
        raise UpstreamError(f"{SOURCE}: series '{series_code}' not found.")
    return _parse_series(raw)
