"""\
Database information models for RubikSQL.

This module provides dataclasses for storing database metadata including
tables, columns, row counts, distinct counts, descriptions, and disabled flags.
"""

__all__ = [
    "DatabaseInfo",
    "TableInfo",
    "ColumnInfo",
]

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class ColumnInfo:
    """Metadata for a database column."""

    col_id: str
    datatype_orig: Optional[str] = None
    datatype_anno: Optional[str] = None
    desc: Optional[str] = None
    enum_index_enabled: Optional[bool] = None
    is_pk: bool = False
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "col_id": self.col_id,
            "datatype_orig": self.datatype_orig,
            "datatype_anno": self.datatype_anno,
            "desc": self.desc,
            "enum_index_enabled": self.enum_index_enabled,
            "is_pk": self.is_pk,
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnInfo":
        """Create from dictionary."""
        return cls(
            col_id=data.get("col_id", ""),
            datatype_orig=data.get("datatype_orig"),
            datatype_anno=data.get("datatype_anno"),
            desc=data.get("desc"),
            enum_index_enabled=data.get("enum_index_enabled"),
            is_pk=data.get("is_pk", False),
            disabled=data.get("disabled", False),
        )


@dataclass
class TableInfo:
    """Metadata for a database table."""

    tab_id: str
    n_rows: Optional[int] = None
    n_cols: Optional[int] = None
    n_cols_enabled: Optional[int] = None
    desc: Optional[str] = None
    disabled: bool = False
    pks: List[str] = field(default_factory=list)
    fks: List[Dict[str, Any]] = field(default_factory=list)
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        return {
            "tab_id": self.tab_id,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "n_cols_enabled": self.n_cols_enabled,
            "desc": self.desc,
            "disabled": self.disabled,
            "pks": self.pks,
            "fks": self.fks,
            "columns": {col_id: col_info.to_dict() for col_id, col_info in self.columns.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TableInfo":
        """Create from dictionary."""
        columns_data = data.get("columns", {})
        columns = {col_id: ColumnInfo.from_dict(col_data) for col_id, col_data in columns_data.items()}
        return cls(
            tab_id=data.get("tab_id", ""),
            n_rows=data.get("n_rows"),
            n_cols=data.get("n_cols"),
            n_cols_enabled=data.get("n_cols_enabled"),
            desc=data.get("desc"),
            disabled=data.get("disabled", False),
            pks=data.get("pks", []),
            fks=data.get("fks", []),
            columns=columns,
        )


@dataclass
class DatabaseInfo:
    """Complete metadata for a database."""

    name: str
    created_at: Optional[str] = None
    desc: Optional[str] = None
    disabled: bool = False
    n_tabs: Optional[int] = None
    n_cols: Optional[int] = None
    n_tabs_enabled: Optional[int] = None
    n_cols_enabled: Optional[int] = None
    tables: Dict[str, TableInfo] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for YAML serialization (merged into info.yaml)."""
        result = {
            "name": self.name,
            "created_at": self.created_at,
            "stats": {
                "db_id": self.name,
                "desc": self.desc,
                "disabled": self.disabled,
                "n_tabs": self.n_tabs,
                "n_cols": self.n_cols,
                "n_tabs_enabled": self.n_tabs_enabled,
                "n_cols_enabled": self.n_cols_enabled,
                "tables": {tab_id: tab_info.to_dict() for tab_id, tab_info in self.tables.items()},
            },
        }
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatabaseInfo":
        """Create from dictionary (from info.yaml)."""
        stats = data.get("stats", {})
        tables_data = stats.get("tables", {})
        tables = {tab_id: TableInfo.from_dict(tab_data) for tab_id, tab_data in tables_data.items()}
        return cls(
            name=data.get("name", ""),
            created_at=data.get("created_at"),
            desc=stats.get("desc"),
            disabled=stats.get("disabled", False),
            n_tabs=stats.get("n_tabs"),
            n_cols=stats.get("n_cols"),
            n_tabs_enabled=stats.get("n_tabs_enabled"),
            n_cols_enabled=stats.get("n_cols_enabled"),
            tables=tables,
        )

    def get_table_column_count(self, tab_id: str) -> int:
        """Get the number of columns for a table."""
        return len(self.tables.get(tab_id, TableInfo(tab_id="")).columns)

    def get_total_column_count(self) -> int:
        """Get total number of columns across all tables."""
        return sum(len(tab_info.columns) for tab_info in self.tables.values())

    def get_total_row_count(self) -> int:
        """Get total row count across all tables."""
        return sum(tab_info.n_rows or 0 for tab_info in self.tables.values())

    def get_enabled_tables(self) -> List[str]:
        """Get list of enabled (non-disabled) table IDs."""
        return [tab_id for tab_id, tab_info in self.tables.items() if not tab_info.disabled]

    def get_enabled_columns(self, tab_id: str) -> List[str]:
        """Get list of enabled (non-disabled) column IDs for a table."""
        table = self.tables.get(tab_id)
        if not table:
            return []
        return [col_id for col_id, col_info in table.columns.items() if not col_info.disabled]
