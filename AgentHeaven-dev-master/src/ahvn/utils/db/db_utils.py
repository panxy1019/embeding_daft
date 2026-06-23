"""\
Database configuration utilities for AgentHeaven.

This module provides functions to parse and resolve database configurations
similar to how LLM configurations are handled. It supports multiple database
providers (SQLite, PostgreSQL, DuckDB, etc.) and generates SQLAlchemy-ready
configurations with URLs and hyperparameters.
"""

from __future__ import annotations

__all__ = [
    "SchemaIndex",
    "DatabaseEngineRegistry",
    "create_database_engine",
    "create_database",
    "drop_database",
    "split_sqls",
    "transpile_sql",
    "prettify_sql",
    "compare_sqls",
    "load_builtin_sql",
    "is_sql_readonly",
    "escape_sql_binds",
    "strip_sql_comments",
    "validate_sql",
]

from ..basic.log_utils import get_logger
from ..basic.debug_utils import raise_mismatch
from ...utils.basic.parser_utils import parse_keys

import os
import re
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING, Callable, Mapping, Sequence
from copy import deepcopy
from collections import OrderedDict

from .sqlglot_runtime import get_sqlglot, sa_dialect_to_sqlglot, resolve_render_dialect

if TYPE_CHECKING:
    from .spec import DatabaseConfigSpec

logger = get_logger(__name__)


from .spec import DATABASE_CONFIG_ENGINE
from sqlalchemy.engine import Engine  # noqa: type annotation; also accessible via sa.engine.Engine
import threading

from ..basic.debug_utils import DatabaseError

SchemaIndex = Dict[str, List[str]]
_READONLY_SQLGLOT_KEYS = {"select", "union", "except", "intersect", "values"}


class DatabaseEngineRegistry:
    """\
    Thread-safe, LRU engine cache keyed by ``materialize(mode="key")``.

    Engines are created with autocreate and pragma event listeners.
    Tracks disposed keys so that stale ``Database`` instances receive a
    clear error instead of silently creating a replacement engine.
    """

    _CACHE_MAX = 2048
    _engines: "OrderedDict[str, Engine]" = OrderedDict()
    _schema_indices: "OrderedDict[str, SchemaIndex]" = OrderedDict()
    _disposed_keys: set[str] = set()
    _lock = threading.Lock()

    @classmethod
    def _spec_key(cls, spec: "DatabaseConfigSpec") -> str:
        return DATABASE_CONFIG_ENGINE.materialize(spec, mode="key")

    @staticmethod
    def _normalize_schema_index(schema_index: Mapping[str, Sequence[str]] | Dict[str, Any] | None) -> "SchemaIndex":
        """Normalize schema index as ``{table_name: [column_name, ...]}``."""
        if not schema_index:
            return {}

        table_map: Any = schema_index
        if isinstance(schema_index, dict) and "tables" in schema_index and isinstance(schema_index.get("tables"), dict):
            table_map = schema_index["tables"]
        if not isinstance(table_map, dict):
            raise ValueError("schema_index must be a mapping of table -> columns, or {'tables': {...}}")

        normalized: SchemaIndex = {}
        for raw_table, raw_columns in table_map.items():
            table = str(raw_table).strip()
            if not table:
                continue
            if raw_columns is None:
                normalized[table] = []
                continue
            if isinstance(raw_columns, str):
                candidates = [raw_columns]
            elif isinstance(raw_columns, dict):
                candidates = list(raw_columns.keys())
            else:
                try:
                    candidates = list(raw_columns)
                except TypeError as e:
                    raise ValueError(f"schema_index[{table!r}] must be iterable of column names") from e

            cols: List[str] = []
            seen = set()
            for c in candidates:
                col = str(c).strip()
                if not col or col in seen:
                    continue
                seen.add(col)
                cols.append(col)
            normalized[table] = cols
        return normalized

    @classmethod
    def get_engine(cls, spec: "DatabaseConfigSpec") -> Engine:
        """\
        Return a cached engine for the spec, creating one if needed.

        Raises:
            DatabaseError: If the engine was previously disposed. Create
                a new ``Database`` instance to obtain a fresh engine.
        """
        key = cls._spec_key(spec)
        with cls._lock:
            if key in cls._disposed_keys:
                raise DatabaseError("Engine has been disposed. Create a new Database instance.")
            if key in cls._engines:
                cls._engines.move_to_end(key)
                return cls._engines[key]
        engine = create_database_engine(spec, autocreate=True)
        with cls._lock:
            if key in cls._disposed_keys:
                engine.dispose()
                raise DatabaseError("Engine has been disposed. Create a new Database instance.")
            if key in cls._engines:
                # Another thread created the engine concurrently; discard ours
                cls._engines.move_to_end(key)
                engine.dispose()
                return cls._engines[key]
            if len(cls._engines) >= cls._CACHE_MAX:
                evicted_key, evicted = cls._engines.popitem(last=False)  # evict LRU
                cls._schema_indices.pop(evicted_key, None)
                evicted.dispose()
            cls._engines[key] = engine
        return engine

    @classmethod
    def clear_disposed(cls, spec: "DatabaseConfigSpec") -> None:
        """Clear the disposed flag for a spec key.

        Called by ``Database.__init__`` so that a freshly created instance
        can obtain a new engine even after the previous one was disposed.
        """
        key = cls._spec_key(spec)
        with cls._lock:
            cls._disposed_keys.discard(key)

    @classmethod
    def get_schema_index(
        cls,
        spec: "DatabaseConfigSpec",
        *,
        builder: Optional[Callable[[], Mapping[str, Sequence[str]] | Dict[str, Any]]] = None,
        refresh: bool = False,
    ) -> "SchemaIndex":
        """Get cached schema index for a database key, optionally building it."""
        key = cls._spec_key(spec)
        with cls._lock:
            if key in cls._disposed_keys:
                raise DatabaseError("Engine has been disposed. Create a new Database instance.")
            if not refresh and key in cls._schema_indices:
                cls._schema_indices.move_to_end(key)
                return deepcopy(cls._schema_indices[key])

        if builder is None:
            return {}
        schema_index = cls._normalize_schema_index(builder())

        with cls._lock:
            if key in cls._disposed_keys:
                return deepcopy(schema_index)
            cls._schema_indices[key] = deepcopy(schema_index)
            cls._schema_indices.move_to_end(key)
            while len(cls._schema_indices) > cls._CACHE_MAX:
                cls._schema_indices.popitem(last=False)
        return deepcopy(schema_index)

    @classmethod
    def set_schema_index(
        cls,
        spec: "DatabaseConfigSpec",
        schema_index: Mapping[str, Sequence[str]] | Dict[str, Any] | None,
    ) -> None:
        """Set/override schema index cache for a database key."""
        key = cls._spec_key(spec)
        normalized = cls._normalize_schema_index(schema_index)
        with cls._lock:
            if key in cls._disposed_keys:
                raise DatabaseError("Engine has been disposed. Create a new Database instance.")
            cls._schema_indices[key] = normalized
            cls._schema_indices.move_to_end(key)
            while len(cls._schema_indices) > cls._CACHE_MAX:
                cls._schema_indices.popitem(last=False)

    @classmethod
    def invalidate_schema_index(cls, spec: "DatabaseConfigSpec") -> None:
        """Invalidate schema index cache for a specific spec."""
        key = cls._spec_key(spec)
        with cls._lock:
            cls._schema_indices.pop(key, None)

    @classmethod
    def dispose(cls, spec: "DatabaseConfigSpec") -> None:
        """Dispose and remove the cached engine for a specific spec."""
        key = cls._spec_key(spec)
        with cls._lock:
            engine = cls._engines.pop(key, None)
            cls._schema_indices.pop(key, None)
            cls._disposed_keys.add(key)
        if engine is not None:
            engine.dispose()

    @classmethod
    def dispose_all(cls) -> None:
        """Dispose and clear all cached engines."""
        with cls._lock:
            for key in list(cls._engines):
                cls._disposed_keys.add(key)
            for engine in cls._engines.values():
                engine.dispose()
            cls._engines.clear()
            cls._schema_indices.clear()


def _register_pragma_listeners(engine: Engine, pragmas: List[str]) -> None:
    """\
    Register SQLAlchemy event listeners that execute pragma statements
    on every new raw DBAPI connection.
    """
    if not pragmas:
        return

    @sa.event.listens_for(engine, "connect")
    def _run_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        for pragma in pragmas:
            cursor.execute(pragma)
        cursor.close()


def create_database_engine(spec: "DatabaseConfigSpec", autocreate: bool = True) -> Engine:
    """\
    Create a SQLAlchemy engine from a resolved ``DatabaseConfigSpec``.

    Handles autocreation of the database and registers pragma event listeners.

    Args:
        spec: Resolved database configuration spec.
        autocreate: Whether to autocreate the database if it does not exist.

    Returns:
        Engine: A SQLAlchemy engine instance.
    """
    engine_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="engine")
    url = engine_kw.pop("url")

    if autocreate:
        try:
            create_database(spec)
        except Exception as e:
            safe_url = make_url(url).render_as_string(hide_password=True) if url else "unknown"
            logger.warning(f"Failed to autocreate database for url={safe_url}: {e}")

    # GaussDB returns a non-standard version string that psycopg2 cannot parse.
    # Patch the PGDialect class-level method to return a fixed PostgreSQL-compatible version.
    if spec.provider == "gauss":
        from sqlalchemy.dialects.postgresql.base import PGDialect

        PGDialect._get_server_version_info = lambda *args: (9, 2)

    logger.info(f"Created database engine for url={url}, {engine_kw}")
    engine = sa.create_engine(url, **engine_kw)
    # Register pragma event listeners
    pragmas = DATABASE_CONFIG_ENGINE.materialize(spec, mode="pragmas")
    _register_pragma_listeners(engine, pragmas)

    return engine


def create_database(spec: "DatabaseConfigSpec") -> None:
    """\
    Create the database if it does not already exist.

    - File-based (SQLite, DuckDB): ensures the parent directory exists.
    - Server-based dialects: uses the ``superuser`` config from the spec
      (or falls back to standard connection params) to connect to a
      maintenance database and issues ``CREATE DATABASE``.

    Args:
        spec: Resolved ``DatabaseConfigSpec``.
    """
    dialect = spec.dialect
    database_name = spec.database

    if not dialect or not database_name:
        return

    # File-based databases: ensure directory exists
    if dialect in ("sqlite", "duckdb"):
        if database_name != ":memory:":
            db_dir = os.path.dirname(database_name)
            if db_dir and not os.path.exists(db_dir):
                from ..basic.file_utils import touch_dir

                touch_dir(db_dir)
        return

    # Server-based dialects (all use superuser DDL)
    if dialect in ("postgresql", "postgres", "mysql", "mssql", "oracle", "starrocks", "hive"):
        _create_server_database(spec)
        return

    logger.debug(f"autocreate not implemented for dialect={dialect}")


def drop_database(spec: "DatabaseConfigSpec") -> None:
    """\
    Drop the database if it exists.

    - File-based (SQLite, DuckDB): removes the database file.
    - Server-based dialects: connects via superuser and issues ``DROP DATABASE``.

    Warning:
        This permanently deletes the database and all its data.
        The caller must ensure the engine is disposed before calling this
        (use ``DatabaseEngineRegistry.dispose(spec)`` first).

    Args:
        spec: Resolved ``DatabaseConfigSpec``.
    """
    dialect = spec.dialect
    database_name = spec.database

    if not dialect or not database_name:
        return

    # File-based databases: remove the file
    if dialect in ("sqlite", "duckdb"):
        if database_name != ":memory:" and os.path.exists(database_name):
            os.remove(database_name)
            logger.info(f"Removed database file '{database_name}'")
        return

    # Server-based dialects
    if dialect in ("postgresql", "postgres", "mysql", "mssql", "oracle", "starrocks", "hive"):
        _drop_server_database(spec)
        return

    logger.debug(f"drop_database not implemented for dialect={dialect}")


def _grant_db_access(conn, dialect: str, database_name: str, username: str, escape) -> None:
    """\
    Grant the regular user full access to a newly created database.

    Called from ``_create_server_database`` when the bootstrapping superuser
    differs from the regular connecting user.  Each dialect has its own DDL:

    - MySQL/MariaDB/OceanBase: ``GRANT ALL PRIVILEGES ON `db`.* TO 'user'@'%'``
    - PostgreSQL: ``ALTER DATABASE "db" OWNER TO "user"``
    - MSSQL: login → db user mapping under the new database
    - StarRocks: ``GRANT ALL ON DATABASE `db` TO 'user'``
    - Oracle: privileges already implicit (user owns the schema)
    """
    safe_db = escape(database_name)
    # Escape single-quotes in the user name for dialects that embed it in SQL string literals.
    safe_user_sq = username.replace("'", "''")
    # PG-style double-quote escape for identifiers.
    safe_user_dq = username.replace('"', '""')

    if dialect == "mysql":
        conn.execute(sa.text(f"GRANT ALL PRIVILEGES ON `{safe_db}`.* TO '{safe_user_sq}'@'%'"))
        conn.execute(sa.text("FLUSH PRIVILEGES"))
    elif dialect in ("postgresql", "postgres"):
        conn.execute(sa.text(f'ALTER DATABASE "{safe_db}" OWNER TO "{safe_user_dq}"'))
    elif dialect == "mssql":
        # Must switch context to the new database to create a user mapping.
        safe_login = username.replace("]", "]]")
        conn.execute(sa.text(f"USE [{safe_db}]"))
        conn.execute(
            sa.text(
                f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'{safe_user_sq}') " f"CREATE USER [{safe_login}] FOR LOGIN [{safe_login}]"
            )
        )
        conn.execute(sa.text(f"ALTER ROLE [db_owner] ADD MEMBER [{safe_login}]"))
    elif dialect == "starrocks":
        conn.execute(sa.text(f"GRANT ALL ON DATABASE `{safe_db}` TO '{safe_user_sq}'"))
    # Oracle: the created USER already owns the schema; CONNECT+RESOURCE grants
    # are typically pre-granted by the DBA, not needed per-db.
    conn.commit()
    logger.info(f"Granted access on '{database_name}' to user '{username}' (dialect={dialect})")


def _create_server_database(spec: "DatabaseConfigSpec") -> None:
    """\
    Create a database on server-based systems using superuser config.

    Uses ``materialize(mode="superuser")`` to obtain the maintenance
    connection URL.  Falls back to regular connection if no superuser
    is configured.

    Dialect-specific handling:
    - PostgreSQL/MySQL/MSSQL: ``CREATE DATABASE``
    - Oracle: ``CREATE USER`` (Oracle uses user/schema instead of database)
    - StarRocks: ``CREATE DATABASE`` (MySQL-compatible DDL)
    - Hive: ``CREATE DATABASE``
    """
    dialect = spec.dialect
    database_name = spec.database

    # Per-dialect existence check and CREATE DDL (unavoidable raw SQL for DDL)
    if dialect == "mssql":
        exists_query = "SELECT 1 FROM sys.databases WHERE name = :name"
        create_tpl = "CREATE DATABASE [{name}]"
        escape = lambda n: n.replace("]", "]]")  # noqa: E731
    elif dialect in ("postgresql", "postgres"):
        exists_query = "SELECT 1 FROM pg_database WHERE datname = :name"
        create_tpl = 'CREATE DATABASE "{name}"'
        escape = lambda n: n.replace('"', '""')  # noqa: E731
    elif dialect == "mysql":
        exists_query = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
        create_tpl = "CREATE DATABASE `{name}`"
        escape = lambda n: n.replace("`", "``")  # noqa: E731
    elif dialect == "oracle":
        exists_query = "SELECT COUNT(*) FROM all_users WHERE username = UPPER(:name)"
        # Password sourced from superuser config; falls back to a generated default
        su = spec.superuser or {}
        oracle_pw = su.get("password") or f"{database_name}_pw"
        create_tpl = 'CREATE USER {{name}} IDENTIFIED BY "{pw}"'.format(pw=oracle_pw)
        escape = lambda n: n.upper()  # noqa: E731
    elif dialect == "starrocks":
        # StarRocks uses MySQL-compatible DDL but with its own dialect driver
        exists_query = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
        create_tpl = "CREATE DATABASE `{name}`"
        escape = lambda n: n.replace("`", "``")  # noqa: E731
    elif dialect == "hive":
        # Hive supports CREATE DATABASE IF NOT EXISTS natively
        exists_query = None  # skip existence check, use IF NOT EXISTS
        create_tpl = "CREATE DATABASE IF NOT EXISTS `{name}`"
        escape = lambda n: n.replace("`", "``")  # noqa: E731
    else:
        return

    # Determine whether to grant privileges to the regular user after creation.
    # Needed when the superuser (used for DDL) differs from the connecting user.
    su_info = spec.superuser or {}
    su_username = su_info.get("username") or spec.username
    reg_username = spec.username
    needs_grant = bool(reg_username and su_username and reg_username != su_username)

    # Get superuser connection kwargs from spec
    su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
    su_url = su_kw.pop("url")

    tmp_engine = sa.create_engine(su_url, **su_kw)
    try:
        with tmp_engine.connect() as conn:
            if exists_query is not None:
                res = conn.execute(sa.text(exists_query), {"name": database_name}).scalar()
            else:
                res = None  # Hive: skip check, rely on IF NOT EXISTS

            if not res:
                safe_name = escape(database_name)
                create_q = create_tpl.format(name=safe_name)
                conn.execute(sa.text(create_q))
                conn.commit()
                logger.info(f"Created database '{database_name}' (dialect={dialect})")

                # Grant the regular user full access to the newly created database.
                if needs_grant:
                    _grant_db_access(conn, dialect, database_name, reg_username, escape)
    finally:
        tmp_engine.dispose()


def _drop_server_database(spec: "DatabaseConfigSpec") -> None:
    """\
    Drop a database on server-based systems using superuser config.

    Uses ``materialize(mode="superuser")`` to obtain the maintenance
    connection URL.
    """
    dialect = spec.dialect
    database_name = spec.database

    if dialect == "mssql":
        drop_tpl = "DROP DATABASE [{name}]"
        exists_query = "SELECT 1 FROM sys.databases WHERE name = :name"
        escape = lambda n: n.replace("]", "]]")  # noqa: E731
    elif dialect in ("postgresql", "postgres"):
        drop_tpl = 'DROP DATABASE "{name}"'
        exists_query = "SELECT 1 FROM pg_database WHERE datname = :name"
        escape = lambda n: n.replace('"', '""')  # noqa: E731
    elif dialect in ("mysql", "starrocks"):
        drop_tpl = "DROP DATABASE `{name}`"
        exists_query = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
        escape = lambda n: n.replace("`", "``")  # noqa: E731
    elif dialect == "oracle":
        drop_tpl = "DROP USER {name} CASCADE"
        exists_query = "SELECT COUNT(*) FROM all_users WHERE username = UPPER(:name)"
        escape = lambda n: n.upper()  # noqa: E731
    elif dialect == "hive":
        drop_tpl = "DROP DATABASE IF EXISTS `{name}` CASCADE"
        exists_query = None
        escape = lambda n: n.replace("`", "``")  # noqa: E731
    else:
        return

    su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
    su_url = su_kw.pop("url")

    tmp_engine = sa.create_engine(su_url, **su_kw)
    try:
        with tmp_engine.connect() as conn:
            if exists_query is not None:
                res = conn.execute(sa.text(exists_query), {"name": database_name}).scalar()
                if not res:
                    logger.debug(f"Database '{database_name}' does not exist, skip drop")
                    return

            safe_name = escape(database_name)
            drop_q = drop_tpl.format(name=safe_name)
            conn.execute(sa.text(drop_q))
            conn.commit()
            logger.info(f"Dropped database '{database_name}' (dialect={dialect})")
    finally:
        tmp_engine.dispose()


def split_sqls(queries: str, dialect: str = "sqlite"):
    """\
    Split a string containing multiple SQL queries into a list.

    Args:
        queries (str): The SQL queries to split.
        dialect (str): The SQL dialect to use (default is "sqlite").
            Accepts SQLAlchemy dialect names (auto-mapped to SQLGlot).

    Returns:
        List[str]: A list of individual SQL queries.
    """
    try:
        if not queries.strip():
            return []
        sg_dialect = sa_dialect_to_sqlglot(dialect)
        parsed = get_sqlglot().parse(queries, dialect=sg_dialect)
        return [s.sql().strip() for s in parsed if s is not None]
    except Exception as e:
        raise ValueError(f"Failed to split SQL queries: {e}.")


def transpile_sql(
    query: str,
    src_dialect: str = "sqlite",
    tgt_dialect: str = "sqlite",
    *,
    prefer_backticks: bool = False,
) -> str:
    """\
    Transpile a SQL query from one dialect to another.

    Args:
        query (str): The SQL query to transpile.
        src_dialect (str): The source dialect to transpile from.
            Accepts SQLAlchemy dialect names (auto-mapped to SQLGlot).
        tgt_dialect (str): The target dialect to transpile to.
            Accepts SQLAlchemy dialect names (auto-mapped to SQLGlot).
        prefer_backticks (bool): If True, prefer backticks for identifier
            quoting when the target dialect supports backticks.

    Returns:
        str: The transpiled query.

    Raises:
        ImportError: If SQLGlot is not installed.
        ValueError: If transpilation fails.
    """
    try:
        src = sa_dialect_to_sqlglot(src_dialect)
        tgt = resolve_render_dialect(tgt_dialect, prefer_backticks=prefer_backticks)
        return get_sqlglot().transpile(
            query,
            read=src,
            write=tgt,
            comments=False,
            identify=bool(prefer_backticks),
        )[0]
    except Exception as e:
        raise ValueError(f"Failed to transpile query from {src_dialect} to {tgt_dialect}: {e}.")


def prettify_sql(
    query: str,
    dialect: str = "sqlite",
    comments: bool = True,
    *,
    prefer_backticks: bool = True,
) -> str:
    """\
    Prettify a SQL query for better readability (identify + strip).

    Args:
        query (str): The SQL query to prettify.
        dialect (str): The SQL dialect to use (default is "sqlite").
            Accepts SQLAlchemy dialect names (auto-mapped to SQLGlot).
        comments (bool): Whether to keep comments in the output (default is True).
        prefer_backticks (bool): If True, prefer backticks for identifier
            quoting when the dialect supports backticks (default: True).
            Unsupported dialects continue to use their native quoting.

    Returns:
        str: The prettified SQL query. If failed, returns the original query stripped.
    """
    try:
        sg = get_sqlglot()
        sg_dialect = sa_dialect_to_sqlglot(dialect)
        render_dialect = resolve_render_dialect(dialect, prefer_backticks=prefer_backticks)

        # Pre-pass: inline CTE column aliases for dialects that drop them.
        from .sqlglot_runtime import _dialect_supports_cte_columns, rewrite_cte_column_aliases

        if not _dialect_supports_cte_columns(dialect):
            try:
                trees = sg.parse(query, read=sg_dialect)
                parts = []
                for t in trees:
                    if t is not None:
                        t = rewrite_cte_column_aliases(t)
                        parts.append(
                            t.sql(
                                dialect=render_dialect,
                                identify=True,
                                pretty=True,
                                comments=comments,
                            )
                        )
                return ";\n".join(parts).strip()
            except Exception:
                pass  # fall through to normal transpile

        return sg.transpile(
            query,
            read=sg_dialect,
            write=render_dialect,
            identify=True,
            pretty=True,
            comments=comments,
        )[0].strip()
    except Exception as e:
        from ahvn.utils.basic.debug_utils import error_str

        logger.warning(f"Failed to prettify SQL query: {error_str(e)}")
        logger.warning(f"Original query: {query}")
        return query.strip()


def compare_sqls(sql1: str, sql2: str, db) -> bool:
    """\
    Given two SQL queries, execute them on the provided database and compare their results.

    Args:
        sql1 (str): The first SQL query.
        sql2 (str): The second SQL query.
        db: The database instance with an `execute_sql` method.

    Returns:
        bool: True if the results are identical, False otherwise.
    """
    try:
        res1 = db.execute_sql(sql1)
    except Exception as e:
        logger.warning(f"Failed to execute sql1 for comparison: {e}")
        return False
    try:
        res2 = db.execute_sql(sql2)
    except Exception as e:
        logger.warning(f"Failed to execute sql2 for comparison: {e}")
        return False
    return bool(res1 == res2)


def _sqla_readonly(query: Any) -> Optional[bool]:
    """Conservative SQLAlchemy statement inspection."""
    if query is None:
        return None

    if isinstance(query, sa.schema.DDLElement):
        return False

    if getattr(query, "is_insert", False) or getattr(query, "is_update", False) or getattr(query, "is_delete", False):
        return False

    if getattr(query, "is_dml", False):
        return False

    if getattr(query, "is_select", False):
        # SELECT ... FOR UPDATE is not treated as read-only in conservative mode.
        for lock_key in ("lock", "locks", "for_update"):
            if getattr(query, lock_key, None):
                return False
        if getattr(query, "_for_update_arg", None) is not None:
            return False
        if _sqla_has_func(query):
            # Function calls may have side effects in some backends.
            return False
        return True

    return None


def _sqla_has_func(query: Any) -> bool:
    """Best-effort traversal to detect SQLAlchemy function elements."""
    stack = [query]
    visited = set()
    while stack:
        node = stack.pop()
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        if isinstance(node, sa.sql.functions.FunctionElement):
            return True
        try:
            stack.extend(list(node.get_children()))
        except Exception:
            continue
    return False


def _sqlglot_readonly(query_text: Optional[str], dialect: Optional[str]) -> bool:
    """Conservative SQLGlot-based read-only detection."""
    if not query_text or not query_text.strip():
        return False
    try:
        sg = get_sqlglot()
        sg_dialect = sa_dialect_to_sqlglot(dialect or "sqlite")
        statements = sg.parse(query_text, dialect=sg_dialect)
        if not statements:
            return False
        for stmt in statements:
            key = getattr(stmt, "key", "")
            if key not in _READONLY_SQLGLOT_KEYS:
                return False
            args = getattr(stmt, "args", {}) or {}
            if key == "select":
                if args.get("lock") or args.get("locks") or args.get("into"):
                    return False
            if any(True for _ in stmt.find_all(sg.exp.Func)):
                # Function calls may have side effects in some engines.
                return False
        return True
    except Exception:
        return False


def is_sql_readonly(query: Any, *, query_text: Optional[str] = None, dialect: Optional[str] = None) -> bool:
    """\
    Return True only when we can confidently prove a statement is read-only.

    Conservative policy:
    - Parse/inspect success + known read-only form -> True
    - Anything unknown/unsupported/parse-failed -> False
    """
    by_sqla = _sqla_readonly(query)
    if by_sqla is not None:
        return by_sqla

    text = query_text if query_text is not None else (str(query) if query is not None else None)
    return _sqlglot_readonly(text, dialect)


def load_builtin_sql(query_name: str, dialect: str = "sqlite", **kwargs) -> Tuple[str, str]:
    """\
    Load SQL query from file and return the query with its source dialect.

    If the requested *dialect* has a dedicated entry in the SQL file, it is
    returned directly.  Otherwise the **sqlite** variant is returned as a
    fallback — the caller is expected to pass it through transpilation via
    ``Database.execute(..., transpile="sqlite")``.

    Warning:
        This function uses string formatting (``.format(**kwargs)``) to inject parameters into the SQL query.
        This is vulnerable to SQL injection if ``kwargs`` contains untrusted user input.
        Only use this function with trusted input or for internal queries where parameters are controlled.
        For user-supplied values, prefer using parameterized queries supported by your database driver.

    Args:
        query_name (str): Name of the SQL file (without .sql extension).
        dialect (str): The SQL dialect to use (default is "sqlite").
        **kwargs: Additional parameters for query formatting.

    Returns:
        Tuple[str, str]: ``(sql, source_dialect)`` — the SQL query string and
            the dialect it was written in.  When ``source_dialect == dialect``
            no transpilation is needed.

    Raises:
        FileNotFoundError: If SQL file is not found.
    """
    from ..basic.config_utils import CM_AHVN

    sql_file_path = CM_AHVN.pj("&/sqls", f"{query_name}.sql")
    try:
        with open(sql_file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"SQL file not found: {sql_file_path}")
    queries = parse_keys(content)
    if dialect in queries:
        return queries[dialect].format(**kwargs).strip(), dialect  # SQL Injection Warning
    else:
        return queries["sqlite"].format(**kwargs).strip(), "sqlite"


# ---------------------------------------------------------------------------
# SQL text utilities
# ---------------------------------------------------------------------------


def escape_sql_binds(sql: str) -> str:
    """\
    Escape colon-prefixed tokens in a SQL string so that SQLAlchemy's
    ``text()`` does not misinterpret them as bind parameters.

    Every ``:`` is replaced with ``\\:`` (the SQLAlchemy escape sequence for
    a literal colon inside ``text()``).  ``::`` type-cast operators are
    preserved correctly because each ``:`` is individually escaped into
    ``\\:`` and ``\\:\\:`` round-trips back to ``::`` at execution time.

    Use this when executing arbitrary user-supplied SQL that was **not**
    designed for parameterised execution (e.g. benchmark evaluation,
    ad-hoc queries from NL2SQL systems).

    Args:
        sql: Raw SQL string.

    Returns:
        SQL string safe for ``sa.text()`` execution without parameters.
    """
    return sql.replace(":", r"\:").strip()


def strip_sql_comments(sql: str) -> str:
    """\
    Remove SQL comments from a query string.

    Strips both single-line comments (``-- ...``) and block comments
    (``/* ... */``).

    Args:
        sql: SQL query string.

    Returns:
        SQL string without comments.
    """
    if not sql:
        return ""
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def validate_sql(sql: str, dialect: str = "duckdb") -> bool:
    """\
    Validate SQL syntax for a given dialect using SQLGlot.

    Args:
        sql: SQL query string.
        dialect: Target dialect for validation (default: ``"duckdb"``).

    Returns:
        True if the SQL parses successfully, False otherwise.
    """
    if not sql or not sql.strip():
        return False
    try:
        sg = get_sqlglot()
        sg_dialect = sa_dialect_to_sqlglot(dialect)
        result = sg.parse(sql, dialect=sg_dialect)
        return len(result) > 0 and result[0] is not None
    except Exception:
        return False
