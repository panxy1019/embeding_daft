"""
Comprehensive tests for the SQLResponse class.

Tests cover: constructors, properties, indexing (row, column, cell, subset),
to_list with column selection, convenience accessors, display, cloning,
query/params tracking, execution timing, and table_display integration.
"""

import pytest
from ahvn.utils.db import SQLResponse, table_display

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(rows=None, columns=None):
    """Build a success SQLResponse from raw data (no DB needed)."""
    resp = SQLResponse()
    resp._columns = columns or []
    resp._rows = rows or []
    resp._row_count = len(resp._rows)
    return resp


def _sample_response():
    """3-row, 3-column sample response for reuse."""
    return _make_response(
        columns=["id", "name", "score"],
        rows=[
            {"id": 1, "name": "Alice", "score": 90},
            {"id": 2, "name": "Bob", "score": 85},
            {"id": 3, "name": "Charlie", "score": 70},
        ],
    )


# ===========================================================================
# Constructor tests
# ===========================================================================


class TestSQLResponseConstructors:

    def test_empty(self):
        r = SQLResponse.empty()
        assert r.ok is True
        assert r.rows == []
        assert r.columns == []
        assert r.query is None
        assert r.params is None
        assert r.elapsed is None

    def test_empty_with_metadata(self):
        r = SQLResponse.empty(query="CREATE TABLE t(id INT)", params=None, elapsed=0.001)
        assert r.ok is True
        assert r.query == "CREATE TABLE t(id INT)"
        assert r.elapsed == pytest.approx(0.001)

    def test_from_error(self):
        exc = ValueError("boom")
        r = SQLResponse._from_error(exc, query="SELECT 1", params={"x": 1}, elapsed=0.05)
        assert r.ok is False
        assert r.exception is exc
        assert r.query == "SELECT 1"
        assert r.params == {"x": 1}
        assert r.elapsed == pytest.approx(0.05)
        assert r.error_message  # non-empty

    def test_from_error_raise_on_error(self):
        exc = RuntimeError("fail")
        r = SQLResponse._from_error(exc)
        with pytest.raises(RuntimeError, match="fail"):
            r.raise_on_error()


# ===========================================================================
# Properties & basic access
# ===========================================================================


class TestSQLResponseProperties:

    def test_columns_rows(self):
        r = _sample_response()
        assert r.columns == ["id", "name", "score"]
        assert len(r.rows) == 3

    def test_shape_and_size(self):
        r = _sample_response()
        assert r.shape == (3, 3)
        assert r.size == r.shape

    def test_row_count(self):
        r = _sample_response()
        assert r.row_count == 3

    def test_bool(self):
        assert bool(_sample_response()) is True
        assert bool(SQLResponse._from_error(Exception("x"))) is False

    def test_len(self):
        assert len(_sample_response()) == 3
        assert len(SQLResponse.empty()) == 0

    def test_iter(self):
        r = _sample_response()
        names = [row["name"] for row in r]
        assert names == ["Alice", "Bob", "Charlie"]


# ===========================================================================
# Indexing
# ===========================================================================


class TestSQLResponseIndexing:

    def test_row_by_int(self):
        r = _sample_response()
        assert r[0] == {"id": 1, "name": "Alice", "score": 90}
        assert r[-1]["name"] == "Charlie"

    def test_row_by_slice(self):
        r = _sample_response()
        subset = r[0:2]
        assert len(subset) == 2
        assert subset[1]["name"] == "Bob"

    def test_column_series_by_name(self):
        r = _sample_response()
        assert r["name"] == ["Alice", "Bob", "Charlie"]

    def test_column_series_via_method(self):
        r = _sample_response()
        assert r.column("score") == [90, 85, 70]
        assert r.column(0) == [1, 2, 3]  # by index

    def test_cell_by_row_col_name(self):
        r = _sample_response()
        assert r[1, "name"] == "Bob"
        assert r[0, "score"] == 90

    def test_cell_by_row_col_index(self):
        r = _sample_response()
        assert r[0, 0] == 1  # id
        assert r[2, 1] == "Charlie"  # name

    def test_slice_col(self):
        r = _sample_response()
        assert r[0:2, "name"] == ["Alice", "Bob"]

    def test_list_row_subset(self):
        r = _sample_response()
        subset = r[[0, 2]]
        assert len(subset) == 2
        assert subset[0]["name"] == "Alice"
        assert subset[1]["name"] == "Charlie"

    def test_list_column_subset(self):
        r = _sample_response()
        subset = r[["name", "score"]]
        assert len(subset) == 3
        assert set(subset[0].keys()) == {"name", "score"}
        assert subset[0]["name"] == "Alice"
        assert subset[0]["score"] == 90

    def test_list_empty(self):
        r = _sample_response()
        assert r[[]] == []

    def test_invalid_index(self):
        r = _sample_response()
        with pytest.raises(ValueError):
            r[1.5]

    def test_column_not_found(self):
        r = _sample_response()
        with pytest.raises(ValueError, match="Column 'nonexistent' not found"):
            r["nonexistent"]


# ===========================================================================
# to_list
# ===========================================================================


class TestSQLResponseToList:

    def test_to_list_dict(self):
        r = _sample_response()
        result = r.to_list("dict")
        assert len(result) == 3
        assert result[0] == {"id": 1, "name": "Alice", "score": 90}

    def test_to_list_tuple(self):
        r = _sample_response()
        result = r.to_list("tuple")
        assert result[0] == (1, "Alice", 90)

    def test_to_list_list(self):
        r = _sample_response()
        result = r.to_list("list")
        assert result[0] == [1, "Alice", 90]

    def test_to_list_with_columns_by_name(self):
        r = _sample_response()
        result = r.to_list(columns=["name", "score"])
        assert set(result[0].keys()) == {"name", "score"}
        assert result[0]["name"] == "Alice"

    def test_to_list_with_columns_by_index(self):
        r = _sample_response()
        result = r.to_list(columns=[0, 2])
        assert set(result[0].keys()) == {"id", "score"}

    def test_to_list_columns_tuple_format(self):
        r = _sample_response()
        result = r.to_list("tuple", columns=["name"])
        assert result[0] == ("Alice",)

    def test_to_list_invalid_column(self):
        r = _sample_response()
        with pytest.raises(ValueError, match="Column 'bad' not found"):
            r.to_list(columns=["bad"])

    def test_to_list_invalid_column_index(self):
        r = _sample_response()
        with pytest.raises(ValueError, match="out of range"):
            r.to_list(columns=[99])

    def test_to_list_invalid_row_fmt(self):
        r = _sample_response()
        with pytest.raises(Exception):
            r.to_list("csv")


# ===========================================================================
# Convenience accessors
# ===========================================================================


class TestSQLResponseAccessors:

    def test_fetchall(self):
        r = _sample_response()
        fa = r.fetchall()
        assert len(fa) == 3
        assert fa[0]["name"] == "Alice"

    def test_scalar(self):
        r = _make_response(columns=["cnt"], rows=[{"cnt": 42}])
        assert r.scalar() == 42

    def test_scalar_empty(self):
        r = SQLResponse.empty()
        assert r.scalar() is None

    def test_first(self):
        r = _sample_response()
        assert r.first() == {"id": 1, "name": "Alice", "score": 90}

    def test_first_empty(self):
        r = SQLResponse.empty()
        assert r.first() is None

    def test_clone_is_independent(self):
        r = _sample_response()
        c = r.clone()
        c._rows[0]["name"] = "MODIFIED"
        assert r._rows[0]["name"] == "Alice"  # original unchanged


# ===========================================================================
# Display & string
# ===========================================================================


class TestSQLResponseDisplay:

    def test_str_success_with_rows(self):
        r = _sample_response()
        s = str(r)
        # Should be table_display output — contains column headers and row data
        assert "id" in s
        assert "Alice" in s
        assert "3 rows in total" in s

    def test_str_empty_success(self):
        r = SQLResponse.empty()
        s = str(r)
        assert "OK" in s
        assert "0 rows" in s

    def test_str_error(self):
        r = SQLResponse._from_error(Exception("boom"), query="SELECT bad")
        s = str(r)
        assert "Error" in s
        assert "boom" in s

    def test_repr_success(self):
        r = _sample_response()
        assert "ok=True" in repr(r)
        assert "rows=3" in repr(r)

    def test_repr_error(self):
        r = SQLResponse._from_error(ValueError("x"))
        assert "ok=False" in repr(r)

    def test_to_str_success(self):
        r = _sample_response()
        assert "OK" in r.to_str()
        assert "3 rows" in r.to_str()

    def test_to_str_error_with_traceback(self):
        try:
            raise RuntimeError("trace_test")
        except RuntimeError as e:
            r = SQLResponse._from_error(e)
        s = r.to_str(include_traceback=True)
        assert "Traceback" in s
        assert "trace_test" in s


# ===========================================================================
# table_display integration
# ===========================================================================


class TestTableDisplayIntegration:

    def test_table_display_with_sqlresponse(self):
        r = _sample_response()
        out = table_display(r)
        assert "Alice" in out
        assert "Bob" in out
        assert "3 rows in total" in out

    def test_table_display_truncation(self):
        rows = [{"i": i} for i in range(200)]
        r = _make_response(columns=["i"], rows=rows)
        out = table_display(r, max_rows=10)
        assert "omitted" in out
        assert "200 rows in total" in out

    def test_table_display_markdown_style(self):
        r = _sample_response()
        out = table_display(r, style="MARKDOWN")
        assert "|" in out


# ===========================================================================
# Query & timing metadata
# ===========================================================================


class TestSQLResponseMetadata:

    def test_query_params_on_success(self, minimal_database):
        """execute() should track query and params on success responses."""
        result = minimal_database.execute("SELECT :val AS v", params={"val": 7}, readonly=True)
        assert result.ok
        assert result.query is not None
        assert "val" in result.query.lower() or ":val" in result.query

    def test_elapsed_on_success(self, minimal_database):
        """execute() should record elapsed time."""
        result = minimal_database.execute("SELECT 1 AS one", readonly=True)
        assert result.ok
        assert result.elapsed is not None
        assert result.elapsed >= 0

    def test_query_params_on_error(self, minimal_database):
        """Safe-mode error should track query and params."""
        result = minimal_database.execute("SELECT * FROM nonexistent_table_xyz", safe=True, readonly=True)
        assert not result.ok
        assert result.query is not None
        assert "nonexistent_table_xyz" in result.query

    def test_elapsed_on_ddl(self, minimal_database):
        """DDL (CREATE TABLE) should also record timing."""
        result = minimal_database.execute("CREATE TABLE timing_test (id INTEGER)")
        assert result.elapsed is not None
        assert result.elapsed >= 0


# ===========================================================================
# Deep indexing integration with real DB
# ===========================================================================


class TestSQLResponseIndexingIntegration:

    def test_indexing_after_execute(self, minimal_database):
        """Full indexing test against a real database."""
        minimal_database.execute("CREATE TABLE idx_test (id INTEGER PRIMARY KEY, name VARCHAR(50), score INTEGER)")
        minimal_database.execute("INSERT INTO idx_test VALUES (1, 'Alice', 90)")
        minimal_database.execute("INSERT INTO idx_test VALUES (2, 'Bob', 85)")
        minimal_database.execute("INSERT INTO idx_test VALUES (3, 'Charlie', 70)")

        result = minimal_database.execute("SELECT * FROM idx_test ORDER BY id", readonly=True)

        # Row
        assert result[0]["name"] == "Alice"
        # Column series
        assert result["name"] == ["Alice", "Bob", "Charlie"]
        # Cell
        assert result[1, "score"] == 85
        # Subset rows
        assert len(result[[0, 2]]) == 2
        # Subset columns
        sub = result[["name", "score"]]
        assert "id" not in sub[0]
        assert sub[0]["name"] == "Alice"
        # Shape
        assert result.shape == (3, 3)
        # Scalar
        count_result = minimal_database.execute("SELECT COUNT(*) AS cnt FROM idx_test", readonly=True)
        assert count_result.scalar() == 3
        # First
        assert result.first()["name"] == "Alice"
        # table_display via __str__
        s = str(result)
        assert "Alice" in s
        assert "3 rows in total" in s
