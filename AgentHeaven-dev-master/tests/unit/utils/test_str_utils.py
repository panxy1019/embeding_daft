"""Tests for str_utils: format_value 'param' mode."""

import pytest
from ahvn.utils.basic.str_utils import format_value

# ── format_value param mode ───────────────────────────────────────────────


class TestFormatValueParam:
    def test_basic_string_param(self):
        schema = {"mode": "param", "kwargs": {}}
        result = format_value({"type": "string", "description": "A name"}, schema=schema, key="name")
        assert result == "`name`: string — A name"

    def test_required_param(self):
        schema = {"mode": "param", "kwargs": {"required": True}}
        result = format_value({"type": "integer"}, schema=schema, key="count")
        assert result == "`count`: integer *(required)*"

    def test_enum_param(self):
        schema = {"mode": "param", "kwargs": {}}
        result = format_value({"type": "string", "enum": ["a", "b", "c"]}, schema=schema, key="mode")
        assert result == "`mode`: string (a, b, c)"

    def test_default_param(self):
        schema = {"mode": "param", "kwargs": {}}
        result = format_value({"type": "integer", "default": 10}, schema=schema, key="limit")
        assert result == "`limit`: integer = 10"

    def test_full_param(self):
        schema = {"mode": "param", "kwargs": {"required": True}}
        value = {"type": "string", "enum": ["json", "csv"], "default": "json", "description": "Output format"}
        result = format_value(value, schema=schema, key="fmt")
        assert "`fmt`: string *(required)*" in result
        assert "(json, csv)" in result
        assert "= json" in result
        assert "— Output format" in result

    def test_missing_type_defaults_to_any(self):
        schema = {"mode": "param", "kwargs": {}}
        result = format_value({}, schema=schema, key="x")
        assert "`x`: any" in result

    def test_no_description_no_dash(self):
        schema = {"mode": "param", "kwargs": {}}
        result = format_value({"type": "boolean"}, schema=schema, key="flag")
        assert result == "`flag`: boolean"
        assert "—" not in result
