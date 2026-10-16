# ADR 0002: Tolerant parsing of upstream responses

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The BOE and the Catastro produce JSON by serialising XML. As a result:

- A single child element comes back as an object, and several as an array.
- Optional levels appear or disappear. In the BOE, for example, `epigrafe` is optional between `departamento` and `item`.
- Key casing differs between endpoints, as in `Consulta_RCCOORResult` vs `consulta_dnprcResult`.

Fixed-path parsers such as `data["a"]["b"][0]["c"]` break silently whenever any of these vary.

## Decision

- Use `as_list()` everywhere a collection is expected.
- Use `find_key()`, a case-insensitive depth-first lookup, to reach known fields wherever they sit.
- Walk the BOE summary generically while carrying section and department context.
- Detect upstream errors (Catastro `lerr`) explicitly and return the message to the model.
- Keep **live smoke tests** (`pytest -m live`) that run weekly in CI to catch schema drift early.

## Consequences

- Parsers are a bit less strict: a renamed field returns `None` rather than raising. The live tests exist to make that kind of drift visible.
