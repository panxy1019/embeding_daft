__all__ = [
    "exec_sql",
    "ExecSQLToolSpec",
]

from ahvn.utils.db import Database, SQLErrorResponse, table_display, prettify_sql
from ahvn.tool import ToolSpec
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)


from ..utils.config_utils import RUBIK_CM

from typing import Optional, Dict, Any, Literal
from copy import deepcopy


def exec_sql(db: Database, sql: str) -> Dict[str, Any]:
    """\
    Execute a SQL statement on the database and return the results.

    This function executes a SQL query using the provided Database instance.
    For SELECT queries, it returns the results as a list of dictionaries.
    For INSERT, UPDATE, DELETE queries, it returns an empty list.
    If an error occurs, it returns an error in the result dict.

    Args:
        db: The Database instance to execute the query on.
        sql (str): The SQL query to execute.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
            - output: List[Dict[str, Any]] for successful queries
            - msg: Optional informational message
            - err: Optional error message string

    Example:
        >>> db = Database(provider="sqlite", database="test.db")
        >>> result = exec_sql(db, "SELECT * FROM users LIMIT 5")
        >>> if result["err"]:
        >>>     print(result["err"])
        >>> else:
        >>>     print(result["output"])
        [{'id': 1, 'name': 'Alice'}, {'id': 2, 'name': 'Bob'}, ...]
    """
    sql = prettify_sql(sql, dialect=db.dialect, comments=False)
    result = db.execute(sql, autocommit=True, safe=True)
    if isinstance(result, SQLErrorResponse):
        return {"output": None, "msg": None, "err": result.to_string(include_full=False)}
    output = list() if result is None else result.to_list(row_fmt="dict")
    return {"output": output, "msg": None, "err": None}


class ExecSQLToolSpec(ToolSpec):
    @classmethod
    def from_db(
        cls,
        db: Database,
        name: str = "exec_sql",
        max_rows: Optional[int] = None,
        max_width: Optional[int] = None,
        style: Optional[Literal["DEFAULT", "MARKDOWN", "PLAIN_COLUMNS", "MSWORD_FRIENDLY", "ORGMODE", "SINGLE_BORDER", "DOUBLE_BORDER", "RANDOM"]] = None,
        **display_kwargs,
    ):
        max_rows = max_rows if max_rows is not None else RUBIK_CM.get("tools.exec_sql.max_rows", 32)
        max_width = max_width if max_width is not None else RUBIK_CM.get("tools.exec_sql.max_width", 64)
        style = style if style is not None else RUBIK_CM.get("tools.exec_sql.style", "DEFAULT")
        display_kwargs = deepcopy(display_kwargs) if display_kwargs else RUBIK_CM.get("tools.exec_sql.display_kwargs", dict())

        def wrapper(sql: str) -> str:
            """\
            Execute a SQL query on the database and display formatted results.
            ATTENTION: Only use single-line SQL strings and use `` instead of "" for quotes inside the SQL, to avoid escaping issues.

            Args:
                sql (str): The SQL query to execute.

            Returns:
                str: The query results formatted as a table string, or an error message.
            """
            result = exec_sql(db, sql)
            parts = []
            if result["msg"]:
                parts.append(f"[WARNING] {result['msg']}\n")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if not result["output"]:
                parts.append("Query executed successfully. But no rows returned.")
            else:
                parts.append(table_display(result["output"], max_rows=max_rows, max_width=max_width, style=style, **display_kwargs))
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec
