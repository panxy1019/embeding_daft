__all__ = [
    "SQLResponse",
    "Database",
    "table_display",
]

from .db_utils import *
from .sql_processor import SQLProcessor, SQLGlotProcessor, create_sql_processor
from .sql_healer import SQLHealer, create_sql_healer
from .spec import DATABASE_CONFIG_ENGINE
from ..basic.request_utils import NetworkProxy
from ..basic.log_utils import get_logger
from ..basic.debug_utils import error_str, raise_mismatch, DatabaseError
from ..basic.misc_utils import unique
from typing import Callable, Iterable, List, Dict, Tuple, Any, Union, Optional, Literal, Generator
from copy import deepcopy
from difflib import SequenceMatcher

import re
import time as time_mod
import traceback as tb_mod
import warnings
import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
from sqlalchemy.sql.elements import ClauseElement

import prettytable as pt

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Error classification (replaces DatabaseErrorHandler)
# ---------------------------------------------------------------------------

_ERROR_PATTERNS = {
    sa_exc.OperationalError: [
        (["no such table"], "TableNotFound"),
        (["no such column"], "ColumnNotFound"),
        (["database is locked"], "DatabaseLocked"),
        (["disk i/o error"], "DiskIOError"),
    ],
    sa_exc.ProgrammingError: [
        (["syntax error", "parser error"], "SyntaxError"),
    ],
    sa_exc.IntegrityError: [
        (["foreign key constraint"], "ForeignKeyViolation"),
        (["unique constraint", "not unique"], "UniqueViolation"),
        (["not null constraint", "may not be null"], "NotNullViolation"),
    ],
    sa_exc.DataError: [],
}


def _classify_error(
    e: BaseException,
    query: Optional[str] = None,
    params: Optional[Any] = None,
    db: Optional["Database"] = None,
) -> Tuple[str, str]:
    """\
    Classify a database exception into (error_type, error_message).

    Args:
        e: The exception to classify.
        query: The SQL query that caused the error (for context).
        params: The parameters used with the query.
        db: Optional Database instance for context-aware suggestions.

    Returns:
        Tuple[str, str]: (error_type, error_message).
    """
    orig = getattr(e, "orig", None)
    error_msg = str(orig) if orig else str(e)
    lower_msg = error_msg.lower()

    for exc_type, patterns in _ERROR_PATTERNS.items():
        if not isinstance(e, exc_type):
            continue
        for keywords, error_type in patterns:
            if any(kw in lower_msg for kw in keywords):
                # Add table-name suggestions for "TableNotFound"
                if error_type == "TableNotFound" and db is not None:
                    error_msg = _add_suggestions(
                        error_msg,
                        r"no such table:\s*(\w+)",
                        lambda: db.db_tabs(),
                    )
                return error_type, error_msg
        # Matched the exception class but no keyword — use class name
        return exc_type.__name__, error_msg

    return "UnknownError", error_msg


def _add_suggestions(error_msg: str, pattern: str, get_options_func) -> str:
    """Append fuzzy-match suggestions to an error message."""
    match = re.search(pattern, error_msg, re.IGNORECASE)
    if not match:
        return error_msg
    item_name = match.group(1)
    try:
        options = get_options_func()

        def similarity(a, b):
            return SequenceMatcher(None, str(a), str(b)).ratio()

        sorted_options = sorted(options, key=lambda x: similarity(item_name, x), reverse=True)
        suggestion = sorted_options[0] if sorted_options and similarity(item_name, sorted_options[0]) >= 0.3 else None
        lines = [f"Did you mean '{suggestion}'?"] if suggestion else []
        lines.append(f"Available options: {', '.join(repr(opt) for opt in options)}.")
        return error_msg + "\n" + "\n".join(lines)
    except Exception:
        return error_msg


# ---------------------------------------------------------------------------
# SQLResponse — unified result for all SQL operations
# ---------------------------------------------------------------------------


class SQLResponse:
    """\
    Unified result of any SQL execution — success or failure.

    Every ``Database.execute()`` / ``orm_execute()`` returns an ``SQLResponse``.
    Use ``bool(result)`` or ``result.ok`` to check if the operation succeeded.

    Success path::

        result = db.execute("SELECT * FROM users")
        assert result.ok
        for row in result:
            print(row)

    Error path (safe mode)::

        result = db.execute("SELECT * FROM nonexistent", safe=True)
        if not result:
            print(result.error_type, result.error_message)
            result.raise_on_error()   # re-raise original exception
    """

    __slots__ = (
        "ok",
        # success
        "_columns",
        "_rows",
        "_row_count",
        "_lastrowid",
        # error
        "_error_type",
        "_error_message",
        "_exception",
        # metadata (tracked on both success and error)
        "_query",
        "_params",
        "_elapsed",
    )

    # --- private constructor ---------------------------------------------------

    def __init__(self) -> None:
        # All construction goes through classmethods below.
        self.ok: bool = True
        self._columns: List[str] = []
        self._rows: List[Dict[str, Any]] = []
        self._row_count: int = 0
        self._lastrowid: Optional[int] = None
        self._error_type: str = ""
        self._error_message: str = ""
        self._exception: Optional[BaseException] = None
        self._query: Optional[str] = None
        self._params: Optional[Any] = None
        self._elapsed: Optional[float] = None

    # --- classmethods (the only intended construction paths) --------------------

    @classmethod
    def _from_cursor(
        cls,
        cursor_result,
        query: Optional[str] = None,
        params: Optional[Any] = None,
        elapsed: Optional[float] = None,
    ) -> "SQLResponse":
        """Construct a success response from a SQLAlchemy CursorResult."""
        resp = cls()
        resp._query = query
        resp._params = params
        resp._elapsed = elapsed

        # columns
        try:
            resp._columns = list(cursor_result.keys()) if hasattr(cursor_result, "keys") else []
        except Exception:
            resp._columns = []

        # rows (eagerly fetched)
        try:
            raw_rows = cursor_result.fetchall()
            for row in raw_rows:
                if hasattr(row, "_mapping"):
                    resp._rows.append(dict(row._mapping))
                else:
                    resp._rows.append(dict(zip(resp._columns, row)))
        except Exception:
            pass  # DDL / DML with no result set

        # row_count — NOTE: the correct attribute is .rowcount (no underscore)
        try:
            resp._row_count = getattr(cursor_result, "rowcount", -1)
        except Exception:
            resp._row_count = -1

        # lastrowid
        try:
            resp._lastrowid = getattr(cursor_result, "lastrowid", None)
        except Exception:
            resp._lastrowid = None

        return resp

    @classmethod
    def _from_error(
        cls,
        exception: BaseException,
        query: Optional[str] = None,
        params: Optional[Any] = None,
        db: Optional["Database"] = None,
        elapsed: Optional[float] = None,
    ) -> "SQLResponse":
        """Construct an error response from an exception."""
        resp = cls()
        resp.ok = False
        resp._exception = exception
        resp._query = query
        resp._params = params
        resp._elapsed = elapsed
        resp._error_type, resp._error_message = _classify_error(exception, query, params, db)
        return resp

    @classmethod
    def empty(
        cls,
        query: Optional[str] = None,
        params: Optional[Any] = None,
        elapsed: Optional[float] = None,
    ) -> "SQLResponse":
        """Construct an empty success response (e.g. for DDL that returns nothing)."""
        resp = cls()
        resp._query = query
        resp._params = params
        resp._elapsed = elapsed
        return resp

    # --- success properties -----------------------------------------------------

    @property
    def columns(self) -> List[str]:
        """\
        Column names from the result set.

        Returns:
            List[str]: Column names. Empty list for DDL/DML or errors.
        """
        return list(self._columns)

    @property
    def rows(self) -> List[Dict[str, Any]]:
        """\
        All result rows as a list of dictionaries.

        Returns:
            List[Dict[str, Any]]: Rows. Empty list for DDL/DML or errors.
        """
        return list(self._rows)

    @property
    def shape(self) -> Tuple[int, int]:
        """\
        Shape of the result set as (row_count, column_count).

        Returns:
            Tuple[int, int]: (number of rows, number of columns). (0, 0) for DDL/DML or errors.
        """
        return len(self._rows), len(self._columns)

    @property
    def size(self) -> Tuple[int, int]:
        """\
        Alias for shape.

        Returns:
            Tuple[int, int]: (number of rows, number of columns).
        """
        return self.shape

    @property
    def row_count(self) -> int:
        """\
        Number of rows affected by the operation.
        Notice that this is different from len(rows) for DDL/DML operations, where rows may be empty but row_count indicates how many rows were affected.

        For SELECT queries this is typically the number of rows fetched.
        For INSERT/UPDATE/DELETE this is the number of rows affected.
        Returns -1 if the information is not available.
        """
        return self._row_count

    @property
    def lastrowid(self) -> Optional[int]:
        """\
        Last inserted row ID (if supported by the backend).

        Returns:
            Optional[int]: The last inserted row ID, or None.
        """
        return self._lastrowid

    # --- error properties -------------------------------------------------------

    @property
    def error_type(self) -> str:
        """\
        Classified error category (e.g. ``"TableNotFound"``, ``"SyntaxError"``).

        Returns:
            str: Error type. Empty string when ``ok=True``.
        """
        return self._error_type

    @property
    def error_message(self) -> str:
        """\
        Human-readable error message.

        Returns:
            str: Error message. Empty string when ``ok=True``.
        """
        return self._error_message

    @property
    def exception(self) -> Optional[BaseException]:
        """\
        Original exception with full traceback preserved.

        Returns:
            Optional[BaseException]: The exception, or None when ``ok=True``.
        """
        return self._exception

    @property
    def query(self) -> Optional[str]:
        """\
        The executed SQL query (available on both success and error responses).

        Returns:
            Optional[str]: The query string, or None.
        """
        return self._query

    @property
    def params(self) -> Optional[Any]:
        """\
        The parameters used with the query (available on both success and error responses).

        Returns:
            Optional[Any]: The parameters, or None.
        """
        return self._params

    @property
    def elapsed(self) -> Optional[float]:
        """\
        Wall-clock execution time in seconds.

        Measured around the actual ``conn.execute()`` call (excludes connection
        setup, commit, and result serialisation).

        Returns:
            Optional[float]: Seconds, or None if not measured.
        """
        return self._elapsed

    # --- error utilities --------------------------------------------------------

    def raise_on_error(self) -> None:
        """\
        Re-raise the stored exception if this is an error response.

        Does nothing when ``ok=True``.

        Raises:
            The original exception (with traceback preserved).
        """
        if not self.ok and self._exception is not None:
            raise self._exception

    def traceback(self) -> Optional[str]:
        """\
        Formatted traceback string of the stored exception.

        Returns:
            Optional[str]: The traceback string, or None when ``ok=True``.
        """
        if self._exception is None or self._exception.__traceback__ is None:
            return None
        return "".join(tb_mod.format_exception(type(self._exception), self._exception, self._exception.__traceback__))

    def to_str(self, include_traceback: bool = False) -> str:
        """\
        Format the response as a human-readable string.

        For success: returns a summary of rows/columns.
        For error: returns structured error information.

        Args:
            include_traceback: Include full traceback for errors. Defaults to False.

        Returns:
            str: Formatted string.
        """
        if self.ok:
            return f"OK — {len(self._rows)} rows, {len(self._columns)} columns"
        lines = [
            "Database query execution failed.",
            f"Error Type: {self._error_type}",
            f"Error: {self._error_message}",
        ]
        if self._query:
            lines.append(f"Query: {self._query}")
        if self._params:
            lines.append(f"Params: {self._params}")
        if include_traceback:
            tb_str = self.traceback()
            if tb_str:
                lines.append(f"Traceback:\n{tb_str}")
        return "\n".join(lines)

    # --- data access (success path) ---------------------------------------------

    def to_list(
        self,
        row_fmt: Literal["dict", "tuple", "list"] = "dict",
        columns: Optional[List[Union[str, int]]] = None,
    ) -> Union[List[Dict[str, Any]], List[Tuple], List[List]]:
        """\
        Export rows as a list, optionally selecting a subset of columns.

        Args:
            row_fmt: Output format per row.
                - ``"dict"`` (default) — list of ``{col: val}`` dicts.
                - ``"tuple"`` — list of value tuples.
                - ``"list"``  — list of value lists.
            columns: Optional list of column names (str) or indices (int) to include.
                When ``None``, all columns are included.

        Returns:
            List of dicts, tuples, or lists.

        Examples::

            result.to_list()                          # all rows, all columns (dicts)
            result.to_list("tuple")                    # all rows as tuples
            result.to_list(columns=["name", "age"])    # subset columns
            result.to_list("list", columns=[0, 2])     # subset by index, as lists
        """
        rows = self._rows
        if columns is not None:
            # Resolve column specs to names for consistent dict-key output
            col_names = []
            for c in columns:
                if isinstance(c, int):
                    if not (-len(self._columns) <= c < len(self._columns)):
                        raise ValueError(f"Column index {c} out of range for {len(self._columns)} columns")
                    col_names.append(self._columns[c])
                else:
                    if c not in self._columns:
                        raise ValueError(f"Column '{c}' not found. Available: {self._columns}")
                    col_names.append(c)
            rows = [{k: r[k] for k in col_names} for r in rows]

        if row_fmt == "dict":
            return deepcopy(rows)
        if row_fmt == "tuple":
            return [tuple(row.values()) for row in rows]
        if row_fmt == "list":
            return [list(row.values()) for row in rows]
        raise_mismatch(["dict", "tuple", "list"], got=row_fmt, name="row format")

    # --- convenience accessors ---------------------------------------------------

    def fetchall(self) -> List[Dict[str, Any]]:
        """\
        Return all rows as a list of dicts (backward-compatible alias).

        Equivalent to ``to_list(row_fmt="dict")``.
        """
        return self.to_list(row_fmt="dict")

    def scalar(self) -> Any:
        """\
        Return the first column value of the first row, or ``None``.

        Useful for ``SELECT COUNT(*)`` or single-value queries::

            count = db.execute("SELECT COUNT(*) FROM users").scalar()
        """
        if not self._rows:
            return None
        first_row = self._rows[0]
        return next(iter(first_row.values()), None)

    def first(self) -> Optional[Dict[str, Any]]:
        """\
        Return the first row as a dict, or ``None`` if no rows.

        Example::

            user = db.execute("SELECT * FROM users LIMIT 1").first()
            if user:
                print(user["name"])
        """
        return dict(self._rows[0]) if self._rows else None

    def column(self, col: Union[str, int]) -> List[Any]:
        """\
        Return all values of a single column as a list (series).

        Args:
            col: Column name (str) or positional index (int).

        Returns:
            List[Any]: All values for that column across rows.

        Example::

            names = result.column("name")
            ids   = result.column(0)
        """
        return [self._get_col_value(r, col) for r in self._rows]

    def to_pd(self):
        """\
        Export results as a ``pandas.DataFrame``.

        Returns:
            pandas.DataFrame: DataFrame with column names from the query.

        Raises:
            ImportError: If pandas is not installed.

        Example::

            df = db.execute("SELECT * FROM users").to_pd()
            df.head()
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for to_pd(). Install it with: pip install pandas")
        return pd.DataFrame(self._rows, columns=self._columns)

    def clone(self) -> "SQLResponse":
        """\
        Create a deep copy of this response.

        Returns:
            SQLResponse: Independent copy.
        """
        return deepcopy(self)

    # --- indexing ---------------------------------------------------------------

    def _get_col_value(self, row: Dict[str, Any], col: Union[str, int]) -> Any:
        """Extract a column value from a row by name or index."""
        if isinstance(col, str):
            if col not in row:
                raise ValueError(f"Column '{col}' not found in row")
            return row[col]
        if isinstance(col, int):
            vals = tuple(row.values())
            if not (-len(vals) <= col < len(vals)):
                raise ValueError(f"Column index {col} out of range for row with {len(vals)} columns")
            return vals[col]
        raise ValueError(f"Invalid column specification: {col}")

    def __getitem__(self, idx: Union[int, slice, str, list, Tuple[Union[int, slice], Union[int, str]]]) -> Any:
        """\
        Flexible indexing — supports row, column, cell, and subset access.

        Usage::

            result[0]              # first row (dict)
            result[0:3]            # rows 0–2 (list of dicts)
            result["name"]         # column series (list of values)
            result[0, "name"]      # cell: row 0, column "name"
            result[0, 1]           # cell: row 0, column index 1
            result[0:3, "name"]    # column "name" for rows 0–2
            result[[0, 2, 5]]      # rows at indices 0, 2, 5
            result[["name", "age"]] # subset: list of dicts with only those columns
        """
        # List-based subset indexing
        if isinstance(idx, list):
            if not idx:
                return []
            if isinstance(idx[0], str):
                # Column subset: result[["name", "age"]] → list of dicts
                return self.to_list(columns=idx)
            if isinstance(idx[0], int):
                # Row subset: result[[0, 2, 5]] → list of dicts
                return [self._rows[i] for i in idx]
            raise ValueError(f"List index must contain all str (columns) or all int (row indices), got: {type(idx[0]).__name__}")
        # Column series: result["col_name"]
        if isinstance(idx, str):
            return self.column(idx)
        # Row(s): result[i] or result[start:stop]
        if isinstance(idx, (slice, int)):
            return self._rows[idx]
        # Cell / column slice: result[i, col] or result[start:stop, col]
        if isinstance(idx, tuple) and len(idx) == 2:
            row_spec, col_spec = idx
            if isinstance(row_spec, int):
                return self._get_col_value(self._rows[row_spec], col_spec)
            if isinstance(row_spec, slice):
                return [self._get_col_value(r, col_spec) for r in self._rows[row_spec]]
            raise ValueError(f"Invalid row specification: {row_spec}")
        raise ValueError(f"Invalid index: {idx}")

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __bool__(self) -> bool:
        return self.ok

    # --- dunder ----------------------------------------------------------------

    def __str__(self) -> str:
        if not self.ok:
            return self.to_str()
        if not self._rows:
            affected = f", {self._row_count} affected" if self._row_count > 0 else ""
            return f"OK — 0 rows{affected}"
        return table_display(self)

    def __repr__(self) -> str:
        if self.ok:
            return f"SQLResponse(ok=True, rows={len(self._rows)}, columns={self._columns})"
        return f"SQLResponse(ok=False, error_type={self._error_type!r})"


class Database(object):
    """\
    Universal Database Connector

    Provides a clean, intuitive interface for database operations across different providers
    (SQLite, PostgreSQL, DuckDB, MySQL) with standard connection management:

    1. **Standalone autocommit** (single statements, DDL, reads)::

        db = Database(provider="sqlite", database="mydb")
        result = db.execute("SELECT * FROM table", autocommit=True)

    2. **Context Manager** (recommended for multi-statement transactions)::

        with Database(provider="pg", database="mydb") as db:
            db.execute("INSERT INTO users (name) VALUES (:name)", params={"name": "Alice"})
            # Automatically commits on success, rolls back on exception

    The class automatically handles:

    - Database creation (auto-create if not exists via superuser config)
    - Pragma execution on each new connection (via SQLAlchemy event listeners)
    - Engine caching / connection pooling via ``DatabaseEngineRegistry``
    - SQL transpilation between different database dialects

    Thread-safety:
        Standalone autocommit calls are thread-safe (each creates its
        own connection from the pool).  The context manager (``with db:``)
        stores a connection on the instance and is **not** thread-safe.
        For parallel context-manager usage, create separate ``Database``
        instances via ``clone()``.
    """

    _READONLY_DEFAULT_WARNING_EMITTED = False

    def __init__(
        self,
        database: Optional[str] = None,
        provider: Optional[str] = None,
        pool: Optional[Dict[str, Any]] = None,
        _override: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """\
        Initialize database connection configuration.

        The engine is **not** created here — it is lazily fetched from
        ``DatabaseEngineRegistry`` on first access via the ``engine``
        property.

        Warning:
            Using ``:memory:`` is **not recommended**.  In-memory databases
            cannot reliably share state across connections, which breaks
            connection-pooling, context-manager, and clone() semantics.
            Use a file-based database instead (e.g. ``"./tmp/my.db"``).

        Args:
            database: Database name or path.
            provider: Database provider ('sqlite', 'pg', 'duckdb', etc.)
            pool: Pool configuration to override provider defaults (e.g., {'pool_class': 'static'}).
                    Values are deep-merged on top of the provider's pool config.
            _override: Internal. If provided, passed to ``DatabaseConfigEngine.resolve()``
                to bypass ``CM_AHVN``, avoiding circular dependency at boot time.
            **kwargs: Additional connection parameters
        """
        super().__init__()

        if database == ":memory:":
            warnings.warn(
                "Using ':memory:' databases is NOT recommended. "
                "In-memory databases cannot share state across connections, "
                "which breaks pooling, context-manager and clone() semantics. "
                "Use a file-based database instead (e.g. './tmp/my.db').",
                UserWarning,
                stacklevel=2,
            )
            pool = dict(pool or {})
            pool.setdefault("engine_cache_key", str(id(self)))
        raw = {"provider": provider, "database": database, **kwargs}
        if pool:
            raw["pool"] = pool
        self.spec = DATABASE_CONFIG_ENGINE.resolve(raw, override=_override)
        self.config = DATABASE_CONFIG_ENGINE.materialize(self.spec, mode="spec")
        self.dialect = self.spec.dialect
        self.proxy = NetworkProxy(
            http_proxy=self.spec.args.get("http_proxy"),
            https_proxy=self.spec.args.get("https_proxy"),
            no_proxy=self.spec.args.get("no_proxy"),
        )
        self.sql_processor = create_sql_processor(self.dialect)
        self.sql_healer = create_sql_healer(
            self.dialect,
            schema_loader=self._schema_index_for_healing,
            config=self.spec.args.get("sql_healing"),
        )
        self._conn = None  # only set inside context manager
        self._pending_ctx_readonly: Optional[bool] = None
        self._tx_requested_readonly: Optional[bool] = None
        self._tx_need_commit: bool = False

        # Clear any previous disposed state so this instance can obtain an engine
        DatabaseEngineRegistry.clear_disposed(self.spec)

    def __call__(self, readonly: Optional[bool] = True) -> "Database":
        """\
        Configure context-manager defaults for ``with db():`` usage.

        Args:
            readonly: Transaction default for the upcoming context block.
                ``True`` (default) keeps ``with db():`` read-oriented by default.
                Individual statements still auto-infer mutating intent when
                ``execute(..., readonly=None)``.
        """
        if self._conn is not None:
            raise DatabaseError("Cannot reconfigure readonly mode while a context manager is active.")
        self._pending_ctx_readonly = readonly
        return self

    @property
    def engine(self):
        """\
        Lazily obtain the shared engine from ``DatabaseEngineRegistry``.

        The proxy context is applied here so that first-time engine
        creation (including ``create_database`` bootstrapping) goes
        through the configured network proxy.

        Raises:
            DatabaseError: If the engine was previously disposed.
        """
        with self.proxy:
            return DatabaseEngineRegistry.get_engine(self.spec)

    def clone(self) -> "Database":
        """\
        Create an independent Database instance with the same configuration.

        The clone **shares the same engine** (and therefore the same connection pool)
        but has its own connection state, making it safe for parallel operations.

        Warning:
            For in-memory databases (`:memory:`), cloned instances do NOT share data.

        Returns:
            Database: A new independent Database instance.
        """
        if self.spec.database == ":memory:":
            logger.warning(
                "Cloning an in-memory database - cloned instances will NOT share data. "
                "Use a file-based database for parallel operations requiring shared state."
            )

        spec_dict = DATABASE_CONFIG_ENGINE.materialize(self.spec, mode="spec")
        return Database(**spec_dict)

    def close(self) -> None:
        """No-op.  Engine lifecycle is managed by ``DatabaseEngineRegistry``."""
        pass

    def in_transaction(self) -> bool:
        """\
        Check if currently in a transaction (context manager only).

        Returns:
            bool: True if inside a context manager with an active transaction.
        """
        return self._conn is not None and self._conn.in_transaction()

    def commit(self):
        """\
        Commit the current transaction (context manager only).
        """
        if self._conn is not None and self._conn.in_transaction():
            self._conn.commit()
            self._tx_need_commit = False

    def rollback(self):
        """\
        Rollback the current transaction (context manager only).
        """
        if self._conn is not None and self._conn.in_transaction():
            self._conn.rollback()
        self._tx_need_commit = False

    @property
    def db_name(self) -> Optional[str]:
        """\
        Resolve the current database name by querying the live engine.

        Uses a layered fallback of standard SQL functions so that no
        dialect-specific branching is required:

        - **Layer 1** ``current_database()`` — PostgreSQL, DuckDB, StarRocks, Trino
        - **Layer 2** ``database()``          — MySQL, OceanBase, MariaDB
        - **Layer 3** ``db_name()``            — Microsoft SQL Server
        - **Layer 4** ``PRAGMA database_list`` — SQLite (returns SQLite's
            internal name, always ``"main"`` for the main attached database)
        - **Layer 5** ``Inspector.default_schema_name`` — generic fallback

        Returns:
            Optional[str]: The current database name as reported by the engine,
                or ``None`` if it cannot be determined.
        """
        # Layer 1: current_database() — PostgreSQL, DuckDB, StarRocks, Trino
        try:
            with self.engine.connect() as conn:
                return conn.execute(sa.select(sa.func.current_database())).scalar()
        except Exception:
            pass

        # Layer 2: database() — MySQL, OceanBase, MariaDB
        try:
            with self.engine.connect() as conn:
                return conn.execute(sa.select(sa.func.database())).scalar()
        except Exception:
            pass

        # Layer 3: db_name() — MSSQL
        try:
            with self.engine.connect() as conn:
                return conn.execute(sa.select(sa.func.db_name())).scalar()
        except Exception:
            pass

        # Layer 4: PRAGMA database_list — SQLite
        # Returns (seq, name, file); 'name' is SQLite's internal label for the
        # attached database, always 'main' for the primary database.
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sa.text("PRAGMA database_list")).first()
                if row is not None:
                    return row[1]  # 'name' column — e.g. 'main'
        except Exception:
            pass

        # Layer 5: Inspector default schema (generic fallback)
        try:
            return sa.inspect(self.engine).default_schema_name
        except Exception as e:
            logger.warning(f"Failed to determine database name: {error_str(e)}")
            return None

    def _build_schema_index_for_healing(self) -> Dict[str, List[str]]:
        """Build a schema index as ``{table_name: [column_name, ...]}``."""
        inspector = sa.inspect(self.engine)
        table_names = set(inspector.get_table_names())
        try:
            table_names.update(inspector.get_view_names())
        except Exception:
            pass

        schema_index: Dict[str, List[str]] = {}
        for table_name in sorted(table_names):
            try:
                columns = inspector.get_columns(table_name)
                col_names = [str(col.get("name")) for col in columns if col.get("name")]
                # Keep stable order and uniqueness for deterministic matching.
                schema_index[table_name] = list(dict.fromkeys(col_names))
            except Exception:
                schema_index[table_name] = []
        return schema_index

    def _schema_index_for_healing(self) -> Dict[str, List[str]]:
        """Get globally cached schema index for this database connection key."""
        return DatabaseEngineRegistry.get_schema_index(
            self.spec,
            builder=self._build_schema_index_for_healing,
        )

    @staticmethod
    def _is_schema_mutation_sql(query_text: Optional[str]) -> bool:
        if not query_text:
            return False
        return re.match(r"^\s*(create|drop|alter|truncate|rename)\b", query_text, re.IGNORECASE) is not None

    @staticmethod
    def _finalize_transaction(conn, should_commit: bool) -> None:
        """\
        End the current transaction by committing or rolling back.
        """
        if conn is None or not conn.in_transaction():
            return
        if should_commit:
            conn.commit()
        else:
            conn.rollback()

    def _resolve_query_readonly(self, query, *, query_str: Optional[str], readonly: Optional[bool]) -> bool:
        """Resolve read-only mode with explicit override and conservative auto-detection."""
        if readonly is not None:
            return readonly
        if self._tx_requested_readonly is False:
            return False
        from .db_utils import is_sql_readonly

        return is_sql_readonly(query, query_text=query_str, dialect=self.dialect)

    @classmethod
    def _warn_readonly_default_deprecation(cls, sql: str) -> None:
        """Warn once that implicit readonly auto-detection will change in a future release."""
        if cls._READONLY_DEFAULT_WARNING_EMITTED:
            return
        warnings.warn(
            f"The following SQL is detected as read-only by default:\n{sql}\n"
            "Database.execute()/orm_execute() currently auto-detect readonly when readonly=None; "
            "this default will change to readonly=False in a future release. "
            "Set readonly=True explicitly for read-only SQL queries.",
            DeprecationWarning,
            stacklevel=4,
        )
        cls._READONLY_DEFAULT_WARNING_EMITTED = True

    def _reset_context_transaction_state(self) -> None:
        """Reset per-context transaction bookkeeping state."""
        self._tx_requested_readonly = None
        self._tx_need_commit = False

    def _maybe_invalidate_healing_schema_cache(self, query_text: Optional[str]) -> None:
        """Invalidate cached schema index when DDL likely changed the schema."""
        if not self._is_schema_mutation_sql(query_text):
            return
        try:
            DatabaseEngineRegistry.invalidate_schema_index(self.spec)
        except Exception as e:
            logger.debug(f"Failed to invalidate schema cache after DDL: {error_str(e)}")

    def __enter__(self):
        """\
        Context manager entry: establishes connection and begins transaction.

        Raises:
            DatabaseError: If a context manager is already active on this instance
                (nesting is not supported).
        """
        if self._conn is not None:
            raise DatabaseError("Cannot nest Database context managers. " "Use a single context manager or create a clone() for parallel operations.")
        self._tx_requested_readonly = self._pending_ctx_readonly
        self._pending_ctx_readonly = None
        self._tx_need_commit = False
        self._conn = self.engine.connect()
        self._conn.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """\
        Context manager exit: commits or rolls back transaction and closes connection.
        """
        try:
            if exc_type is not None:
                self._finalize_transaction(self._conn, should_commit=False)
            else:
                self._finalize_transaction(self._conn, should_commit=self._tx_need_commit)
        finally:
            try:
                self._conn.close()
            except Exception as close_err:
                logger.warning(f"Failed to close connection on __exit__: {error_str(close_err)}")
            self._conn = None
            self._reset_context_transaction_state()
        return False

    def heal_sql(
        self,
        query: str,
        *,
        dialect: Optional[str] = None,
        schema_index: Optional[Dict[str, Any]] = None,
        prefer_backticks: Optional[bool] = None,
    ) -> str:
        """\
        Heal malformed SQL using the schema-aware SQL healer.

        This is an explicit/manual recovery API and does not run automatically
        inside ``execute()``.

        Args:
            query: Raw SQL text to heal.
            dialect: Optional source dialect for parsing.
            schema_index: Optional schema override mapping in the form
                ``{table_name: [column_name, ...]}`` or ``{"tables": {...}}``.
                When provided, this mapping is used instead of live inspection.
            prefer_backticks: Optional rendering override. If ``True`` and the
                target dialect supports backticks, output uses backtick
                identifiers. If ``None``, the SQL-healing config is used.

        Returns:
            str: Healed SQL text.
        """
        if isinstance(query, ClauseElement):
            raise ValueError("heal_sql expects raw SQL text, not ClauseElement")
        return self.sql_healer.heal(
            query,
            dialect=dialect,
            schema_index=schema_index,
            prefer_backticks=prefer_backticks,
        )

    def orm_execute(
        self,
        query,
        autocommit: Optional[bool] = None,
        readonly: Optional[bool] = None,
        **kwargs,
    ) -> SQLResponse:
        """
        Execute a SQLAlchemy ORM query or statement.

        Args:
            query: SQLAlchemy ORM statement or ClauseElement
            autocommit: Commit behaviour.
                None (default) — auto-resolve: commit if standalone, skip if in context manager.
                True  — commit immediately (standalone or within context manager).
                False — never commit; standalone usage raises ``DatabaseError``.
            readonly: Read-only behaviour.
                None (default) -> auto-detect from statement intent.
                True  -> treat as read-only (commit not required).
                False -> treat as non-read-only (commit required).
            **kwargs: Additional keyword arguments for query execution

        Returns:
            SQLResponse: Unified result — check ``result.ok`` for success/failure.

        Examples:
            # Using SQLAlchemy ORM statements
            from sqlalchemy import select, insert, update, delete

            # Select statement
            stmt = select(users_table).where(users_table.c.id == 1)
            result = db.orm_execute(stmt)

            # Insert statement
            stmt = insert(users_table).values(name="Alice")
            db.orm_execute(stmt, autocommit=True)

            # Update statement
            stmt = update(users_table).where(users_table.c.id == 1).values(name="Bob")
            db.orm_execute(stmt, autocommit=True)

            # Delete statement
            stmt = delete(users_table).where(users_table.c.id == 1)
            db.orm_execute(stmt, autocommit=True)
        """

        if not isinstance(query, ClauseElement):
            raise ValueError("orm_execute only accepts SQLAlchemy ORM statements (ClauseElement)")

        try:
            return self._exec_sql(query, params=None, autocommit=autocommit, readonly=readonly)
        except Exception as e:
            logger.debug(f"ORM Query: {query}")
            logger.error(f"Database ORM execution failed:\n{error_str(e)}")
            raise DatabaseError(f"\nDatabase ORM execution failed:\n{error_str(e)}\nQuery: {query}\n") from e

    def execute(
        self,
        query: str,
        transpile: Optional[str] = None,
        autocommit: Optional[bool] = None,
        params: Optional[Union[Dict[str, Any], List[Dict[str, Any]], Tuple]] = None,
        safe: bool = False,
        readonly: Optional[bool] = None,
        **kwargs,
    ) -> SQLResponse:
        """
        Execute a raw SQL query against the database.

        Args:
            query: The SQL query to execute (raw SQL string)
            transpile: Source dialect to transpile from (if different from target)
            autocommit: Commit behaviour.
                None (default) — auto-resolve: commit if standalone, skip if in context manager.
                True  — commit immediately (standalone or within context manager).
                False — never commit; standalone usage raises ``DatabaseError``.
            params: Query parameters (dict for named, tuple/list for positional)
            safe: If True, returns ``SQLResponse`` with ``ok=False`` on error
                instead of raising an exception (default: False)
            readonly: Read-only behaviour.
                None (default) -> auto-detect from SQL intent.
                True  -> treat as read-only (commit not required).
                False -> treat as non-read-only (commit required).
            **kwargs: Additional keyword arguments for query execution

        Returns:
            SQLResponse: Unified result — check ``result.ok`` for success/failure.

        Examples:
            # Simple query (uses temporary connection with autocommit)
            result = db.execute("SELECT * FROM users")
            for row in result:
                print(row)

            # Parameterized query
            result = db.execute("SELECT * FROM users WHERE id = :id", params={"id": 1})

            # Parameterized insert
            db.execute(
                "INSERT INTO users (name) VALUES (:name)",
                params={"name": "Alice"}
            )

            # Transactional operation
            with db:
                db.execute("INSERT INTO users (name) VALUES (:name)", params={"name": "Bob"})
                db.execute("UPDATE users SET active = TRUE WHERE name = :name", params={"name": "Bob"})

            # Cross-database SQL (transpile from PostgreSQL to the current database dialect, i.e., SQLite)
            result = db.execute("SELECT * FROM users LIMIT 10", transpile="postgresql")

            # Safe mode - returns error response instead of raising
            result = db.execute("SELECT * FROM nonexistent", safe=True)
            if not result:
                print(result.error_type, result.error_message)

        Note:
            For SQLAlchemy ORM operations, use orm_execute() method instead.
        """
        # If user passes a ClauseElement to execute(), redirect to orm_execute
        # but don't pass params since ClauseElement should have its own parameters
        if isinstance(query, ClauseElement):
            if params is not None:
                logger.warning("Parameters ignored when executing ClauseElement via execute(). Use orm_execute() for ClauseElement queries.")
            return self.orm_execute(query, autocommit=autocommit, readonly=readonly, **kwargs)

        try:
            # Skip transpilation when source dialect matches database dialect
            if transpile and transpile == self.dialect:
                transpile = None

            # Process string query with optional transpilation and parameters
            processed_query, processed_params = self.sql_processor.process_query(query, params, transpile_from=transpile)
            return self._exec_sql(sa.text(processed_query), processed_params, autocommit=autocommit, safe=safe, readonly=readonly)
        except Exception as e:
            if safe:
                return SQLResponse._from_error(e, query=query, params=params, db=self)
            logger.debug(f"SQL Query: {query}")
            logger.debug(f"Parameters: {params}")
            logger.error(f"Database execution failed:\n{error_str(e)}")
            raise DatabaseError(f"Database execution failed:\n{e}\nQuery: {query}\nParams: {params}\n") from e

    def _exec_sql(self, query, params=None, autocommit: Optional[bool] = None, safe: bool = False, readonly: Optional[bool] = None) -> SQLResponse:
        """\
        Internal method to execute SQL queries.

        Autocommit semantics (3-option):
            - ``None`` (default): auto-resolve.
                Inside context manager → no commit (transaction managed by ``__exit__``).
                Standalone → commit (temporary connection, auto-closed).
            - ``True``: commit immediately.
                Inside context manager → commit the current transaction inline.
                Standalone → commit and close temporary connection.
            - ``False``: never commit.
                Inside context manager → no commit (same as None).
                Standalone → raise ``DatabaseError`` (use ``with db:`` instead).

        Args:
            query: SQLAlchemy text() or ClauseElement
            params: Query parameters
            autocommit: Commit behaviour (None / True / False)
            safe: If True, re-raise exceptions for caller to handle
            readonly: Read-only behaviour (None / True / False).
                ``None`` auto-detects statement intent.

        Returns:
            SQLResponse
        """
        # Stringify query for metadata (ClauseElement → compiled SQL text)
        query_str = str(query) if query is not None else None
        query_readonly = self._resolve_query_readonly(query, query_str=query_str, readonly=readonly)
        if readonly is None and query_readonly:
            self._warn_readonly_default_deprecation(sql=query_str or "")

        if self._conn is not None:
            # --- Context-manager path ---
            try:
                t0 = time_mod.perf_counter()
                result = self._conn.execute(query, params) if params else self._conn.execute(query)
                elapsed = time_mod.perf_counter() - t0
                response = (
                    SQLResponse._from_cursor(result, query=query_str, params=params, elapsed=elapsed)
                    if result
                    else SQLResponse.empty(query=query_str, params=params, elapsed=elapsed)
                )
                self._maybe_invalidate_healing_schema_cache(query_str)
                if not query_readonly:
                    self._tx_need_commit = True
                if autocommit is True:
                    self._finalize_transaction(self._conn, should_commit=self._tx_need_commit)
                    self._tx_need_commit = False
                return response
            except Exception as e:
                try:
                    self._finalize_transaction(self._conn, should_commit=False)
                    self._tx_need_commit = False
                except Exception as rb_err:
                    logger.warning(f"Rollback failed (context-manager path): {error_str(rb_err)}")
                if safe:
                    raise
                raise DatabaseError(f"Database execution failed: {error_str(e)}") from e
        else:
            # --- Standalone path ---
            if autocommit is False:
                raise DatabaseError("autocommit=False requires a context manager. " "Use `with db:` or set autocommit=None/True.")
            conn = self.engine.connect()
            try:
                conn.begin()
                t0 = time_mod.perf_counter()
                result = conn.execute(query, params) if params else conn.execute(query)
                elapsed = time_mod.perf_counter() - t0
                response = (
                    SQLResponse._from_cursor(result, query=query_str, params=params, elapsed=elapsed)
                    if result
                    else SQLResponse.empty(query=query_str, params=params, elapsed=elapsed)
                )
                self._finalize_transaction(conn, should_commit=(not query_readonly))
                self._maybe_invalidate_healing_schema_cache(query_str)
                return response
            except Exception as e:
                try:
                    self._finalize_transaction(conn, should_commit=False)
                except Exception as rb_err:
                    logger.warning(f"Rollback failed (standalone path): {error_str(rb_err)}")
                if safe:
                    raise
                raise DatabaseError(f"Database execution failed: {error_str(e)}") from e
            finally:
                try:
                    conn.close()
                except Exception as close_err:
                    logger.warning(f"Failed to close connection (standalone path): {error_str(close_err)}")

    # === Internal Helpers ===

    def _reflect_table(self, tab_name: str) -> sa.Table:
        """\
        Reflect a table from the database and return a SQLAlchemy Table object.

        This is a convenience wrapper around ``sa.Table(..., autoload_with=engine)``
        used by ORM-based feature methods.

        Args:
            tab_name: Name of the table to reflect

        Returns:
            sa.Table: Reflected SQLAlchemy Table object
        """
        metadata = sa.MetaData()
        return sa.Table(tab_name, metadata, autoload_with=self.engine)

    # === Database Features ===
    # Fallback hierarchy: sa.inspect → SQLAlchemy ORM → built-in SQL

    def db_tabs(self) -> List[str]:
        """\
        List all table names in the database.

        Returns:
            List[str]: List of table names
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            return inspector.get_table_names()
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/db_tabs", dialect=self.dialect)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["tab_name"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to list tables: {error_str(e)}")
            return []

    def db_views(self) -> List[str]:
        """\
        List all view names in the database.

        Returns:
            List[str]: List of view names
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            return inspector.get_view_names()
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/db_views", dialect=self.dialect)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["view_name"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to list views: {error_str(e)}")
            return []

    def tab_cols(self, tab_name: str, full_info: bool = False):
        """\
        List column information for a specific table.

        Args:
            tab_name: Name of the table
            full_info: If True, return full column information; if False, return only column names

        Returns:
            When full_info=True: List of column dictionaries with full metadata
            When full_info=False: List[str] of column names
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            columns = inspector.get_columns(tab_name)
            if full_info:
                return [{("col_name" if k == "name" else k): v for k, v in col.items()} for col in columns]
            else:
                return [col["name"] for col in columns]
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/tab_cols", dialect=self.dialect, tab_name=tab_name)
            result = self.execute(sql, transpile=src, readonly=True)
            if full_info:
                return result.to_list()
            else:
                return [row["col_name"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to list columns for table {tab_name}: {error_str(e)}")
            return []

    def tab_pks(self, tab_name: str) -> List[str]:
        """\
        List primary key column names for a specific table.

        Args:
            tab_name: Name of the table

        Returns:
            List[str]: List of primary key column names
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            pks = inspector.get_pk_constraint(tab_name)
            pk_columns = pks.get("constrained_columns", []) if pks else []
            if pk_columns:  # Only return if we found primary keys
                return pk_columns
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/tab_pks", dialect=self.dialect, tab_name=tab_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["col_name"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to list primary keys for table {tab_name}: {error_str(e)}")
            return []

    def tab_fks(self, tab_name: str) -> List[Dict[str, str]]:
        """\
        List foreign key information for a specific table.

        Args:
            tab_name: Name of the table

        Returns:
            List[Dict[str, str]]: List of foreign key information with keys:
                - col_name: Column name in the current table
                - tab_ref: Referenced table name
                - col_ref: Referenced column name
                - name: Foreign key constraint name
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            fks = inspector.get_foreign_keys(tab_name)
            result = []
            for fk in fks:
                for col, ref_col in zip(fk["constrained_columns"], fk["referred_columns"]):
                    result.append(
                        {
                            "col_name": col,
                            "tab_ref": fk["referred_table"],
                            "col_ref": ref_col,
                            "name": fk.get("name", f"FK_{col}_{fk['referred_table']}_{ref_col}"),
                        }
                    )
            return result
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/tab_fks", dialect=self.dialect, tab_name=tab_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()
        except Exception as e:
            logger.error(f"Failed to list foreign keys for table {tab_name}: {error_str(e)}")
            return []

    def row_count(self, tab_name: str) -> int:
        """\
        Get row count for a specific table.

        Args:
            tab_name: Name of the table

        Returns:
            int: Number of rows in the table
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            stmt = sa.select(sa.func.count().label("cnt")).select_from(table)
            result = self.orm_execute(stmt, readonly=True)
            return result.to_list()[0]["cnt"]
        except Exception as e:
            logger.warning(f"ORM row_count failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/row_count", dialect=self.dialect, tab_name=tab_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()[0]["cnt"]
        except Exception as e:
            logger.error(f"Failed to count rows for table {tab_name}: {error_str(e)}")
            return 0

    def col_agg(self, tab_name: str, col_name: str, agg: str = "COUNT", distinct: bool = False) -> Optional[Any]:
        """\
        Get aggregated value for a specific column in a table.

        Args:
            tab_name: Name of the table
            col_name: Name of the column
            agg: Aggregation function ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX')
            distinct: Whether to apply DISTINCT to the aggregation

        Returns:
            Optional[Any]: Aggregated value, or None if aggregation fails
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            agg_func = getattr(sa.func, agg.lower())
            expr = agg_func(sa.distinct(col)) if distinct else agg_func(col)
            stmt = sa.select(expr.label("agg"))
            result = self.orm_execute(stmt, readonly=True)
            return result.to_list()[0]["agg"]
        except Exception as e:
            logger.warning(f"ORM col_agg failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql(
                "utils/col_agg",
                dialect=self.dialect,
                tab_name=tab_name,
                col_name=col_name,
                agg=agg,
                distinct="DISTINCT " if distinct else "",
            )
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()[0]["agg"]
        except Exception as e:
            logger.error(f"Failed to compute {agg} for column {col_name} in table {tab_name}: {error_str(e)}")
            return None

    def col_type(self, tab_name: str, col_name: str) -> str:
        """\
        Get column type for a specific column in a table.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            str: Column type
        """
        # Layer 1: sa.inspect
        try:
            inspector = sa.inspect(self.engine)
            for col in inspector.get_columns(tab_name):
                if col["name"] == col_name:
                    return str(col["type"])
            raise ValueError(f"Column {col_name} not found in table {tab_name}")
        except Exception as e:
            logger.warning(f"Inspector failed, falling back to ORM: {error_str(e)}")

        # Layer 2: ORM (reflect table and check column type)
        try:
            table = self._reflect_table(tab_name)
            if col_name in table.c:
                return str(table.c[col_name].type)
            raise ValueError(f"Column {col_name} not found in table {tab_name}")
        except Exception as e:
            logger.warning(f"ORM col_type failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_type", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()[0]["col_type"]
        except Exception as e:
            logger.error(f"Failed to get column type for {tab_name}.{col_name}: {error_str(e)}")
            return ""

    # === Data Analysis Methods ===
    # Fallback hierarchy: ORM → built-in SQL

    def col_distincts(self, tab_name: str, col_name: str) -> List[Any]:
        """\
        Get distinct values for a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            List[Any]: List of distinct values
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            stmt = sa.select(sa.distinct(col).label("col_enums"))
            result = self.orm_execute(stmt, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.warning(f"ORM col_distincts failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_distincts", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to get distinct values for column {col_name} in table {tab_name}: {error_str(e)}")
            return []

    def col_enums(self, tab_name: str, col_name: str) -> List[Any]:
        """\
        Get all enumerated values for a specific column (including duplicates).

        This method returns all values from a column, including duplicates.
        For unique values only, use col_distincts() instead.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            List[Any]: List of all enumerated values (may contain duplicates)
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            stmt = sa.select(col.label("col_enums"))
            result = self.orm_execute(stmt, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.warning(f"ORM col_enums failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_enums", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to get enumerated values for column {col_name} in table {tab_name}: {error_str(e)}")
            return []

    def col_freqs(self, tab_name: str, col_name: str) -> List[Dict[str, Any]]:
        """\
        Get value frequencies for a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            List[Dict[str, Any]]: List of value-frequency pairs
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            stmt = (
                sa.select(
                    col.label("col_enums"),
                    sa.func.count().label("freq"),
                )
                .group_by(col)
                .order_by(sa.desc("freq"))
            )
            result = self.orm_execute(stmt, readonly=True)
            return result.to_list()
        except Exception as e:
            logger.warning(f"ORM col_freqs failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_freqs", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()
        except Exception as e:
            logger.error(f"Failed to get frequencies for column {col_name} in table {tab_name}: {error_str(e)}")
            return []

    def col_freqk(self, tab_name: str, col_name: str, topk: int = 20) -> List[Dict[str, Any]]:
        """\
        Get top-k value frequencies for a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column. If col_name is prepended with '-', sorts by ascending frequency.
            topk: Number of top values to return

        Returns:
            List[Dict[str, Any]]: List of top-k value-frequency pairs
        """
        # Layer 2: ORM
        try:
            ascending = col_name.startswith("-")
            actual_col_name = col_name[1:] if ascending else col_name
            table = self._reflect_table(tab_name)
            col = table.c[actual_col_name]
            order = sa.asc("freq") if ascending else sa.desc("freq")
            stmt = (
                sa.select(
                    col.label("col_enums"),
                    sa.func.count().label("freq"),
                )
                .group_by(col)
                .order_by(order)
                .limit(topk)
            )
            result = self.orm_execute(stmt, readonly=True)
            return result.to_list()
        except Exception as e:
            logger.warning(f"ORM col_freqk failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            if not col_name.startswith("-"):
                sql, src = load_builtin_sql("utils/col_freqk", dialect=self.dialect, tab_name=tab_name, col_name=col_name, topk=topk)
            else:
                sql, src = load_builtin_sql("utils/col_freqk_asc", dialect=self.dialect, tab_name=tab_name, col_name=col_name[1:], topk=topk)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()
        except Exception as e:
            logger.error(f"Failed to get top-{topk} frequencies for column {col_name} in table {tab_name}: {error_str(e)}")
            return []

    def col_nonnulls(self, tab_name: str, col_name: str) -> List[Any]:
        """\
        Get list of non-null values for a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            List[Any]: List of non-null values
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            stmt = sa.select(col.label("col_enums")).where(col.isnot(None))
            result = self.orm_execute(stmt, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.warning(f"ORM col_nonnulls failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_nonnulls", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return [row["col_enums"] for row in result.to_list()]
        except Exception as e:
            logger.error(f"Failed to get non-null values for column {col_name} in table {tab_name}: {error_str(e)}")
            return []

    def browse(self, tab_name: str, limit: Optional[int] = None, offset: int = 0, orderby: Optional[str] = None, **kwargs) -> SQLResponse:
        """\
        Browse table data with pagination, filtering, and ordering.

        Uses ORM for cross-dialect compatibility.

        Args:
            tab_name (str): Name of the table to browse
            limit (int, optional): Maximum number of rows to return (default: None - no limit)
            offset (int): Number of rows to skip before starting to return rows (default: 0)
            orderby (str, optional): Column name to order by. Prefix with '-' for descending order (default: None - no ordering)
            **kwargs: Column-value filters. For exact match, provide single value.

        Returns:
            SQLResponse: Query results

        Examples:
            # Get first 10 rows
            result = db.browse('users', limit=10)

            # Get rows 20-30, ordered by name descending
            result = db.browse('users', limit=10, offset=20, orderby='-name')

            # Filter by single value
            result = db.browse('users', limit=10, status='active')

            # Filter with IN operation
            result = db.browse('users', limit=10, status=['active', 'pending'])

            # Combined filters
            result = db.browse('users', limit=10, status='active', role=['admin', 'user'])
        """
        # Layer 2: ORM (primary — no inspect or SQL fallback needed)
        try:
            table = self._reflect_table(tab_name)
            stmt = sa.select(table)
            for col_name, value in kwargs.items():
                if col_name not in table.c:
                    raise ValueError(f"Column '{col_name}' not found in table '{tab_name}'")
                col = table.c[col_name]
                if isinstance(value, (list, tuple)):
                    stmt = stmt.where(col.in_(value))
                else:
                    stmt = stmt.where(col == value)

            if orderby:
                if orderby.startswith("-"):
                    order_col_name = orderby[1:]
                    order_direction = sa.desc
                else:
                    order_col_name = orderby
                    order_direction = sa.asc
                if order_col_name not in table.c:
                    raise ValueError(f"Column '{order_col_name}' not found in table '{tab_name}'")
                stmt = stmt.order_by(order_direction(table.c[order_col_name]))
            if limit is not None:
                stmt = stmt.limit(limit)
            stmt = stmt.offset(offset)
            return self.orm_execute(stmt, readonly=True)
        except Exception as e:
            logger.error(f"Failed to browse table {tab_name}: {error_str(e)}")
            raise Exception(f"Table browse failed for {tab_name}: {e}")

    def row_sample(self, tab_name: str, n_sample: int = 100, modulus: int = 1061109589, seed: int = 42) -> SQLResponse:
        """\
        Sample rows from a table using deterministic hashing.

        This method provides deterministic sampling across different database providers.
        The sampling is based on hashing techniques that ensure reproducibility with the same seed.

        Args:
            tab_name: Name of the table to sample from
            n_sample: Number of rows to sample (default: 100)
            modulus: Modulus value for hash-based sampling (default: 1061109589)
            seed: Random seed for reproducible sampling (default: 42)

        Returns:
            SQLResponse: Sampled rows

        Examples:
            # Sample 100 rows with default parameters
            result = db.row_sample('users', n_sample=100)

            # Sample with custom seed for different sample
            result = db.row_sample('users', n_sample=50, seed=123)

            # Sample with custom modulus
            result = db.row_sample('users', n_sample=200, modulus=1000000007, seed=1)
        """
        # Layer 3: Built-in SQL only (sampling is too dialect-specific for ORM)
        try:
            sql, src = load_builtin_sql("utils/row_sample", dialect=self.dialect, tab_name=tab_name, n_sample=n_sample, modulus=modulus, seed=seed)
            result = self.execute(sql, transpile=src, readonly=True)
            return result
        except Exception as e:
            logger.error(f"Failed to sample table {tab_name}: {error_str(e)}")
            raise Exception(f"Table sampling failed for {tab_name}: {e}")

    def col_percentile(self, tab_name: str, col_name: str, percentiles: Optional[List[int]] = [0, 25, 50, 75, 100]) -> Dict[str, Any]:
        """\
        Calculate specified percentiles for a numeric column.

        Args:
            tab_name: Name of the table
            col_name: Name of the numeric column
            percentiles: List of percentiles to calculate (default: [0, 25, 50, 75, 100])

        Returns:
            Dict[str, Any]: Dictionary mapping percentiles to their calculated values.
                e.g., "p0": value at 0th percentile, "p25": value at 25th percentile, etc.
        """
        # Layer 3: Built-in SQL only (percentile functions are too dialect-specific for ORM)
        try:
            results = dict()
            for p in unique(percentiles):
                if not (0 <= p <= 100):
                    raise ValueError(f"Percentile {p} is out of range. Must be between 0 and 100.")
                sql, src = load_builtin_sql("utils/col_percentile", dialect=self.dialect, tab_name=tab_name, col_name=col_name, p=p)
                result = self.execute(sql, transpile=src, readonly=True)
                results[f"p{p}"] = result.to_list()[0]["p"]
            return results
        except Exception as e:
            logger.error(f"Failed to calculate percentiles for column {col_name} in table {tab_name}: {error_str(e)}")
            return {}

    def col_lengths(self, tab_name: str, col_name: str) -> Dict[str, Any]:
        """\
        Get minimum, maximum, and average length of values in a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            Dict[str, Any]: Dictionary with keys:
                - min_len: Minimum length value
                - max_len: Maximum length value
                - avg_len: Average length value
        """
        # Layer 2: ORM
        try:
            table = self._reflect_table(tab_name)
            col = table.c[col_name]
            stmt = sa.select(
                sa.func.min(sa.func.length(col)).label("min_len"),
                sa.func.max(sa.func.length(col)).label("max_len"),
                sa.func.avg(sa.func.length(col)).label("avg_len"),
            )
            result = self.orm_execute(stmt, readonly=True)
            return result.to_list()[0]
        except Exception as e:
            logger.warning(f"ORM col_lengths failed, falling back to SQL: {error_str(e)}")

        # Layer 3: Built-in SQL
        try:
            sql, src = load_builtin_sql("utils/col_lengths", dialect=self.dialect, tab_name=tab_name, col_name=col_name)
            result = self.execute(sql, transpile=src, readonly=True)
            return result.to_list()[0]
        except Exception as e:
            logger.error(f"Failed to get column lengths for {col_name} in table {tab_name}: {error_str(e)}")
            return {"min_len": None, "max_len": None, "avg_len": None}

    # === Comment Methods ===
    # Read: sa.inspect  |  Write: sa.schema DDL objects
    # Supported by most dialects (PostgreSQL, MySQL, Oracle, DuckDB, StarRocks,
    # Hive, Trino, MSSQL).  SQLite does NOT support comments — reads return
    # None, writes raise ``DatabaseError``.

    def _supports_comments(self) -> bool:
        """\
        Check whether the current dialect supports ``COMMENT ON`` operations.

        Returns:
            bool: ``True`` if the dialect supports table/column comments.
        """
        try:
            sa.inspect(self.engine).get_table_comment(
                # Probe with a table that certainly does NOT exist so the only
                # failure mode is NotImplementedError (dialect unsupported).
                "__ahvn_comment_probe__"
            )
            return True  # dialect supports it (table not found is a different error)
        except NotImplementedError:
            return False
        except Exception:
            # Any other error (e.g. "table not found") means the dialect *does*
            # support the method — it just couldn't find the table.
            return True

    def tab_comment(self, tab_name: str) -> Optional[str]:
        """\
        Get the comment for a table.

        Args:
            tab_name: Name of the table

        Returns:
            Optional[str]: The table comment, or ``None`` if no comment is set
                or the dialect does not support comments.
        """
        # Layer 1: sa.inspect
        try:
            info = sa.inspect(self.engine).get_table_comment(tab_name)
            return info.get("text") if info else None
        except NotImplementedError:
            return None
        except Exception as e:
            logger.error(f"Failed to get comment for table {tab_name}: {error_str(e)}")
            return None

    def col_comment(self, tab_name: str, col_name: str) -> Optional[str]:
        """\
        Get the comment for a specific column.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Returns:
            Optional[str]: The column comment, or ``None`` if no comment is set
                or the dialect does not support comments.
        """
        # Layer 1: sa.inspect
        try:
            columns = sa.inspect(self.engine).get_columns(tab_name)
            for col in columns:
                if col["name"] == col_name:
                    return col.get("comment")
            logger.warning(f"Column {col_name} not found in table {tab_name}")
            return None
        except Exception as e:
            logger.error(f"Failed to get comment for column {col_name} in table {tab_name}: {error_str(e)}")
            return None

    def tab_comments(self, tab_name: str) -> Dict[str, Optional[str]]:
        """\
        Get comments for all columns in a table, keyed by column name.

        Args:
            tab_name: Name of the table

        Returns:
            Dict[str, Optional[str]]: Mapping of column names to their comments.
                Columns without comments have ``None`` values.
        """
        # Layer 1: sa.inspect
        try:
            columns = sa.inspect(self.engine).get_columns(tab_name)
            return {col["name"]: col.get("comment") for col in columns}
        except Exception as e:
            logger.error(f"Failed to get column comments for table {tab_name}: {error_str(e)}")
            return {}

    def set_tab_comment(self, tab_name: str, comment: str) -> None:
        """\
        Set or update the comment on a table.

        Uses ``sa.schema.SetTableComment`` DDL, which is dialect-aware.

        Args:
            tab_name: Name of the table
            comment:  Comment text to set

        Raises:
            DatabaseError: If the dialect does not support comments or the
                operation fails.
        """
        table = self._reflect_table(tab_name)
        table.comment = comment
        self.orm_execute(sa.schema.SetTableComment(table))

    def set_col_comment(self, tab_name: str, col_name: str, comment: str) -> None:
        """\
        Set or update the comment on a column.

        Uses ``sa.schema.SetColumnComment`` DDL, which is dialect-aware.

        Args:
            tab_name: Name of the table
            col_name: Name of the column
            comment:  Comment text to set

        Raises:
            DatabaseError: If the dialect does not support comments, the
                column does not exist, or the operation fails.
        """
        table = self._reflect_table(tab_name)
        if col_name not in table.c:
            raise DatabaseError(f"Column '{col_name}' not found in table '{tab_name}'")
        col = table.c[col_name]
        col.comment = comment
        self.orm_execute(sa.schema.SetColumnComment(col))

    def drop_tab_comment(self, tab_name: str) -> None:
        """\
        Remove the comment from a table.

        Uses ``sa.schema.DropTableComment`` DDL, which is dialect-aware.

        Args:
            tab_name: Name of the table

        Raises:
            DatabaseError: If the dialect does not support comments or the
                operation fails.
        """
        table = self._reflect_table(tab_name)
        self.orm_execute(sa.schema.DropTableComment(table))

    def drop_col_comment(self, tab_name: str, col_name: str) -> None:
        """\
        Remove the comment from a column.

        Uses ``sa.schema.DropColumnComment`` DDL, which is dialect-aware.

        Args:
            tab_name: Name of the table
            col_name: Name of the column

        Raises:
            DatabaseError: If the dialect does not support comments, the
                column does not exist, or the operation fails.
        """
        table = self._reflect_table(tab_name)
        if col_name not in table.c:
            raise DatabaseError(f"Column '{col_name}' not found in table '{tab_name}'")
        col = table.c[col_name]
        self.orm_execute(sa.schema.DropColumnComment(col))

    # === Table Creation Methods ===

    def _normalize_table(self, table) -> sa.Table:
        """\
        Normalize a table argument into a ``sa.Table`` object.

        Accepts:
        - ``sa.Table`` instance — returned as-is.
        - ``ExportableEntity`` subclass (ORM model class) — returns its ``__table__``.

        Args:
            table: A ``sa.Table`` or an ``ExportableEntity`` subclass.

        Returns:
            sa.Table: The underlying SQLAlchemy Table object.

        Raises:
            TypeError: If the argument is not a recognized table type.
        """
        if isinstance(table, sa.Table):
            return table
        # Check if it's an ORM model class (has __table__ attribute)
        if isinstance(table, type) and hasattr(table, "__table__"):
            tbl = getattr(table, "__table__", None)
            if isinstance(tbl, sa.Table):
                return tbl
        raise TypeError(f"Expected sa.Table or ORM model class, got {type(table).__name__}")

    def create_tab(self, table, checkfirst: bool = True) -> None:
        """\
        Create a single table in the database.

        Accepts either a ``sa.Table`` object or an ``ExportableEntity`` subclass.

        Args:
            table: A ``sa.Table`` object or an ``ExportableEntity`` subclass.
            checkfirst: If True, skip creation if the table already exists (default: True).

        Raises:
            TypeError: If the argument is not a recognized table type.
            Exception: If the creation operation fails.

        Examples:
            # Schema-based (sa.Table)
            metadata = sa.MetaData()
            users = sa.Table("users", metadata,
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("name", sa.String(100)),
            )
            db.create_tab(users)

            # ORM-based (ExportableEntity subclass)
            db.create_tab(MyModel)
        """
        sa_table = self._normalize_table(table)
        sa_table.create(self.engine, checkfirst=checkfirst)

    def create_tabs(self, tables, checkfirst: bool = True) -> None:
        """\
        Create multiple tables in the database.

        Accepts a list of ``sa.Table`` objects and/or ``ExportableEntity`` subclasses.
        Foreign key dependencies are automatically resolved by SQLAlchemy's
        ``MetaData.create_all()``.

        All tables are collected into a shared ``MetaData`` and created in a
        single ``create_all()`` call, which handles FK dependency ordering.

        Args:
            tables: Iterable of ``sa.Table`` objects and/or ``ExportableEntity`` subclasses.
            checkfirst: If True, skip tables that already exist (default: True).

        Raises:
            TypeError: If any element is not a recognized table type.
            Exception: If the creation operation fails.

        Examples:
            # Mixed schema and ORM tables
            db.create_tabs([users_table, orders_table, MyOrmModel])
        """
        # Collect all sa.Table objects
        sa_tables = [self._normalize_table(t) for t in tables]

        # Group by metadata — tables sharing a MetaData are created together
        meta_groups: Dict[int, sa.MetaData] = {}
        for tbl in sa_tables:
            mid = id(tbl.metadata)
            if mid not in meta_groups:
                meta_groups[mid] = tbl.metadata
        # If all tables share one MetaData, use create_all directly
        if len(meta_groups) == 1:
            meta = next(iter(meta_groups.values()))
            meta.create_all(self.engine, tables=sa_tables, checkfirst=checkfirst)
        else:
            # Multiple MetaData objects — create each group separately
            for meta in meta_groups.values():
                group_tables = [t for t in sa_tables if id(t.metadata) == id(meta)]
                meta.create_all(self.engine, tables=group_tables, checkfirst=checkfirst)

    # === Database Manipulation Methods ===
    def clear_tab(self, tab_name: str) -> None:
        """\
        Clear all data from a specific table without deleting the table itself.

        Uses SQLAlchemy ORM to ensure compatibility across all database backends.

        Args:
            tab_name: Name of the table to clear

        Raises:
            Exception: If the clearing operation fails
        """
        try:
            metadata = sa.MetaData()
            table = sa.Table(tab_name, metadata, autoload_with=self.engine)
            delete_stmt = sa.delete(table)
            self.orm_execute(delete_stmt)
            logger.info(f"Cleared table: {tab_name}")
        except Exception as e:
            logger.error(f"Failed to clear table {tab_name}: {error_str(e)}")
            raise Exception(f"Table clear failed for {tab_name}: {e}")

    def add_tab_col(self, table: sa.Table, column: sa.Column) -> None:
        """\
        Add a single column to an existing table.

        Tries three layers in order:
        1. SQLAlchemy DDL ``AddColumn`` construct (dialect-aware ORM path).
        2. Built-in SQL from ``resources/sqls/utils/add_tab_col.sql``
           (dialect-specific ``ALTER TABLE … ADD COLUMN`` variants).

        Args:
            table: The ``sa.Table`` that should receive the new column.
            column: The ``sa.Column`` to add.

        Raises:
            Exception: If all layers fail.
        """
        preparer = self.engine.dialect.identifier_preparer
        table_name = preparer.format_table(table)
        column_def = str(sa.schema.CreateColumn(column).compile(dialect=self.engine.dialect))

        # Layer 1: SQLAlchemy compile pipeline — dialect-correct DDL via sa.text
        try:
            with self.engine.begin() as conn:
                conn.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN {column_def}"))
            logger.info(f"Added column '{column.name}' to '{table_name}' via ORM DDL")
            return
        except Exception as e:
            logger.warning(f"ORM DDL AddColumn failed, falling back to builtin SQL: {error_str(e)}")

        # Layer 2: Built-in SQL (dialect-specific ALTER TABLE variant)
        try:
            sql, src = load_builtin_sql("utils/add_tab_col", dialect=self.dialect, table_name=table_name, column_sql=column_def)
            result = self.execute(sql, transpile=src if src != self.dialect else None, autocommit=True)
            if not result.ok:
                raise Exception(f"{result.error_type}: {result.error_message}")
            logger.info(f"Added column '{column.name}' to '{table_name}' via builtin SQL")
            return
        except Exception as e:
            logger.error(f"Builtin SQL add_tab_col failed: {error_str(e)}")
            raise Exception(f"add_tab_col failed for '{column.name}' on '{table_name}': {e}")

    def rename_tab(self, old_name: str, new_name: str) -> None:
        """\
        Rename a table.

        Uses the dialect-specific SQL from ``resources/sqls/utils/rename_tab.sql``.
        Pass raw (unquoted) table names — the SQL resource handles dialect syntax.

        Args:
            old_name: Current table name.
            new_name: Desired table name.

        Raises:
            Exception: If the rename operation fails.
        """
        try:
            sql, src = load_builtin_sql("utils/rename_tab", dialect=self.dialect, old_name=old_name, new_name=new_name)
            result = self.execute(sql, transpile=src if src != self.dialect else None, autocommit=True)
            if not result.ok:
                raise Exception(f"{result.error_type}: {result.error_message}")
            logger.info(f"Renamed table '{old_name}' to '{new_name}'")
        except Exception as e:
            logger.error(f"rename_tab failed: {error_str(e)}")
            raise Exception(f"rename_tab failed '{old_name}' -> '{new_name}': {e}")

    def sort_tab_col(
        self,
        table: sa.Table,
        order: Union[List[str], Callable[[str], Any]],
    ) -> None:
        """\
        Reorder columns of an existing table.

        Because no SQL dialect supports native column reordering, this method
        uses the universal "rebuild" pattern:

        1. Create a temporary table with columns in the desired order.
        2. Copy all rows via ``INSERT INTO tmp SELECT cols FROM orig``.
        3. Drop the original table.
        4. Rename the temporary table to the original name.

        Args:
            table: The ``sa.Table`` whose columns should be reordered.
            order: Either a ``List[str]`` of column names in the desired order,
                   or a sort key ``Callable[[str], Any]`` applied to column names.
                   When a list, unlisted column names are appended at the end in
                   their original relative order.  Names not present in the table
                   are silently ignored.

        Raises:
            Exception: If any step of the rebuild fails.  In that case the
                temporary table is dropped and the original is left intact.

        Notes:
            - Full table copy — avoid on very large tables in latency-sensitive contexts.
            - External FK constraints, views, and triggers referencing this table
              are **not** restored automatically after the rename.
            - Table-level constraints beyond per-column ones (e.g., multi-column
              unique constraints) are not preserved in the rebuilt table.
        """
        col_names = [c.name for c in table.c]
        if callable(order):
            sorted_names = sorted(col_names, key=order)
        else:
            order_set = set(order)
            sorted_names = [n for n in order if n in set(col_names)] + [n for n in col_names if n not in order_set]

        if sorted_names == col_names:
            logger.info(f"sort_tab_col: '{table.name}' already in desired order, skipping")
            return

        preparer = self.engine.dialect.identifier_preparer
        orig_name = table.name
        tmp_name = f"_ahvn_reorder_{orig_name}"

        # Drop any leftover temp table from a previous failed run
        if tmp_name in self.db_tabs():
            self.drop_tab(tmp_name)

        # Build temp table with columns in the new order
        tmp_meta = sa.MetaData()
        ordered_cols = [
            sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, server_default=c.server_default)
            for c in (table.c[n] for n in sorted_names)
        ]
        tmp_table = sa.Table(tmp_name, tmp_meta, *ordered_cols)
        tmp_table.create(self.engine)

        try:
            quoted_cols = ", ".join(preparer.quote(n) for n in sorted_names)
            quoted_tmp = preparer.quote(tmp_name)
            quoted_orig = preparer.quote(orig_name)
            result = self.execute(
                f"INSERT INTO {quoted_tmp} ({quoted_cols}) SELECT {quoted_cols} FROM {quoted_orig}",
                autocommit=True,
            )
            if not result.ok:
                raise Exception(f"{result.error_type}: {result.error_message}")
            table.drop(self.engine)
            self.rename_tab(tmp_name, orig_name)
            logger.info(f"sort_tab_col: reordered {len(sorted_names)} columns in '{orig_name}'")
        except Exception as e:
            try:
                tmp_table.drop(self.engine, checkfirst=True)
            except Exception:
                pass
            logger.error(f"sort_tab_col failed for '{orig_name}': {error_str(e)}")
            raise Exception(f"sort_tab_col failed for '{orig_name}': {e}")

    def drop_tab(self, tab_name: str) -> None:
        """\
        Drop a specific table from the database.

        Uses SQLAlchemy ORM to ensure compatibility across all database backends.

        Args:
            tab_name: Name of the table to drop

        Raises:
            Exception: If the drop operation fails
        """
        try:
            metadata = sa.MetaData()
            table = sa.Table(tab_name, metadata, autoload_with=self.engine)
            table.drop(self.engine)
            logger.info(f"Dropped table: {tab_name}")
        except Exception as e:
            logger.error(f"Failed to drop table {tab_name}: {error_str(e)}")
            raise Exception(f"Table drop failed for {tab_name}: {e}")

    def drop_view(self, view_name: str) -> None:
        """\
        Drop a specific view from the database.

        Args:
            view_name: Name of the view to drop

        Raises:
            Exception: If the drop operation fails
        """
        try:
            self.execute(f"DROP VIEW IF EXISTS {view_name}")
            logger.info(f"Dropped view: {view_name}")
        except Exception as e:
            logger.error(f"Failed to drop view {view_name}: {error_str(e)}")
            raise Exception(f"View drop failed for {view_name}: {e}")

    def drop(self) -> None:
        """\
        Drop all tables in the database.

        Uses SQLAlchemy metadata reflection to drop all tables.

        Raises:
            DatabaseError: If the database drop operation fails
        """
        try:
            metadata = sa.MetaData()
            metadata.reflect(bind=self.engine)
            metadata.drop_all(bind=self.engine, checkfirst=True)
            logger.info("Dropped all tables using metadata")
        except Exception as e:
            logger.warning(f"Metadata drop failed, trying fallback: {error_str(e)}")
            tables = self.db_tabs()
            for table_name in tables:
                try:
                    self.drop_tab(table_name)
                except Exception as table_e:
                    logger.warning(f"Failed to drop table {table_name}: {error_str(table_e)}")

    def init(self) -> None:
        """\
        Drop all tables and re-initialize.

        Raises:
            Exception: If the database initialization fails
        """
        self.drop()

    def clear(self) -> None:
        """\
        Clear all data from tables in the database without deleting the tables themselves.

        Uses the `clear_tab` method to ensure compatibility across all database backends.

        Raises:
            Exception: If the clearing operation fails
        """
        tables = self.db_tabs()
        for table_name in tables:
            try:
                self.clear_tab(table_name)
            except Exception as e:
                logger.error(f"Failed to clear table {table_name}: {error_str(e)}")


def table_display(
    table: Union["SQLResponse", Iterable[Dict]],
    schema: Optional[List[str]] = None,
    max_rows: int = 64,
    max_width: int = 64,
    style: Literal["DEFAULT", "MARKDOWN", "PLAIN_COLUMNS", "MSWORD_FRIENDLY", "ORGMODE", "SINGLE_BORDER", "DOUBLE_BORDER", "RANDOM"] = "DEFAULT",
    **kwargs,
):
    """\
    Render a tabular display of SQL query results or iterable dictionaries using PrettyTable.

    Args:
        table (Union[SQLResponse, Iterable[Dict]]): The table data to display. Can be a SQLResponse object
            (from a database query) or any iterable of dictionaries (e.g., list of dicts).
        schema (Optional[List[str]], optional): List of column names to use as the table schema. If not provided,
            the schema is inferred from the SQLResponse or from the first row of the iterable.
        max_rows (int, optional): Maximum number of rows to display (including the last row and an ellipsis row if truncated).
            If the table has more than `max_rows + 1` rows, the output will show the first `max_rows-1` rows, an ellipsis row,
            and the last row. Defaults to 64.
        max_width (int, optional): Maximum width for each column in the output table. Defaults to 64.
        style (Literal["DEFAULT", "MARKDOWN", "PLAIN_COLUMNS", "MSWORD_FRIENDLY", "ORGMODE", "SINGLE_BORDER", "DOUBLE_BORDER", "RANDOM"], optional): The style to use for the table (supported by PrettyTable). Defaults to "DEFAULT".
        **kwargs: Additional keyword arguments passed to PrettyTable.

    Returns:
        str: A string representation of the formatted table, including the number of rows in total.

    Raises:
        ValueError: If the provided table rows do not match the schema in length.

    Example:
        >>> result = db.execute("SELECT * FROM users")
        >>> table_display(result, max_rows=5)
    """
    if isinstance(table, SQLResponse):
        schema = table.columns
        table = table.to_list(row_fmt="dict")
    else:
        table = list(table)
        schema = schema or (list() if not table else list(table[0].keys()))
        if not all(len(row) == len(schema) for row in table):
            raise ValueError(f"Table failed to display. All rows must have the same number of columns as the schema.\nSchema: {schema}\nTable:\n{table}")

    # Define table styles directly
    styles = {
        "DEFAULT": pt.TableStyle.DEFAULT,
        "MARKDOWN": pt.TableStyle.MARKDOWN,
        "PLAIN_COLUMNS": pt.TableStyle.PLAIN_COLUMNS,
        "MSWORD_FRIENDLY": pt.TableStyle.MSWORD_FRIENDLY,
        "ORGMODE": pt.TableStyle.ORGMODE,
        "SINGLE_BORDER": pt.TableStyle.SINGLE_BORDER,
        "DOUBLE_BORDER": pt.TableStyle.DOUBLE_BORDER,
        "RANDOM": pt.TableStyle.RANDOM,
    }

    ptable = pt.PrettyTable(schema, **kwargs)
    ptable.set_style(styles.get(style, "DEFAULT"))
    ptable.float_format = ".6"
    ptable.max_width = max_width
    if (max_rows is not None) and (len(table) > max_rows + 1):
        bottom_cnt = max_rows // 2
        top_cnt = max_rows - bottom_cnt
        omitted_cnt = len(table) - max_rows
        for row in table[:top_cnt]:
            ptable.add_row([val for _, val in zip(schema, row.values())])
        ptable.add_row([f"... ({omitted_cnt} rows omitted)" if i == 0 else "..." for i, _ in enumerate(schema)])
        for row in table[-bottom_cnt:] if bottom_cnt > 0 else []:
            ptable.add_row([val for _, val in zip(schema, row.values())])
    else:
        for row in table:
            ptable.add_row([val for _, val in zip(schema, row.values())])

    return str(ptable) + f"\n{len(table)} rows in total."
