"""
Table Creation Demo — Database.create_tab() & Database.create_tabs()
====================================================================

This tutorial demonstrates how to create database tables using ahvn's
Database class.  Three approaches are shown:

1. **Schema-based** — ``sa.Table`` + ``sa.Column`` (lightweight, dynamic)
2. **ORM-based** — ``ExportableEntity`` subclass (strong typing, relationships)
3. **Batch creation** — ``create_tabs()`` with FK auto-ordering

Run:
    python tutorials/table_creation_demo.py
"""

import os
import tempfile

import sqlalchemy as sa
from ahvn.utils.db.base import Database, table_display
from ahvn.utils.db.types import ExportableEntity, get_base


# ---------------------------------------------------------------------------
# 1. Schema-Based Table Creation (sa.Table + sa.Column)
# ---------------------------------------------------------------------------
def demo_schema_based(db: Database):
    """Create a table from a plain sa.Table definition."""

    metadata = sa.MetaData()
    products = sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("price", sa.Float, default=0.0),
        sa.Column("in_stock", sa.Boolean, default=True),
    )

    db.create_tab(products)
    print("[1] Created 'products' table (schema-based)")
    print("    Columns:", db.tab_cols("products"))

    # Insert sample data
    db.execute(
        "INSERT INTO products (name, price, in_stock) VALUES (:n, :p, :s)",
        params={"n": "Widget", "p": 9.99, "s": True},
    )
    db.execute(
        "INSERT INTO products (name, price, in_stock) VALUES (:n, :p, :s)",
        params={"n": "Gadget", "p": 24.50, "s": False},
    )

    result = db.execute("SELECT * FROM products")
    print(table_display(result))
    print()


# ---------------------------------------------------------------------------
# 2. ORM-Based Table Creation (ExportableEntity subclass)
# ---------------------------------------------------------------------------
def demo_orm_based(db: Database):
    """Create a table from an ORM model class."""

    Base = get_base()

    class Author(ExportableEntity, Base):
        __tablename__ = "authors"
        id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
        name = sa.Column(sa.String(100), nullable=False)
        country = sa.Column(sa.String(60))

    db.create_tab(Author)
    print("[2] Created 'authors' table (ORM-based)")
    print("    Columns:", db.tab_cols("authors"))

    # Insert via raw SQL (ORM sessions are also possible, but this is simpler)
    db.execute(
        "INSERT INTO authors (name, country) VALUES (:n, :c)",
        params={"n": "Alice", "c": "US"},
    )
    db.execute(
        "INSERT INTO authors (name, country) VALUES (:n, :c)",
        params={"n": "Bob", "c": "UK"},
    )

    result = db.execute("SELECT * FROM authors")
    print(table_display(result))
    print()


# ---------------------------------------------------------------------------
# 3. Batch Creation with Foreign Keys (create_tabs)
# ---------------------------------------------------------------------------
def demo_batch_with_fks(db: Database):
    """Create multiple related tables in one call; FK ordering is automatic."""

    metadata = sa.MetaData()

    # Parent table
    departments = sa.Table(
        "departments",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )

    # Child table — references departments.id
    employees = sa.Table(
        "employees",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("dept_id", sa.Integer, sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("salary", sa.Float),
    )

    # Pass child BEFORE parent — create_tabs handles ordering automatically
    db.create_tabs([employees, departments])
    print("[3] Created 'departments' + 'employees' tables (batch, FK auto-ordered)")
    print("    departments columns:", db.tab_cols("departments"))
    print("    employees columns:", db.tab_cols("employees"))
    print("    employees FKs:", db.tab_fks("employees"))

    # Populate
    db.execute("INSERT INTO departments (name) VALUES (:n)", params={"n": "Engineering"})
    db.execute("INSERT INTO departments (name) VALUES (:n)", params={"n": "Marketing"})
    db.execute(
        "INSERT INTO employees (name, dept_id, salary) VALUES (:n, :d, :s)",
        params={"n": "Charlie", "d": 1, "s": 95000},
    )
    db.execute(
        "INSERT INTO employees (name, dept_id, salary) VALUES (:n, :d, :s)",
        params={"n": "Diana", "d": 2, "s": 88000},
    )

    print("\ndepartments:")
    print(table_display(db.execute("SELECT * FROM departments")))
    print("employees:")
    print(table_display(db.execute("SELECT * FROM employees")))


# ---------------------------------------------------------------------------
# 4. Idempotent Creation (checkfirst=True is the default)
# ---------------------------------------------------------------------------
def demo_checkfirst(db: Database):
    """Demonstrate that create_tab is safe to call repeatedly."""

    metadata = sa.MetaData()
    t = sa.Table(
        "idempotent_tab",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("val", sa.String(50)),
    )

    db.create_tab(t)
    db.execute("INSERT INTO idempotent_tab (id, val) VALUES (:i, :v)", params={"i": 1, "v": "first"})

    # Calling create_tab again does NOT fail or drop existing data
    db.create_tab(t)

    result = db.execute("SELECT * FROM idempotent_tab")
    assert result.to_list() == [{"id": 1, "val": "first"}]
    print("[4] create_tab(checkfirst=True) is idempotent — existing data preserved")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Use a temporary file-based SQLite database for the demo
    tmp = tempfile.mktemp(suffix=".db", prefix="ahvn_demo_")
    print(f"=== Table Creation Demo (database: {tmp}) ===\n")

    db = Database(provider="sqlite", database=tmp)

    try:
        demo_schema_based(db)
        demo_orm_based(db)
        demo_batch_with_fks(db)
        demo_checkfirst(db)
        print("All tables:", db.db_tabs())
        print("\n=== Demo complete ===")
    finally:
        # Clean up
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    main()
