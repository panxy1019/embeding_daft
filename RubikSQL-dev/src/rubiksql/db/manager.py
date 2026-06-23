"""\
Database configuration manager for RubikSQL.

Manages database connections stored in folders with separate info.yaml and connection.yaml.
"""

__all__ = ["DatabaseConfig", "DatabaseManager", "RUBIK_DBM"]

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime

from ahvn.utils.basic import (
    pj,
    load_yaml,
    save_yaml,
    touch_dir,
    exists_file,
    exists_dir,
    delete_dir,
    list_dirs,
)
from ahvn.utils.db import Database
from ahvn.utils.basic.progress_utils import NoProgress

from rubiksql.utils.config_utils import RUBIK_CM, rpj
from rubiksql.db.info import DatabaseInfo, TableInfo, ColumnInfo
from rubiksql.utils.progress_utils import RubikSQLRichProgress


# Supported database types and their ahvn provider mapping
DB_TYPE_TO_PROVIDER = {
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "postgresql": "pg",
    "pg": "pg",
    "mysql": "mysql",
    "mssql": "mssql",
}

# File-based database types (use database path)
FILE_BASED_TYPES = {"sqlite", "duckdb"}

# Server-based database types (use host/port/user/password)
SERVER_BASED_TYPES = {"postgresql", "pg", "mysql", "mssql"}


@dataclass
class DatabaseConfig:
    """Configuration for a database connection."""

    name: str
    created_at: Optional[str] = None
    connection: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().astimezone().isoformat()

    def get_info_dict(self) -> Dict[str, Any]:
        """Get info dict for info.yaml."""
        return {
            "name": self.name,
            "created_at": self.created_at,
        }

    def get_connection_dict(self) -> Dict[str, Any]:
        """Get connection dict for connection.yaml (raw kwargs for Database)."""
        return dict(self.connection)

    @classmethod
    def from_dicts(cls, info: Dict[str, Any], connection: Dict[str, Any]) -> "DatabaseConfig":
        """Create from info and connection dictionaries."""
        return cls(
            name=info.get("name", ""),
            created_at=info.get("created_at"),
            connection=connection,
        )

    def get_connection_kwargs(self) -> Dict[str, Any]:
        """Get kwargs for ahvn.Database connection (same as connection dict)."""
        return dict(self.connection)

    def connect(self) -> Database:
        """Create and return a Database connection."""
        return Database(**self.get_connection_kwargs())


class DatabaseManager:
    """Manages database configurations stored in folders."""

    def __init__(self):
        """Initialize database manager."""
        pass

    @property
    def dbs_path(self) -> str:
        """Get the databases folder path."""
        path = rpj(RUBIK_CM.get("core.dbs_path", "~/.rubiksql/databases"), abs=True)
        touch_dir(path)
        return path

    def _get_db_dir(self, name: str) -> str:
        """Get the folder path for a database."""
        return rpj(self.dbs_path, name, abs=True)

    def _get_info_file(self, name: str) -> str:
        """Get the info.yaml file path for a database."""
        return rpj(self._get_db_dir(name), "info.yaml", abs=True)

    def _get_connection_file(self, name: str) -> str:
        """Get the connection.yaml file path for a database."""
        return rpj(self._get_db_dir(name), "connection.yaml", abs=True)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize database name for use as folder name."""
        invalid_chars = '/\\:*?"<>|'
        sanitized = name
        for char in invalid_chars:
            sanitized = sanitized.replace(char, "_")
        return sanitized.strip()

    def list_dbs(self) -> List[DatabaseConfig]:
        """List all registered databases."""
        if not exists_dir(self.dbs_path):
            return []

        configs = []
        for dirname in list_dirs(self.dbs_path):
            info_file = self._get_info_file(dirname)
            conn_file = self._get_connection_file(dirname)
            if exists_file(info_file) and exists_file(conn_file):
                try:
                    info = load_yaml(info_file) or {}
                    connection = load_yaml(conn_file) or {}
                    configs.append(DatabaseConfig.from_dicts(info, connection))
                except Exception:
                    continue
        return sorted(configs, key=lambda c: c.name)

    def get_db(self, name: str) -> Optional[DatabaseConfig]:
        """Get a database configuration by name."""
        info_file = self._get_info_file(name)
        conn_file = self._get_connection_file(name)
        if not exists_file(info_file) or not exists_file(conn_file):
            return None
        try:
            info = load_yaml(info_file) or {}
            connection = load_yaml(conn_file) or {}
            return DatabaseConfig.from_dicts(info, connection)
        except Exception:
            return None

    def db_exists(self, name: str) -> bool:
        """Check if a database with the given name exists."""
        return exists_dir(self._get_db_dir(name)) and exists_file(self._get_info_file(name))

    def add_db(
        self,
        name: str,
        test: bool = False,
        **kwargs,
    ) -> DatabaseConfig:
        """
        Add a new database configuration.

        Args:
            name: Display name for the database (used as folder name)
            test: Whether to test the connection before saving
            **kwargs: Connection parameters passed directly to ahvn.Database

        Returns:
            The created DatabaseConfig

        Raises:
            ValueError: If name already exists or parameters are invalid
            ConnectionError: If test=True and connection fails
        """
        # Validate name
        sanitized_name = self._sanitize_name(name)
        if not sanitized_name:
            raise ValueError("Database name cannot be empty")

        if self.db_exists(sanitized_name):
            raise ValueError(f"Database '{sanitized_name}' already exists")

        # Validate provider if specified
        provider = kwargs.get("provider", "").lower()
        if provider and provider not in DB_TYPE_TO_PROVIDER:
            valid = ", ".join(sorted(DB_TYPE_TO_PROVIDER.keys()))
            raise ValueError(f"Invalid provider '{provider}'. Valid options: {valid}")

        # Normalize provider name
        if provider:
            kwargs["provider"] = DB_TYPE_TO_PROVIDER[provider]

        # Normalize database path for file-based providers
        normalized_provider = kwargs.get("provider", provider)
        if normalized_provider in FILE_BASED_TYPES and "database" in kwargs:
            kwargs["database"] = pj(kwargs["database"], abs=True)

        # Create config with raw connection kwargs
        config = DatabaseConfig(
            name=sanitized_name,
            connection=kwargs,
        )

        # Test connection if requested
        if test:
            self.test_connection(config)

        # Create folder and save files
        db_dir = self._get_db_dir(sanitized_name)
        touch_dir(db_dir)
        touch_dir(pj(db_dir, "kb", abs=True))
        save_yaml(config.get_info_dict(), self._get_info_file(sanitized_name))
        save_yaml(config.get_connection_dict(), self._get_connection_file(sanitized_name))

        return config

    def remove_db(self, name: str) -> bool:
        """
        Remove a database configuration.

        Args:
            name: Database name to remove

        Returns:
            True if removed successfully

        Raises:
            ValueError: If database doesn't exist
        """
        db_dir = self._get_db_dir(name)
        if not exists_dir(db_dir):
            raise ValueError(f"Database '{name}' not found")

        delete_dir(db_dir)
        return True

    def update_db(self, name: str, **connection_kwargs) -> DatabaseConfig:
        """
        Update a database configuration's connection parameters.

        Args:
            name: Database name to update
            **connection_kwargs: Connection fields to update

        Returns:
            Updated DatabaseConfig

        Raises:
            ValueError: If database doesn't exist
        """
        config = self.get_db(name)
        if config is None:
            raise ValueError(f"Database '{name}' not found")

        # Update connection kwargs
        config.connection.update(connection_kwargs)

        # Normalize provider if updated
        provider = config.connection.get("provider", "").lower()
        if provider and provider in DB_TYPE_TO_PROVIDER:
            config.connection["provider"] = DB_TYPE_TO_PROVIDER[provider]

        # Normalize database path for file-based providers
        normalized_provider = config.connection.get("provider", provider)
        if normalized_provider in FILE_BASED_TYPES and "database" in config.connection:
            config.connection["database"] = pj(config.connection["database"], abs=True)

        # Save updated connection
        save_yaml(config.get_connection_dict(), self._get_connection_file(name))

        return config

    def test_connection(self, name_or_config) -> bool:
        """
        Test database connection.

        Args:
            name_or_config: Database name (str) or DatabaseConfig

        Returns:
            True if connection successful

        Raises:
            ValueError: If database not found
            ConnectionError: If connection fails
        """
        if isinstance(name_or_config, str):
            config = self.get_db(name_or_config)
            if config is None:
                raise ValueError(f"Database '{name_or_config}' not found")
        else:
            config = name_or_config

        try:
            db = config.connect()
            db.execute("SELECT 1")
            db.close_conn()
            return True
        except Exception as e:
            raise ConnectionError(f"Connection failed: {e}") from e

    def connect(self, name: str) -> Database:
        """
        Get a Database connection for the named database.

        Args:
            name: Database name

        Returns:
            Database instance

        Raises:
            ValueError: If database not found
        """
        config = self.get_db(name)
        if config is None:
            raise ValueError(f"Database '{name}' not found")
        return config.connect()

    # =========================================================================
    # Database Info Methods (merged into info.yaml)
    # =========================================================================
    def load_db_info(self, name: str) -> Optional[DatabaseInfo]:
        """
        Load database metadata from info.yaml (stats section).

        Args:
            name: Database name

        Returns:
            DatabaseInfo or None if stats section doesn't exist
        """
        info_file = self._get_info_file(name)
        if not exists_file(info_file):
            return None
        try:
            data = load_yaml(info_file) or {}
            # Only return DatabaseInfo if stats section exists
            if "stats" not in data:
                return None
            return DatabaseInfo.from_dict(data)
        except Exception:
            return None

    def save_db_info(self, name: str, db_info: DatabaseInfo) -> None:
        """
        Merge database metadata into info.yaml.

        This preserves existing name and created_at fields while merging stats.

        Args:
            name: Database name
            db_info: DatabaseInfo to merge

        Raises:
            ValueError: If database doesn't exist
        """
        if not self.db_exists(name):
            raise ValueError(f"Database '{name}' not found")

        info_file = self._get_info_file(name)

        # Load existing info.yaml to preserve name/created_at
        existing_data = {}
        if exists_file(info_file):
            existing_data = load_yaml(info_file) or {}

        # Merge with db_info data (db_info.to_dict() includes name/created_at/desc/disabled/stats)
        merged_data = db_info.to_dict()

        # Preserve original created_at if it existed
        if "created_at" in existing_data and existing_data["created_at"]:
            merged_data["created_at"] = existing_data["created_at"]

        save_yaml(merged_data, info_file)

    def initialize_db_info(self, name: str, reset: bool = False, progress: Optional[Any] = None) -> DatabaseInfo:
        """
        Initialize or update database metadata.

        This method ensures database info exists, with two modes:
        - reset=False (default): Reuse cached metadata (desc, disabled, datatype_anno, PKs/FKs, etc.)
        - reset=True: Force re-extraction from database, clearing ALL user edits

        Args:
            name: Database name
            reset: Whether to force re-extraction of all metadata (default: False)
            progress: Optional progress tracker (for database-level updates)

        Returns:
            DatabaseInfo with complete metadata

        Raises:
            ValueError: If database not found
        """
        # If reset=True, clear existing info to force re-extraction
        if reset:
            db_info = self.load_db_info(name)
            if db_info is not None:
                # Clear tables to force full re-scan
                db_info.tables = {}
                self.save_db_info(name, db_info)

        # Always use update_db_info to initialize/update
        # update_db_info will handle caching (skip cached columns when not reset) and show progress
        return self.update_db_info(name, tab_id=None, col_id=None, reset=reset, progress=progress)

    def update_db_info(
        self, name: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, reset: bool = False, progress: Optional[Any] = None
    ) -> DatabaseInfo:
        """
        Update database metadata with hierarchical scope control.

        This method provides a unified interface for updating metadata at different levels:
        - update_db_info(name) → Update entire database (all tables and columns)
        - update_db_info(name, tab_id="table1") → Update single table and all its columns
        - update_db_info(name, tab_id="table1", col_id="col1") → Update single column

        Args:
            name: Database name
            tab_id: Optional table ID to limit update to single table
            col_id: Optional column ID to limit update to single column (requires tab_id)
            reset: Whether to force re-extraction of all metadata from database (default: False)
            progress: Optional progress tracker (for database-level updates)

        Returns:
            Updated DatabaseInfo

        Raises:
            ValueError: If database/table/column not found, or col_id without tab_id
        """
        if col_id is not None and tab_id is None:
            raise ValueError("tab_id must be provided when col_id is specified")

        config = self.get_db(name)
        if config is None:
            raise ValueError(f"Database '{name}' not found")

        db = config.connect()

        # Load or create db_info
        db_info = self.load_db_info(name)
        if db_info is None:
            all_tabs = db.db_tabs()
            total_cols = sum(len(db.tab_cols(t)) for t in all_tabs)
            db_info = DatabaseInfo(
                name=name,
                created_at=config.created_at,
                desc=None,
                disabled=False,
                n_tabs=len(all_tabs),
                n_cols=total_cols,
                n_tabs_enabled=len(all_tabs),
                n_cols_enabled=total_cols,
                tables={},
            )

        # Scope 1: Update entire database (with progress tracking)
        if tab_id is None:
            all_tabs = db.db_tabs()
            total_cols = 0

            # Use progress tracking for full database update
            progress_cls = progress or RubikSQLRichProgress
            with progress_cls(desc=f"Updating {name}") as pbar:
                # First pass: count total columns for progress bar
                total_columns = 0
                for tab in all_tabs:
                    total_columns += len(db.tab_cols(tab))

                pbar.reset(total=total_columns)
                completed = 0

                for tab in all_tabs:
                    n_rows = db.row_count(tab)
                    table_cols = db.tab_cols(tab)
                    existing_tab = db_info.tables.get(tab)

                    # Check if table is fully cached (all columns exist and not disabled)
                    table_fully_cached = existing_tab is not None and not existing_tab.disabled and set(existing_tab.columns.keys()) == set(table_cols)

                    # Extract PKs/FKs from database if reset=True or they don't exist
                    if reset or existing_tab is None or not existing_tab.pks:
                        pks = db.tab_pks(tab)
                    else:
                        pks = existing_tab.pks

                    if reset or existing_tab is None or not existing_tab.fks:
                        fks = db.tab_fks(tab)
                    else:
                        fks = existing_tab.fks

                    columns = {}
                    for col in table_cols:
                        completed += 1

                        # Check if column is cached (exists and not disabled)
                        existing_col = existing_tab.columns.get(col) if existing_tab else None
                        col_cached = existing_tab is not None and not existing_tab.disabled and existing_col is not None and not existing_col.disabled

                        if col_cached and not reset:
                            # Column cached and not resetting, reuse it but update is_pk if needed
                            col_info = existing_col
                            # Update is_pk based on current pks (only for standalone PKs)
                            is_standalone_pk = len(pks) == 1 and col in pks
                            if col_info.is_pk != is_standalone_pk:
                                col_info = ColumnInfo(
                                    col_id=col_info.col_id,
                                    datatype_orig=col_info.datatype_orig,
                                    datatype_anno=col_info.datatype_anno,
                                    desc=col_info.desc,
                                    enum_index_enabled=col_info.enum_index_enabled,
                                    is_pk=is_standalone_pk,
                                    disabled=col_info.disabled,
                                )
                            columns[col] = col_info
                        else:
                            # Process new/disabled column OR reset=True (clear all user edits)
                            datatype_orig = str(db.col_type(tab, col))
                            # When reset=True, clear all user edits; otherwise preserve them
                            col_disabled = False if reset else (existing_col.disabled if existing_col else False)
                            # Set is_pk only for standalone PKs
                            is_standalone_pk = len(pks) == 1 and col in pks

                            columns[col] = ColumnInfo(
                                col_id=col,
                                datatype_orig=datatype_orig,
                                datatype_anno=None if reset else (existing_col.datatype_anno if existing_col else None),
                                desc=None if reset else (existing_col.desc if existing_col else None),
                                enum_index_enabled=None if reset else (existing_col.enum_index_enabled if existing_col else None),
                                is_pk=is_standalone_pk,
                                disabled=col_disabled,
                            )

                        # Update progress for each column (cached or new)
                        pbar.emit(
                            {
                                "message": f"{tab}.{col}",
                                "step": "db.info.update",
                                "step_current": completed,
                                "step_total": total_columns,
                                "progress": completed / total_columns if total_columns > 0 else 0,
                            }
                        )
                        pbar.update(1)

                        # Incremental save after each column with real-time enabled count updates
                        # When reset=True, clear all user edits; otherwise preserve them
                        tab_disabled = False if reset else (existing_tab.disabled if existing_tab else False)
                        n_cols_enabled = sum(1 for c in columns.values() if not c.disabled)

                        db_info.tables[tab] = TableInfo(
                            tab_id=tab,
                            n_rows=n_rows,
                            n_cols=len(columns),
                            n_cols_enabled=n_cols_enabled,
                            desc=None if reset else (existing_tab.desc if existing_tab else None),
                            disabled=tab_disabled,
                            pks=pks,
                            fks=fks,
                            columns=columns,
                        )

                        # Update database-level enabled counts in real-time
                        db_info.n_tabs = len(db_info.tables)
                        db_info.n_tabs_enabled = sum(1 for t in db_info.tables.values() if not t.disabled)
                        db_info.n_cols = sum(len(t.columns) for t in db_info.tables.values())
                        db_info.n_cols_enabled = sum(t.n_cols_enabled or 0 for t in db_info.tables.values() if not t.disabled)

                        # Only save if table was not fully cached (at least one column updated)
                        if not table_fully_cached or not col_cached:
                            self.save_db_info(name, db_info)

                    total_cols += len(columns)

            # Final update to ensure counts are accurate
            db_info.n_tabs = len(all_tabs)
            db_info.n_cols = total_cols
            db_info.n_tabs_enabled = sum(1 for t in db_info.tables.values() if not t.disabled)
            db_info.n_cols_enabled = sum(t.n_cols_enabled or 0 for t in db_info.tables.values() if not t.disabled)

        # Scope 2: Update single table
        elif col_id is None:
            all_tables = db.db_tabs()
            if tab_id not in all_tables:
                db.close_conn()
                raise ValueError(f"Table '{tab_id}' not found in database '{name}'")

            n_rows = db.row_count(tab_id)
            existing_tab = db_info.tables.get(tab_id)

            # Extract PKs/FKs from database if reset=True or they don't exist
            if reset or existing_tab is None or not existing_tab.pks:
                pks = db.tab_pks(tab_id)
            else:
                pks = existing_tab.pks

            if reset or existing_tab is None or not existing_tab.fks:
                fks = db.tab_fks(tab_id)
            else:
                fks = existing_tab.fks

            columns = {}
            for col in db.tab_cols(tab_id):
                datatype_orig = str(db.col_type(tab_id, col))

                # When reset=True, clear all user edits; otherwise preserve them
                existing_col = existing_tab.columns.get(col) if existing_tab else None
                # Set is_pk only for standalone PKs
                is_standalone_pk = len(pks) == 1 and col in pks

                columns[col] = ColumnInfo(
                    col_id=col,
                    datatype_orig=datatype_orig,
                    datatype_anno=None if reset else (existing_col.datatype_anno if existing_col else None),
                    desc=None if reset else (existing_col.desc if existing_col else None),
                    enum_index_enabled=None if reset else (existing_col.enum_index_enabled if existing_col else None),
                    is_pk=is_standalone_pk,
                    disabled=False if reset else (existing_col.disabled if existing_col else False),
                )

            n_cols_enabled = sum(1 for c in columns.values() if not c.disabled)
            db_info.tables[tab_id] = TableInfo(
                tab_id=tab_id,
                n_rows=n_rows,
                n_cols=len(columns),
                n_cols_enabled=n_cols_enabled,
                desc=None if reset else (existing_tab.desc if existing_tab else None),
                disabled=False if reset else (existing_tab.disabled if existing_tab else False),
                pks=pks,
                fks=fks,
                columns=columns,
            )

        # Scope 3: Update single column
        else:
            all_tables = db.db_tabs()
            if tab_id not in all_tables:
                db.close_conn()
                raise ValueError(f"Table '{tab_id}' not found in database '{name}'")

            all_cols = db.tab_cols(tab_id)
            if col_id not in all_cols:
                db.close_conn()
                raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")

            # Ensure table exists
            if tab_id not in db_info.tables:
                db_info.tables[tab_id] = TableInfo(tab_id=tab_id)

            tab_info = db_info.tables[tab_id]

            # Get PKs for is_pk calculation
            pks = tab_info.pks if tab_info.pks else []

            # Compute column metadata
            datatype_orig = str(db.col_type(tab_id, col_id))

            # Set is_pk only for standalone PKs
            is_standalone_pk = len(pks) == 1 and col_id in pks

            # Preserve desc, disabled flag, datatype_anno, and index_enums if exists
            existing_col = tab_info.columns.get(col_id)
            tab_info.columns[col_id] = ColumnInfo(
                col_id=col_id,
                datatype_orig=datatype_orig,
                datatype_anno=existing_col.datatype_anno if existing_col else None,
                desc=existing_col.desc if existing_col else None,
                enum_index_enabled=existing_col.enum_index_enabled if existing_col else None,
                is_pk=is_standalone_pk,
                disabled=existing_col.disabled if existing_col else False,
            )

            # Update table-level enabled count
            tab_info.n_cols_enabled = sum(1 for c in tab_info.columns.values() if not c.disabled)

        db.close_conn()

        # Save
        self.save_db_info(name, db_info)
        return db_info

    def set_disabled(self, name: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, disabled: bool = True) -> None:
        """
        Enable or disable database objects with hierarchical scope control.

        This method provides a unified interface for enabling/disabling at different levels:
        - set_disabled(name, disabled=True) → Disable entire database (all tables, almost never used)
        - set_disabled(name, tab_id="table1", disabled=True) → Disable single table (notice that this is not the same as disabling all columns)
        - set_disabled(name, tab_id="table1", col_id="col1", disabled=True) → Disable single column

        Args:
            name: Database name
            tab_id: Optional table ID
            col_id: Optional column ID (requires tab_id)
            disabled: True to disable, False to enable

        Raises:
            ValueError: If database info not initialized or object not found
        """
        if col_id is not None and tab_id is None:
            raise ValueError("tab_id must be provided when col_id is specified")

        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        # Scope 1: Disable entire database (all tables)
        if tab_id is None:
            for table_info in db_info.tables.values():
                table_info.disabled = disabled
            # Recompute database-level enabled counts
            db_info.n_tabs_enabled = sum(1 for t in db_info.tables.values() if not t.disabled)
            db_info.n_cols_enabled = sum(t.n_cols_enabled or 0 for t in db_info.tables.values() if not t.disabled)

        # Scope 2: Disable single table
        elif col_id is None:
            if tab_id not in db_info.tables:
                raise ValueError(f"Table '{tab_id}' not found in database info.")
            db_info.tables[tab_id].disabled = disabled
            # Recompute database-level enabled counts
            db_info.n_tabs_enabled = sum(1 for t in db_info.tables.values() if not t.disabled)
            db_info.n_cols_enabled = sum(t.n_cols_enabled or 0 for t in db_info.tables.values() if not t.disabled)

        # Scope 3: Disable single column
        else:
            if tab_id not in db_info.tables:
                raise ValueError(f"Table '{tab_id}' not found in database info.")
            if col_id not in db_info.tables[tab_id].columns:
                raise ValueError(f"Column '{col_id}' not found in table '{tab_id}' info.")
            db_info.tables[tab_id].columns[col_id].disabled = disabled
            # Recompute table-level enabled count
            tab_info = db_info.tables[tab_id]
            tab_info.n_cols_enabled = sum(1 for c in tab_info.columns.values() if not c.disabled)
            # Recompute database-level enabled counts
            db_info.n_tabs_enabled = sum(1 for t in db_info.tables.values() if not t.disabled)
            db_info.n_cols_enabled = sum(t.n_cols_enabled or 0 for t in db_info.tables.values() if not t.disabled)

        self.save_db_info(name, db_info)

    def set_description(self, name: str, tab_id: Optional[str] = None, col_id: Optional[str] = None, description: str = "") -> None:
        """
        Set description for database objects with hierarchical scope control.

        This method provides a unified interface for setting descriptions at different levels:
        - set_description(name, description="...") → Set database description
        - set_description(name, tab_id="table1", description="...") → Set table description
        - set_description(name, tab_id="table1", col_id="col1", description="...") → Set column description

        Args:
            name: Database name
            tab_id: Optional table ID
            col_id: Optional column ID (requires tab_id)
            description: Description text to set

        Raises:
            ValueError: If database info not initialized or object not found
        """
        if col_id is not None and tab_id is None:
            raise ValueError("tab_id must be provided when col_id is specified")

        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        # Scope 1: Set database description
        if tab_id is None:
            db_info.desc = description

        # Scope 2: Set table description
        elif col_id is None:
            if tab_id not in db_info.tables:
                raise ValueError(f"Table '{tab_id}' not found in database info.")
            db_info.tables[tab_id].desc = description

        # Scope 3: Set column description
        else:
            if tab_id not in db_info.tables:
                raise ValueError(f"Table '{tab_id}' not found in database info.")
            if col_id not in db_info.tables[tab_id].columns:
                raise ValueError(f"Column '{col_id}' not found in table '{tab_id}' info.")
            db_info.tables[tab_id].columns[col_id].desc = description

        self.save_db_info(name, db_info)

    def set_datatype_anno(self, name: str, tab_id: str, col_id: str, datatype_anno: str) -> None:
        """
        Set human-annotated datatype for a column.

        Args:
            name: Database name
            tab_id: Table ID
            col_id: Column ID
            datatype_anno: Annotated datatype string to set

        Raises:
            ValueError: If database info not initialized or object not found, or invalid datatype
        """
        from rubiksql.utils.db_utils import ColumnType
        from ahvn.utils.basic.debug_utils import raise_mismatch

        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        if tab_id not in db_info.tables:
            raise ValueError(f"Table '{tab_id}' not found in database info.")
        if col_id not in db_info.tables[tab_id].columns:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}' info.")

        # Validate and normalize datatype annotation
        if datatype_anno:
            # Special handling for "null" to clear the annotation
            if datatype_anno.lower() == "null":
                datatype_anno = None
            else:
                # Convert to uppercase for validation and storage
                datatype_anno_upper = datatype_anno.upper()
                valid_types = [t.value for t in ColumnType]
                if datatype_anno_upper not in valid_types:
                    raise_mismatch(
                        supported=valid_types + ["null"],
                        got=datatype_anno,
                        name="datatype",
                        comment="Invalid datatype annotation. Must be one of: " + ", ".join(valid_types) + ", or null",
                    )
                datatype_anno = datatype_anno_upper

        db_info.tables[tab_id].columns[col_id].datatype_anno = datatype_anno
        self.save_db_info(name, db_info)

    def set_enum_index_enabled(self, name: str, tab_id: str, col_id: str, enum_index_enabled: bool) -> None:
        """
        Set whether to index enums for a column.

        Args:
            name: Database name
            tab_id: Table ID
            col_id: Column ID
            enum_index_enabled: True to enable enum indexing, False to disable

        Raises:
            ValueError: If database info not initialized or object not found
        """
        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        if tab_id not in db_info.tables:
            raise ValueError(f"Table '{tab_id}' not found in database info.")
        if col_id not in db_info.tables[tab_id].columns:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}' info.")

        db_info.tables[tab_id].columns[col_id].enum_index_enabled = enum_index_enabled
        self.save_db_info(name, db_info)

    # =========================================================================
    # Foreign Key Management Methods
    # =========================================================================
    # NOTE: Primary keys are read-only and extracted from the database schema.
    # They cannot be manually added or removed - use the database's ALTER TABLE commands instead.

    def add_fk(self, name: str, tab_id: str, col_name: str, tab_ref: str, col_ref: str, fk_name: Optional[str] = None) -> None:
        """
        Add a foreign key to a table.

        Args:
            name: Database name
            tab_id: Table ID
            col_name: Column name in this table
            tab_ref: Referenced table name
            col_ref: Referenced column name
            fk_name: Optional constraint name

        Raises:
            ValueError: If database info not initialized, table/column not found
        """
        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        if tab_id not in db_info.tables:
            raise ValueError(f"Table '{tab_id}' not found in database info.")

        tab_info = db_info.tables[tab_id]

        # Validate column exists
        if col_name not in tab_info.columns:
            raise ValueError(f"Column '{col_name}' not found in table '{tab_id}' info.")

        # Validate referenced table exists
        if tab_ref not in db_info.tables:
            raise ValueError(f"Referenced table '{tab_ref}' not found in database info.")

        # Validate referenced column exists
        if col_ref not in db_info.tables[tab_ref].columns:
            raise ValueError(f"Referenced column '{col_ref}' not found in table '{tab_ref}' info.")

        # Check if exact FK already exists (same col, ref table, ref col, AND name)
        for fk in tab_info.fks:
            if fk["col_name"] == col_name and fk["tab_ref"] == tab_ref and fk["col_ref"] == col_ref and fk.get("name") == fk_name:
                raise ValueError(f"Foreign key from '{col_name}' to '{tab_ref}.{col_ref}' with name '{fk_name}' already exists.")

        # Add FK
        fk_def = {"col_name": col_name, "tab_ref": tab_ref, "col_ref": col_ref, "name": fk_name}
        tab_info.fks.append(fk_def)

        self.save_db_info(name, db_info)

    def remove_fk(
        self,
        name: str,
        tab_id: str,
        col_name: Optional[str] = None,
        tab_ref: Optional[str] = None,
        col_ref: Optional[str] = None,
        fk_name: Optional[str] = None,
    ) -> None:
        """
        Remove a foreign key from a table.

        Filtering logic:
        - When only fk_name is specified: Remove all FKs with matching name
        - When col_name/tab_ref/col_ref specified without fk_name: Remove all FKs matching those criteria
        - When both specified: Remove FKs matching both column/table/column criteria AND name

        Args:
            name: Database name
            tab_id: Table ID
            col_name: Optional column name in this table
            tab_ref: Optional referenced table
            col_ref: Optional referenced column
            fk_name: Optional FK constraint name

        Raises:
            ValueError: If database info not initialized, table not found, FK not found, or no filter criteria provided
        """
        db_info = self.load_db_info(name)
        if db_info is None:
            raise ValueError(f"Database info for '{name}' not found. Initialize with initialize_db_info() first.")

        if tab_id not in db_info.tables:
            raise ValueError(f"Table '{tab_id}' not found in database info.")

        # At least one filter criterion must be provided
        if col_name is None and tab_ref is None and col_ref is None and fk_name is None:
            raise ValueError("At least one filter criterion must be provided (col_name, tab_ref, col_ref, or fk_name).")

        tab_info = db_info.tables[tab_id]

        # Find and remove matching FKs
        original_count = len(tab_info.fks)
        remaining_fks = []

        for fk in tab_info.fks:
            # Check if FK matches removal criteria
            matches = True

            # Filter by column/table/column criteria if any are specified
            if col_name is not None:
                matches = matches and fk["col_name"] == col_name
            if tab_ref is not None:
                matches = matches and fk["tab_ref"] == tab_ref
            if col_ref is not None:
                matches = matches and fk["col_ref"] == col_ref

            # Additionally filter by name if specified
            if fk_name is not None:
                matches = matches and fk.get("name") == fk_name

            if not matches:
                remaining_fks.append(fk)

        if len(remaining_fks) == original_count:
            raise ValueError("No foreign key found matching the criteria.")

        tab_info.fks = remaining_fks
        self.save_db_info(name, db_info)

    def get_active_db(self) -> Optional[str]:
        """
        Get the currently activated database name.

        Returns:
            Database name or None if no database is activated
        """
        return RUBIK_CM.get("core.activate_db", None)

    def set_active_db(self, name: Optional[str]) -> None:
        """
        Set the active database.

        Args:
            name: Database name to activate, or None to deactivate

        Raises:
            ValueError: If database doesn't exist (when activating)
        """
        if name is not None:
            if not self.db_exists(name):
                raise ValueError(f"Database '{name}' not found")

        # Update config
        RUBIK_CM.set("core.activate_db", name, level="global")
        RUBIK_CM.save()

    def activate(self, name: str) -> str:
        """
        Activate a database as the default.

        Args:
            name: Database name to activate (case-insensitive)

        Returns:
            The actual database name with correct casing

        Raises:
            ValueError: If database doesn't exist
        """
        # Use get_db to find the database (case-insensitive lookup via filesystem)
        # and get the actual name with correct casing from info.yaml
        config = self.get_db(name)
        if config is None:
            raise ValueError(f"Database '{name}' not found")

        # Use the correct casing from the config
        self.set_active_db(config.name)
        return config.name

    def deactivate(self) -> None:
        """
        Deactivate the current active database.
        """
        self.set_active_db(None)


# Global singleton instance
RUBIK_DBM = DatabaseManager()
