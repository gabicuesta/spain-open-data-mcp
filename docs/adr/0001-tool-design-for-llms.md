# ADR 0001: Tool design for LLMs

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

Three upstream APIs expose dozens of endpoints between them. The obvious approach is to map each endpoint to a tool. But tool selection by the model gets worse as the number of tools grows, and so does the size of the tool definitions it has to keep in context.

Responses can also be large: INE tables hold hundreds of series, and the BOE summary has hundreds of items on a busy day.

## Decision

- Expose **8 tools** covering the workflows people actually need: discover, then fetch. Don't mirror the APIs.
- **Name tools `<source>_<verb>_<object>`** so the model can scan them easily.
- Write **tool descriptions for the model**: say what the tool returns, give an example identifier and point to the next tool to call.
- Give **every tool that can return a lot a size control** (`limit`, `last_n`, `max_series`, `query`), with small defaults.
- **Return Pydantic models**, so clients get structured content and a schema.
- **Mark every tool read-only, idempotent and open-world** so clients can auto-approve them safely.

## Consequences

- Some advanced queries need two calls, for example `ine_list_tables` and then `ine_get_table`. We accept that in exchange for better tool selection and smaller contexts.
- New endpoints are only added when a real workflow needs them.
