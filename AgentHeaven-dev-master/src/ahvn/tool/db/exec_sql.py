__all__ = [
    "execute_sql",
    "toolspec_factory_builtins_execute_sql",
]

import re

from ...utils.db import Database, table_display, SQLResponse
from ...utils.db.db_utils import is_sql_readonly
from ...utils.basic.config_utils import CM_AHVN
from ..base import ToolSpec
from typing import Optional, List, Dict, Any, Literal, Union


def _try_simple_table_heal(query: str, error_message: str) -> str:
    """Try to fix a table-name typo using the fuzzy suggestion in the error message.

    Applies only when ``_add_suggestions`` embedded a "Did you mean" hint in the
    error message (currently supported for SQLite-style "no such table:" errors).
    Returns an empty string when no actionable suggestion is found.
    """
    table_match = re.search(r"no such table:\s*(\w+)", error_message, re.IGNORECASE)
    suggestion_match = re.search(r"Did you mean '(\w+)'\?", error_message)
    if table_match and suggestion_match:
        wrong_table = table_match.group(1)
        correct_table = suggestion_match.group(1)
        healed = re.sub(r"\b" + re.escape(wrong_table) + r"\b", correct_table, query, flags=re.IGNORECASE)
        return healed
    return ""


def execute_sql(db, query: str, *, heal_sql: bool = True) -> Union[List[Dict[str, Any]], SQLResponse]:
    """\
    Execute a SQL statement on the database and return the results.

    This function executes a SQL query using the provided Database instance.
    For SELECT queries, it returns the results as a list of dictionaries.
    For INSERT, UPDATE, DELETE queries, it returns an empty list.
    If an error occurs, it returns an ``SQLResponse`` with ``ok=False``.

    Args:
        db: The Database instance to execute the query on.
        query (str): The SQL query to execute.
        heal_sql (bool): Whether to attempt a one-shot SQL healing fallback
            when initial execution fails.

    Returns:
        Union[List[Dict[str, Any]], SQLResponse]: The query results as a list of dictionaries,
            an empty list for write operations, or an error SQLResponse on failure.

    Example:
        >>> db = Database(provider="sqlite", database="test.db")
        >>> result = execute_sql(db, "SELECT * FROM users LIMIT 5")
        >>> if isinstance(result, SQLResponse) and not result.ok:
        >>>     print(result.to_str())
        >>> else:
        >>>     print(result)
        [{'id': 1, 'name': 'Alice'}, {'id': 2, 'name': 'Bob'}, ...]
    """
    result = db.execute(query, safe=True, readonly=is_sql_readonly(query, dialect=db.dialect))
    if not result.ok and heal_sql:
        # First try a simple, deterministic fix for TableNotFound errors.
        if result.error_type == "TableNotFound":
            simple_healed = _try_simple_table_heal(query, result.error_message)
            if simple_healed and simple_healed.strip() != str(query).strip():
                simple_result = db.execute(simple_healed, safe=True, readonly=is_sql_readonly(simple_healed, dialect=db.dialect))
                if simple_result.ok:
                    return simple_result.to_list(row_fmt="dict")
        # Fall back to LLM-based healing for other failures.
        try:
            healed_query = db.heal_sql(query)
        except Exception:
            healed_query = ""
        if healed_query and healed_query.strip() and healed_query.strip() != str(query).strip():
            healed_result = db.execute(healed_query, safe=True, readonly=is_sql_readonly(healed_query, dialect=db.dialect))
            if healed_result.ok:
                return healed_result.to_list(row_fmt="dict")
    if not result.ok:
        return result
    return result.to_list(row_fmt="dict")


def toolspec_factory_builtins_execute_sql(
    db: Database,
    heal_sql: bool = True,
    max_rows: Optional[int] = None,
    max_width: Optional[int] = None,
    style: Optional[Literal["DEFAULT", "MARKDOWN", "PLAIN_COLUMNS", "MSWORD_FRIENDLY", "ORGMODE", "SINGLE_BORDER", "DOUBLE_BORDER", "RANDOM"]] = None,
    name: Optional[str] = "exec_sql",
    **table_display_kwargs,
) -> ToolSpec:
    """\
    Create a ToolSpec for executing SQL queries with a specific Database instance bound.

    This factory function creates a ToolSpec from the execute_sql function, binds
    the database parameter to a specific Database instance, and wraps the output
    with table_display for formatted results.

    Display parameters default to values from config (db.display section).
    Explicitly provided parameters override config defaults.

    Args:
        db (Database): The Database instance to bind to the tool.
        heal_sql (bool): Whether to auto-attempt SQL healing when execution fails.
        max_rows (int, optional): Maximum number of rows to display. Defaults to config value (`db.display.max_rows`).
        max_width (int, optional): Maximum width for each column. Defaults to config value (`db.display.max_width`).
        style (Literal, optional): The style to use for table display. Defaults to config value (`db.display.style`).
        **table_display_kwargs: Additional keyword arguments passed to table_display.

    Returns:
        ToolSpec: A ToolSpec with the database parameter bound, ready to execute SQL queries with formatted output.

    Example:
        >>> db = Database(provider="sqlite", database="test.db")
        >>> # Use config defaults
        >>> tool = toolspec_factory_builtins_execute_sql(db)
        >>> # Override specific parameters
        >>> tool = toolspec_factory_builtins_execute_sql(db, max_rows=20, style="SINGLE_BORDER")
        >>> result = tool.call(query="SELECT * FROM users LIMIT 5")
        >>> print(result)
        # Formatted table output
    """
    # Get defaults from config
    display_config = CM_AHVN.get("db.display", {})
    max_rows = max_rows if max_rows is not None else display_config.get("max_rows", 64)
    max_width = max_width if max_width is not None else display_config.get("max_width", 64)
    style = style if style is not None else display_config.get("style", "DEFAULT")

    # Create a wrapper function that formats the output with table_display
    def execute_sql_formatted(db, query: str) -> str:
        """\
        Execute a SQL query on the database and return formatted results.

        Args:
            db: The Database instance to execute the query on.
            query (str): The SQL query to execute.

        Returns:
            str: The query results formatted as a table string, or an error message.
        """
        result = execute_sql(db, query, heal_sql=heal_sql)

        # Handle error response
        if isinstance(result, SQLResponse) and not result.ok:
            return result.to_str(include_traceback=False)

        # Handle empty result
        if not result:
            return "Query executed successfully. But no rows returned."

        # Format successful result as table
        return table_display(result, max_rows=max_rows, max_width=max_width, style=style, **table_display_kwargs)

    # Create a ToolSpec from the wrapped function
    tool_spec = ToolSpec.from_func(
        func=execute_sql_formatted,
        parse_docstring=True,
        description="Execute a SQL query on the database and return the results as a formatted table.",
        name=name,
    )

    # Bind the db parameter to the specific Database instance
    tool_spec.bind(param="db", state_key=None, default=db)

    return tool_spec
