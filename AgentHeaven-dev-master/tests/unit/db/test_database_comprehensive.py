"""
Comprehensive tests for the Database class covering gaps identified in DB_REFACTOR_GOAL.md task 9.

Focus areas:
- SQLResponse.row_count for DML operations (INSERT/UPDATE/DELETE)
- execute() safe=True mode (error response instead of raising)
- autocommit=False standalone usage raises DatabaseError
- Error classification and error message preservation
- orm_execute() with SQLAlchemy statements
- row_sample() and col_percentile() with real data
- browse() with pagination, filtering, and ordering
- drop_tab() / drop_view() / drop() / clear_tab()
- col_agg() / col_freqs() / col_freqk() / col_nonnulls() / col_lengths()
- clone() — independent instances with shared engine
- execute() with transpile parameter
- Concurrent access (thread safety)
- Rollback on error — exception info preserved
"""

import pytest
import threading
import time
import warnings
import sqlalchemy as sa
from ahvn.utils.db.base import Database, DatabaseError, SQLResponse
from ahvn.utils.db import DatabaseEngineRegistry

# ===========================================================================
# Helpers
# ===========================================================================


def _setup_scores_table(db: Database) -> None:
    """Create and populate a scores table for reuse across tests."""
    db.execute("CREATE TABLE scores (id INTEGER PRIMARY KEY, name TEXT, score REAL, category TEXT)")
    rows = [
        (1, "Alice", 95.0, "A"),
        (2, "Bob", 72.5, "B"),
        (3, "Charlie", 88.0, "A"),
        (4, "Diana", 60.0, "C"),
        (5, "Eve", 72.5, "B"),
        (6, "Frank", 50.0, "C"),
        (7, "Grace", 88.0, "A"),
        (8, "Hank", 45.0, "C"),
        (9, "Iris", 95.0, "A"),
        (10, "Jack", 72.5, "B"),
    ]
    for row_id, name, score, category in rows:
        db.execute(
            "INSERT INTO scores (id, name, score, category) VALUES (:id, :name, :score, :category)",
            params={"id": row_id, "name": name, "score": score, "category": category},
        )


def _install_tx_event_counter(engine):
    counters = {"commit": 0, "rollback": 0}

    def _on_commit(conn):
        counters["commit"] += 1

    def _on_rollback(conn):
        counters["rollback"] += 1

    sa.event.listen(engine, "commit", _on_commit)
    sa.event.listen(engine, "rollback", _on_rollback)
    return counters, _on_commit, _on_rollback


def _remove_tx_event_counter(engine, on_commit, on_rollback):
    sa.event.remove(engine, "commit", on_commit)
    sa.event.remove(engine, "rollback", on_rollback)


# ===========================================================================
# 1. SQLResponse row_count for DML
# ===========================================================================


class TestSQLResponseRowCountDML:
    """Verify row_count reflects rows affected by DML, not rows fetched."""

    def test_insert_row_count(self, minimal_database):
        minimal_database.execute("CREATE TABLE dml_test (id INTEGER PRIMARY KEY, val TEXT)")
        result = minimal_database.execute("INSERT INTO dml_test (id, val) VALUES (:id, :val)", params={"id": 1, "val": "hello"})
        # INSERT should report 1 affected row
        assert result.ok is True
        assert result.row_count == 1

    def test_update_row_count(self, minimal_database):
        minimal_database.execute("CREATE TABLE dml_upd (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(3):
            minimal_database.execute("INSERT INTO dml_upd (id, val) VALUES (:id, :val)", params={"id": i, "val": f"v{i}"})
        result = minimal_database.execute("UPDATE dml_upd SET val = 'updated'")
        assert result.ok is True
        assert result.row_count == 3

    def test_delete_row_count(self, minimal_database):
        minimal_database.execute("CREATE TABLE dml_del (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(5):
            minimal_database.execute("INSERT INTO dml_del (id, val) VALUES (:id, :val)", params={"id": i, "val": f"v{i}"})
        result = minimal_database.execute("DELETE FROM dml_del WHERE id < 3")
        assert result.ok is True
        assert result.row_count == 3

    def test_select_row_count_vs_len(self, minimal_database):
        """For SELECT, row_count and len(result) should be consistent."""
        minimal_database.execute("CREATE TABLE dml_sel (id INTEGER PRIMARY KEY)")
        for i in range(7):
            minimal_database.execute("INSERT INTO dml_sel VALUES (:id)", params={"id": i})
        result = minimal_database.execute("SELECT * FROM dml_sel", readonly=True)
        assert len(result) == 7
        # row_count may equal 7 or -1 depending on dialect; but rows should be fetchable
        assert len(result.rows) == 7


# ===========================================================================
# 2. execute() safe=True — error response instead of raising
# ===========================================================================


class TestExecuteSafeMode:
    """Verify execute(safe=True) returns SQLResponse(ok=False) without raising."""

    def test_safe_syntax_error(self, minimal_database):
        result = minimal_database.execute("THIS IS NOT SQL", safe=True)
        assert result.ok is False
        assert result.exception is not None
        assert result.error_message  # non-empty

    def test_safe_table_not_found(self, minimal_database):
        result = minimal_database.execute("SELECT * FROM nonexistent_table_xyz", safe=True, readonly=True)
        assert result.ok is False
        assert result.error_type  # classified

    def test_safe_preserves_query(self, minimal_database):
        bad_sql = "SELECT * FROM no_such_table_abc"
        result = minimal_database.execute(bad_sql, safe=True, readonly=True)
        assert result.ok is False
        assert result.query == bad_sql

    def test_safe_success_returns_ok(self, minimal_database):
        result = minimal_database.execute("SELECT 1 AS n", safe=True, readonly=True)
        assert result.ok is True
        assert result.scalar() == 1

    def test_safe_preserves_traceback(self, minimal_database):
        result = minimal_database.execute("SELECT * FROM ghost_table_zzz", safe=True, readonly=True)
        assert result.ok is False
        tb = result.traceback()
        # traceback may be None if exception has no __traceback__, but exception must exist
        assert result.exception is not None
        assert tb is None or isinstance(tb, str)


# ===========================================================================
# 3. autocommit=False standalone raises DatabaseError
# ===========================================================================


class TestAutocommitFalseBehavior:
    """autocommit=False outside a context manager must raise DatabaseError."""

    def test_standalone_autocommit_false_raises(self, minimal_database):
        with pytest.raises(DatabaseError, match="context manager"):
            minimal_database.execute("SELECT 1", autocommit=False, readonly=True)

    def test_context_manager_autocommit_false_no_raise(self, minimal_database):
        """Inside a context manager, autocommit=False is the implicit default — no error."""
        minimal_database.execute("CREATE TABLE ac_test (id INTEGER PRIMARY KEY)")
        with minimal_database as db:
            # autocommit=None (default) inside context manager — should not raise
            db.execute("INSERT INTO ac_test VALUES (:id)", params={"id": 1}, autocommit=False)
        # Committed via __exit__
        result = minimal_database.execute("SELECT COUNT(*) AS n FROM ac_test", readonly=True)
        assert result.scalar() == 1

    def test_context_manager_autocommit_true_commits_inline(self, minimal_database):
        """autocommit=True inside a context manager commits immediately."""
        minimal_database.execute("CREATE TABLE ac_inline (id INTEGER PRIMARY KEY)")
        with minimal_database as db:
            db.execute("INSERT INTO ac_inline VALUES (:id)", params={"id": 42}, autocommit=True)
        result = minimal_database.execute("SELECT id FROM ac_inline", readonly=True)
        assert result.first()["id"] == 42


# ===========================================================================
# 4. readonly inference / commit gating
# ===========================================================================


class TestReadonlyInferenceAndCommitGating:
    def test_standalone_select_skips_commit(self, minimal_database):
        counters, on_commit, on_rollback = _install_tx_event_counter(minimal_database.engine)
        try:
            result = minimal_database.orm_execute(sa.select(sa.literal(1).label("n")), readonly=True)
            assert result.scalar() == 1
        finally:
            _remove_tx_event_counter(minimal_database.engine, on_commit, on_rollback)

        assert counters["commit"] == 0
        assert counters["rollback"] >= 1

    def test_standalone_dml_noop_still_commits(self, minimal_database):
        minimal_database.execute("CREATE TABLE no_change (id INTEGER PRIMARY KEY, val TEXT)")
        minimal_database.execute("INSERT INTO no_change VALUES (1, 'a')")

        counters, on_commit, on_rollback = _install_tx_event_counter(minimal_database.engine)
        try:
            result = minimal_database.execute("UPDATE no_change SET val = 'a' WHERE id = 999")
            assert result.ok is True
            assert result.row_count == 0
        finally:
            _remove_tx_event_counter(minimal_database.engine, on_commit, on_rollback)

        assert counters["commit"] >= 1

    def test_standalone_dml_change_commits(self, minimal_database):
        minimal_database.execute("CREATE TABLE has_change (id INTEGER PRIMARY KEY, val TEXT)")

        counters, on_commit, on_rollback = _install_tx_event_counter(minimal_database.engine)
        try:
            result = minimal_database.execute("INSERT INTO has_change VALUES (1, 'x')")
            assert result.ok is True
            assert result.row_count == 1
        finally:
            _remove_tx_event_counter(minimal_database.engine, on_commit, on_rollback)

        assert counters["commit"] >= 1

    def test_with_db_call_defaults_readonly_but_dml_is_auto_detected(self, minimal_database):
        minimal_database.execute("CREATE TABLE ctx_ro (id INTEGER PRIMARY KEY)")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with minimal_database() as db:
                assert db._tx_requested_readonly is True
                db.execute("INSERT INTO ctx_ro VALUES (1)")

        result = minimal_database.execute("SELECT COUNT(*) AS n FROM ctx_ro", readonly=True)
        assert result.scalar() == 1

    def test_explicit_readonly_true_rolls_back_mutation(self, minimal_database):
        minimal_database.execute("CREATE TABLE force_ro (id INTEGER PRIMARY KEY)")
        minimal_database.execute("INSERT INTO force_ro VALUES (1)", readonly=True)
        result = minimal_database.execute("SELECT COUNT(*) AS n FROM force_ro", readonly=True)
        assert result.scalar() == 0


# ===========================================================================
# 5. Error classification and rollback preservation
# ===========================================================================


class TestErrorClassificationAndRollback:
    """Error type/message are preserved; rollback does not swallow errors."""

    def test_error_type_table_not_found(self, minimal_database):
        result = minimal_database.execute("SELECT * FROM no_table_123", safe=True, readonly=True)
        assert result.ok is False
        assert result.error_type == "TableNotFound"

    def test_error_type_syntax_error(self, minimal_database):
        result = minimal_database.execute("SELECTT 1", safe=True, readonly=True)
        assert result.ok is False
        # May be classified as SyntaxError or generic
        assert result.error_type  # any non-empty classification

    def test_integrity_error_unique_violation(self, minimal_database):
        minimal_database.execute("CREATE TABLE uniq_test (id INTEGER PRIMARY KEY)")
        minimal_database.execute("INSERT INTO uniq_test VALUES (1)")
        result = minimal_database.execute("INSERT INTO uniq_test VALUES (1)", safe=True)
        assert result.ok is False
        assert result.error_type == "UniqueViolation"

    def test_rollback_after_error_in_transaction(self, minimal_database):
        """After a failed statement in a transaction, the original data is intact."""
        minimal_database.execute("CREATE TABLE rb_test (id INTEGER PRIMARY KEY, val TEXT)")
        minimal_database.execute("INSERT INTO rb_test VALUES (1, 'original')")
        # Force a rollback by triggering an error inside a transaction
        try:
            with minimal_database as db:
                db.execute("UPDATE rb_test SET val = 'changed' WHERE id = 1")
                db.execute("INSERT INTO rb_test VALUES (1, 'dup')")  # PK violation → rollback
        except Exception:
            pass

        # Original data should still be intact after rollback
        result = minimal_database.execute("SELECT val FROM rb_test WHERE id = 1", readonly=True)
        assert result.first()["val"] == "original"

    def test_exception_chain_preserved(self, minimal_database):
        """The __cause__ chain on DatabaseError should wrap the original SA exception."""
        with pytest.raises(DatabaseError) as exc_info:
            minimal_database.execute("SELECT * FROM nonexistent_table_foobar", readonly=True)
        # The DatabaseError should have a __cause__ from SQLAlchemy
        assert exc_info.value.__cause__ is not None


# ===========================================================================
# 5. orm_execute() with SQLAlchemy statements
# ===========================================================================


class TestOrmExecute:
    """Test orm_execute with real SQLAlchemy SELECT/INSERT/UPDATE/DELETE."""

    def _create_and_populate(self, db: Database) -> sa.Table:
        db.execute("CREATE TABLE orm_tab (id INTEGER PRIMARY KEY, name TEXT, score REAL)")
        db.execute("INSERT INTO orm_tab VALUES (1, 'Alice', 90.0)")
        db.execute("INSERT INTO orm_tab VALUES (2, 'Bob', 75.5)")
        db.execute("INSERT INTO orm_tab VALUES (3, 'Carol', 85.0)")
        meta = sa.MetaData()
        return sa.Table("orm_tab", meta, autoload_with=db.engine)

    def test_orm_select(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.select(tbl).order_by(tbl.c.id)
        result = minimal_database.orm_execute(stmt, readonly=True)
        assert result.ok is True
        rows = result.to_list()
        assert len(rows) == 3
        assert rows[0]["name"] == "Alice"

    def test_orm_select_with_where(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.select(tbl).where(tbl.c.score > 80)
        result = minimal_database.orm_execute(stmt, readonly=True)
        assert result.ok is True
        names = {r["name"] for r in result.to_list()}
        assert names == {"Alice", "Carol"}

    def test_orm_insert(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.insert(tbl).values(id=4, name="Dave", score=60.0)
        result = minimal_database.orm_execute(stmt)
        assert result.ok is True
        assert result.row_count == 1

        count_result = minimal_database.execute("SELECT COUNT(*) AS n FROM orm_tab", readonly=True)
        assert count_result.scalar() == 4

    def test_orm_update(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.update(tbl).where(tbl.c.name == "Bob").values(score=80.0)
        result = minimal_database.orm_execute(stmt)
        assert result.ok is True
        assert result.row_count == 1

        check = minimal_database.execute("SELECT score FROM orm_tab WHERE name = 'Bob'", readonly=True)
        assert check.scalar() == 80.0

    def test_orm_delete(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.delete(tbl).where(tbl.c.score < 80)
        result = minimal_database.orm_execute(stmt)
        assert result.ok is True
        assert result.row_count == 1  # Only Bob (<80)

        count = minimal_database.execute("SELECT COUNT(*) AS n FROM orm_tab", readonly=True)
        assert count.scalar() == 2

    def test_orm_execute_non_clause_raises(self, minimal_database):
        """Passing a plain string to orm_execute should raise ValueError."""
        with pytest.raises(ValueError, match="ClauseElement"):
            minimal_database.orm_execute("SELECT 1")

    def test_orm_aggregate_select(self, minimal_database):
        tbl = self._create_and_populate(minimal_database)
        stmt = sa.select(sa.func.avg(tbl.c.score).label("avg_score"))
        result = minimal_database.orm_execute(stmt, readonly=True)
        assert result.ok is True
        avg = result.scalar()
        assert avg is not None
        assert abs(avg - (90.0 + 75.5 + 85.0) / 3) < 0.01


# ===========================================================================
# 6. row_sample() and col_percentile()
# ===========================================================================


class TestRowSampleAndColPercentile:
    """row_sample returns a subset; col_percentile returns correct statistics."""

    def test_row_sample_basic(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.row_sample("scores", n_sample=5)
        assert isinstance(result, SQLResponse)
        assert result.ok is True
        rows = result.to_list()
        assert isinstance(rows, list)
        # Should return at most n_sample rows (may be fewer if table is small)
        assert len(rows) <= 10

    def test_row_sample_returns_valid_schema(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.row_sample("scores", n_sample=10)
        assert result.ok is True
        if result.to_list():
            assert "id" in result.columns
            assert "score" in result.columns

    def test_row_sample_empty_table(self, minimal_database):
        minimal_database.execute("CREATE TABLE empty_sample (id INTEGER PRIMARY KEY)")
        result = minimal_database.row_sample("empty_sample", n_sample=10)
        assert result.ok is True
        assert result.to_list() == []

    def test_col_percentile_basic(self, minimal_database):
        _setup_scores_table(minimal_database)
        percentiles = minimal_database.col_percentile("scores", "score")
        assert isinstance(percentiles, dict)
        assert "p0" in percentiles
        assert "p100" in percentiles
        # p0 should be min (45.0), p100 should be max (95.0)
        assert percentiles["p0"] == pytest.approx(45.0)
        assert percentiles["p100"] == pytest.approx(95.0)

    def test_col_percentile_custom(self, minimal_database):
        _setup_scores_table(minimal_database)
        percentiles = minimal_database.col_percentile("scores", "score", percentiles=[50])
        assert "p50" in percentiles
        median = percentiles["p50"]
        assert 40.0 <= median <= 100.0

    def test_col_percentile_invalid_range(self, minimal_database):
        _setup_scores_table(minimal_database)
        # Out-of-range percentile should return empty dict (error caught internally)
        result = minimal_database.col_percentile("scores", "score", percentiles=[150])
        assert result == {}

    def test_col_percentile_empty_table(self, minimal_database):
        minimal_database.execute("CREATE TABLE empty_pct (n REAL)")
        result = minimal_database.col_percentile("empty_pct", "n", percentiles=[50])
        # Either empty dict or dict with None values
        assert isinstance(result, dict)


# ===========================================================================
# 7. browse() — pagination, filtering, ordering
# ===========================================================================


class TestBrowse:
    """Test browse() with limit/offset/orderby and column filters."""

    def test_browse_all(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores")
        assert result.ok is True
        assert len(result.to_list()) == 10

    def test_browse_limit(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", limit=3)
        assert result.ok is True
        assert len(result.to_list()) == 3

    def test_browse_offset(self, minimal_database):
        _setup_scores_table(minimal_database)
        all_rows = minimal_database.browse("scores", orderby="id").to_list()
        offset_rows = minimal_database.browse("scores", offset=3, orderby="id").to_list()
        assert len(offset_rows) == 7
        assert offset_rows[0]["id"] == all_rows[3]["id"]

    def test_browse_orderby_asc(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", orderby="score").to_list()
        scores = [r["score"] for r in result]
        assert scores == sorted(scores)

    def test_browse_orderby_desc(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", orderby="-score").to_list()
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_browse_filter_exact(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", category="A").to_list()
        assert len(result) == 4
        assert all(r["category"] == "A" for r in result)

    def test_browse_filter_in(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", category=["A", "B"]).to_list()
        assert len(result) == 7
        assert all(r["category"] in ("A", "B") for r in result)

    def test_browse_filter_and_limit(self, minimal_database):
        _setup_scores_table(minimal_database)
        result = minimal_database.browse("scores", limit=2, category="C").to_list()
        assert len(result) == 2
        assert all(r["category"] == "C" for r in result)

    def test_browse_invalid_column_raises(self, minimal_database):
        _setup_scores_table(minimal_database)
        with pytest.raises(Exception):
            minimal_database.browse("scores", nonexistent_col="x")


# ===========================================================================
# 8. drop_tab / drop_view / drop / clear_tab
# ===========================================================================


class TestDropAndClear:
    """Test table/view dropping and data clearing operations."""

    def test_drop_tab(self, minimal_database):
        minimal_database.execute("CREATE TABLE drop_me (id INTEGER PRIMARY KEY)")
        assert "drop_me" in minimal_database.db_tabs()
        minimal_database.drop_tab("drop_me")
        assert "drop_me" not in minimal_database.db_tabs()

    def test_drop_tab_removes_data(self, minimal_database):
        minimal_database.execute("CREATE TABLE drop_data (id INTEGER PRIMARY KEY)")
        minimal_database.execute("INSERT INTO drop_data VALUES (1)")
        minimal_database.drop_tab("drop_data")
        assert "drop_data" not in minimal_database.db_tabs()

    def test_drop_view(self, minimal_database):
        minimal_database.execute("CREATE TABLE src_for_view (id INTEGER PRIMARY KEY)")
        minimal_database.execute("CREATE VIEW my_view AS SELECT id FROM src_for_view")
        assert "my_view" in minimal_database.db_views()
        minimal_database.drop_view("my_view")
        assert "my_view" not in minimal_database.db_views()

    def test_drop_all(self, minimal_database):
        minimal_database.execute("CREATE TABLE d1 (id INTEGER PRIMARY KEY)")
        minimal_database.execute("CREATE TABLE d2 (id INTEGER PRIMARY KEY)")
        minimal_database.drop()
        assert minimal_database.db_tabs() == []

    def test_clear_tab(self, minimal_database):
        minimal_database.execute("CREATE TABLE clr_tab (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(5):
            minimal_database.execute("INSERT INTO clr_tab VALUES (:id, :val)", params={"id": i, "val": f"v{i}"})
        assert minimal_database.row_count("clr_tab") == 5
        minimal_database.clear_tab("clr_tab")
        assert minimal_database.row_count("clr_tab") == 0
        # Schema preserved
        assert "clr_tab" in minimal_database.db_tabs()

    def test_clear_db(self, minimal_database):
        minimal_database.execute("CREATE TABLE cb1 (id INTEGER PRIMARY KEY)")
        minimal_database.execute("CREATE TABLE cb2 (id INTEGER PRIMARY KEY)")
        minimal_database.execute("INSERT INTO cb1 VALUES (1)")
        minimal_database.execute("INSERT INTO cb2 VALUES (2)")
        minimal_database.clear()
        assert minimal_database.row_count("cb1") == 0
        assert minimal_database.row_count("cb2") == 0
        assert "cb1" in minimal_database.db_tabs()
        assert "cb2" in minimal_database.db_tabs()


# ===========================================================================
# 9. col_agg / col_freqs / col_freqk / col_nonnulls / col_lengths
# ===========================================================================


class TestColumnStatistics:
    """Test aggregation and statistical feature methods."""

    def test_col_agg_count(self, minimal_database):
        _setup_scores_table(minimal_database)
        count = minimal_database.col_agg("scores", "score", agg="COUNT")
        assert count == 10

    def test_col_agg_count_distinct(self, minimal_database):
        _setup_scores_table(minimal_database)
        # Distinct scores: 95.0, 72.5, 88.0, 60.0, 50.0, 45.0 = 6
        count = minimal_database.col_agg("scores", "score", agg="COUNT", distinct=True)
        assert count == 6

    def test_col_agg_sum(self, minimal_database):
        _setup_scores_table(minimal_database)
        total = minimal_database.col_agg("scores", "score", agg="SUM")
        assert total is not None
        expected = 95.0 + 72.5 + 88.0 + 60.0 + 72.5 + 50.0 + 88.0 + 45.0 + 95.0 + 72.5
        assert abs(total - expected) < 0.01

    def test_col_agg_max(self, minimal_database):
        _setup_scores_table(minimal_database)
        max_val = minimal_database.col_agg("scores", "score", agg="MAX")
        assert max_val == pytest.approx(95.0)

    def test_col_agg_min(self, minimal_database):
        _setup_scores_table(minimal_database)
        min_val = minimal_database.col_agg("scores", "score", agg="MIN")
        assert min_val == pytest.approx(45.0)

    def test_col_freqs(self, minimal_database):
        _setup_scores_table(minimal_database)
        freqs = minimal_database.col_freqs("scores", "category")
        assert isinstance(freqs, list)
        assert len(freqs) == 3  # A, B, C
        freq_dict = {r["col_enums"]: r["freq"] for r in freqs}
        assert freq_dict["A"] == 4
        assert freq_dict["B"] == 3
        assert freq_dict["C"] == 3

    def test_col_freqk_top2(self, minimal_database):
        _setup_scores_table(minimal_database)
        freqs = minimal_database.col_freqk("scores", "category", topk=2)
        assert isinstance(freqs, list)
        assert len(freqs) == 2
        # Top-2 by count: A(4), B(3) or C(3)
        top_val = freqs[0]["col_enums"]
        assert top_val == "A"

    def test_col_nonnulls(self, minimal_database):
        minimal_database.execute("CREATE TABLE nullable_tab (id INTEGER PRIMARY KEY, val TEXT)")
        minimal_database.execute("INSERT INTO nullable_tab VALUES (1, 'hello')")
        minimal_database.execute("INSERT INTO nullable_tab VALUES (2, NULL)")
        minimal_database.execute("INSERT INTO nullable_tab VALUES (3, 'world')")
        nonnulls = minimal_database.col_nonnulls("nullable_tab", "val")
        assert isinstance(nonnulls, list)
        assert len(nonnulls) == 2
        assert None not in nonnulls
        assert set(nonnulls) == {"hello", "world"}

    def test_col_lengths(self, minimal_database):
        minimal_database.execute("CREATE TABLE len_tab (id INTEGER PRIMARY KEY, word TEXT)")
        words = ["hi", "hello", "hey", "h"]
        for i, w in enumerate(words):
            minimal_database.execute("INSERT INTO len_tab VALUES (:id, :word)", params={"id": i, "word": w})
        lengths = minimal_database.col_lengths("len_tab", "word")
        assert isinstance(lengths, dict)
        # Should contain min, max, avg keys
        assert "min" in lengths or len(lengths) > 0
        if "min" in lengths and "max" in lengths:
            assert lengths["min"] == 1
            assert lengths["max"] == 5


# ===========================================================================
# 10. clone() — independent context, shared engine
# ===========================================================================


class TestClone:
    """clone() produces an independent instance sharing the same engine."""

    def test_clone_creates_independent_instance(self, minimal_database):
        clone = minimal_database.clone()
        assert clone is not minimal_database

    def test_clone_shares_engine(self, minimal_database):
        clone = minimal_database.clone()
        assert clone.engine is minimal_database.engine

    def test_clone_independent_transaction(self, minimal_database):
        minimal_database.execute("CREATE TABLE clone_test (id INTEGER PRIMARY KEY, val TEXT)")
        minimal_database.execute("INSERT INTO clone_test VALUES (1, 'original')")

        clone = minimal_database.clone()

        # Each can read the same data
        r1 = minimal_database.execute("SELECT val FROM clone_test WHERE id = 1", readonly=True)
        r2 = clone.execute("SELECT val FROM clone_test WHERE id = 1", readonly=True)
        assert r1.first()["val"] == r2.first()["val"] == "original"

    def test_clone_context_manager_independent(self, minimal_database):
        """Two context managers on clone() are independent."""
        minimal_database.execute("CREATE TABLE clone_cm (id INTEGER PRIMARY KEY)")

        clone = minimal_database.clone()
        with minimal_database as db1:
            db1.execute("INSERT INTO clone_cm VALUES (1)")
        with clone as db2:
            db2.execute("INSERT INTO clone_cm VALUES (2)")

        count = minimal_database.execute("SELECT COUNT(*) AS n FROM clone_cm", readonly=True).scalar()
        assert count == 2


# ===========================================================================
# 11. execute() with transpile parameter
# ===========================================================================


class TestTranspile:
    """Verify execute(transpile=...) translates SQL to the target dialect."""

    def test_same_dialect_skips_transpile(self, minimal_database):
        """Transpiling from sqlite to sqlite should be a no-op."""
        result = minimal_database.execute("SELECT 1 AS n", transpile="sqlite", readonly=True)
        assert result.ok is True
        assert result.scalar() == 1

    def test_transpile_postgres_to_sqlite(self, minimal_database):
        """A PostgreSQL-flavoured LIMIT query transpiled to SQLite should work."""
        minimal_database.execute("CREATE TABLE tp_test (id INTEGER PRIMARY KEY)")
        for i in range(5):
            minimal_database.execute("INSERT INTO tp_test VALUES (:id)", params={"id": i})
        # PostgreSQL uses the same LIMIT syntax as SQLite for basic queries
        result = minimal_database.execute("SELECT id FROM tp_test ORDER BY id LIMIT 3", transpile="postgresql", readonly=True)
        assert result.ok is True
        assert len(result.to_list()) == 3


# ===========================================================================
# 12. Concurrent access — thread safety
# ===========================================================================


class TestConcurrentAccess:
    """Multiple threads reading/writing via standalone execute should be safe."""

    def test_concurrent_reads(self, minimal_database):
        minimal_database.execute("CREATE TABLE conc_read (id INTEGER PRIMARY KEY, val INTEGER)")
        for i in range(20):
            minimal_database.execute("INSERT INTO conc_read VALUES (:id, :v)", params={"id": i, "v": i * 2})

        results = []
        errors = []

        def read_worker():
            try:
                r = minimal_database.execute("SELECT SUM(val) AS s FROM conc_read", readonly=True)
                results.append(r.scalar())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == [], f"Concurrent read errors: {errors}"
        assert len(results) == 10
        # SUM(val) = sum(0,2,4,...,38) = 380
        assert all(s == 380 for s in results)

    def test_concurrent_writes_with_clones(self, minimal_database):
        """Multiple writers using clones should each commit successfully."""
        minimal_database.execute("CREATE TABLE conc_write (id INTEGER PRIMARY KEY, thread_id INTEGER)")

        errors = []

        def write_worker(thread_id: int):
            clone = minimal_database.clone()
            try:
                clone.execute(
                    "INSERT INTO conc_write VALUES (:id, :tid)",
                    params={"id": thread_id, "tid": thread_id},
                    readonly=False,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == [], f"Concurrent write errors: {errors}"
        count = minimal_database.execute("SELECT COUNT(*) AS n FROM conc_write", readonly=True).scalar()
        assert count == 10


# ===========================================================================
# 13. SQLResponse integration tests
# ===========================================================================


class TestSQLResponseIntegration:
    """Integration tests: SQLResponse produced by real execute() calls."""

    def test_elapsed_is_measured(self, minimal_database):
        result = minimal_database.execute("SELECT 1 AS n", readonly=True)
        assert result.ok is True
        assert result.elapsed is not None
        assert result.elapsed >= 0.0

    def test_query_preserved_on_success(self, minimal_database):
        sql = "SELECT 42 AS answer"
        result = minimal_database.execute(sql, readonly=True)
        assert result.query == sql

    def test_params_preserved_on_success(self, minimal_database):
        sql = "SELECT :x AS val"
        params = {"x": 99}
        result = minimal_database.execute(sql, params=params, readonly=True)
        assert result.params == params

    def test_first_on_result(self, minimal_database):
        result = minimal_database.execute("SELECT 7 AS n", readonly=True)
        row = result.first()
        assert row is not None
        assert row["n"] == 7

    def test_scalar_on_count(self, minimal_database):
        minimal_database.execute("CREATE TABLE scalar_test (id INTEGER PRIMARY KEY)")
        for i in range(4):
            minimal_database.execute("INSERT INTO scalar_test VALUES (:id)", params={"id": i})
        count = minimal_database.execute("SELECT COUNT(*) AS n FROM scalar_test", readonly=True).scalar()
        assert count == 4

    def test_to_list_tuple_format(self, minimal_database):
        minimal_database.execute("CREATE TABLE tl_test (a INTEGER, b TEXT)")
        minimal_database.execute("INSERT INTO tl_test VALUES (1, 'x')")
        result = minimal_database.execute("SELECT a, b FROM tl_test", readonly=True)
        rows = result.to_list(row_fmt="tuple")
        assert rows == [(1, "x")]

    def test_column_method(self, minimal_database):
        minimal_database.execute("CREATE TABLE col_test (id INTEGER, name TEXT)")
        for i, n in enumerate(["a", "b", "c"]):
            minimal_database.execute("INSERT INTO col_test VALUES (:id, :n)", params={"id": i, "n": n})
        result = minimal_database.execute("SELECT id, name FROM col_test ORDER BY id", readonly=True)
        names = result.column("name")
        assert names == ["a", "b", "c"]

    def test_indexing_cell(self, minimal_database):
        minimal_database.execute("CREATE TABLE idx_test (x INTEGER, y INTEGER)")
        minimal_database.execute("INSERT INTO idx_test VALUES (10, 20)")
        result = minimal_database.execute("SELECT x, y FROM idx_test", readonly=True)
        assert result[0, "x"] == 10
        assert result[0, 1] == 20

    def test_raise_on_error(self, minimal_database):
        result = minimal_database.execute("SELECT * FROM raise_table_zzz", safe=True, readonly=True)
        assert result.ok is False
        with pytest.raises(Exception):
            result.raise_on_error()


# ===========================================================================
# 14. engine property and DatabaseEngineRegistry
# ===========================================================================


class TestEngineAndRegistry:
    """Verify engine access and registry behaviour."""

    def test_engine_is_accessible(self, minimal_database):
        engine = minimal_database.engine
        assert engine is not None
        assert hasattr(engine, "connect")

    def test_engine_cached(self, minimal_database):
        """Two accesses to .engine return the same object."""
        e1 = minimal_database.engine
        e2 = minimal_database.engine
        assert e1 is e2

    def test_dialect_attribute(self, minimal_database):
        assert isinstance(minimal_database.dialect, str)
        assert len(minimal_database.dialect) > 0

    def test_spec_attribute(self, minimal_database):
        spec = minimal_database.spec
        assert spec is not None
        assert spec.dialect == minimal_database.dialect
