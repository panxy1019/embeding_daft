"""Tests for conservative SQL read-only detection and readonly execution paths."""

from __future__ import annotations

import time
import warnings
import builtins

import pytest
import sqlalchemy as sa

from ahvn.utils.db.base import Database
from ahvn.utils.db.db_utils import is_sql_readonly
import ahvn.utils.db.db_utils as db_utils
from ahvn.utils.db.sqlglot_runtime import get_sqlglot
from ahvn.utils.basic.deps_utils import OptionalDependencyError


class TestSqlReadonlyDetection:
    def test_sqla_select_is_readonly(self):
        stmt = sa.select(sa.literal(1).label("n"))
        assert is_sql_readonly(stmt, dialect="sqlite") is True

    def test_sqla_select_with_function_is_not_readonly(self):
        stmt = sa.select(sa.func.count(sa.literal(1)).label("n"))
        assert is_sql_readonly(stmt, dialect="sqlite") is False

    def test_sqla_insert_is_not_readonly(self):
        table = sa.table("t", sa.column("a"))
        stmt = sa.insert(table).values(a=1)
        assert is_sql_readonly(stmt, dialect="sqlite") is False

    def test_text_update_is_not_readonly(self):
        query = "UPDATE t SET a = 1"
        assert is_sql_readonly(sa.text(query), query_text=query, dialect="sqlite") is False

    def test_text_select_for_update_is_not_readonly(self):
        query = "SELECT * FROM t FOR UPDATE"
        assert is_sql_readonly(sa.text(query), query_text=query, dialect="postgresql") is False

    def test_text_select_is_readonly_when_sqlglot_available(self):
        try:
            get_sqlglot()
        except Exception:
            pytest.skip("sqlglot not available")
        query = "SELECT 1 AS n"
        assert is_sql_readonly(sa.text(query), query_text=query, dialect="sqlite") is True


class TestReadonlyBypassAndWarnings:
    def test_explicit_readonly_true_does_not_import_detector(self, minimal_database, monkeypatch):
        raw_import = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("db_utils") and ("is_sql_readonly" in (fromlist or ())):
                raise AssertionError("detector import should not run when readonly=True")
            return raw_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guard)
        result = minimal_database.execute("SELECT 1 AS n", readonly=True)
        assert result.scalar() == 1

    def test_explicit_readonly_false_does_not_import_detector(self, minimal_database, monkeypatch):
        raw_import = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("db_utils") and ("is_sql_readonly" in (fromlist or ())):
                raise AssertionError("detector import should not run when readonly=False")
            return raw_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guard)
        result = minimal_database.execute("SELECT 1 AS n", readonly=False)
        assert result.scalar() == 1

    def test_explicit_readonly_true_skips_detection(self, minimal_database, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("detection should not run when readonly=True")

        monkeypatch.setattr(db_utils, "is_sql_readonly", _boom)
        result = minimal_database.execute("SELECT 1 AS n", readonly=True)
        assert result.scalar() == 1

    def test_explicit_readonly_false_skips_detection(self, minimal_database, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("detection should not run when readonly=False")

        monkeypatch.setattr(db_utils, "is_sql_readonly", _boom)
        result = minimal_database.execute("SELECT 1 AS n", readonly=False)
        assert result.scalar() == 1

    def test_orm_explicit_readonly_true_skips_detection(self, minimal_database, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("detection should not run when orm readonly=True")

        monkeypatch.setattr(db_utils, "is_sql_readonly", _boom)
        stmt = sa.select(sa.literal(1).label("n"))
        result = minimal_database.orm_execute(stmt, readonly=True)
        assert result.scalar() == 1

    def test_orm_explicit_readonly_false_skips_detection(self, minimal_database, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("detection should not run when orm readonly=False")

        monkeypatch.setattr(db_utils, "is_sql_readonly", _boom)
        stmt = sa.select(sa.literal(1).label("n"))
        result = minimal_database.orm_execute(stmt, readonly=False)
        assert result.scalar() == 1

    def test_readonly_none_uses_detection(self, minimal_database, monkeypatch):
        calls = {"n": 0}

        def _spy(*args, **kwargs):
            calls["n"] += 1
            return True

        monkeypatch.setattr(db_utils, "is_sql_readonly", _spy)
        Database._READONLY_DEFAULT_WARNING_EMITTED = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = minimal_database.execute("SELECT 1 AS n")
        assert result.scalar() == 1
        assert calls["n"] == 1

    def test_readonly_none_warns_once(self, minimal_database):
        # Plain-text SELECT detection requires sqlglot; skip when it is not installed.
        try:
            get_sqlglot()
        except OptionalDependencyError:
            pytest.skip("sqlglot not available")
        Database._READONLY_DEFAULT_WARNING_EMITTED = False
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            minimal_database.execute("SELECT 1 AS n")
            minimal_database.execute("SELECT 2 AS n")
        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) == 1
        assert "readonly=False in a future release" in str(dep_warnings[0].message)

    def test_explicit_readonly_has_no_deprecation_warning(self, minimal_database):
        Database._READONLY_DEFAULT_WARNING_EMITTED = False
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            minimal_database.execute("SELECT 1 AS n", readonly=True)
            minimal_database.execute("SELECT 2 AS n", readonly=False)
        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert dep_warnings == []


class TestReadonlyDetectionPerformance:
    def test_sqla_detection_cost(self):
        stmt = sa.select(sa.literal(1).label("n"))
        n = 2000
        t0 = time.perf_counter()
        for _ in range(n):
            assert is_sql_readonly(stmt, dialect="sqlite") is True
        elapsed = time.perf_counter() - t0
        avg_us = elapsed * 1e6 / n
        # Conservative upper bound for CI variance.
        assert avg_us < 3000, f"avg SQLAlchemy deduction cost too high: {avg_us:.2f}us"

    def test_text_detection_cost_when_sqlglot_available(self):
        try:
            get_sqlglot()
        except Exception:
            pytest.skip("sqlglot not available")
        query = "SELECT 1 AS n"
        stmt = sa.text(query)
        n = 300
        t0 = time.perf_counter()
        for _ in range(n):
            assert is_sql_readonly(stmt, query_text=query, dialect="sqlite") is True
        elapsed = time.perf_counter() - t0
        avg_ms = elapsed * 1e3 / n
        # Parsing text is heavier than SQLAlchemy-typed statements.
        assert avg_ms < 20, f"avg text deduction cost too high: {avg_ms:.3f}ms"
