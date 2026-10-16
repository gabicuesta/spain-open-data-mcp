"""MCP server wiring: tools, a guided prompt and the stdio / HTTP entry point."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__, boe, catastro, ine
from .http import JsonFetcher, UpstreamError, build_client

MADRID = ZoneInfo("Europe/Madrid")

# Every tool only reads public data from services we don't control.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

INSTRUCTIONS = """\
Tools for Spanish public data.
- INE: official statistics. Find an operation (e.g. IPC, EPA, IPV), list its tables, then fetch a
  table or a series code. Values are numbers; units are in the series name.
- BOE: the daily Official State Gazette summary, filterable by keyword and section.
- Catastro: non-protected property data (location, use, built area, year built) by cadastral
  reference or coordinates. Owners and cadastral values are never available.
Prefer narrow queries and small `last_n` / `limit` values: responses go into your context window.
Always cite the source (INE, BOE or Catastro) and the period or date of the data you use.
"""


def build_server(fetcher: JsonFetcher | None = None) -> MCPServer:
    fetcher = fetcher or JsonFetcher(build_client())
    mcp = MCPServer(
        name="spain-open-data",
        title="Spain Open Data",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/gabicuesta/spain-open-data-mcp",
    )

    async def guarded(coro):
        try:
            return await coro
        except UpstreamError as exc:
            # Readable, actionable message for the model instead of a stack trace.
            raise ToolError(str(exc)) from exc

    # ---------------------------------------------------------------- INE

    @mcp.tool(title="INE: search statistical operations", annotations=READ_ONLY)
    async def ine_search_operations(
        query: Annotated[str | None, Field(description="Words to match, e.g. 'precios vivienda'")] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> list[ine.Operation]:
        """Find INE statistical operations (IPC, EPA, Índice de Precios de Vivienda...). Accent-insensitive."""
        return await guarded(ine.search_operations(fetcher, query, limit))

    @mcp.tool(title="INE: list tables of an operation", annotations=READ_ONLY)
    async def ine_list_tables(
        operation: Annotated[str, Field(description="Operation code or id, e.g. 'IPC' or '25'")],
        query: Annotated[str | None, Field(description="Optional words to filter table names")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[ine.Table]:
        """List the data tables published for an INE operation."""
        return await guarded(ine.list_tables(fetcher, operation, query, limit))

    @mcp.tool(title="INE: get table data", annotations=READ_ONLY)
    async def ine_get_table(
        table_id: Annotated[int, Field(description="Table id from ine_list_tables, e.g. 50902")],
        last_n: Annotated[int, Field(ge=1, le=120, description="Most recent periods per series")] = 12,
        query: Annotated[str | None, Field(description="Filter series by name, e.g. 'nacional general'")] = None,
        max_series: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[ine.Series]:
        """Fetch the latest values of the series in an INE table."""
        return await guarded(ine.get_table(fetcher, table_id, last_n, query, max_series))

    @mcp.tool(title="INE: get a series", annotations=READ_ONLY)
    async def ine_get_series(
        series_code: Annotated[str, Field(description="Series code, e.g. 'IPC251852'")],
        last_n: Annotated[int, Field(ge=1, le=240)] = 12,
    ) -> ine.Series:
        """Fetch the latest values of a single INE series."""
        return await guarded(ine.get_series(fetcher, series_code, last_n))

    # ---------------------------------------------------------------- BOE

    @mcp.tool(title="BOE: daily summary", annotations=READ_ONLY)
    async def boe_daily_summary(
        day: Annotated[str | None, Field(description="YYYY-MM-DD. Defaults to today in Madrid")] = None,
        query: Annotated[str | None, Field(description="Words that must appear in the title")] = None,
        section: Annotated[
            str | None, Field(description="Filter by section name, e.g. 'Disposiciones generales'")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
    ) -> boe.BoeSummary:
        """List what the BOE published on a given day, with links to HTML and PDF."""
        try:
            target = date.fromisoformat(day) if day else datetime.now(MADRID).date()
        except ValueError as exc:
            raise ToolError("`day` must be in YYYY-MM-DD format.") from exc
        return await guarded(boe.daily_summary(fetcher, target, query, section, limit))

    # ----------------------------------------------------------- Catastro

    @mcp.tool(title="Catastro: parcel at coordinates", annotations=READ_ONLY)
    async def catastro_reference_from_coordinates(
        latitude: Annotated[float, Field(ge=27.0, le=44.5, description="WGS84 latitude")],
        longitude: Annotated[float, Field(ge=-18.5, le=4.5, description="WGS84 longitude")],
    ) -> list[catastro.Parcel]:
        """Find the cadastral parcel and address at a point in Spain. If the point is on a street or
        square, returns the nearest parcels (match='nearby', with distance_m)."""
        return await guarded(catastro.reference_from_coordinates(fetcher, latitude, longitude))

    @mcp.tool(title="Catastro: locate a reference", annotations=READ_ONLY)
    async def catastro_coordinates_from_reference(
        cadastral_reference: Annotated[str, Field(description="14 or 20 character cadastral reference")],
    ) -> list[catastro.Parcel]:
        """Get the address and coordinates of a cadastral reference."""
        return await guarded(catastro.coordinates_from_reference(fetcher, cadastral_reference))

    @mcp.tool(title="Catastro: property details", annotations=READ_ONLY)
    async def catastro_property_details(
        cadastral_reference: Annotated[str, Field(description="14 or 20 character cadastral reference")],
    ) -> catastro.Property:
        """Use, built area, year built and units of a property. A 14-char parcel reference with
        several properties returns `candidates`; call again with one of them."""
        return await guarded(catastro.property_details(fetcher, cadastral_reference))

    # ------------------------------------------------------------- Prompts

    @mcp.prompt(title="Property context report")
    def property_report(cadastral_reference: str) -> str:
        """Build a short, sourced report about a property and its market context."""
        return (
            f"Write a short report about the property with cadastral reference {cadastral_reference}.\n"
            "1. Use catastro_property_details (and catastro_coordinates_from_reference for the location).\n"
            "2. Use ine_search_operations to find the housing price index (Índice de Precios de Vivienda) "
            "and the construction cost index, then fetch the latest values for the property's region "
            "or, if not available, for Spain.\n"
            "3. Check boe_daily_summary for today with query 'vivienda' for relevant new regulation.\n"
            "Explain what the age and size of the building imply for a renovation. Cite every figure "
            "with its source and period. Do not invent data that the tools did not return."
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Spain Open Data MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
