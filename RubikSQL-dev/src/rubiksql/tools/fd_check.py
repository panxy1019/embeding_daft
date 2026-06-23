__all__ = [
    "fd_check",
    "FDCheckToolSpec",
]

from ahvn.tool import ToolSpec
from ahvn.utils.db import Database
from ahvn.utils.basic.log_utils import get_logger
from sqlalchemy import MetaData, Table, select, func, cast, String, literal

logger = get_logger(__name__)

from ..klbase import RubikSQLKLBase

from typing import List, Dict, Any

from .db_info import get_col_kl


def fd_check(
    kb: RubikSQLKLBase,
    db: Database,
    db_id: str,
    tab_id: str,
    X: List[str],
    Y: List[str],
) -> Dict[str, Any]:
    """\
    Check functional dependency: X -> Y

    Returns True if and only if for any identical X values, all Y values are the same.
    That is, X functionally determines Y.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        db (Database): The database instance.
        db_id (str): Database identifier.
        tab_id (str): Table identifier (table name).
        X (List[str]): List of column names (col_id) that form the determinant.
            Should be column names only, without table prefix (e.g., "id", not "table.id").
        Y (List[str]): List of column names (col_id) that are determined.
            Should be column names only, without table prefix (e.g., "name", not "table.name").

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
            - output: bool, True if functional dependency holds, False otherwise
            - msg: Optional informational message
            - err: Optional error message string

    Example:
        >>> result = fd_check(kb, db, "test_db", "students", ["id"], ["age", "height"])
        >>> if result["err"]:
        >>>     print(result["err"])
        >>> else:
        >>>     print(f"Functional dependency holds: {result['output']}")
    """
    if not X or not Y:
        return {"output": None, "msg": None, "err": "Both X and Y must be non-empty lists."}

    try:
        # Normalize column names (strip whitespace) and check for empty
        all_col_ids = [col_id.strip() for col_id in X + Y]
        empty_cols = [col_id for col_id in all_col_ids if not col_id]
        if empty_cols:
            return {
                "output": None,
                "msg": None,
                "err": f"Empty column names found: {empty_cols}",
            }

        # Build SQL query using SQLAlchemy ORM to avoid explicit SQL strings
        # SQLAlchemy will automatically validate column existence when accessing table.c[col]
        metadata = MetaData()
        table = Table(tab_id, metadata, autoload_with=db.engine)

        # Get SQLAlchemy Column objects for X and Y (directly use slices)
        # This will raise KeyError if column doesn't exist, which we catch below
        try:
            X_col_objs = [table.c[col] for col in all_col_ids[: len(X)]]
            Y_col_objs = [table.c[col] for col in all_col_ids[len(X) :]]
        except KeyError as e:
            missing_col = str(e).split("'")[1] if "'" in str(e) else "unknown"
            raise ValueError(f'Column "{missing_col}" not found in table "{tab_id}".')

        # Verify all columns exist in the knowledge base (for consistency with other tools)
        for col_id in all_col_ids:
            try:
                get_col_kl(kb, db_id=db_id, tab_id=tab_id, col_id=col_id)
            except ValueError:
                raise ValueError(f'Column "{col_id}" not found in table "{tab_id}".')

        # Build Y expression: for multiple columns, concatenate with delimiter
        # Use COALESCE to handle NULL values consistently
        if len(Y_col_objs) == 1:
            y_expr = func.coalesce(cast(Y_col_objs[0], String), literal("__NULL__"))
        else:
            # Concatenate multiple Y columns: y1 || '|||' || y2 || '|||' || y3 ...
            y_parts = [func.coalesce(cast(col, String), literal("__NULL__")) for col in Y_col_objs]
            y_expr = y_parts[0]
            for part in y_parts[1:]:
                y_expr = y_expr.concat(literal("|||")).concat(part)

        # Check if table is empty (no rows)
        # If table is empty, functional dependency trivially holds (no violations possible)
        count_query = select(func.count()).select_from(table)
        try:
            count_result = db.orm_execute(count_query, autocommit=True)
            count_rows = count_result.to_list(row_fmt="dict")
            if count_rows and count_rows[0].get("count_1", 0) == 0:
                # Empty table: functional dependency trivially holds
                return {
                    "output": True,
                    "msg": "Table is empty, functional dependency trivially holds.",
                    "err": None,
                }
        except Exception as e:
            logger.warning(f"Failed to check table row count: {e}")
            # Continue with the main query

        # Build subquery: GROUP BY X, COUNT(DISTINCT Y)
        # This finds groups where the same X values map to different Y values
        subquery = (
            select(*X_col_objs, func.count(func.distinct(y_expr)).label("cnt"))
            .select_from(table)
            .group_by(*X_col_objs)
            .having(func.count(func.distinct(y_expr)) > 1)
        ).subquery()

        # Outer query: COUNT(*) to get number of violations
        query = select(func.count().label("violation_count")).select_from(subquery)

        # Execute query using orm_execute for SQLAlchemy objects
        try:
            result = db.orm_execute(query, autocommit=True)
            rows = result.to_list(row_fmt="dict")
            # COUNT query should always return at least one row
            if not rows:
                return {
                    "output": None,
                    "msg": None,
                    "err": "Unexpected query result format.",
                }
            violation_count = rows[0].get("violation_count", 0)
        except Exception as e:
            logger.error(f"SQL execution failed: {e}")
            return {
                "output": None,
                "msg": None,
                "err": f"SQL execution failed: {str(e)}",
            }
        fd_holds = violation_count == 0

        return {
            "output": fd_holds,
            "msg": None,
            "err": None,
        }

    except ValueError as e:
        return {"output": None, "msg": None, "err": str(e)}
    except Exception as e:
        logger.error(f"fd_check failed: {e}")
        return {"output": None, "msg": None, "err": f"Unexpected error: {str(e)}"}


class FDCheckToolSpec(ToolSpec):
    @classmethod
    def from_kb_and_db(
        cls,
        kb: RubikSQLKLBase,
        db: Database,
        db_id: str,
        name: str = "fd_check",
    ):
        def wrapper(tab_id: str, X: List[str], Y: List[str]) -> str:
            result = fd_check(kb=kb, db=db, db_id=db_id, tab_id=tab_id, X=X, Y=Y)
            parts = []
            if result["msg"]:
                parts.append(f"[INFO] {result['msg']}")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"] is not None:
                parts.append(str(result["output"]))
            else:
                parts.append("Unable to determine functional dependency.")
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec
