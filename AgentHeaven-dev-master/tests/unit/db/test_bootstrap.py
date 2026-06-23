"""
Test script for database bootstrapping (task 7).

Tests create_database + drop_database on all supported providers.
For file-based (sqlite, duckdb): creates in temp dir, cleans up after.
For server-based: connects to Docker instances, creates a test database, verifies, then drops.

Usage:
    python tests/unit/db/test_bootstrap.py
"""

import os
import sys
import tempfile
import uuid
from importlib.util import find_spec
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ahvn.utils.db import Database, DatabaseEngineRegistry, create_database, drop_database
from ahvn.utils.db.spec import DATABASE_CONFIG_ENGINE

# Short unique suffix for test database names
_UID = uuid.uuid4().hex[:8]


def _test_name(provider: str) -> str:
    return f"ahvn_bootstrap_test_{_UID}_{provider}"


@pytest.mark.parametrize(
    "provider,ext",
    [
        ("sqlite", "db"),
        pytest.param("duckdb", "duckdb", marks=pytest.mark.skipif(find_spec("duckdb_engine") is None, reason="duckdb_engine is not installed")),
    ],
)
def test_file_based(provider: str, ext: str):
    """Test bootstrapping for file-based databases (sqlite, duckdb)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "subdir", f"test.{ext}")
        print(f"  [{provider}] path={db_path}")

        # Database() is lazy — engine (and thus create_database) triggers on first access
        db = Database(provider=provider, database=db_path)

        # Accessing engine triggers create_database → parent dir should be created
        _ = db.engine
        assert os.path.isdir(os.path.dirname(db_path)), "Parent dir not created"

        # Verify we can use the database
        db.execute("CREATE TABLE test_tbl (id INTEGER PRIMARY KEY, val TEXT)", autocommit=True)
        db.execute("INSERT INTO test_tbl (id, val) VALUES (1, 'hello')", autocommit=True)
        result = db.execute("SELECT * FROM test_tbl", autocommit=True, readonly=True)
        rows = result.to_list()
        assert len(rows) == 1 and rows[0]["val"] == "hello", f"Unexpected result: {rows}"

        # drop_database should remove the file
        DatabaseEngineRegistry.dispose(db.spec)
        drop_database(db.spec)
        assert not os.path.exists(db_path), "Database file not removed"

        print(f"  [{provider}] OK (create dir + use + drop file)")


@pytest.mark.skip(reason="Requires external server infrastructure (Docker containers)")
def test_server_based(provider: str, password: str = None, extra_kw: dict = None):
    """Test bootstrapping for server-based databases."""
    db_name = _test_name(provider)
    kw = {"provider": provider, "database": db_name}
    if password:
        kw["password"] = password
    if extra_kw:
        kw.update(extra_kw)

    print(f"  [{provider}] database={db_name}")

    try:
        # Resolve spec (this does NOT connect yet)
        spec = DATABASE_CONFIG_ENGINE.resolve(kw)

        # 1. Create the database
        create_database(spec)
        print(f"  [{provider}] create_database: OK")

        # 2. Verify creation by connecting and running a simple query.
        #    For Oracle, the "database" is a user/schema, not a connectable service —
        #    verify via superuser connection instead of connecting as the new user.
        if spec.dialect == "oracle":
            import sqlalchemy as sa

            su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
            su_url = su_kw.pop("url")
            tmp_engine = sa.create_engine(su_url, **su_kw)
            try:
                with tmp_engine.connect() as conn:
                    res = conn.execute(
                        sa.text("SELECT COUNT(*) FROM all_users WHERE username = :name"),
                        {"name": db_name.upper()},
                    ).scalar()
                    assert res and res > 0, f"User {db_name} not found after CREATE USER"
            finally:
                tmp_engine.dispose()
            print(f"  [{provider}] verify user exists: OK")
        else:
            db = Database(**kw)
            try:
                result = db.execute("SELECT 1 AS test_col", autocommit=True, readonly=True)
                rows = result.to_list()
                assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
                print(f"  [{provider}] connect+query: OK")
            finally:
                DatabaseEngineRegistry.dispose(db.spec)

        # 3. Drop the database
        drop_database(spec)
        print(f"  [{provider}] drop_database: OK")

        # 4. Verify it's gone (try to create again — should succeed without error)
        create_database(spec)
        drop_database(spec)
        print(f"  [{provider}] re-create+re-drop: OK")

    except Exception as e:
        print(f"  [{provider}] FAILED: {e}")
        # Attempt cleanup
        try:
            spec = DATABASE_CONFIG_ENGINE.resolve(kw)
            drop_database(spec)
        except Exception:
            pass
        raise


def main():
    print(f"=== Database Bootstrapping Test (uid={_UID}) ===\n")
    results = {}

    # --- File-based ---
    for provider, ext in [("sqlite", "db"), ("duckdb", "duckdb")]:
        if provider == "duckdb" and find_spec("duckdb_engine") is None:
            results[provider] = "SKIP (duckdb_engine is not installed)"
            continue
        print(f"Testing {provider}...")
        try:
            test_file_based(provider, ext)
            results[provider] = "PASS"
        except Exception as e:
            results[provider] = f"FAIL: {e}"
            print(f"  [{provider}] FAIL: {e}")

    # --- Server-based ---
    # Each entry: (provider, password, extra_kw, description)
    # Credentials match Docker container environment variables.
    server_tests = [
        (
            "pg",
            "password",
            {
                "username": "rubik",
                "superuser": {"username": "rubik", "password": "password", "database": "postgres"},
            },
            "PostgreSQL",
        ),
        (
            "mysql",
            "password",
            {
                "superuser": {"password": "password"},
            },
            "MySQL",
        ),
        (
            "oracle",
            "password",
            {
                "superuser": {"password": "password", "params": {"service_name": "FREEPDB1"}},
            },
            "Oracle",
        ),
        ("starrocks", None, None, "StarRocks"),
        # ("trino", None, None, "Trino"),  # Not running — Trino uses catalogs, no CREATE DATABASE
        # ("gauss", "password", None, "GaussDB"),  # Not running
    ]

    for provider, pw, extra_kw, desc in server_tests:
        print(f"Testing {desc} ({provider})...")
        try:
            test_server_based(provider, password=pw, extra_kw=extra_kw)
            results[provider] = "PASS"
        except Exception as e:
            results[provider] = f"FAIL: {e}"
            import traceback

            traceback.print_exc()

    # --- Summary ---
    print("\n=== Results ===")
    for provider, status in results.items():
        icon = "✓" if status == "PASS" else ("⊘" if "SKIP" in status else "✗")
        print(f"  {icon} {provider}: {status}")

    failed = [k for k, v in results.items() if v != "PASS" and "SKIP" not in v]
    if failed:
        print(f"\n{len(failed)} provider(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} providers passed!")


if __name__ == "__main__":
    main()
