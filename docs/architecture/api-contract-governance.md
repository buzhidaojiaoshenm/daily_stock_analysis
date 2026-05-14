# API Contract Governance

This repository treats `docs/architecture/api_spec.json` as the reviewable API
contract snapshot for FastAPI routes.

## Artifacts

- `docs/architecture/api_spec.json`: normalized OpenAPI JSON exported from the
  running FastAPI app factory.
- `apps/dsa-web/src/types/openapi.generated.ts`: generated TypeScript contract
  types derived from the OpenAPI component schemas and route operations.

Regenerate both artifacts after API, schema, auth, status, or response payload
changes:

```bash
python scripts/api_contract.py --write
```

Check for drift without modifying files:

```bash
python scripts/api_contract.py --check
```

`./scripts/ci_gate.sh` runs the drift check as part of the default backend gate.

## Change Policy

- Prefer additive changes: add fields, enum values, response variants, or new
  endpoints before removing or renaming existing fields.
- Preserve old fields or route shapes when Web, desktop, bot, or external
  callers may still depend on them.
- When a field or route must be renamed, keep a compatibility path and document
  the canonical replacement plus removal criteria in the PR.
- For task and status APIs, new terminal states must be represented in both
  OpenAPI and Web task types before the backend starts returning them.
- Breaking changes must call out compatibility impact, migration steps, and
  rollback behavior in the PR description.

## Deprecation Behavior

Deprecated fields or routes should remain readable until all maintained clients
have been updated. During the deprecation window:

- Docs should name the canonical field or route.
- API responses may include both old and new fields if needed for compatibility.
- Tests should cover both the canonical path and the compatibility path.
- Changelog entries should describe the user-visible contract change.
