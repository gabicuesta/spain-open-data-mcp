from datetime import date

import pytest

from spain_open_data_mcp import boe, catastro, ine
from spain_open_data_mcp.http import UpstreamError, find_key

# ------------------------------------------------------------------ INE


async def test_search_operations_is_accent_insensitive(router, make_fetcher):
    router.add("/OPERACIONES_DISPONIBLES", "ine_operations.json")
    ops = await ine.search_operations(make_fetcher(), "construccion")
    assert [o.code for o in ops] == ["ICC"]


async def test_search_operations_without_query_returns_all(router, make_fetcher):
    router.add("/OPERACIONES_DISPONIBLES", "ine_operations.json")
    ops = await ine.search_operations(make_fetcher(), limit=2)
    assert len(ops) == 2


async def test_list_tables_filters_by_name(router, make_fetcher):
    router.add("/TABLAS_OPERACION/IPC", "ine_tables.json")
    tables = await ine.list_tables(make_fetcher(), "IPC", query="comunidades")
    assert [t.id for t in tables] == [50913]


async def test_get_table_parses_dates_secret_values_and_sorts(router, make_fetcher):
    router.add("/DATOS_TABLA/50902", "ine_table.json")
    series = await ine.get_table(make_fetcher(), 50902, last_n=2)

    assert series[0].code == "IPC251852"
    assert series[0].name == "Nacional. Índice general. Índice."
    assert [p.date for p in series[0].points] == sorted(p.date for p in series[0].points)
    assert series[0].points[-1].value == 118.2
    assert series[1].points[0].value is None  # marked Secreto
    assert router.requests[0].url.params["nult"] == "2"


async def test_get_table_filter_by_series_name(router, make_fetcher):
    router.add("/DATOS_TABLA/50902", "ine_table.json")
    series = await ine.get_table(make_fetcher(), 50902, query="variacion anual")
    assert [s.code for s in series] == ["IPC251855"]


async def test_cache_avoids_second_request(router, make_fetcher):
    router.add("/OPERACIONES_DISPONIBLES", "ine_operations.json")
    fetcher = make_fetcher()
    await ine.search_operations(fetcher, "IPC")
    await ine.search_operations(fetcher, "vivienda")
    assert len(router.requests) == 1


async def test_http_errors_become_readable(router, make_fetcher):
    router.add("/DATOS_SERIE/NOPE", 500)
    with pytest.raises(UpstreamError, match="INE: HTTP 500"):
        await ine.get_series(make_fetcher(), "NOPE")


# ------------------------------------------------------------------ BOE


async def test_boe_summary_walks_dicts_and_lists(router, make_fetcher):
    router.add("/sumario/20261015", "boe_sumario.json")
    summary = await boe.daily_summary(make_fetcher(), date(2026, 10, 15))

    assert summary.total_items == 3
    ids = [i.id for i in summary.items]
    assert "BOE-S-2026-248" not in ids  # the summary itself is not an item
    first = summary.items[0]
    assert first.section == "I. Disposiciones generales"
    assert first.department == "MINISTERIO DE VIVIENDA Y AGENDA URBANA"
    assert first.url_pdf.endswith("BOE-A-2026-20001.pdf")


async def test_boe_summary_filters(router, make_fetcher):
    router.add("/sumario/20261015", "boe_sumario.json")
    fetcher = make_fetcher()

    by_query = await boe.daily_summary(fetcher, date(2026, 10, 15), query="catastral")
    assert [i.id for i in by_query.items] == ["BOE-A-2026-20003"]

    by_section = await boe.daily_summary(fetcher, date(2026, 10, 15), section="disposiciones generales")
    assert [i.id for i in by_section.items] == ["BOE-A-2026-20001"]


async def test_boe_limit(router, make_fetcher):
    router.add("/sumario/20261015", "boe_sumario.json")
    summary = await boe.daily_summary(make_fetcher(), date(2026, 10, 15), limit=1)
    assert summary.total_items == 3 and summary.returned == 1 and len(summary.items) == 1


async def test_boe_missing_day(router, make_fetcher):
    with pytest.raises(UpstreamError, match="404"):
        await boe.daily_summary(make_fetcher(), date(2026, 10, 18))


# ------------------------------------------------------------- Catastro


async def test_reference_from_coordinates(router, make_fetcher):
    router.add("/Consulta_RCCOOR", "catastro_rccoor.json")
    parcels = await catastro.reference_from_coordinates(make_fetcher(), 40.4168, -3.7038)

    assert parcels[0].cadastral_reference == "0847106VK4704F"
    assert parcels[0].match == "exact"
    assert parcels[0].latitude == pytest.approx(40.41678)
    params = router.requests[0].url.params
    assert params["CoorX"] == "-3.7038" and params["CoorY"] == "40.4168"  # X is longitude


async def test_point_without_parcel_falls_back_to_nearby(router, make_fetcher):
    # Real response for Puerta del Sol (a public square has no parcel).
    router.add("/Consulta_RCCOOR", "catastro_no_parcel.json")
    router.add("/Consulta_RCCOOR_Distancia", "catastro_distancia.json")
    parcels = await catastro.reference_from_coordinates(make_fetcher(), 40.41678, -3.70379)

    assert [p.cadastral_reference for p in parcels] == ["0847101VK4704F", "0848301VK4704F"]  # nearest first, deduped
    assert parcels[0].match == "nearby" and parcels[0].distance_m == 18.7
    assert len(router.requests) == 2


async def test_nearby_fallback_respects_limit(router, make_fetcher):
    router.add("/Consulta_RCCOOR", "catastro_no_parcel.json")
    router.add("/Consulta_RCCOOR_Distancia", "catastro_distancia.json")
    parcels = await catastro.reference_from_coordinates(make_fetcher(), 40.41678, -3.70379, max_nearby=1)
    assert len(parcels) == 1


async def test_catastro_errors_are_surfaced(router, make_fetcher):
    router.add("/Consulta_RCCOOR", "catastro_error.json")
    with pytest.raises(UpstreamError, match="NO HAY NINGUNA PARCELA"):
        await catastro.reference_from_coordinates(make_fetcher(), 40.0, -3.0)


async def test_property_details(router, make_fetcher):
    router.add("/Consulta_DNPRC", "catastro_dnprc.json")
    prop = await catastro.property_details(make_fetcher(), "9872023 VH5797S 0001 WX")

    assert prop.cadastral_reference == "9872023VH5797S0001WX"
    assert prop.use == "Residencial"
    assert prop.built_area_m2 == 92
    assert prop.year_built == 1968
    assert prop.kind == "UR"
    assert [u.use for u in prop.units] == ["VIVIENDA", "ELEMENTOS COMUNES"]
    assert prop.units[0].floor == "02" and prop.units[0].floor_area_m2 == 80


async def test_property_details_multiple_candidates(router, make_fetcher):
    router.add("/Consulta_DNPRC", "catastro_dnprc_multi.json")
    prop = await catastro.property_details(make_fetcher(), "9872023VH5797S")
    assert prop.candidates == ["9872023VH5797S0001WX", "9872023VH5797S0002EM"]


@pytest.mark.parametrize("bad", ["123", "9872023VH5797S0001W", "ÑÑÑÑÑÑÑÑÑÑÑÑÑÑ"])
def test_reference_validation(bad):
    with pytest.raises(UpstreamError):
        catastro.normalise_reference(bad)


def test_find_key_is_case_insensitive():
    assert find_key({"A": {"Consulta_DNPRCResult": {"x": 1}}}, "consulta_dnprcresult") == {"x": 1}
