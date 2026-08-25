"""JSON Schema documents for letitloop public JSON interfaces."""

from __future__ import annotations

import copy
from typing import Any

from .contract import VALID_CHECK_KINDS, VALID_QC_LENSES, VALID_RISK_TIERS, VALID_STATUSES
from .goal import VALID_GOAL_STATUSES

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def contract_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    path_entry = {
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1}},
        "required": ["path"],
        "additionalProperties": True,
    }
    check = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "enum": sorted(VALID_CHECK_KINDS)},
            "path": {"type": "string"},
            "command": {"type": "string"},
            "expected": {},
            "schema": {},
        },
        "required": ["id", "kind"],
        "allOf": [
            {
                "if": {
                    "properties": {
                        "kind": {
                            "enum": ["command", "content_exact", "content_regex", "min_size"]
                        }
                    },
                    "required": ["kind"],
                },
                "then": {"required": ["expected"]},
            }
        ],
        "additionalProperties": True,
    }
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://github.com/sdageltc/letitloop/raw/main/schemas/contract.schema.json",
        "title": "letitloop task contract",
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "minLength": 1},
            "title": {"type": "string"},
            "status": {"type": "string", "enum": sorted(VALID_STATUSES)},
            "risk_tier": {"type": "string", "enum": sorted(VALID_RISK_TIERS)},
            "workspace_scope": {
                "type": "object",
                "properties": {
                    "allow": string_array,
                    "deny": string_array,
                    "scratch_dir": {"type": "string"},
                },
                "required": ["allow", "deny"],
                "additionalProperties": False,
            },
            "objective": {"type": "string"},
            "worker": {
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "max_attempts": {"type": "integer", "minimum": 1},
                },
                "required": ["model", "max_attempts"],
                "additionalProperties": True,
            },
            "inputs": {"type": "array", "items": path_entry},
            "outputs": {"type": "array", "items": path_entry},
            "acceptance_checks": {"type": "array", "items": check},
            "qc": {
                "type": "object",
                "properties": {
                    "required": {"type": "boolean"},
                    "lens": {"type": "string", "enum": sorted(VALID_QC_LENSES)},
                },
                "required": ["required", "lens"],
                "additionalProperties": True,
            },
            "quality_spec": {
                "type": "object",
                "properties": {
                    "required_sections": string_array,
                    "quality_dimensions": {"type": "object"},
                    "hard_failures": string_array,
                    "minimum_counts": {"type": "object"},
                    "minimum_score": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": True,
            },
            "quality_plan": {"type": "object"},
            "components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "description": {"type": "string"},
                    },
                    "required": ["id", "files"],
                    "additionalProperties": True,
                },
            },
            "required_mcp_servers": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
            "next_action": {"type": "string"},
        },
        "required": [
            "task_id",
            "title",
            "status",
            "risk_tier",
            "workspace_scope",
            "objective",
            "worker",
            "outputs",
            "acceptance_checks",
            "qc",
        ],
        "additionalProperties": False,
    }


def goal_schema() -> dict[str, Any]:
    statuses = sorted(VALID_GOAL_STATUSES | {status.lower() for status in VALID_GOAL_STATUSES})
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://github.com/sdageltc/letitloop/raw/main/schemas/goal.schema.json",
        "title": "letitloop goal",
        "type": "object",
        "properties": {
            "goal_id": {"type": "string", "minLength": 1},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "constraints": {"type": "object"},
            "dependencies": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
            "status": {"type": "string", "enum": statuses},
        },
        "required": ["goal_id", "title", "description"],
        "additionalProperties": False,
    }


def mcp_schema() -> dict[str, Any]:
    from .mcp_server import LetItLoopMCPServer

    calls = []
    for tool in LetItLoopMCPServer.get_tool_definitions():
        calls.append(
            {
                "properties": {
                    "name": {"const": tool["name"]},
                    "arguments": tool["inputSchema"],
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            }
        )
    request_id = {"type": ["string", "integer", "null"]}
    base = {"jsonrpc": {"const": "2.0"}, "id": request_id}
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://github.com/sdageltc/letitloop/raw/main/schemas/mcp.schema.json",
        "title": "letitloop MCP stdio request",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    **base,
                    "method": {"const": "initialize"},
                    "params": {"type": "object"},
                },
                "required": ["jsonrpc", "id", "method"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    **base,
                    "method": {"const": "tools/list"},
                    "params": {
                        "type": "object",
                        "properties": {"cursor": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                "required": ["jsonrpc", "id", "method"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    **base,
                    "method": {"const": "tools/call"},
                    "params": {"oneOf": calls},
                },
                "required": ["jsonrpc", "id", "method", "params"],
                "additionalProperties": False,
            },
        ],
    }


SCHEMA_BUILDERS = {"contract": contract_schema, "goal": goal_schema, "mcp": mcp_schema}


def get_schema(kind: str) -> dict[str, Any]:
    try:
        schema = SCHEMA_BUILDERS[kind]()
    except KeyError as exc:
        raise ValueError(f"unknown schema kind: {kind}") from exc
    return copy.deepcopy(schema)
