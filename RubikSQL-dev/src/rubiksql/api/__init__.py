"""\
High-level Python API for RubikSQL.

This package provides a clean facade for common operations, making caching
and resource management invisible to the caller. All CLI commands have
equivalent Python API functions here.

Usage:
    from rubiksql.api import (
        list_dbs, add_db, remove_db, load_db,
        activate_db, deactivate_db, get_active_db,
        update_db_info, set_description, set_disabled,
        load_kb, build_kb, kb_status
    )

    # Database Management
    dbs = list_dbs()
    config = add_db(name="mydb", provider="sqlite", database="./data.db")
    db = load_db("mydb")

    # Active Database
    activate_db("mydb")
    active = get_active_db()
    deactivate_db()

    # Database Information
    db_info = update_db_info("mydb")
    set_description("mydb", "Production database")
    set_disabled("mydb", tab_id="sensitive_logs", disabled=True)

    # Knowledge Base
    for event in build_kb("mydb"):
        print(f"Building: {event['progress']*100:.0f}%")

    kb = load_kb("mydb")
    status = kb_status("mydb")

API Categories:
    - Database Management: list, add, remove, load databases
    - Active Database: activate/deactivate, get active database
    - Database Information: metadata, descriptions, enable/disable
    - Knowledge Base: build, load, query, manage knowledge bases
"""

# =============================================================================
# Database Management
# =============================================================================

from .database import (
    list_dbs,
    add_db,
    remove_db,
    load_db,
    get_db_config,
    db_exists,
    get_kb_path,
)


# =============================================================================
# Active Database Management
# =============================================================================

from .database import (
    get_active_db,
    activate_db,
    deactivate_db,
)


# =============================================================================
# Database Information Management
# =============================================================================

from .database import (
    init_db_info,
    load_db_info,
    update_db_info,
    set_description,
    set_disabled,
    set_datatype_anno,
    set_enum_index_enabled,
)


# =============================================================================
# Knowledge Base Management
# =============================================================================

from .knowledge import (
    load_kb,
    purge_kb,
    build_column,
    build_column_type,
    build_enum,
    build_table,
    build_database,
    build_database_desc,
    build_table_desc,
    build_column_desc,
    build_table_synonyms,
    build_column_synonyms,
    search_knowledge,
)


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    # Database Management
    "list_dbs",
    "add_db",
    "remove_db",
    "load_db",
    "get_db_config",
    "db_exists",
    "get_kb_path",
    # Active Database
    "get_active_db",
    "activate_db",
    "deactivate_db",
    # Database Information
    "init_db_info",
    "load_db_info",
    "update_db_info",
    "set_description",
    "set_disabled",
    "set_datatype_anno",
    "set_enum_index_enabled",
    # Knowledge Base Management
    "load_kb",
    "purge_kb",
    "build_column",
    "build_column_type",
    "build_enum",
    "build_table",
    "build_database",
    "build_database_desc",
    "build_table_desc",
    "build_column_desc",
    "build_table_synonyms",
    "build_column_synonyms",
    "search_knowledge",
]
