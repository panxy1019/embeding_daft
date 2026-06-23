"""Shared helpers for tool-related CLI commands."""

from __future__ import annotations

from typing import Any, Dict, List


def parse_kv_args(args: List[str]) -> Dict[str, Any]:
    """Parse ``key=value`` arguments into a dictionary."""
    from ..utils.basic.type_utils import autotype

    parsed: Dict[str, Any] = {}
    for arg in args:
        idx = arg.find("=")
        if idx < 0:
            raise ValueError(f"Invalid argument '{arg}': expected key=value format.")

        key = arg[:idx]
        value = autotype(arg[idx + 1 :])
        if key in parsed:
            existing = parsed[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                parsed[key] = [existing, value]
        else:
            parsed[key] = value
    return parsed
