#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export and check API contract artifacts.

Artifacts:
- docs/architecture/api_spec.json
- apps/dsa-web/src/types/openapi.generated.ts
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


OPENAPI_PATH = Path("docs/architecture/api_spec.json")
WEB_TYPES_PATH = Path("apps/dsa-web/src/types/openapi.generated.ts")


@dataclass(frozen=True)
class ContractArtifacts:
    openapi_json: str
    web_types_ts: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_openapi() -> Dict[str, Any]:
    repo_root = str(_repo_root())
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from api.app import create_app

    return create_app().openapi()


def _json_dumps(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _ts_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not cleaned:
        return "Unnamed"
    if cleaned[0].isdigit():
        return f"_{cleaned}"
    return cleaned


def _prop_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_$][0-9A-Za-z_$]*", name):
        return name
    return json.dumps(name)


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ref_name(ref: str) -> str:
    return _ts_name(ref.rsplit("/", 1)[-1])


def _schema_to_ts(schema: Mapping[str, Any], *, indent: int = 0) -> str:
    if not schema:
        return "unknown"

    if "$ref" in schema:
        return _ref_name(str(schema["$ref"]))

    if "const" in schema:
        return _literal(schema["const"])

    if "enum" in schema:
        values = [value for value in schema["enum"] if value is not None]
        enum_ts = " | ".join(_literal(value) for value in values) or "never"
        if any(value is None for value in schema["enum"]):
            enum_ts = f"{enum_ts} | null"
        return enum_ts

    nullable = bool(schema.get("nullable"))
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            parts = []
            has_null = False
            for item in schema.get(union_key) or []:
                if item.get("type") == "null":
                    has_null = True
                    continue
                parts.append(_schema_to_ts(item, indent=indent))
            result = " | ".join(parts) if parts else "unknown"
            if has_null:
                result = f"{result} | null"
            return result

    if "allOf" in schema:
        parts = [_schema_to_ts(item, indent=indent) for item in schema.get("allOf") or []]
        return " & ".join(parts) if parts else "unknown"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        has_null = "null" in schema_type
        non_null_types = [item for item in schema_type if item != "null"]
        result = " | ".join(_schema_to_ts({"type": item}, indent=indent) for item in non_null_types)
        return f"{result} | null" if has_null else result

    if schema_type == "string":
        result = "string"
    elif schema_type in {"integer", "number"}:
        result = "number"
    elif schema_type == "boolean":
        result = "boolean"
    elif schema_type == "array":
        item_ts = _schema_to_ts(schema.get("items") or {}, indent=indent)
        result = f"Array<{item_ts}>"
    elif schema_type == "object" or "properties" in schema:
        result = _object_schema_to_ts(schema, indent=indent)
    else:
        result = "unknown"

    if nullable:
        return f"{result} | null"
    return result


def _object_schema_to_ts(schema: Mapping[str, Any], *, indent: int = 0) -> str:
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties")
    if not properties:
        if isinstance(additional, Mapping):
            return f"Record<string, {_schema_to_ts(additional, indent=indent)}>"
        return "Record<string, unknown>"

    required = set(schema.get("required") or [])
    pad = " " * indent
    child_pad = " " * (indent + 2)
    lines = ["{"]
    for name, prop_schema in sorted(properties.items()):
        optional = "" if name in required else "?"
        lines.append(
            f"{child_pad}{_prop_name(name)}{optional}: "
            f"{_schema_to_ts(prop_schema, indent=indent + 2)};"
        )
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def _emit_component_type(name: str, schema: Mapping[str, Any]) -> str:
    type_name = _ts_name(name)
    body = _schema_to_ts(schema, indent=0)
    if body.startswith("{\n"):
        return f"export interface {type_name} {body}\n"
    return f"export type {type_name} = {body};\n"


def _operation_name(path: str, method: str) -> str:
    raw = f"{method}_{path.strip('/') or 'root'}"
    raw = raw.replace("{", "by_").replace("}", "")
    parts = re.split(r"[^0-9A-Za-z]+", raw)
    first, *rest = [part for part in parts if part]
    return _ts_name(first.lower() + "".join(part[:1].upper() + part[1:] for part in rest))


def _response_ts(responses: Mapping[str, Any]) -> str:
    refs: List[str] = []
    for status_code, response in sorted(responses.items()):
        if not str(status_code).startswith(("2", "4", "5")):
            continue
        content = (response or {}).get("content") or {}
        json_content = content.get("application/json") or {}
        schema = json_content.get("schema")
        if schema:
            refs.append(_schema_to_ts(schema))
    return " | ".join(dict.fromkeys(refs)) if refs else "unknown"


def _request_body_ts(operation: Mapping[str, Any]) -> str:
    content = ((operation.get("requestBody") or {}).get("content") or {})
    json_content = content.get("application/json") or {}
    schema = json_content.get("schema")
    return _schema_to_ts(schema) if schema else "never"


def _generate_web_types(openapi: Mapping[str, Any]) -> str:
    schemas = ((openapi.get("components") or {}).get("schemas") or {})
    lines = [
        "// Generated by scripts/api_contract.py. Do not edit by hand.",
        "",
    ]

    for name in sorted(schemas):
        lines.append(_emit_component_type(name, schemas[name]))

    lines.append("export interface ApiOperations {")
    paths = openapi.get("paths") or {}
    for path in sorted(paths):
        for method in sorted(paths[path]):
            operation = paths[path][method] or {}
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_name = _operation_name(path, method)
            request_ts = _request_body_ts(operation)
            response_ts = _response_ts(operation.get("responses") or {})
            lines.append(f"  {op_name}: {{")
            lines.append(f"    method: {_literal(method.upper())};")
            lines.append(f"    path: {_literal(path)};")
            lines.append(f"    request: {request_ts};")
            lines.append(f"    response: {response_ts};")
            lines.append("  };")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def build_contract_artifacts(output_root: Optional[Path] = None) -> ContractArtifacts:
    root = output_root or _repo_root()
    openapi = _load_openapi()
    openapi_json = _json_dumps(openapi)
    web_types_ts = _generate_web_types(openapi)

    for relative_path, content in (
        (OPENAPI_PATH, openapi_json),
        (WEB_TYPES_PATH, web_types_ts),
    ):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return ContractArtifacts(openapi_json=openapi_json, web_types_ts=web_types_ts)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _diff(expected: str, actual: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def check_contract_artifacts(root: Optional[Path] = None) -> int:
    repo = root or _repo_root()
    artifacts = build_contract_artifacts(Path("/tmp/dsa-api-contract-check"))
    failures = []
    for relative_path, generated in (
        (OPENAPI_PATH, artifacts.openapi_json),
        (WEB_TYPES_PATH, artifacts.web_types_ts),
    ):
        current = _read(repo / relative_path)
        if current != generated:
            failures.append(
                _diff(
                    current,
                    generated,
                    fromfile=str(relative_path),
                    tofile=f"generated/{relative_path}",
                )
            )

    if failures:
        sys.stderr.write(
            "API contract artifacts are out of date. Run:\n"
            "  python scripts/api_contract.py --write\n\n"
        )
        sys.stderr.write("\n".join(failures))
        return 1
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export or check API contract artifacts.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Regenerate contract artifacts")
    mode.add_argument("--check", action="store_true", help="Fail if contract artifacts drift")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.write:
        build_contract_artifacts()
        return 0
    return check_contract_artifacts()


if __name__ == "__main__":
    raise SystemExit(main())
