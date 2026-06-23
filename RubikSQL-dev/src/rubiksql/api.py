"""\
High-level Python API for RubikSQL.

This module provides a clean facade for common operations, making caching
and resource management invisible to the caller. All CLI commands have
equivalent Python API functions here.

Usage:
    from rubiksql.api import list_dbs, load_db, load_kb, build_kb

    # List databases
    dbs = list_dbs()

    # Load database connection
    db = load_db("mydb")

    # Load cached knowledge base
    klbase = load_kb("mydb")

    # Build knowledge base
    for event in build_kb("mydb", force=True):
        print(event)
"""

from typing import List, Optional, Dict, Any, Generator, Union
from datetime import datetime

from ahvn.utils.basic import pj, exists_dir, exists_file, load_json
from ahvn.utils.db import Database

from rubiksql.db import RUBIK_DBM, DatabaseConfig
from rubiksql.klbase import RubikSQLKLBase, RUBIK_KBM
from rubiksql.tools import RubikSQLToolkit
from rubiksql.utils.progress_utils import RubikSQLSilentProgress


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
