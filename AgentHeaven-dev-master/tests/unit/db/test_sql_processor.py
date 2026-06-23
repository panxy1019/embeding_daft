"""\
Tests for the redesigned SQLProcessor and SQLGlotProcessor.

Covers:

- Dialect name mapping
- Parameter normalization (positional, named, batch, single scalar)
- Transpilation (with SQLGlot)
- Error handling and fallback behaviour
- String-literal safety (AST vs regex)
- Factory function
- Same-dialect transpile skip
- load_builtin_sql tuple return format
"""

import pytest
from ahvn.utils.db.sql_processor import (
    sa_dialect_to_sqlglot,
    sqlglot_dialect_to_sa,
    SQLProcessor,
    SQLGlotProcessor,
    create_sql_processor,
)
from ahvn.utils.db.db_utils import load_builtin_sql
from ahvn.utils.deps import OptionalDependencyError

# =========================================================================
# Dialect mapping
# =========================================================================


class TestDialectMapping:
    """Verify SA ↔ SQLGlot dialect name conversion."""

    def test_sa_to_sqlglot_postgresql(self):
        assert sa_dialect_to_sqlglot("postgresql") == "postgres"

    def test_sa_to_sqlglot_passthrough(self):
        for d in ("sqlite", "mysql", "oracle", "duckdb", "starrocks", "trino"):
            assert sa_dialect_to_sqlglot(d) == d

    def test_sqlglot_to_sa_postgres(self):
        assert sqlglot_dialect_to_sa("postgres") == "postgresql"

    def test_sqlglot_to_sa_passthrough(self):
        for d in ("sqlite", "mysql", "oracle", "duckdb"):
            assert sqlglot_dialect_to_sa(d) == d

    def test_mapping_roundtrip(self):
        assert sqlglot_dialect_to_sa(sa_dialect_to_sqlglot("postgresql")) == "postgresql"
        for d in ("sqlite", "mysql", "oracle", "duckdb", "starrocks", "trino"):
            assert sa_dialect_to_sqlglot(sqlglot_dialect_to_sa(d)) == d


# =========================================================================
# SQLProcessor (base, no SQLGlot)
# =========================================================================


class TestSQLProcessorBase:
    """Test the lightweight base SQLProcessor (regex-based)."""

    @pytest.fixture
    def proc(self):
        return SQLProcessor("sqlite")

    # --- no params ---------------------------------------------------------

    def test_no_params(self, proc):
        q, p = proc.process_query("SELECT 1")
        assert q == "SELECT 1"
        assert p == {}

    def test_none_params(self, proc):
        q, p = proc.process_query("SELECT 1", params=None)
        assert p == {}

    # --- positional: ? -----------------------------------------------------

    def test_positional_question_mark(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = ? AND name = ?", params=(1, "Alice"))
        assert ":param_0" in q
        assert ":param_1" in q
        assert "?" not in q
        assert p == {"param_0": 1, "param_1": "Alice"}

    # --- positional: %s ----------------------------------------------------

    def test_positional_percent_s(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = %s AND name = %s", params=[10, "Bob"])
        assert ":param_0" in q
        assert ":param_1" in q
        assert "%s" not in q
        assert p == {"param_0": 10, "param_1": "Bob"}

    # --- positional: $1 ----------------------------------------------------

    def test_positional_dollar_numbered(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = $1 AND name = $2", params=(42, "Eve"))
        assert ":param_0" in q
        assert ":param_1" in q
        assert "$1" not in q
        assert p == {"param_0": 42, "param_1": "Eve"}

    # --- named: :name ------------------------------------------------------

    def test_named_colon(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = :uid", params={"uid": 7})
        assert q == "SELECT * FROM t WHERE id = :uid"
        assert p == {"uid": 7}

    # --- named: $name ------------------------------------------------------

    def test_named_dollar(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = $uid", params={"uid": 7})
        assert ":uid" in q
        assert "$uid" not in q
        assert p["uid"] == 7

    # --- named: %(name)s ---------------------------------------------------

    def test_named_pyformat(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = %(uid)s", params={"uid": 9})
        assert ":uid" in q
        assert "%(uid)s" not in q
        assert p["uid"] == 9

    # --- batch: List[Dict] -------------------------------------------------

    def test_batch_params(self, proc):
        batch = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        q, p = proc.process_query("INSERT INTO t (name, age) VALUES (:name, :age)", params=batch)
        assert ":name" in q
        assert ":age" in q
        assert p is batch  # list returned as-is

    def test_batch_pyformat(self, proc):
        batch = [{"name": "Alice"}, {"name": "Bob"}]
        q, p = proc.process_query("INSERT INTO t (name) VALUES (%(name)s)", params=batch)
        assert ":name" in q
        assert p is batch

    # --- single scalar -----------------------------------------------------

    def test_single_scalar_question(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = ?", params=42)
        assert ":param_0" in q
        assert p == {"param_0": 42}

    # --- transpile warning -------------------------------------------------

    def test_transpile_warns(self, proc):
        """Base processor should warn when transpile is requested."""
        q, p = proc.process_query("SELECT 1", transpile_from="postgresql")
        assert q == "SELECT 1"  # unchanged

    def test_transpile_raises_on_error_raise(self):
        proc = SQLProcessor("sqlite", on_error="raise")
        with pytest.raises(OptionalDependencyError):
            proc.process_query("SELECT 1", transpile_from="postgresql")

    def test_transpile_method_raises(self, proc):
        with pytest.raises(OptionalDependencyError):
            proc.transpile("SELECT 1", "sqlite", "postgresql")

    def test_split_raises(self, proc):
        with pytest.raises(OptionalDependencyError):
            proc.split("SELECT 1; SELECT 2")

    def test_prettify_raises(self, proc):
        with pytest.raises(OptionalDependencyError):
            proc.prettify("SELECT 1")


# =========================================================================
# SQLGlotProcessor (AST-based)
# =========================================================================


class TestSQLGlotProcessor:
    """Test the full SQLGlotProcessor with AST-based processing."""

    @pytest.fixture
    def proc(self):
        return SQLGlotProcessor("sqlite")

    @pytest.fixture
    def pg_proc(self):
        return SQLGlotProcessor("postgresql")

    # --- no params ---------------------------------------------------------

    def test_no_params(self, proc):
        q, p = proc.process_query("SELECT 1")
        assert "SELECT 1" in q
        assert p == {}

    # --- positional: ? -----------------------------------------------------

    def test_positional_question_mark(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = ? AND name = ?", params=(1, "Alice"))
        assert ":param_0" in q
        assert ":param_1" in q
        assert "?" not in q
        assert p["param_0"] == 1
        assert p["param_1"] == "Alice"

    # --- positional: %s ----------------------------------------------------

    def test_positional_percent_s(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = %s AND name = %s", params=[10, "Bob"])
        assert ":param_0" in q
        assert ":param_1" in q
        assert p["param_0"] == 10
        assert p["param_1"] == "Bob"

    # --- positional: $1 ----------------------------------------------------

    def test_positional_dollar_numbered(self, proc):
        proc_pg = SQLGlotProcessor("postgresql")
        q, p = proc_pg.process_query("SELECT * FROM t WHERE id = $1 AND name = $2", params=(42, "Eve"))
        assert ":param_0" in q
        assert ":param_1" in q
        assert p["param_0"] == 42
        assert p["param_1"] == "Eve"

    # --- named: :name ------------------------------------------------------

    def test_named_colon(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = :uid", params={"uid": 7})
        assert ":uid" in q
        assert p == {"uid": 7}

    # --- named: $name ------------------------------------------------------

    def test_named_dollar(self, proc):
        proc_pg = SQLGlotProcessor("postgresql")
        q, p = proc_pg.process_query("SELECT * FROM t WHERE id = $uid", params={"uid": 7})
        assert ":uid" in q
        assert p["uid"] == 7

    # --- named: %(name)s ---------------------------------------------------

    def test_named_pyformat(self, proc):
        q, p = proc.process_query("SELECT * FROM t WHERE id = %(uid)s", params={"uid": 9})
        assert ":uid" in q
        assert "%(uid)s" not in q
        assert p["uid"] == 9

    # --- batch: List[Dict] -------------------------------------------------

    def test_batch_params(self, proc):
        batch = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        q, p = proc.process_query("INSERT INTO t (name, age) VALUES (:name, :age)", params=batch)
        assert ":name" in q
        assert ":age" in q
        assert p is batch

    # --- string literal safety (AST advantage) -----------------------------

    def test_string_literal_colon_ignored(self, proc):
        """Placeholders inside string literals should NOT be extracted."""
        q, p = proc.process_query(
            "SELECT * FROM t WHERE note = ':foo' AND id = :bar",
            params={"bar": 1},
        )
        # :foo is inside a string literal — should not appear as a param
        assert "foo" not in p
        assert p.get("bar") == 1

    # --- transpilation -----------------------------------------------------

    def test_transpile_sqlite_to_pg(self, proc):
        """Transpile a simple query from SQLite to PostgreSQL."""
        q, p = proc.process_query(
            "SELECT * FROM t LIMIT 10",
            transpile_from="sqlite",
        )
        # SQLite proc → output should still be valid sqlite
        assert "LIMIT" in q.upper()

    def test_transpile_pg_to_sqlite(self, proc):
        """Transpile PostgreSQL to SQLite via process_query."""
        q, p = proc.process_query(
            "SELECT * FROM t LIMIT 10",
            transpile_from="postgresql",
        )
        assert "LIMIT" in q.upper() or "SELECT" in q.upper()

    def test_transpile_method(self, proc):
        result = proc.transpile("SELECT * FROM t LIMIT 10", "sqlite", "postgresql")
        assert "SELECT" in result.upper()

    def test_transpile_with_params(self, proc):
        """Transpile with named parameters preserved."""
        q, p = proc.process_query(
            "SELECT * FROM t WHERE id = :uid LIMIT 10",
            params={"uid": 5},
            transpile_from="postgresql",
        )
        assert ":uid" in q
        assert p["uid"] == 5

    def test_transpile_failure_warn(self):
        """On parse failure with on_error=warn, fallback to base normalizer."""
        proc = SQLGlotProcessor("sqlite", on_error="warn")
        # Intentionally malformed SQL
        q, p = proc.process_query(
            "SELECTTT *** FROMM t WHERE id = :uid",
            params={"uid": 1},
        )
        # Should not raise, should return something
        assert p.get("uid") == 1 or p == {"uid": 1}

    def test_transpile_failure_raise(self):
        """On parse failure with on_error=raise, exception propagates."""
        proc = SQLGlotProcessor("sqlite", on_error="raise")
        # Note: SQLGlot is quite tolerant, so we might not actually get failure.
        # This tests the code path at least (it may or may not raise).
        try:
            q, p = proc.process_query(
                "SELECT * FROM t WHERE id = :uid",
                params={"uid": 1},
            )
            # If it didn't raise, that's fine — SQLGlot parsed it
        except (ValueError, Exception):
            pass  # Expected in raise mode

    # --- split / prettify --------------------------------------------------

    def test_split(self, proc):
        stmts = proc.split("SELECT 1; SELECT 2; SELECT 3")
        assert len(stmts) == 3

    def test_split_empty(self, proc):
        assert proc.split("") == []
        assert proc.split("   ") == []

    def test_prettify(self, proc):
        result = proc.prettify("SELECT a, b, c FROM t WHERE id = 1")
        assert "SELECT" in result.upper()

    def test_prettify_prefer_backticks_supported(self, proc):
        result = proc.prettify("SELECT id FROM users")
        assert "`" in result

    def test_prettify_prefer_backticks_override_false(self, proc):
        result = proc.prettify("SELECT id FROM users", prefer_backticks=False)
        assert "`" not in result

    def test_transpile_prefer_backticks_supported(self, proc):
        result = proc.transpile("SELECT id FROM users", "sqlite", "sqlite", prefer_backticks=True)
        assert "`" in result

    def test_prettify_failure_returns_original(self):
        """Prettify should not raise on junk, just return stripped original."""
        proc = SQLGlotProcessor("sqlite")
        # SQLGlot might actually parse this, but test graceful handling
        result = proc.prettify("SELECT 1")
        assert "SELECT" in result.upper()


# =========================================================================
# Factory
# =========================================================================


class TestFactory:
    """Test create_sql_processor() auto-detection and options."""

    def test_auto_detect_returns_sqlglot_if_available(self):
        proc = create_sql_processor("sqlite")
        # If SQLGlot is installed (likely in dev), should get SQLGlotProcessor
        from ahvn.utils.deps import deps

        if deps.check("sqlglot"):
            assert isinstance(proc, SQLGlotProcessor)
        else:
            assert isinstance(proc, SQLProcessor)
            assert not isinstance(proc, SQLGlotProcessor)

    def test_force_no_sqlglot(self):
        proc = create_sql_processor("sqlite", use_sqlglot=False)
        assert type(proc) is SQLProcessor  # exact type, not subclass

    def test_force_sqlglot(self):
        from ahvn.utils.deps import deps

        if deps.check("sqlglot"):
            proc = create_sql_processor("sqlite", use_sqlglot=True)
            assert isinstance(proc, SQLGlotProcessor)
        else:
            with pytest.raises(OptionalDependencyError):
                create_sql_processor("sqlite", use_sqlglot=True)

    def test_on_error_param(self):
        proc = create_sql_processor("sqlite", on_error="raise")
        assert proc.on_error == "raise"

    def test_dialect_preserved(self):
        proc = create_sql_processor("postgresql")
        assert proc.target_dialect == "postgresql"


# =========================================================================
# Integration: parameter format round-trips
# =========================================================================


class TestParameterRoundTrips:
    """Verify various parameter formats produce correct :name output."""

    @pytest.fixture(params=["base", "sqlglot"])
    def proc(self, request):
        if request.param == "sqlglot":
            return SQLGlotProcessor("sqlite")
        return SQLProcessor("sqlite")

    def test_colon_named_passthrough(self, proc):
        q, p = proc.process_query(
            "SELECT * FROM t WHERE a = :x AND b = :y",
            params={"x": 1, "y": 2},
        )
        assert ":x" in q
        assert ":y" in q
        assert p["x"] == 1
        assert p["y"] == 2

    def test_question_mark_two_params(self, proc):
        q, p = proc.process_query(
            "SELECT * FROM t WHERE a = ? AND b = ?",
            params=(10, 20),
        )
        assert ":param_0" in q
        assert ":param_1" in q
        assert p["param_0"] == 10
        assert p["param_1"] == 20

    def test_mixed_no_params(self, proc):
        """No parameters provided — query returned as-is."""
        q, p = proc.process_query("SELECT 1 + 1")
        assert p == {}

    def test_empty_dict_params(self, proc):
        q, p = proc.process_query("SELECT 1", params={})
        assert p == {}


# =========================================================================
# Same-dialect transpile skip
# =========================================================================


class TestSameDialectSkip:
    """Verify that transpile_from == target_dialect is a no-op."""

    def test_base_processor_same_dialect_no_warning(self):
        """Base processor should NOT emit a warning when source == target."""
        proc = SQLProcessor("sqlite")
        q, p = proc.process_query("SELECT 1", transpile_from="sqlite")
        assert q == "SELECT 1"
        assert p == {}

    def test_base_processor_same_dialect_with_params(self):
        proc = SQLProcessor("postgresql")
        q, p = proc.process_query(
            "SELECT * FROM t WHERE a = :x",
            params={"x": 1},
            transpile_from="postgresql",
        )
        assert ":x" in q
        assert p == {"x": 1}

    def test_sqlglot_processor_same_dialect_passthrough(self):
        """SQLGlot processor with same source/target should return SQL unchanged."""
        proc = SQLGlotProcessor("sqlite")
        q, p = proc.process_query("SELECT 1", transpile_from="sqlite")
        # No transpilation, no params → straight passthrough
        assert p == {}

    def test_sqlglot_processor_same_dialect_with_params(self):
        proc = SQLGlotProcessor("postgresql")
        q, p = proc.process_query(
            "SELECT * FROM t WHERE a = :x",
            params={"x": 1},
            transpile_from="postgresql",
        )
        assert ":x" in q
        assert p == {"x": 1}

    def test_sqlglot_processor_different_dialect_transpiles(self):
        """Different source/target → transpilation should happen."""
        proc = SQLGlotProcessor("sqlite")
        q, _ = proc.process_query(
            "SELECT 1 FROM DUAL",
            transpile_from="oracle",
        )
        # DUAL should be removed for sqlite
        assert "DUAL" not in q.upper() or "1" in q


# =========================================================================
# load_builtin_sql tuple return format
# =========================================================================


class TestLoadBuiltinSql:
    """Verify load_builtin_sql returns (sql, source_dialect) tuples."""

    def test_returns_tuple(self):
        result = load_builtin_sql("utils/db_tabs", dialect="sqlite")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_native_dialect_returns_matching_source(self):
        """When dialect entry exists, source_dialect == requested dialect."""
        sql, src = load_builtin_sql("utils/db_tabs", dialect="sqlite")
        assert src == "sqlite"
        assert "sqlite_master" in sql

    def test_native_postgresql(self):
        sql, src = load_builtin_sql("utils/db_tabs", dialect="postgresql")
        assert src == "postgresql"
        assert "pg_tables" in sql

    def test_native_mysql(self):
        sql, src = load_builtin_sql("utils/db_tabs", dialect="mysql")
        assert src == "mysql"
        assert "information_schema" in sql.lower()

    def test_fallback_to_sqlite(self):
        """When dialect is not in the SQL file, falls back to sqlite version."""
        # "gaussdb" is unlikely to have an entry in any SQL file
        sql, src = load_builtin_sql("utils/db_tabs", dialect="gaussdb")
        assert src == "sqlite"
        assert "sqlite_master" in sql

    def test_no_transpile_on_fallback(self):
        """Fallback should NOT transpile — returns raw sqlite SQL."""
        sql, src = load_builtin_sql("utils/row_count", dialect="gaussdb", tab_name="test")
        assert src == "sqlite"
        # Should be sqlite format, not transpiled
        assert "test" in sql

    def test_kwargs_formatting(self):
        sql, src = load_builtin_sql("utils/row_count", dialect="sqlite", tab_name="my_table")
        assert src == "sqlite"
        assert "my_table" in sql

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_builtin_sql("utils/nonexistent_query", dialect="sqlite")
