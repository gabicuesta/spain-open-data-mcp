"""Smoke tests against the real APIs. Skipped by default; run with `pytest -m live`.

They check that the response shapes the parsers rely on still hold upstream.
"""

from datetime import date, timedelta

import pytest

from spain_open_data_mcp import boe, catastro, ine
from spain_open_data_mcp.http import JsonFetcher, build_client

pytestmark = pytest.mark.live


@pytest.fixture
def fetcher():
    return JsonFetcher(build_client())


async def test_ine_live(fetcher):
    ops = await ine.search_operations(fetcher, "IPC")
    assert any(o.code == "IPC" for o in ops)
    series = await ine.get_table(fetcher, 50902, last_n=1, max_series=1)
    assert series and series[0].points


async def test_boe_live(fetcher):
    day = date.today()
    while day.weekday() == 6:  # no BOE on Sundays
        day -= timedelta(days=1)
    summary = await boe.daily_summary(fetcher, day, limit=5)
    assert summary.total_items > 0


async def test_catastro_live(fetcher):
    # Puerta del Sol is a public square: no parcel at the point, so this exercises the nearby fallback.
    parcels = await catastro.reference_from_coordinates(fetcher, 40.41678, -3.70379)
    assert parcels and len(parcels[0].cadastral_reference) == 14
    assert parcels[0].match == "nearby"
    prop = await catastro.property_details(fetcher, parcels[0].cadastral_reference)
    assert prop.candidates or prop.use
