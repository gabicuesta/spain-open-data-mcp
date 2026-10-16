"""Catastro (Dirección General del Catastro) public OVC web services, JSON flavour.

Docs: https://ovc.catastro.meh.es/ovcservweb/ovcswlocalizacionrc/ovccoordenadas.asmx
Only non-protected data is available: location, use, built area and year of construction.
Owners and cadastral values are protected and never exposed by these services.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .http import JsonFetcher, UpstreamError, as_list, find_key

BASE_URL = "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero"
COORDS_URL = f"{BASE_URL}/COVCCoordenadas.svc/json"
STREETS_URL = f"{BASE_URL}/COVCCallejero.svc/json"
SOURCE = "Catastro"
SRS = "EPSG:4326"

_RC_PATTERN = re.compile(r"^[0-9A-Z]{14}([0-9A-Z]{6})?$")


class Parcel(BaseModel):
    cadastral_reference: str = Field(description="14-character parcel reference (pc1 + pc2)")
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    match: str = Field("exact", description="'exact' if the point falls on the parcel, 'nearby' otherwise")
    distance_m: float | None = Field(None, description="Distance from the point, for nearby parcels")


class BuildingUnit(BaseModel):
    use: str | None = Field(None, description="Main use, e.g. 'Residencial'")
    floor_area_m2: float | None = None
    year_built: int | None = None
    floor: str | None = None
    door: str | None = None


class Property(BaseModel):
    cadastral_reference: str
    address: str | None = None
    kind: str | None = Field(None, description="'UR' urban or 'RU' rural")
    use: str | None = None
    built_area_m2: float | None = None
    year_built: int | None = None
    units: list[BuildingUnit] = []
    candidates: list[str] = Field(
        default_factory=list,
        description="When the parcel holds several properties, their 20-char references",
    )


def normalise_reference(rc: str) -> str:
    cleaned = re.sub(r"[\s-]", "", rc).upper()
    if not _RC_PATTERN.match(cleaned):
        raise UpstreamError("Catastro: a cadastral reference has 14 or 20 letters/digits, e.g. 9872023VH5797S0001WX.")
    return cleaned


# Catastro error code for "there is no parcel at these coordinates" (streets, squares, parks...).
NO_PARCEL_AT_POINT = "16"


def _errors(raw: Any) -> list[tuple[str, str]]:
    out = []
    for err in as_list(find_key(raw, "lerr")):
        for e in as_list(find_key(err, "err") or err):
            if isinstance(e, dict) and e.get("des"):
                out.append((str(e.get("cod", "")).strip(), str(e["des"]).strip()))
    return out


def _raise_on_errors(raw: Any) -> None:
    errors = _errors(raw)
    if errors:
        raise UpstreamError(f"{SOURCE}: {'; '.join(des for _, des in errors)}")


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    f = _float(value)
    return int(f) if f is not None else None


def _parcel_nodes(node: Any) -> list[dict]:
    """Every object that carries a parcel reference (`pc`), wherever it is nested.

    The exact lookup nests them under coordenadas/coord; the nearby lookup under
    coordenadas_distancias/coordd/lpcd/pcd. Walking generically covers both.
    """
    if isinstance(node, list):
        return [n for child in node for n in _parcel_nodes(child)]
    if not isinstance(node, dict):
        return []
    if isinstance(node.get("pc"), dict) and node["pc"].get("pc1"):
        return [node]
    return [n for child in node.values() for n in _parcel_nodes(child)]


def _parcels(raw: Any, match: str = "exact") -> list[Parcel]:
    out: dict[str, Parcel] = {}
    for c in _parcel_nodes(raw):
        pc, geo = c["pc"], c.get("geo") or {}
        ref = f"{pc.get('pc1', '')}{pc.get('pc2', '')}"
        parcel = Parcel(
            cadastral_reference=ref,
            address=c.get("ldt"),
            latitude=_float(geo.get("ycen")),
            longitude=_float(geo.get("xcen")),
            match=match,
            distance_m=_float(c.get("dis")),
        )
        if ref not in out or (parcel.distance_m or 0) < (out[ref].distance_m or 0):
            out[ref] = parcel
    return sorted(out.values(), key=lambda p: p.distance_m or 0)


async def reference_from_coordinates(
    fetcher: JsonFetcher, latitude: float, longitude: float, max_nearby: int = 5
) -> list[Parcel]:
    """Parcel at a point. If the point is on a street or square, return the nearest parcels instead."""
    params = {"SRS": SRS, "CoorX": longitude, "CoorY": latitude}
    raw = await fetcher.get_json(f"{COORDS_URL}/Consulta_RCCOOR", params=params, source=SOURCE)

    errors = _errors(raw)
    if not errors:
        return _parcels(raw)
    if not any(cod == NO_PARCEL_AT_POINT for cod, _ in errors):
        _raise_on_errors(raw)

    # Fallback: parcels within ~50 m, nearest first.
    nearby = await fetcher.get_json(f"{COORDS_URL}/Consulta_RCCOOR_Distancia", params=params, source=SOURCE)
    _raise_on_errors(nearby)
    parcels = _parcels(nearby, match="nearby")[:max_nearby]
    if not parcels:
        raise UpstreamError(f"{SOURCE}: no parcels at or within ~50 m of that point.")
    return parcels


async def coordinates_from_reference(fetcher: JsonFetcher, cadastral_reference: str) -> list[Parcel]:
    rc = normalise_reference(cadastral_reference)[:14]
    raw = await fetcher.get_json(
        f"{COORDS_URL}/Consulta_CPMRC",
        params={"SRS": SRS, "RefCat": rc, "Provincia": "", "Municipio": ""},
        source=SOURCE,
    )
    _raise_on_errors(raw)
    return _parcels(raw)


def _join_rc(rc: dict) -> str:
    return "".join(str(rc.get(k, "")) for k in ("pc1", "pc2", "car", "cc1", "cc2"))


async def property_details(fetcher: JsonFetcher, cadastral_reference: str) -> Property:
    rc = normalise_reference(cadastral_reference)
    raw = await fetcher.get_json(f"{STREETS_URL}/Consulta_DNPRC", params={"RefCat": rc}, source=SOURCE)
    _raise_on_errors(raw)

    bico = find_key(raw, "bico")
    if not bico:
        # A 14-char parcel reference with several properties returns a list to choose from.
        candidates = [_join_rc(find_key(r, "rc") or {}) for r in as_list(find_key(raw, "rcdnp"))]
        candidates = [c for c in candidates if c]
        if not candidates:
            raise UpstreamError(f"{SOURCE}: no property found for {rc}.")
        return Property(cadastral_reference=rc, candidates=candidates[:50])

    bi = find_key(bico, "bi") or {}
    debi = find_key(bi, "debi") or {}
    units = []
    for cons in as_list(find_key(bico, "lcons")):
        for c in as_list(cons.get("cons") if isinstance(cons, dict) and "cons" in cons else cons):
            if not isinstance(c, dict):
                continue
            loint = find_key(c, "loint") or {}
            units.append(
                BuildingUnit(
                    use=c.get("lcd"),
                    floor_area_m2=_float(find_key(c, "stl")),
                    year_built=_int(find_key(c, "ant")),
                    floor=loint.get("pt"),
                    door=loint.get("pu"),
                )
            )

    rc_obj = find_key(bi, "rc") or {}
    return Property(
        cadastral_reference=_join_rc(rc_obj) or rc,
        address=find_key(bi, "ldt"),
        kind=find_key(bi, "cn"),
        use=debi.get("luso"),
        built_area_m2=_float(debi.get("sfc")),
        year_built=_int(debi.get("ant")),
        units=units,
    )
