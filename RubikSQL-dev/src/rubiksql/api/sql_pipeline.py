"""\
SQL File Processing Pipeline for RubikSQL.

Provides utilities for:
1. Prettifying SQL files with comments retained/removed
2. Adding SQL queries to demo.json with auto-increment ID
"""

__all__ = [
    "prettify_sql_file",
    "add_to_demo_json",
    "add_to_demo_json_by_id",
]

import re
from typing import Optional, Dict, Any

from ahvn.utils.basic.serialize_utils import load_txt, save_txt, load_json, save_json
from ahvn.utils.db.db_utils import prettify_sql


def _parse_sql_header(sql_content: str) -> Dict[str, Any]:
    """\
    Parse SQL file header to extract QUERY and QUERY TIME.

    Expected format in the header comment:
    /*
    QUERY: <question text>
    QUERY TIME: <YYYYMM>
    ...
    */

    Args:
        sql_content: Raw SQL file content.

    Returns:
        Dict with 'query' and 'query_time' keys.
    """
    result = {
        "query": None,
        "query_time": None,
    }

    # Find the header comment block
    header_match = re.search(r"/\*\s*(.*?)\s*\*/", sql_content, re.DOTALL)
    if not header_match:
        return result

    header_text = header_match.group(1)

    # Extract QUERY (single line only - stops at newline)
    query_match = re.search(r"^QUERY:\s*(.+)$", header_text, re.MULTILINE)
    if query_match:
        result["query"] = query_match.group(1).strip()

    # Extract QUERY TIME
    time_match = re.search(r"QUERY\s*TIME:\s*(\d+)", header_text)
    if time_match:
        result["query_time"] = int(time_match.group(1))

    return result


def _get_next_id(demo_data: list) -> str:
    """\
    Get the next auto-increment ID from demo.json data.

    Args:
        demo_data: List of existing demo entries.

    Returns:
        Next ID in format 'Q00001'.
    """
    if not demo_data:
        return "Q00001"

    max_id = 0
    for entry in demo_data:
        entry_id = entry.get("id", "Q00000")
        if entry_id.startswith("Q"):
            try:
                num = int(entry_id[1:])
                max_id = max(max_id, num)
            except ValueError:
                continue

    return f"Q{max_id + 1:05d}"


def prettify_sql_file(
    filepath: str,
    dialect: str = "duckdb",
    comments: bool = True,
    rewrite: bool = True,
) -> str:
    """\
    Prettify a SQL file.

    Args:
        filepath: Path to the SQL file.
        dialect: SQL dialect (default: duckdb).
        comments: Whether to retain comments (default: True).
        rewrite: Whether to rewrite the file in place (default: True).

    Returns:
        The prettified SQL string.
    """
    sql_content = load_txt(filepath)
    prettified = prettify_sql(sql_content, dialect=dialect, comments=comments)

    if rewrite:
        save_txt(prettified, filepath)

    return prettified


def add_to_demo_json(
    filepath: str,
    demo_json_path: str,
    dialect: str = "duckdb",
) -> Dict[str, Any]:
    """\
    Parse a SQL file and add it to demo.json.

    The SQL file should have a header comment with:
    - QUERY: The natural language question
    - QUERY TIME: The query time in YYYYMM format

    The entry will be added with:
    - id: Auto-incremented (Q00001, Q00002, ...)
    - question: Extracted from QUERY
    - context: query_time from QUERY TIME, empty user_profile
    - sql: Prettified SQL without comments
    - metadata: verified=False, empty query_tags

    Args:
        filepath: Path to the SQL file.
        demo_json_path: Path to demo.json.
        dialect: SQL dialect for prettifying (default: duckdb).

    Returns:
        The new entry that was added.

    Raises:
        ValueError: If QUERY or QUERY TIME cannot be extracted.
    """
    # Read and parse the SQL file
    sql_content = load_txt(filepath)
    header_info = _parse_sql_header(sql_content)

    if header_info["query"] is None:
        raise ValueError(f"Could not extract QUERY from SQL file header: {filepath}")
    if header_info["query_time"] is None:
        raise ValueError(f"Could not extract QUERY TIME from SQL file header: {filepath}")

    # Prettify SQL without comments
    prettified_sql = prettify_sql(sql_content, dialect=dialect, comments=False)

    # Load existing demo.json
    demo_data = load_json(demo_json_path)
    if not isinstance(demo_data, list):
        demo_data = []

    # Create new entry
    new_id = _get_next_id(demo_data)
    new_entry = {
        "id": new_id,
        "question": header_info["query"],
        "context": {
            "query_time": header_info["query_time"],
            "user_profile": {
                "occupation": None,
                "caliber": None,
                "currency": None,
                "region": {},
                "department": {},
                "preferences": [],
            },
        },
        "schema": None,
        "sql": prettified_sql,
        "metadata": {
            "difficulty": None,
            "query_tags": [],
            "verified": False,
        },
    }

    # Append and save
    demo_data.append(new_entry)
    save_json(demo_json_path, demo_data, indent=4)

    return new_entry


def add_to_demo_json_by_id(
    filepath: str,
    demo_json_path: str,
    query_id: str,
    dialect: str = "duckdb",
    alt_suffix: Optional[str] = None,
) -> Dict[str, Any]:
    """\
    Parse a SQL file and add it to demo.json using the specified query ID.

    Similar to add_to_demo_json but uses the provided query_id instead of auto-incrementing.
    Supports alternative SQL files via alt_suffix parameter (e.g., '1', '2', etc.).

    Args:
        filepath: Path to the SQL file.
        demo_json_path: Path to demo.json.
        query_id: Query ID to use (e.g., 'Q00017').
        dialect: SQL dialect for prettifying (default: duckdb).
        alt_suffix: Alternative SQL suffix (e.g., '1' for Q00017.1.sql). If provided,
                   the SQL will be stored as 'sql.1' instead of 'sql'.

    Returns:
        The new entry that was added.

    Raises:
        ValueError: If QUERY or QUERY TIME cannot be extracted.
    """
    # Read and parse the SQL file
    sql_content = load_txt(filepath)
    header_info = _parse_sql_header(sql_content)

    if header_info["query"] is None:
        raise ValueError(f"Could not extract QUERY from SQL file header: {filepath}")
    if header_info["query_time"] is None:
        raise ValueError(f"Could not extract QUERY TIME from SQL file header: {filepath}")

    # Prettify SQL without comments
    prettified_sql = prettify_sql(sql_content, dialect=dialect, comments=False)

    # Load existing demo.json
    demo_data = [d for d in load_json(demo_json_path) if d["id"] != query_id]
    if not isinstance(demo_data, list):
        demo_data = []

    # Determine if this is an existing entry or a new one
    existing_entry = None
    for d in load_json(demo_json_path):
        if d["id"] == query_id:
            existing_entry = d
            break

    # Create or update entry
    if existing_entry and alt_suffix:
        # Update existing entry with alternative SQL
        new_entry = existing_entry
        sql_key = f"sql.{alt_suffix}"
        new_entry[sql_key] = prettified_sql
    else:
        # Create new entry with specified ID
        new_entry = {
            "id": query_id,
            "question": header_info["query"],
            "context": {
                "query_time": header_info["query_time"],
                "user_profile": {
                    "occupation": None,
                    "caliber": None,
                    "currency": None,
                    "region": {},
                    "department": {},
                    "preferences": [],
                },
            },
            "schema": None,
            "dialect": dialect,
            "sql": prettified_sql,
            "metadata": {
                "difficulty": None,
                "query_tags": [],
                "order-relevant": None,
                "verified": False,
            },
        }

    # Reorder entry keys to ensure proper order: id, question, context, schema, dialect, sql, sql.1, sql.2, ..., metadata
    # Define key order
    key_order = ["id", "question", "context", "schema", "dialect", "sql", "metadata"]

    # Get all sql alternative keys (sql.1, sql.2, etc.)
    alt_sql_keys = sorted([k for k in new_entry.keys() if k.startswith("sql.")])

    # Build ordered dictionary
    ordered_entry = {}
    for key in key_order[:-1]:  # All keys except metadata
        if key in new_entry:
            ordered_entry[key] = new_entry[key]

    # Insert alternative SQLs after main 'sql' key, before 'metadata'
    for alt_key in alt_sql_keys:
        ordered_entry[alt_key] = new_entry[alt_key]

    # Add metadata at the end
    if "metadata" in new_entry:
        ordered_entry["metadata"] = new_entry["metadata"]

    save_json(sorted(demo_data + [ordered_entry], key=lambda d: d["id"]), demo_json_path, indent=4)

    return new_entry
