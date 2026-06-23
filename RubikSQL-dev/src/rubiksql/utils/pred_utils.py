__all__ = [
    "pred_to_sql",
]

from ahvn.utils.basic.str_utils import value_repr

from typing import Any, Dict, List, Union


def pred_to_sql(predicate: Dict[str, Any]) -> str:
    """\
    Convert a predicate JSON structure to a SQL predicate string.

    The predicate JSON follows a protocol similar to KLOp.expr:
    - {"FIELD:tab.col": {"==": value}} -> "tab"."col" = 'value'
    - {"FIELD:tab.col": {"IN": [v1, v2]}} -> "tab"."col" IN ('v1', 'v2')
    - {"AND": [...]} -> (expr1) AND (expr2) AND ...
    - {"OR": [...]} -> (expr1) OR (expr2) OR ...
    - {"NOT": expr} -> NOT (expr)
    - {"FIELD:tab.col": {"<": value}} -> "tab"."col" < value
    - {"FIELD:tab.col": {"<=": value}} -> "tab"."col" <= value
    - {"FIELD:tab.col": {">": value}} -> "tab"."col" > value
    - {"FIELD:tab.col": {">=": value}} -> "tab"."col" >= value
    - {"FIELD:tab.col": {"!=": value}} -> "tab"."col" != 'value'
    - {"FIELD:tab.col": {"LIKE": pattern}} -> "tab"."col" LIKE 'pattern'
    - {"FIELD:tab.col": {"ILIKE": pattern}} -> "tab"."col" ILIKE 'pattern'
    - {"FIELD:tab.col": {"BETWEEN": [min, max]}} -> "tab"."col" BETWEEN min AND max
    - {"FIELD:tab.col": {"IS NULL": True}} -> "tab"."col" IS NULL
    - {"FIELD:tab.col": {"IS NOT NULL": True}} -> "tab"."col" IS NOT NULL

    Simplified forms for Enum/Predicate UKFTs:
    - {"tab": tab_id, "col": col_id, "==": value} -> "tab_id"."col_id" = 'value'
    - {"tab": tab_id, "col": col_id, "IN": [v1, v2]} -> "tab_id"."col_id" IN ('v1', 'v2')

    Args:
        predicate: A dictionary representing the predicate in JSON format.

    Returns:
        A SQL predicate string with proper quoting.

    Examples:
        >>> pred_to_sql({"tab": "users", "col": "status", "==": "active"})
        '"users"."status" = \\'active\\''

        >>> pred_to_sql({"tab": "orders", "col": "amount", ">": 100})
        '"orders"."amount" > 100'

        >>> pred_to_sql({"AND": [
        ...     {"tab": "users", "col": "age", ">=": 18},
        ...     {"tab": "users", "col": "status", "==": "active"}
        ... ]})
        '("users"."age" >= 18) AND ("users"."status" = \\'active\\')'
    """
    if not predicate:
        return ""

    # Handle logical operators
    if "AND" in predicate:
        parts = [pred_to_sql(p) for p in predicate["AND"]]
        parts = [p for p in parts if p]
        if len(parts) == 0:
            return ""
        if len(parts) == 1:
            return parts[0]
        return " AND ".join(f"({p})" for p in parts)

    if "OR" in predicate:
        parts = [pred_to_sql(p) for p in predicate["OR"]]
        parts = [p for p in parts if p]
        if len(parts) == 0:
            return ""
        if len(parts) == 1:
            return parts[0]
        return " OR ".join(f"({p})" for p in parts)

    if "NOT" in predicate:
        inner = pred_to_sql(predicate["NOT"])
        return f"NOT ({inner})" if inner else ""

    # Handle FIELD:tab.col format (KLOp.expr style)
    for key, value in predicate.items():
        if key.startswith("FIELD:"):
            field_path = key[6:]  # Remove "FIELD:" prefix
            if "." in field_path:
                tab_id, col_id = field_path.split(".", 1)
            else:
                tab_id, col_id = None, field_path
            return _parse_field_condition(tab_id, col_id, value)

    # Handle simplified form: {"tab": ..., "col": ..., "op": value}
    # Also support {"tab_id": ..., "col_id": ..., "op": value} for compatibility with synonym extraction
    if ("tab" in predicate and "col" in predicate) or ("tab_id" in predicate and "col_id" in predicate):
        tab_id = predicate.get("tab") or predicate.get("tab_id")
        col_id = predicate.get("col") or predicate.get("col_id")
        # Find the operator
        ops = {"==", "!=", "<", "<=", ">", ">=", "IN", "LIKE", "ILIKE", "BETWEEN", "IS NULL", "IS NOT NULL"}
        for op in ops:
            if op in predicate:
                return _parse_field_condition(tab_id, col_id, {op: predicate[op]})

    return ""


def _format_field(tab_id: str, col_id: str) -> str:
    """Format a field reference with proper quoting."""
    if tab_id:
        return f"{value_repr(tab_id)}.{value_repr(col_id)}"
    return value_repr(col_id)


def _format_value(value: Any) -> str:
    """Format a value for SQL with proper quoting."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # String values get quoted
    return value_repr(value)


def _parse_field_condition(tab_id: str, col_id: str, condition: Dict[str, Any]) -> str:
    """Parse a field condition dictionary into SQL."""
    field = _format_field(tab_id, col_id)

    if "==" in condition:
        return f"{field} = {_format_value(condition['=='])}"

    if "!=" in condition:
        return f"{field} != {_format_value(condition['!='])}"

    if "<" in condition:
        return f"{field} < {_format_value(condition['<'])}"

    if "<=" in condition:
        return f"{field} <= {_format_value(condition['<='])}"

    if ">" in condition:
        return f"{field} > {_format_value(condition['>'])}"

    if ">=" in condition:
        return f"{field} >= {_format_value(condition['>='])}"

    if "IN" in condition:
        values = condition["IN"]
        if not values:
            return ""
        formatted_values = ", ".join(_format_value(v) for v in values)
        return f"{field} IN ({formatted_values})"

    if "LIKE" in condition:
        return f"{field} LIKE {_format_value(condition['LIKE'])}"

    if "ILIKE" in condition:
        return f"{field} ILIKE {_format_value(condition['ILIKE'])}"

    if "BETWEEN" in condition:
        min_val, max_val = condition["BETWEEN"]
        return f"{field} BETWEEN {_format_value(min_val)} AND {_format_value(max_val)}"

    if "IS NULL" in condition and condition["IS NULL"]:
        return f"{field} IS NULL"

    if "IS NOT NULL" in condition and condition["IS NOT NULL"]:
        return f"{field} IS NOT NULL"

    # Handle nested AND within a field (e.g., from KLOp.BETWEEN)
    if "AND" in condition:
        parts = []
        for cond in condition["AND"]:
            parts.append(_parse_field_condition(tab_id, col_id, cond))
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return " AND ".join(f"({p})" for p in parts)

    # Handle nested OR within a field
    if "OR" in condition:
        parts = []
        for cond in condition["OR"]:
            parts.append(_parse_field_condition(tab_id, col_id, cond))
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return parts[0]
        return " OR ".join(f"({p})" for p in parts)

    return ""
