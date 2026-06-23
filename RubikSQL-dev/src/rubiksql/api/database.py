"""\
Database management API for RubikSQL.

This module provides functions for managing database connections.
"""

from typing import List, Optional, Any

from ahvn.utils.basic import pj
from ahvn.utils.db import Database

from rubiksql.db import RUBIK_DBM, DatabaseConfig, DatabaseInfo, TableInfo, ColumnInfo


def list_dbs() -> List[str]:
    """\
    List all registered database names.

    Equivalent to: rubiksql db list

    Returns:
        List of database names (strings) sorted alphabetically.
    """
    configs = RUBIK_DBM.list_dbs()
    return [cfg.name for cfg in configs]


def add_db(
    name: str,
    test: bool = False,
    **kwargs,
) -> DatabaseConfig:
    """\
    Add a new database connection.

    Equivalent to: rubiksql db add -n <name> -p <provider>

    Args:
        name: Display name for the database (used as folder name).
        provider: Database provider (sqlite, duckdb, pg, mysql, mssql).
        test: Whether to test the connection before saving.
        **kwargs: Connection parameters (database, host, port, username, password, etc.)

    Returns:
        The created DatabaseConfig.

    Raises:
        ValueError: If name already exists or parameters are invalid.
        ConnectionError: If test=True and connection fails.
    """
    return RUBIK_DBM.add_db(name=name, test=test, **kwargs)


def remove_db(db_id: str) -> bool:
    """\
    Remove a database configuration.

    Equivalent to: rubiksql db remove <db_id>

    Args:
        db_id: Database name/identifier.

    Returns:
        True if removed successfully.

    Raises:
        ValueError: If database not found.
    """
    return RUBIK_DBM.remove_db(db_id)


def load_db(db_id: str) -> Database:
    """\
    Get a Database connection for a registered database.

    Equivalent to: RUBIK_DBM.connect(db_id)

    Args:
        db_id: Database name/identifier.

    Returns:
        Database connection instance.

    Raises:
        ValueError: If database not found.
    """
    return RUBIK_DBM.connect(db_id)


def get_db_config(db_id: str) -> Optional[DatabaseConfig]:
    """\
    Get database configuration without connecting.

    Args:
        db_id: Database name/identifier.

    Returns:
        DatabaseConfig or None if not found.
    """
    return RUBIK_DBM.get_db(db_id)


def db_exists(db_id: str) -> bool:
    """\
    Check if a database exists.

    Args:
        db_id: Database name/identifier.

    Returns:
        True if database exists.
    """
    return RUBIK_DBM.db_exists(db_id)


def get_kb_path(db_id: str) -> str:
    """\
    Get the knowledge base path for a database.

    Args:
        db_id: Database name/identifier.

    Returns:
        Path to KB directory (may not exist yet).
    """
    db_dir = RUBIK_DBM._get_db_dir(db_id)
    return pj(db_dir, "kb")


# =========================================================================
# Database Info API
# =========================================================================
#
# Design Pattern: Hierarchical Scope Control
# -------------------------------------------
# All database info operations use a unified hierarchical pattern:
# - init_db_info(db_id) → Initialize entire database metadata
# - update_db_info(db_id) → Update entire database
# - update_db_info(db_id, tab_id="table1") → Update single table
# - update_db_info(db_id, tab_id="table1", col_id="col1") → Update single column
#
# Same pattern for enable/disable:
# - set_disabled(db_id, disabled=False) → Enable entire database
# - set_disabled(db_id, tab_id="table1", disabled=True) → Disable single table
# - set_disabled(db_id, tab_id="table1", col_id="col1", disabled=True) → Disable single column
#
# This pattern should be used for ALL future database operations.
# =========================================================================


def init_db_info(db_id: str, reset: bool = False) -> DatabaseInfo:
    """\
    Initialize complete database metadata.

    Equivalent to: rubiksql info -n <db_id> [-r]

    When reset=False (default), reuses cached values from info.yaml if they exist.
    When reset=True, forces re-extraction from database, clearing all user edits.

    Args:
        db_id: Database name/identifier.
        reset: Whether to force re-extraction, clearing all user edits (default: False).

    Returns:
        DatabaseInfo with complete metadata.

    Raises:
        ValueError: If database not found.
    """
    return RUBIK_DBM.initialize_db_info(db_id, reset=reset)


def load_db_info(db_id: str) -> Optional[DatabaseInfo]:
    """\
    Load database metadata from file.

    Args:
        db_id: Database name/identifier.

    Returns:
        DatabaseInfo or None if not initialized yet.
    """
    return RUBIK_DBM.load_db_info(db_id)


def update_db_info(db_id: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, reset: bool = False, progress: Optional[Any] = None) -> DatabaseInfo:
    """\
    Update database metadata with hierarchical scope control.

    Equivalent to:
        rubiksql db info update <db_id>
        rubiksql db info update <db_id> --table <tab_id>
        rubiksql db info update <db_id> --table <tab_id> --column <col_id>

    Scope behavior:
        - update_db_info(db_id) → Update entire database
        - update_db_info(db_id, tab_id="table1") → Update single table
        - update_db_info(db_id, tab_id="table1", col_id="col1") → Update single column

    Args:
        db_id: Database name/identifier.
        tab_id: Optional table ID to limit update to single table.
        col_id: Optional column ID to limit update to single column (requires tab_id).
        reset: Whether to force re-extraction, clearing all user edits (default: False).
        progress: Optional progress tracker class (for database-level updates).

    Returns:
        Updated DatabaseInfo.

    Raises:
        ValueError: If database/table/column not found, or col_id without tab_id.
    """
    return RUBIK_DBM.update_db_info(db_id, tab_id=tab_id, col_id=col_id, reset=reset, progress=progress)


def set_disabled(db_id: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, disabled: bool = True) -> None:
    """\
    Enable or disable database objects with hierarchical scope control.

    Scope behavior:
        - set_disabled(db_id, disabled=False) → Enable entire database
        - set_disabled(db_id, tab_id="table1", disabled=True) → Disable single table
        - set_disabled(db_id, tab_id="table1", col_id="col1", disabled=True) → Disable single column

    Args:
        db_id: Database name/identifier.
        tab_id: Optional table ID.
        col_id: Optional column ID (requires tab_id).
        disabled: True to disable, False to enable.

    Raises:
        ValueError: If database info not initialized or object not found.
    """
    RUBIK_DBM.set_disabled(db_id, tab_id=tab_id, col_id=col_id, disabled=disabled)


# =========================================================================
# Active Database Management
# =========================================================================


def get_active_db() -> Optional[str]:
    """\
    Get the currently activated database name.

    Returns:
        Database name or None if no database is activated.
    """
    return RUBIK_DBM.get_active_db()


def activate_db(db_id: str) -> str:
    """\
    Activate a database as the default.

    After activation, all CLI commands can omit the --name/-n argument
    and will use this database by default.

    Args:
        db_id: Database name/identifier to activate (case-insensitive).

    Returns:
        The actual database name with correct casing.

    Raises:
        ValueError: If database doesn't exist.
    """
    return RUBIK_DBM.activate(db_id)


def deactivate_db() -> None:
    """\
    Deactivate the current active database.

    After deactivation, all CLI commands will require the --name/-n argument.
    """
    RUBIK_DBM.deactivate()


# Backward compatibility aliases (deprecated, use *_db variants instead)
get_active_database = get_active_db
activate_database = activate_db
deactivate_database = deactivate_db


def set_description(db_id: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, description: str = "") -> None:
    """\
    Set description for database objects with hierarchical scope control.

    Scope behavior:
        - set_description(db_id, description="...") → Set database description
        - set_description(db_id, tab_id="table1", description="...") → Set table description
        - set_description(db_id, tab_id="table1", col_id="col1", description="...") → Set column description

    Args:
        db_id: Database name/identifier.
        tab_id: Optional table ID.
        col_id: Optional column ID (requires tab_id).
        description: Description text to set.

    Raises:
        ValueError: If database info not initialized or object not found.
    """
    RUBIK_DBM.set_description(db_id, tab_id=tab_id, col_id=col_id, description=description)


def set_datatype_anno(db_id: str, tab_id: str, col_id: str, datatype_anno: str) -> None:
    """\
    Set human-annotated datatype for a column.

    Equivalent to: rubiksql update type -n <db_id> -t <tab_id> -c <col_id> -d <datatype_anno>

    The datatype must be one of: LONGTEXT, DATETIME, IDENTIFIER, CATEGORICAL, INTEGER, FLOAT, TEXT, UNKNOWN, or null.
    Case-insensitive (will be converted to uppercase). Use "null" to clear the annotation.

    Args:
        db_id: Database name/identifier.
        tab_id: Table ID.
        col_id: Column ID.
        datatype_anno: Annotated datatype string to set, or "null"/"" to clear.

    Raises:
        ValueError: If database info not initialized, object not found, or invalid datatype.
    """
    RUBIK_DBM.set_datatype_anno(db_id, tab_id=tab_id, col_id=col_id, datatype_anno=datatype_anno)


def set_enum_index_enabled(db_id: str, tab_id: str, col_id: str, enum_index_enabled: bool) -> None:
    """\
    Set whether to index enums for a column.

    Equivalent to: rubiksql update enable_enum -n <db_id> -t <tab_id> -c <col_id>

    Args:
        db_id: Database name/identifier.
        tab_id: Table ID.
        col_id: Column ID.
        enum_index_enabled: True to enable enum indexing, False to disable.

    Raises:
        ValueError: If database info not initialized or object not found.
    """
    RUBIK_DBM.set_enum_index_enabled(db_id, tab_id=tab_id, col_id=col_id, enum_index_enabled=enum_index_enabled)
