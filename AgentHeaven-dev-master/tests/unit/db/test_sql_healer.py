"""Tests for SQL healer and Database.heal_sql integration."""

import pytest

from ahvn.utils.deps import deps
from ahvn.utils.db.sql_healer import SQLHealer, resolve_sql_healing_options
from ahvn.utils.db import sql_healer as sql_healer_module
from ahvn.utils.db.db_utils import prettify_sql, transpile_sql


def _users_schema():
    return {"users": ["id", "username", "user_name", "email"]}


def _require_sqlglot():
    if not deps.check("sqlglot"):
        pytest.skip("sqlglot is not installed")


def test_resolve_sql_healing_options_defaults():
    opts = resolve_sql_healing_options(None)
    assert opts["aggressiveness"] == "balanced"
    assert opts["prefer_backticks"] is True


def test_resolve_sql_healing_options_invalid_aggressiveness_fallback():
    opts = resolve_sql_healing_options({"aggressiveness": "ultra", "prefer_backticks": False})
    assert opts["aggressiveness"] == "balanced"
    assert opts["prefer_backticks"] is False


def test_heal_sql_repair_wrapped_and_escaped_query():
    _require_sqlglot()
    healer = SQLHealer("sqlite", schema_loader=_users_schema, config={"aggressiveness": "aggressive"})
    broken = "\"SELECT usrname FROM usres WHERE usrname = 'alice'\""
    healed = healer.heal(broken)
    assert "usres" not in healed.lower()
    assert "usrname" not in healed.lower()
    assert "users" in healed.lower()
    assert "username" in healed.lower()


def test_heal_sql_aggressiveness_controls_ambiguity():
    _require_sqlglot()
    query = "SELECT usrname FROM users"

    conservative = SQLHealer("sqlite", schema_loader=_users_schema, config={"aggressiveness": "conservative"})
    aggressive = SQLHealer("sqlite", schema_loader=_users_schema, config={"aggressiveness": "aggressive"})

    healed_conservative = conservative.heal(query)
    healed_aggressive = aggressive.heal(query)

    assert "usrname" in healed_conservative.lower()
    assert "usrname" not in healed_aggressive.lower()
    assert "username" in healed_aggressive.lower()


def test_heal_sql_prefer_backticks_supported_only():
    _require_sqlglot()

    def schema():
        return {"users": ["id"]}

    mysql_healer = SQLHealer("mysql", schema_loader=schema, config={"aggressiveness": "off", "prefer_backticks": True})
    sqlite_healer = SQLHealer("sqlite", schema_loader=schema, config={"aggressiveness": "off", "prefer_backticks": True})
    postgres_healer = SQLHealer("postgresql", schema_loader=schema, config={"aggressiveness": "off", "prefer_backticks": True})

    healed_mysql = mysql_healer.heal("select id from users")
    healed_sqlite = sqlite_healer.heal("select id from users")
    healed_postgres = postgres_healer.heal("select id from users")

    assert "`" in healed_mysql
    assert "`" in healed_sqlite
    assert "`" not in healed_postgres


def test_heal_sql_prefer_backticks_override():
    _require_sqlglot()
    healer = SQLHealer("sqlite", schema_loader=lambda: {"users": ["id"]}, config={"aggressiveness": "off", "prefer_backticks": False})
    with_backticks = healer.heal("select id from users", prefer_backticks=True)
    without_backticks = healer.heal("select id from users", prefer_backticks=False)
    assert "`" in with_backticks
    assert "`" not in without_backticks


def test_prettify_sql_prefer_backticks_supported_only():
    _require_sqlglot()
    sqlite_sql = prettify_sql("select id from users", dialect="sqlite")
    duckdb_sql = prettify_sql("select id from users", dialect="duckdb")
    postgres_sql = prettify_sql("select id from users", dialect="postgresql")
    assert "`" in sqlite_sql
    assert "`" not in duckdb_sql
    assert "`" not in postgres_sql


def test_prettify_sql_prefer_backticks_override_false():
    _require_sqlglot()
    sqlite_sql = prettify_sql("select id from users", dialect="sqlite", prefer_backticks=False)
    assert "`" not in sqlite_sql


def test_transpile_sql_prefer_backticks_supported_only():
    _require_sqlglot()
    sqlite_sql = transpile_sql("select id from users", src_dialect="sqlite", tgt_dialect="sqlite", prefer_backticks=True)
    postgres_sql = transpile_sql("select id from users", src_dialect="sqlite", tgt_dialect="postgresql", prefer_backticks=True)
    assert "`" in sqlite_sql
    assert "`" not in postgres_sql


def test_heal_sql_rapidfuzz_missing_uses_fallback(monkeypatch):
    _require_sqlglot()
    orig_check = sql_healer_module.deps.check

    def _fake_check(name: str) -> bool:
        if name == "rapidfuzz":
            return False
        return orig_check(name)

    monkeypatch.setattr(sql_healer_module.deps, "check", _fake_check)

    healer = SQLHealer("sqlite", schema_loader=_users_schema, config={"aggressiveness": "aggressive"})
    healed = healer.heal("SELECT usrname FROM usres")
    assert "usres" not in healed.lower()
    assert "usrname" not in healed.lower()


def test_heal_sql_missing_sqlglot_is_nonfatal(monkeypatch):
    orig_check = sql_healer_module.deps.check

    def _fake_check(name: str) -> bool:
        if name == "sqlglot":
            return False
        return orig_check(name)

    monkeypatch.setattr(sql_healer_module.deps, "check", _fake_check)

    healer = SQLHealer("sqlite", schema_loader=_users_schema, config={"aggressiveness": "aggressive"})
    out = healer.heal("  SELECT * FROM usres  ")
    assert out == "SELECT * FROM usres"


def test_database_heal_sql_executes_repaired_query(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT)")
    minimal_database.execute(
        "INSERT INTO users (id, username, email) VALUES (:id, :username, :email)",
        params={"id": 1, "username": "alice", "email": "alice@example.com"},
        readonly=False,
    )

    healed = minimal_database.heal_sql("\"SELECT usrname FROM usres WHERE usrname = 'alice'\"")
    result = minimal_database.execute(healed, safe=True, readonly=True)
    assert result.ok is True
    rows = result.to_list()
    assert len(rows) == 1
    assert rows[0]["username"] == "alice"


def test_database_execute_does_not_auto_heal(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    minimal_database.execute("INSERT INTO users (id, username) VALUES (:id, :username)", params={"id": 1, "username": "alice"})

    bad_sql = "SELECT usrname FROM usres"
    bad_result = minimal_database.execute(bad_sql, safe=True, readonly=True)
    assert bad_result.ok is False

    healed = minimal_database.heal_sql(bad_sql)
    good_result = minimal_database.execute(healed, safe=True, readonly=True)
    assert good_result.ok is True


def test_database_heal_sql_prefer_backticks_override(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    with_backticks = minimal_database.heal_sql("SELECT id FROM users", prefer_backticks=True)
    without_backticks = minimal_database.heal_sql("SELECT id FROM users", prefer_backticks=False)
    assert "`" in with_backticks
    assert "`" not in without_backticks


def test_database_heal_sql_external_schema_index_override(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")

    # Ensure external schema_index path is used (no live inspector call needed).
    def _fail_builder():
        raise AssertionError("schema builder should not be called when schema_index is provided")

    minimal_database._build_schema_index_for_healing = _fail_builder  # type: ignore[attr-defined]

    healed = minimal_database.heal_sql(
        "SELECT usrname FROM usres",
        schema_index={"users": ["id", "username"]},
    )
    assert "users" in healed.lower()
    assert "username" in healed.lower()
    assert "usres" not in healed.lower()
    assert "usrname" not in healed.lower()


def test_database_schema_index_cache_shared_across_instances(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")

    # Prime global schema-index cache for this connection key.
    healed_first = minimal_database.heal_sql("SELECT usrname FROM usres")
    assert "users" in healed_first.lower()

    cloned = minimal_database.clone()

    def _fail_builder():
        raise AssertionError("schema index should be served from registry cache")

    cloned._build_schema_index_for_healing = _fail_builder  # type: ignore[attr-defined]
    healed_second = cloned.heal_sql("SELECT usrname FROM usres")
    assert "users" in healed_second.lower()


def test_database_heal_sql_external_schema_index_tables_wrapper(minimal_database):
    _require_sqlglot()
    minimal_database.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    healed = minimal_database.heal_sql(
        "SELECT usrname FROM usres",
        schema_index={"tables": {"users": ["id", "username"]}},
    )
    assert "users" in healed.lower()
    assert "username" in healed.lower()
