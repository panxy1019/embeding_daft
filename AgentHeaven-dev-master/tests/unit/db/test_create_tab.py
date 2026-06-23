"""
Tests for Database.create_tab() and Database.create_tabs() methods.

Covers schema-based (sa.Table) and ORM-based (ExportableEntity) table creation,
including foreign key relationships, checkfirst behavior, and error handling.
"""

import pytest
import sqlalchemy as sa
from ahvn.utils.db.base import Database
from ahvn.utils.db.types import ExportableEntity, get_base

# ---------------------------------------------------------------------------
# Helpers — fresh metadata/tables per test to avoid cross-test collisions
# ---------------------------------------------------------------------------


def _make_simple_table(name="simple_tab", metadata=None):
    """Create a simple sa.Table with a few columns."""
    metadata = metadata or sa.MetaData()
    return sa.Table(
        name,
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100)),
        sa.Column("value", sa.Integer),
    )


def _make_parent_child(metadata=None):
    """Create parent + child tables with FK relationship."""
    metadata = metadata or sa.MetaData()
    parent = sa.Table(
        "ct_parent",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(50)),
    )
    child = sa.Table(
        "ct_child",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("ct_parent.id")),
        sa.Column("data", sa.String(100)),
    )
    return parent, child


# ---------------------------------------------------------------------------
# ORM model fixtures
# ---------------------------------------------------------------------------

Base = get_base()


class _OrmSimple(ExportableEntity):
    __tablename__ = "orm_simple_tab"
    __table_args__ = {"extend_existing": True}
    id = sa.Column(sa.Integer, primary_key=True)
    title = sa.Column(sa.String(200))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateTab:
    """Test Database.create_tab() method."""

    def test_create_tab_schema(self, minimal_database):
        """Create table from sa.Table object."""
        tbl = _make_simple_table("ct_schema_1")
        minimal_database.create_tab(tbl)
        assert "ct_schema_1" in minimal_database.db_tabs()

    def test_create_tab_orm(self, minimal_database):
        """Create table from ExportableEntity subclass."""
        minimal_database.create_tab(_OrmSimple)
        assert "orm_simple_tab" in minimal_database.db_tabs()

    def test_create_tab_checkfirst_true(self, minimal_database):
        """Creating the same table twice with checkfirst=True should not error."""
        tbl = _make_simple_table("ct_check_1")
        minimal_database.create_tab(tbl)
        minimal_database.create_tab(tbl, checkfirst=True)  # no error
        assert "ct_check_1" in minimal_database.db_tabs()

    def test_create_tab_invalid_type(self, minimal_database):
        """Passing a non-table type should raise TypeError."""
        with pytest.raises(TypeError):
            minimal_database.create_tab("not_a_table")

    def test_create_tab_invalid_dict(self, minimal_database):
        """Passing a dict should raise TypeError."""
        with pytest.raises(TypeError):
            minimal_database.create_tab({"name": "bad"})

    def test_create_tab_data_round_trip(self, minimal_database):
        """Verify table created via create_tab is usable for CRUD."""
        tbl = _make_simple_table("ct_crud_1")
        minimal_database.create_tab(tbl)

        # Insert
        minimal_database.execute("INSERT INTO ct_crud_1 (id, name, value) VALUES (:id, :name, :value)", params={"id": 1, "name": "Alice", "value": 42})

        # Select
        result = minimal_database.execute("SELECT * FROM ct_crud_1 WHERE id = 1", readonly=True)
        rows = result.to_list()
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        assert rows[0]["value"] == 42

    def test_create_tab_columns_inspected(self, minimal_database):
        """Columns of created table should be inspectable."""
        tbl = _make_simple_table("ct_inspect_1")
        minimal_database.create_tab(tbl)
        cols = minimal_database.tab_cols("ct_inspect_1")
        assert "id" in cols
        assert "name" in cols
        assert "value" in cols


class TestCreateTabs:
    """Test Database.create_tabs() method (batch creation)."""

    def test_create_tabs_multiple(self, minimal_database):
        """Create multiple independent tables at once."""
        m = sa.MetaData()
        t1 = sa.Table("ct_batch_a", m, sa.Column("id", sa.Integer, primary_key=True))
        t2 = sa.Table("ct_batch_b", m, sa.Column("id", sa.Integer, primary_key=True))
        minimal_database.create_tabs([t1, t2])
        tabs = minimal_database.db_tabs()
        assert "ct_batch_a" in tabs
        assert "ct_batch_b" in tabs

    def test_create_tabs_with_fk_ordering(self, minimal_database):
        """FK dependency ordering: child references parent; order should not matter."""
        parent, child = _make_parent_child()
        # Pass child first — MetaData.create_all handles ordering
        minimal_database.create_tabs([child, parent])
        tabs = minimal_database.db_tabs()
        assert "ct_parent" in tabs
        assert "ct_child" in tabs

        # Verify FK is functional
        minimal_database.execute("INSERT INTO ct_parent (id, label) VALUES (:id, :label)", params={"id": 1, "label": "p1"})
        minimal_database.execute("INSERT INTO ct_child (id, parent_id, data) VALUES (:id, :pid, :data)", params={"id": 1, "pid": 1, "data": "c1"})
        result = minimal_database.execute("SELECT * FROM ct_child", readonly=True)
        assert len(result.to_list()) == 1

    def test_create_tabs_mixed_schema_orm(self, minimal_database):
        """Mix sa.Table and ORM classes in create_tabs."""
        schema_tbl = _make_simple_table("ct_mixed_schema")
        minimal_database.create_tabs([schema_tbl, _OrmSimple])
        tabs = minimal_database.db_tabs()
        assert "ct_mixed_schema" in tabs
        assert "orm_simple_tab" in tabs

    def test_create_tabs_empty_list(self, minimal_database):
        """Creating with empty list should be a no-op."""
        before = set(minimal_database.db_tabs())
        minimal_database.create_tabs([])
        after = set(minimal_database.db_tabs())
        assert before == after

    def test_create_tabs_checkfirst(self, minimal_database):
        """Re-creating existing tables with checkfirst=True is safe."""
        m = sa.MetaData()
        t1 = sa.Table("ct_recheck", m, sa.Column("id", sa.Integer, primary_key=True))
        minimal_database.create_tabs([t1])
        minimal_database.create_tabs([t1], checkfirst=True)  # should not raise

    def test_create_tabs_different_metadata(self, minimal_database):
        """Tables from different MetaData objects should all be created."""
        m1 = sa.MetaData()
        m2 = sa.MetaData()
        t1 = sa.Table("ct_meta_1", m1, sa.Column("id", sa.Integer, primary_key=True))
        t2 = sa.Table("ct_meta_2", m2, sa.Column("id", sa.Integer, primary_key=True))
        minimal_database.create_tabs([t1, t2])
        tabs = minimal_database.db_tabs()
        assert "ct_meta_1" in tabs
        assert "ct_meta_2" in tabs


class TestCreateTabDropRoundtrip:
    """Test create → verify → drop → re-create cycle."""

    def test_create_drop_recreate(self, minimal_database):
        """Full lifecycle: create → insert → drop → re-create → insert."""
        tbl = _make_simple_table("ct_lifecycle")
        minimal_database.create_tab(tbl)
        minimal_database.execute(
            "INSERT INTO ct_lifecycle (id, name, value) VALUES (:id, :name, :value)",
            params={"id": 1, "name": "first", "value": 10},
        )
        assert minimal_database.row_count("ct_lifecycle") == 1

        # Drop
        minimal_database.drop_tab("ct_lifecycle")
        assert "ct_lifecycle" not in minimal_database.db_tabs()

        # Re-create (need fresh metadata since old one may be stale)
        tbl2 = _make_simple_table("ct_lifecycle")
        minimal_database.create_tab(tbl2)
        assert "ct_lifecycle" in minimal_database.db_tabs()
        assert minimal_database.row_count("ct_lifecycle") == 0

        # Insert again
        minimal_database.execute(
            "INSERT INTO ct_lifecycle (id, name, value) VALUES (:id, :name, :value)",
            params={"id": 2, "name": "second", "value": 20},
        )
        assert minimal_database.row_count("ct_lifecycle") == 1
