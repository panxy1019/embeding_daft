"""UKFT Builder: construct RubikSQL UKFT objects from Parquet profile data.

This module contains factory functions that build DatabaseUKFT, TableUKFT,
ColumnUKFT, and EnumUKFT objects directly from Daft profiling results,
without requiring a live database connection.

Each function mimics the corresponding RubikSQL `from_*()` classmethod but
takes pre-computed profile data instead of querying a database.
"""

from typing import Dict, List, Any, Optional, Tuple


def build_database_ukft(
    db_id: str,
    table_ids: List[str],
    total_columns: int,
    description: str = "",
) -> Dict[str, Any]:
    """Build a DatabaseUKFT-compatible dictionary from profile data.

    Args:
        db_id: Database identifier (e.g., "sales").
        table_ids: List of table IDs in this database.
        total_columns: Total number of columns across all tables.
        description: Human-readable description (optional).

    Returns:
        Dictionary suitable for constructing a DatabaseUKFT.
    """
    return {
        "name": f"db:{db_id}",
        "content": description or f"Database: {db_id}",
        "content_resources": {
            "db_id": db_id,
            "tabs": table_ids,
            "# tabs": len(table_ids),
            "# cols": total_columns,
        },
        "tags": [
            "[UKF_TYPE:db-database]",
            f"[DATABASE:{db_id}]",
        ],
        "source": "system",
        "verified": True,
        "system": True,
    }


def build_table_ukft(
    db_id: str,
    table_id: str,
    column_ids: List[str],
    total_rows: int,
    primary_keys: Optional[List[str]] = None,
    foreign_keys: Optional[List[Dict]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Build a TableUKFT-compatible dictionary from profile data.

    Args:
        db_id: Database identifier.
        table_id: Table identifier.
        column_ids: List of column IDs in this table.
        total_rows: Total row count.
        primary_keys: List of primary key column IDs.
        foreign_keys: List of FK specs [{col, ref_tab, ref_col}].
        description: Human-readable description (optional).

    Returns:
        Dictionary suitable for constructing a TableUKFT.
    """
    return {
        "name": f"tab:{db_id}.{table_id}",
        "content": description or f"Table: {db_id}.{table_id}",
        "content_resources": {
            "db_id": db_id,
            "tab_id": table_id,
            "# rows": total_rows,
            "# cols": len(column_ids),
            "cols": column_ids,
            "pks": primary_keys or [],
            "fks": foreign_keys or [],
        },
        "tags": [
            "[UKF_TYPE:db-table]",
            f"[DATABASE:{db_id}]",
            f"[TABLE:{table_id}]",
        ],
        "source": "system",
        "verified": True,
        "system": True,
    }


def build_column_ukft(
    db_id: str,
    table_id: str,
    column_id: str,
    dtype: str,
    dtype_anno: Optional[str],
    total_rows: int,
    distinct_count: int,
    null_count: int,
    top_enums: List[str],
    bot_enums: List[str],
    is_pk: bool = False,
    foreign_keys: Optional[List[Dict]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Build a ColumnUKFT-compatible dictionary from profile data.

    Args:
        db_id: Database identifier.
        table_id: Table identifier.
        column_id: Column identifier.
        dtype: Original Parquet data type.
        dtype_anno: Annotated/overridden data type (can be None).
        total_rows: Total row count for the table.
        distinct_count: Number of distinct values.
        null_count: Number of null values.
        top_enums: Top-N most frequent values.
        bot_enums: Bottom-N least frequent values.
        is_pk: Whether this column is a primary key.
        foreign_keys: FK references from this column.
        description: Human-readable description (optional).

    Returns:
        Dictionary suitable for constructing a ColumnUKFT.
    """
    deduced_type = dtype_anno or _deduce_datatype(dtype, distinct_count, total_rows)

    # Compute simple frequency distribution
    if total_rows > 0:
        distinct_ratio = distinct_count / total_rows
        freq_dists = [min(distinct_ratio * (i + 1) / 20, 1.0) for i in range(20)]
    else:
        freq_dists = [0.0] * 20

    return {
        "name": f"col:{db_id}.{table_id}.{column_id}",
        "content": description or f"Column: {db_id}.{table_id}.{column_id}",
        "content_resources": {
            "db_id": db_id,
            "tab_id": table_id,
            "col_id": column_id,
            "datatype_orig": dtype,
            "datatype_anno": dtype_anno,
            "datatype": deduced_type,
            "enum_index": True,
            "# rows": total_rows,
            "# distincts": distinct_count,
            "# null": null_count,
            "null_candidates": [],
            "top_enums": top_enums[:20],
            "bot_enums": bot_enums[-20:] if len(bot_enums) > 20 else bot_enums,
            "freq_dists": freq_dists,
            "is_pk": is_pk,
            "fks": foreign_keys or [],
        },
        "tags": [
            "[UKF_TYPE:db-column]",
            f"[DATABASE:{db_id}]",
            f"[TABLE:{table_id}]",
            f"[COLUMN:{column_id}]",
            f"[DATATYPE:{deduced_type}]",
        ],
        "source": "system",
        "verified": True,
        "system": True,
    }


def build_enum_ukft(
    db_id: str,
    table_id: str,
    column_id: str,
    enum_value: str,
    freq: int,
) -> Dict[str, Any]:
    """Build an EnumUKFT-compatible dictionary from profile data.

    EnumUKFTs are the primary targets for vector embedding (vec-enums engine).

    Args:
        db_id: Database identifier.
        table_id: Table identifier.
        column_id: Column identifier.
        enum_value: The distinct value.
        freq: Frequency count.

    Returns:
        Dictionary suitable for constructing an EnumUKFT.
    """
    enum_str = str(enum_value)

    return {
        "name": f"{table_id}.{column_id}={enum_str}",
        "content": f"Enum: {table_id}.{column_id} = {enum_str}",
        "content_resources": {
            "db_id": db_id,
            "tab_id": table_id,
            "col_id": column_id,
            "enum": enum_str,
            "freq": freq,
            "predicate": {
                "tab": table_id,
                "col": column_id,
                "==": enum_str,
            },
        },
        "tags": [
            "[UKF_TYPE:db-enum]",
            f"[DATABASE:{db_id}]",
            f"[TABLE:{table_id}]",
            f"[COLUMN:{column_id}]",
            f"[ENUM:{enum_str}]",
        ],
        "source": "system",
        "verified": True,
        "system": True,
    }


def _deduce_datatype(dtype: str, distinct_count: int, total_count: int) -> str:
    """Deduce the RubikSQL ColumnType from raw Parquet dtype.

    This is a simplified version of RubikSQL's type deduction logic.
    For production use, consider calling ColumnUKFT.type_deduction() directly.

    Args:
        dtype: Raw Parquet/Arrow data type string.
        distinct_count: Number of distinct values.
        total_count: Total row count.

    Returns:
        One of: INTEGER, FLOAT, DATETIME, CATEGORICAL, IDENTIFIER, TEXT, LONGTEXT, UNKNOWN
    """
    dtype_lower = dtype.lower()

    # Numeric types
    if any(t in dtype_lower for t in ('int', 'integer', 'long')):
        return 'INTEGER'
    if any(t in dtype_lower for t in ('float', 'double', 'decimal', 'number')):
        return 'FLOAT'

    # Temporal types
    if any(t in dtype_lower for t in ('timestamp', 'date', 'time', 'datetime')):
        return 'DATETIME'

    # Boolean
    if 'bool' in dtype_lower:
        return 'CATEGORICAL'

    # Text types - use heuristics
    if any(t in dtype_lower for t in ('string', 'large_string', 'utf8', 'text')):
        if total_count > 0:
            ratio = distinct_count / total_count
            if ratio > 0.99:
                return 'IDENTIFIER'  # Near-unique values = identifier
            if distinct_count <= 64:
                return 'CATEGORICAL'  # Low cardinality = categorical
            if distinct_count > 100000:
                return 'LONGTEXT'
        return 'TEXT'

    return 'UNKNOWN'
