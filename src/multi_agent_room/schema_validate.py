"""T-M10-04：轻量 schema 校验与错误映射。"""

from __future__ import annotations

from typing import Any


class SchemaError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}:{message}")
        self.code = code
        self.message = message


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
    """校验 required + 基础 type；失败抛 SchemaError（房间可读）。"""
    if not isinstance(args, dict):
        raise SchemaError("schema_type", "参数必须为 object")
    required = list(schema.get("required") or [])
    missing = [k for k in required if k not in args or args[k] is None]
    if missing:
        raise SchemaError(
            "schema_required",
            f"缺少必填字段: {', '.join(missing)}",
        )
    props = schema.get("properties") or {}
    for key, rules in props.items():
        if key not in args:
            continue
        expected = rules.get("type")
        if not expected:
            continue
        val = args[key]
        if not _type_ok(expected, val):
            raise SchemaError(
                "schema_type",
                f"字段 {key} 期望 {expected}，实际 {type(val).__name__}",
            )


def _type_ok(expected: str, val: Any) -> bool:
    if expected == "string":
        return isinstance(val, str)
    if expected == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if expected == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    if expected == "boolean":
        return isinstance(val, bool)
    if expected == "array":
        return isinstance(val, list)
    if expected == "object":
        return isinstance(val, dict)
    return True
