__all__ = [
    "submit_sql",
    "SubmitSQLToolSpec",
]

from ahvn.utils.db import Database, table_display
from ahvn.tool import ToolSpec
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)


from ..utils.config_utils import RUBIK_CM

from typing import Dict, Any, Optional, Literal
from copy import deepcopy

from .exec_sql import exec_sql


def submit_sql(db: Database, query: str) -> Dict[str, Any]:
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
    return exec_sql(db, query)


class SubmitSQLToolSpec(ToolSpec):
    @classmethod
    def from_db(
        cls,
        db: Database,
        name: str = "submit_sql",
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
            Submit a compilable SQL query as your final answer. Your answer will be validated by execution.
            If there is an error, you will receive an error message and can try again.

            Args:
                sql (str): The SQL query to submit.

            Returns:
                str: The query results formatted as a table string, or an error message.
            """
            result = submit_sql(db, sql)
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
