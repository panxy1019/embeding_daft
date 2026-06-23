"""\
SQL Processor — cross-dialect transpilation and parameter normalization.

Two-tier architecture:

* **SQLProcessor** (default) — lightweight, no optional dependencies.
  Normalizes parameter formats to SQLAlchemy ``:name`` style via simple
  string-level conversion.  Cannot transpile between dialects.

* **SQLGlotProcessor** (requires ``sqlglot``) — full-featured.
  Uses SQLGlot AST for safe parameter extraction (ignores string-literal
  contents), cross-dialect transpilation, and robust parameter normalization.

Use ``create_sql_processor()`` factory to get the right class automatically.
"""

from __future__ import annotations

__all__ = [
    "SA_TO_SQLGLOT",
    "SQLGLOT_TO_SA",
    "sa_dialect_to_sqlglot",
    "sqlglot_dialect_to_sa",
    "SQLProcessor",
    "SQLGlotProcessor",
    "create_sql_processor",
]

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from ..basic.log_utils import get_logger
from ..basic.debug_utils import DatabaseError
from ..deps import deps, OptionalDependencyError
from .sqlglot_runtime import get_sqlglot as _get_sqlglot_runtime
from .sqlglot_runtime import (
    SA_TO_SQLGLOT as _SA_TO_SQLGLOT_RUNTIME,
    SQLGLOT_TO_SA as _SQLGLOT_TO_SA_RUNTIME,
    resolve_render_dialect as _resolve_render_dialect_runtime,
    sa_dialect_to_sqlglot as _sa_dialect_to_sqlglot_runtime,
    sqlglot_dialect_to_sa as _sqlglot_dialect_to_sa_runtime,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dialect name mapping
# ---------------------------------------------------------------------------

SA_TO_SQLGLOT: Dict[str, str] = _SA_TO_SQLGLOT_RUNTIME
"""SQLAlchemy dialect name → SQLGlot dialect name."""

SQLGLOT_TO_SA: Dict[str, str] = _SQLGLOT_TO_SA_RUNTIME
"""SQLGlot dialect name → SQLAlchemy dialect name."""


def sa_dialect_to_sqlglot(dialect: str) -> str:
    """Convert a SQLAlchemy dialect identifier to its SQLGlot equivalent."""
    return _sa_dialect_to_sqlglot_runtime(dialect)


def sqlglot_dialect_to_sa(dialect: str) -> str:
    """Convert a SQLGlot dialect identifier to its SQLAlchemy equivalent."""
    return _sqlglot_dialect_to_sa_runtime(dialect)


# ---------------------------------------------------------------------------
# Regex patterns for base-class parameter normalization
# ---------------------------------------------------------------------------

# Positional: ? (JDBC style)
_RE_JDBC = re.compile(r"(?<!')\?(?!')")

# Named: :name (SQLAlchemy / Oracle style) — skip ::type casts
_RE_COLON_NAME = re.compile(r"(?<!:):([A-Za-z_]\w*)(?!:)")

# Named: %(name)s (psycopg2 style)
_RE_PYFORMAT = re.compile(r"%\(([^)]+)\)s")

# Named: $name (DuckDB / shell style)
_RE_DOLLAR_NAME = re.compile(r"\$([A-Za-z_]\w*)")

# Positional: $1, $2, … (PostgreSQL positional)
_RE_DOLLAR_POS = re.compile(r"\$(\d+)")


# ---------------------------------------------------------------------------
# SQLProcessor — base class, no SQLGlot
# ---------------------------------------------------------------------------


class SQLProcessor:
    """\
    Lightweight SQL processor — parameter normalization only.

    Converts all common parameter placeholder formats to SQLAlchemy's
    ``:name`` style so that queries can be executed via ``sa.text()``.

    Transpilation is **not** supported and will emit a warning (or raise,
    depending on *on_error*).  Install ``sqlglot`` for transpilation.

    Limitations:
        Regex-based normalization cannot distinguish placeholders inside
        string literals (e.g. ``WHERE note = ':foo'``).  Use
        ``SQLGlotProcessor`` for AST-based safe handling.

    Parameters:
        target_dialect: SQLAlchemy dialect name (e.g. ``"sqlite"``, ``"postgresql"``).
        on_error: ``"warn"`` (default) to log + fallback, ``"raise"`` to propagate errors.
    """

    def __init__(self, target_dialect: str, *, on_error: str = "warn"):
        self.target_dialect = target_dialect
        self.on_error = on_error

    # -- public API ---------------------------------------------------------

    def process_query(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Dict[str, Any]], List, Tuple]] = None,
        transpile_from: Optional[str] = None,
    ) -> Tuple[str, Union[Dict[str, Any], List[Dict[str, Any]]]]:
        """\
        Normalize a SQL query and its parameters for ``sa.text()`` execution.

        Args:
            query: Raw SQL string.
            params: Parameters — ``dict`` for named, ``list``/``tuple`` for
                positional, ``List[Dict]`` for batch execution, or ``None``.
            transpile_from: Source dialect to transpile **from**.  If the
                source differs from *target_dialect* and SQLGlot is not
                available, a warning/error is emitted and the original SQL
                is kept as-is.

        Returns:
            ``(processed_query, normalized_params)`` where the query uses
            ``:name`` placeholders and *normalized_params* is a ``dict``
            (or ``List[Dict]`` for batch).
        """
        if transpile_from and transpile_from != self.target_dialect:
            msg = (
                f"SQL transpilation from '{transpile_from}' to "
                f"'{self.target_dialect}' requested, but SQLGlot is not "
                f"available.  Install with: pip install sqlglot"
            )
            if self.on_error == "raise":
                raise OptionalDependencyError(msg)
            logger.warning(msg)

        return self._normalize_params(query, params)

    def transpile(self, query: str, src_dialect: str, tgt_dialect: str, *, prefer_backticks: bool = False) -> str:
        """\
        Transpile *query* from *src_dialect* to *tgt_dialect*.

        Raises ``OptionalDependencyError`` in the base class (no SQLGlot).
        """
        raise OptionalDependencyError("SQL transpilation requires SQLGlot. Install with: pip install sqlglot")

    def split(self, queries: str, dialect: Optional[str] = None) -> List[str]:
        """\
        Split a multi-statement SQL string into individual statements.

        Raises ``OptionalDependencyError`` in the base class (no SQLGlot).
        """
        raise OptionalDependencyError("SQL splitting requires SQLGlot. Install with: pip install sqlglot")

    def prettify(self, query: str, dialect: Optional[str] = None, *, comments: bool = True, prefer_backticks: bool = True) -> str:
        """\
        Pretty-print a SQL query.

        Raises ``OptionalDependencyError`` in the base class (no SQLGlot).
        """
        raise OptionalDependencyError("SQL prettification requires SQLGlot. Install with: pip install sqlglot")

    # -- internal -----------------------------------------------------------

    def _normalize_params(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Dict[str, Any]], List, Tuple]],
    ) -> Tuple[str, Union[Dict[str, Any], List[Dict[str, Any]]]]:
        """\
        Normalize parameter placeholders to ``:name`` format.

        Dispatches by *params* type:

        - ``None`` → passthrough ``{}``
        - ``dict`` → named normalization
        - ``list``/``tuple`` of scalars → positional normalization
        - ``List[Dict]`` → batch (normalize query once, keep list)
        """
        if params is None:
            return query, {}

        if isinstance(params, (list, tuple)):
            if params and isinstance(params[0], dict):
                # Batch execution: List[Dict]
                return self._normalize_batch(query, params)
            return self._normalize_positional(query, params)

        if isinstance(params, dict):
            return self._normalize_named(query, params)

        # Single scalar value — treat as a 1-element positional
        return self._normalize_positional(query, (params,))

    # --- positional --------------------------------------------------------

    def _normalize_positional(self, query: str, params: Union[List, Tuple]) -> Tuple[str, Dict[str, Any]]:
        """\
        Convert positional placeholders (``?``, ``%s``, ``$1``) to ``:param_N``.
        """
        counter = iter(range(len(params)))
        param_dict: Dict[str, Any] = {}

        def _make(match, idx=None):
            i = idx if idx is not None else next(counter)
            name = f"param_{i}"
            if i < len(params):
                param_dict[name] = params[i]
            return f":{name}"

        # $1, $2, ... (1-indexed)
        if _RE_DOLLAR_POS.search(query):

            def _dollar_pos_repl(m):
                idx = int(m.group(1)) - 1
                return _make(m, idx=idx)

            query = _RE_DOLLAR_POS.sub(_dollar_pos_repl, query)
            return query, param_dict

        # ? or %s (sequential)
        if "?" in query:
            query = _RE_JDBC.sub(lambda m: _make(m), query)
        elif "%s" in query:
            query = re.sub(r"(?<!')%s(?!')", lambda m: _make(m), query)

        return query, param_dict

    # --- named -------------------------------------------------------------

    def _normalize_named(self, query: str, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """\
        Normalize named placeholders to ``:name`` format.

        Handles ``%(name)s``, ``$name``, and ``:name`` (passthrough).
        """
        out_params: Dict[str, Any] = {}

        # %(name)s → :name
        if _RE_PYFORMAT.search(query):

            def _pyf_repl(m):
                name = m.group(1)
                if name in params:
                    out_params[name] = params[name]
                return f":{name}"

            query = _RE_PYFORMAT.sub(_pyf_repl, query)

        # $name → :name  (skip if preceded by digit — $1 form is positional)
        if "$" in query and _RE_DOLLAR_NAME.search(query):

            def _dollar_name_repl(m):
                name = m.group(1)
                if name in params:
                    out_params[name] = params[name]
                return f":{name}"

            query = _RE_DOLLAR_NAME.sub(_dollar_name_repl, query)

        # :name — already correct, just collect matching params
        for m in _RE_COLON_NAME.finditer(query):
            name = m.group(1)
            if name in params:
                out_params[name] = params[name]

        # If nothing was collected, pass all params through
        # (covers the case where query has no recognizable placeholders
        #  or uses an unsupported format)
        if not out_params:
            out_params = dict(params)

        return query, out_params

    # --- batch -------------------------------------------------------------

    def _normalize_batch(self, query: str, params: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """\
        Normalize query for batch (``executemany``) execution.

        The query is normalized once using the first dict's keys;
        the parameter list is returned as-is.
        """
        if not params:
            return query, params

        query, _ = self._normalize_named(query, params[0])
        return query, params


# ---------------------------------------------------------------------------
# SQLGlotProcessor — full AST-based processor
# ---------------------------------------------------------------------------


class SQLGlotProcessor(SQLProcessor):
    """\
    Full-featured SQL processor powered by SQLGlot AST.

    Provides:

    * **Transpilation** — convert SQL between any two dialects supported
      by SQLGlot (sqlite, postgres, mysql, tsql, oracle, duckdb, hive,
      starrocks, trino, …).
    * **Safe parameter normalization** — uses the parsed AST so that
      placeholders inside string literals or comments are correctly
      ignored.
    * **Error handling** — configurable via *on_error*: ``"warn"``
      (default) logs the error and falls back to the original SQL;
      ``"raise"`` propagates the exception.

    Parameters:
        target_dialect: SQLAlchemy dialect name.
        on_error: ``"warn"`` or ``"raise"``.
    """

    def __init__(self, target_dialect: str, *, on_error: str = "warn"):
        super().__init__(target_dialect, on_error=on_error)
        self._sg = _get_sqlglot_runtime()

    # -- public API ---------------------------------------------------------

    def process_query(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Dict[str, Any]], List, Tuple]] = None,
        transpile_from: Optional[str] = None,
    ) -> Tuple[str, Union[Dict[str, Any], List[Dict[str, Any]]]]:
        """\
        Process a SQL query with optional transpilation and robust
        AST-based parameter normalization.

        Steps:

        1. Pre-process ``%s`` → ``?`` (SQLGlot cannot parse ``%s``).
        2. Parse the SQL into an AST using the *source* dialect.
        3. Walk the AST, normalize all ``Placeholder`` / ``Parameter``
           nodes to ``Placeholder(this=name)`` (named ``:name`` form).
        4. If *transpile_from* differs from *target_dialect*, transpile.
        5. Generate SQL and post-process to ensure ``:name`` output
           (some dialects render ``%(name)s`` or ``$name``).
        6. Build the normalized parameter dictionary.
        """
        src_dialect = transpile_from or self.target_dialect
        src_sg = sa_dialect_to_sqlglot(src_dialect)
        tgt_sg = sa_dialect_to_sqlglot(self.target_dialect)

        # Determine if we need AST processing
        need_transpile = transpile_from and transpile_from != self.target_dialect
        need_ast = need_transpile or params is not None

        if not need_ast:
            return query, params or {}

        # --- handle batch params (List[Dict]) ---
        if isinstance(params, (list, tuple)) and params and isinstance(params[0], dict):
            processed_query, _ = self._ast_process(query, params[0], src_sg, tgt_sg, need_transpile)
            return processed_query, params

        # --- handle positional params ---
        if isinstance(params, (list, tuple)):
            return self._ast_process_positional(query, params, src_sg, tgt_sg, need_transpile)

        # --- handle single scalar ---
        if params is not None and not isinstance(params, dict):
            return self._ast_process_positional(query, (params,), src_sg, tgt_sg, need_transpile)

        # --- handle named params (dict) or no params ---
        return self._ast_process(query, params, src_sg, tgt_sg, need_transpile)

    def transpile(self, query: str, src_dialect: str, tgt_dialect: str, *, prefer_backticks: bool = False) -> str:
        """\
        Transpile *query* from *src_dialect* to *tgt_dialect* via SQLGlot.

        Dialect names are SQLAlchemy-style and automatically mapped.
        When ``prefer_backticks`` is True, identifier quoting uses backticks
        if the target dialect supports them.

        Raises:
            ValueError: If transpilation fails and *on_error* is ``"raise"``.
        """
        src_sg = sa_dialect_to_sqlglot(src_dialect)
        tgt_sg = _resolve_render_dialect_runtime(tgt_dialect, prefer_backticks=prefer_backticks)
        try:
            results = self._sg.transpile(
                query,
                read=src_sg,
                write=tgt_sg,
                comments=False,
                identify=bool(prefer_backticks),
            )
            return results[0]
        except Exception as e:
            msg = f"SQL transpilation failed ({src_dialect}→{tgt_dialect}): {e}"
            if self.on_error == "raise":
                raise ValueError(msg) from e
            logger.warning(f"{msg}; returning original SQL")
            return query

    def split(self, queries: str, dialect: Optional[str] = None) -> List[str]:
        """\
        Split multi-statement SQL into individual statements.

        Args:
            queries: SQL text (may contain semicolons).
            dialect: Parse dialect (default: *target_dialect*).

        Returns:
            List of individual SQL strings.
        """
        d = sa_dialect_to_sqlglot(dialect or self.target_dialect)
        if not queries.strip():
            return []
        parsed = self._sg.parse(queries, dialect=d)
        return [s.sql().strip() for s in parsed if s is not None]

    def prettify(self, query: str, dialect: Optional[str] = None, *, comments: bool = True, prefer_backticks: bool = True) -> str:
        """\
        Pretty-print a SQL query using SQLGlot.

        Args:
            query: SQL text.
            dialect: Parse/write dialect (default: *target_dialect*).
            comments: Keep comments in output (default: True).
            prefer_backticks: If True, prefer backticks for identifier quoting
                when the dialect supports backticks (default: True).
                Unsupported dialects continue to use their native quoting.

        Returns:
            Formatted SQL, or original (stripped) on failure.
        """
        d = sa_dialect_to_sqlglot(dialect or self.target_dialect)
        render_dialect = _resolve_render_dialect_runtime(dialect or self.target_dialect, prefer_backticks=prefer_backticks)
        try:
            result = self._sg.transpile(
                query,
                read=d,
                write=render_dialect,
                identify=True,
                pretty=True,
                comments=comments,
            )
            return result[0].strip()
        except Exception as e:
            logger.warning(f"Failed to prettify SQL: {e}")
            return query.strip()

    # -- internal: AST processing -------------------------------------------

    def _ast_process(
        self,
        query: str,
        params: Optional[Dict[str, Any]],
        src_sg: str,
        tgt_sg: str,
        need_transpile: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        """\
        AST-based processing for named parameters.

        1. Pre-process ``%s`` → ``?``
        2. Parse → normalize params → transpile → generate → post-process
        """
        exp = self._sg.exp
        params = params or {}

        # Pre-process %s → ? (SQLGlot cannot parse %s as placeholder)
        query_pre, ps_count = self._preprocess_percent_s(query)

        try:
            tree = self._sg.parse_one(query_pre, dialect=src_sg)
        except Exception as e:
            msg = f"SQLGlot parse failed (dialect={src_sg}): {e}"
            if self.on_error == "raise":
                raise ValueError(msg) from e
            logger.warning(f"{msg}; falling back to base normalizer")
            return super()._normalize_params(query, params)

        # Normalize all parameter nodes to Placeholder(this=name)
        out_params: Dict[str, Any] = {}
        self._normalize_ast_params(tree, exp, params, out_params)

        # Transpile
        if need_transpile:
            try:
                sql_out = tree.sql(dialect=tgt_sg)
            except Exception as e:
                msg = f"SQLGlot transpilation failed ({src_sg}→{tgt_sg}): {e}"
                if self.on_error == "raise":
                    raise ValueError(msg) from e
                logger.warning(f"{msg}; using source dialect")
                sql_out = tree.sql(dialect=src_sg)
        else:
            sql_out = tree.sql(dialect=tgt_sg)

        # Post-process: ensure :name format
        sql_out = self._postprocess_param_format(sql_out, out_params)

        return sql_out, out_params

    def _ast_process_positional(
        self,
        query: str,
        params: Union[List, Tuple],
        src_sg: str,
        tgt_sg: str,
        need_transpile: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        """\
        AST-based processing for positional parameters.

        Converts positional params to named ``param_0, param_1, …``
        dict, then delegates to ``_ast_process()``.
        """
        named = {f"param_{i}": v for i, v in enumerate(params)}

        exp = self._sg.exp

        # Pre-process %s → ?
        query_pre, _ = self._preprocess_percent_s(query)

        try:
            tree = self._sg.parse_one(query_pre, dialect=src_sg)
        except Exception as e:
            msg = f"SQLGlot parse failed (dialect={src_sg}): {e}"
            if self.on_error == "raise":
                raise ValueError(msg) from e
            logger.warning(f"{msg}; falling back to base normalizer")
            return super()._normalize_positional(query, params)

        # Walk AST — convert placeholders/parameters to named form
        counter = [0]
        out_params: Dict[str, Any] = {}

        for node in list(tree.find_all(exp.Placeholder)):
            if node.args.get("jdbc"):
                # ? → :param_N
                name = f"param_{counter[0]}"
                counter[0] += 1
            elif node.this:
                # Already named (shouldn't normally happen for positional)
                name = str(node.this)
            else:
                name = f"param_{counter[0]}"
                counter[0] += 1
            node.replace(exp.Placeholder(this=name))
            if name in named:
                out_params[name] = named[name]

        for node in list(tree.find_all(exp.Parameter)):
            child = node.this
            if isinstance(child, exp.Literal) and not child.is_string:
                # $1 → :param_0
                idx = int(child.this) - 1
                name = f"param_{idx}"
            elif isinstance(child, exp.Var):
                name = str(child.this)
            else:
                name = f"param_{counter[0]}"
                counter[0] += 1
            node.replace(exp.Placeholder(this=name))
            if name in named:
                out_params[name] = named[name]

        # Transpile / generate
        if need_transpile:
            try:
                sql_out = tree.sql(dialect=tgt_sg)
            except Exception as e:
                msg = f"SQLGlot transpilation failed ({src_sg}→{tgt_sg}): {e}"
                if self.on_error == "raise":
                    raise ValueError(msg) from e
                logger.warning(f"{msg}; using source dialect")
                sql_out = tree.sql(dialect=src_sg)
        else:
            sql_out = tree.sql(dialect=tgt_sg)

        sql_out = self._postprocess_param_format(sql_out, out_params)

        # Fill missing params
        if not out_params:
            out_params = named

        return sql_out, out_params

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _preprocess_percent_s(query: str) -> Tuple[str, int]:
        """\
        Replace ``%s`` with ``?`` before SQLGlot parsing.

        SQLGlot cannot parse ``%s`` (interprets ``%`` as modulo).
        ``?`` (JDBC placeholder) is well-supported.

        Returns:
            ``(preprocessed_query, count_of_replacements)``
        """
        count = 0

        def _repl(m):
            nonlocal count
            count += 1
            return "?"

        out = re.sub(r"(?<!')%s(?!')", _repl, query)
        return out, count

    def _normalize_ast_params(
        self,
        tree,
        exp,
        params: Dict[str, Any],
        out_params: Dict[str, Any],
    ) -> None:
        """\
        Walk the AST and normalize all parameter nodes to
        ``Placeholder(this=name)`` in-place.

        Populates *out_params* with matched values from *params*.
        """
        counter = [0]

        # Handle Placeholder nodes  (:name, ?, %(name)s)
        for node in list(tree.find_all(exp.Placeholder)):
            if node.args.get("jdbc"):
                # ? → :param_N  (unnamed positional)
                name = f"param_{counter[0]}"
                counter[0] += 1
            elif node.this:
                # :name or %(name)s — extract the name
                raw = node.this
                name = str(raw.this) if hasattr(raw, "this") else str(raw)
            else:
                name = f"param_{counter[0]}"
                counter[0] += 1

            node.replace(exp.Placeholder(this=name))
            if name in params:
                out_params[name] = params[name]

        # Handle Parameter nodes  ($1, $name)
        for node in list(tree.find_all(exp.Parameter)):
            child = node.this
            if isinstance(child, exp.Literal) and not child.is_string:
                # $1 → positional (1-indexed)
                idx = int(child.this) - 1
                name = f"param_{idx}"
            elif isinstance(child, exp.Var):
                # $name → named
                name = str(child.this)
            else:
                name = f"param_{counter[0]}"
                counter[0] += 1

            node.replace(exp.Placeholder(this=name))
            if name in params:
                out_params[name] = params[name]

        # If no AST params were found, pass params through
        if not out_params and params:
            out_params.update(params)

    @staticmethod
    def _postprocess_param_format(sql: str, params: Dict[str, Any]) -> str:
        """\
        Ensure the output SQL uses ``:name`` placeholders.

        Some SQLGlot dialects render named placeholders differently:

        * postgres: ``%(name)s``
        * duckdb: ``$name``

        This method normalizes them back to ``:name``.
        """
        # %(name)s → :name
        sql = _RE_PYFORMAT.sub(lambda m: f":{m.group(1)}", sql)

        # $name → :name  (only for known param names to avoid false positives)
        if "$" in sql and params:
            for name in params:
                sql = sql.replace(f"${name}", f":{name}")

        return sql


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_sql_processor(
    dialect: str,
    *,
    use_sqlglot: Optional[bool] = None,
    on_error: str = "warn",
) -> SQLProcessor:
    """\
    Create the appropriate SQL processor.

    Args:
        dialect: SQLAlchemy dialect name (e.g. ``"sqlite"``, ``"postgresql"``).
        use_sqlglot: ``True`` to require SQLGlot (raises if missing),
            ``False`` to use the base processor, ``None`` (default) to
            auto-detect.
        on_error: ``"warn"`` (default) or ``"raise"`` — how to handle
            transpilation / parse failures.

    Returns:
        ``SQLGlotProcessor`` if SQLGlot is available (or required),
        ``SQLProcessor`` otherwise.
    """
    if use_sqlglot is True:
        deps.require("sqlglot", "SQL transpilation")
        return SQLGlotProcessor(dialect, on_error=on_error)

    if use_sqlglot is False:
        return SQLProcessor(dialect, on_error=on_error)

    # Auto-detect
    if deps.check("sqlglot"):
        return SQLGlotProcessor(dialect, on_error=on_error)

    return SQLProcessor(dialect, on_error=on_error)
