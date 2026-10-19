"""Schema-driven benchmark inputs, shared by HTTP validation and saved run validation."""
from __future__ import annotations

import math
from .protocol import robot_data_pb2 as wire


def encode_inputs(definition: dict, supplied: dict) -> list[wire.DebugCommandArgumentValue]:
    definitions = {item["id"]: item for item in definition.get("inputs", [])}
    unknown = set(supplied) - set(definitions)
    if unknown:
        raise ValueError(f"Unknown benchmark inputs: {', '.join(sorted(unknown))}")
    encoded = []
    for key, item in definitions.items():
        kind = item.get("valueType", "").lower()
        value = supplied.get(key)
        if key not in supplied:
            default = item.get("defaultValue", {})
            field = {"float64": "float64Value", "int64": "int64Value", "enum": "enumValue",
                     "boolean": "booleanValue", "string": "stringValue"}.get(kind)
            if field in default:
                value = default[field]
                if kind == "int64": value = int(value)
            elif item.get("required", False):
                raise ValueError(f"Required benchmark input: {key}")
            else:
                continue
        result = wire.DebugCommandArgumentValue(id=key)
        if kind in ("float64", "int64"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
            if kind == "int64" and (not isinstance(value, int) or not -(2**63) <= value < 2**63):
                raise ValueError(f"{key} must be an integer")
            if value < item.get("minFloat64", item.get("min", 0)) or value > item.get("maxFloat64", item.get("max", 0)):
                raise ValueError(f"{key} is outside its advertised range")
            if kind == "int64": result.int64_value = value
            else: result.float64_value = value
        elif kind == "boolean":
            if not isinstance(value, bool): raise ValueError(f"{key} must be boolean")
            result.boolean_value = value
        elif kind == "enum":
            if not isinstance(value, str) or value not in {o["id"] for o in item.get("enumOptions", [])}:
                raise ValueError(f"{key} must be an advertised choice")
            result.enum_value = value
        elif kind == "string":
            if not isinstance(value, str): raise ValueError(f"{key} must be text")
            result.string_value = value
        else:
            raise ValueError(f"Unsupported input type: {kind}")
        encoded.append(result)
    return encoded
