"""End-to-end through the MCP protocol, using an in-process client."""

from mcp import Client

from spain_open_data_mcp.server import build_server

EXPECTED_TOOLS = {
    "ine_search_operations",
    "ine_list_tables",
    "ine_get_table",
    "ine_get_series",
    "boe_daily_summary",
    "catastro_reference_from_coordinates",
    "catastro_coordinates_from_reference",
    "catastro_property_details",
}


async def test_tools_are_listed_and_read_only(make_fetcher):
    async with Client(build_server(make_fetcher())) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS
        for tool in tools:
            assert tool.annotations and tool.annotations.read_only_hint, tool.name
            assert tool.description, tool.name


async def test_call_tool_returns_structured_content(router, make_fetcher):
    router.add("/OPERACIONES_DISPONIBLES", "ine_operations.json")
    async with Client(build_server(make_fetcher())) as client:
        result = await client.call_tool("ine_search_operations", {"query": "vivienda"})
        assert not result.is_error
        assert "IPV" in str(result.structured_content)


async def test_upstream_errors_become_tool_errors(router, make_fetcher):
    router.add("/Consulta_RCCOOR", "catastro_error.json")
    async with Client(build_server(make_fetcher())) as client:
        result = await client.call_tool("catastro_reference_from_coordinates", {"latitude": 40.0, "longitude": -3.0})
        assert result.is_error
        assert "NO HAY NINGUNA PARCELA" in result.content[0].text


async def test_invalid_date_is_rejected_cleanly(make_fetcher):
    async with Client(build_server(make_fetcher())) as client:
        result = await client.call_tool("boe_daily_summary", {"day": "15/10/2026"})
        assert result.is_error
        assert "YYYY-MM-DD" in result.content[0].text


async def test_prompt_is_available(make_fetcher):
    async with Client(build_server(make_fetcher())) as client:
        prompts = (await client.list_prompts()).prompts
        assert [p.name for p in prompts] == ["property_report"]


def test_today_falls_back_when_no_timezone_database(monkeypatch):
    from spain_open_data_mcp import server

    def missing(_key):
        raise server.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(server, "ZoneInfo", missing)
    assert server._madrid_today()  # no crash, e.g. on Windows without tzdata
