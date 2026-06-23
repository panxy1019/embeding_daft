"""Database-backed persistence for function capsules."""

from __future__ import annotations

__all__ = [
    "CapsuleStore",
    "CapsuleORMEntity",
    "CapsuleManager",
    "get_capsule_store",
    "get_capsule_manager",
    "CP_AHVN",
]

import datetime
import gzip
from contextlib import contextmanager
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column, Index, LargeBinary

if TYPE_CHECKING:
    from .core import Capsule

from ..basic.file_utils import exists_path
from ..basic.log_utils import get_logger
from ..registry.contracts import normalize_registry_record
from ..basic.serialize_utils import dumps_json, loads_json
from ..db.types import DatabaseIdType, DatabaseJsonType, DatabaseTextType, ExportableEntity

logger = get_logger(__name__)


class CapsuleORMEntity(ExportableEntity):
    """ORM entity for persisted capsules."""

    __tablename__ = "capsules"

    id = Column(DatabaseIdType(), primary_key=True)
    qualname = Column(DatabaseTextType(length=2047), nullable=False, index=True)
    name = Column(DatabaseTextType(length=255), nullable=False, index=True)
    capsule_version = Column(DatabaseTextType(length=63), nullable=False, server_default="1.0")
    checksum = Column(DatabaseTextType(length=63), nullable=True)
    source_file = Column(DatabaseTextType(length=2047), nullable=True)
    manifest = Column(DatabaseJsonType(), nullable=False)
    schema = Column("schema_col", DatabaseJsonType(), nullable=True)
    payload = Column(LargeBinary, nullable=False)
    tags = Column(DatabaseJsonType(), nullable=True)
    created_at = Column(DatabaseTextType(length=63), nullable=True)
    updated_at = Column(DatabaseTextType(length=63), nullable=True)

    __table_args__ = (
        Index("idx_capsule_qualname", "qualname"),
        Index("idx_capsule_name", "name"),
        {"extend_existing": True},
    )


def _compress_payload(capsule: Dict[str, Any]) -> bytes:
    return gzip.compress(dumps_json(capsule, indent=None).encode("utf-8"))


def _decompress_payload(data: bytes) -> Dict[str, Any]:
    return loads_json(gzip.decompress(data).decode("utf-8"))


class CapsuleStore:
    """Database-backed capsule persistence."""

    CACHE_POLICY = {
        "mode": "none",
        "add_on": True,
    }

    def __init__(self, **config):
        from ..db import Database

        if not config:
            try:
                from ..basic.config_utils import CM_AHVN

                config = CM_AHVN.get("registry.capsule.storage", default={})
                if not config:
                    config = CM_AHVN.get("tool.capsule.storage", default={})
            except Exception:
                config = {}
        if not config:
            config = {"provider": "sqlite", "database": "file:%/capsules.db"}

        self._db = Database(**config)
        self._db.create_tabs([CapsuleORMEntity])
        self._write_lock = threading.RLock()

    @contextmanager
    def tx(self, write: bool = True):
        """Context manager for grouped store operations."""
        if write:
            with self._write_lock:
                with self._db(readonly=False) as db:
                    yield db
            return

        with self._db(readonly=True) as db:
            yield db

    def get(self, capsule_id: str) -> Optional[Dict[str, Any]]:
        stmt = CapsuleORMEntity.get_stmt(capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return _decompress_payload(row["payload"])

    def get_by_qualname(self, qualname: str) -> Optional[Dict[str, Any]]:
        from .core import Capsule

        direct = self.get(Capsule.capsule_id(qualname))
        if direct is not None:
            return direct

        stmt = sa.select(CapsuleORMEntity.id).where(CapsuleORMEntity.qualname == qualname).order_by(CapsuleORMEntity.updated_at.desc())
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return self.get(row["id"])

    def get_checksum(self, capsule_id: str) -> Optional[str]:
        stmt = sa.select(CapsuleORMEntity.checksum).where(CapsuleORMEntity.id == capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.scalar() if result else None

    def _get_created_at(self, capsule_id: str) -> Optional[str]:
        stmt = sa.select(CapsuleORMEntity.created_at).where(CapsuleORMEntity.id == capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.scalar() if result else None

    def _get_tags(self, capsule_id: str) -> Optional[List[str]]:
        stmt = sa.select(CapsuleORMEntity.tags).where(CapsuleORMEntity.id == capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.scalar() if result else None

    def list(self, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = sa.select(
            CapsuleORMEntity.id,
            CapsuleORMEntity.qualname,
            CapsuleORMEntity.name,
            CapsuleORMEntity.capsule_version,
            CapsuleORMEntity.checksum,
            CapsuleORMEntity.source_file,
            CapsuleORMEntity.tags,
            CapsuleORMEntity.created_at,
            CapsuleORMEntity.updated_at,
        ).order_by(CapsuleORMEntity.name)
        result = self._db.orm_execute(stmt, readonly=True)
        rows = result.to_list(row_fmt="dict") if result else []
        rows = [
            normalize_registry_record(
                row,
                extras={
                    "qualname": row.get("qualname"),
                    "name": row.get("name"),
                    "capsule_version": row.get("capsule_version"),
                    "checksum": row.get("checksum"),
                    "source_file": row.get("source_file"),
                    "tags": row.get("tags"),
                },
            )
            for row in rows
        ]
        if tag:
            rows = [row for row in rows if row.get("tags") and tag in row["tags"]]
        return rows

    def list_items(self, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.list(tag=tag)

    def info(self, capsule_id: str) -> Optional[Dict[str, Any]]:
        stmt = sa.select(
            CapsuleORMEntity.id,
            CapsuleORMEntity.qualname,
            CapsuleORMEntity.name,
            CapsuleORMEntity.capsule_version,
            CapsuleORMEntity.checksum,
            CapsuleORMEntity.source_file,
            CapsuleORMEntity.tags,
            CapsuleORMEntity.created_at,
            CapsuleORMEntity.updated_at,
        ).where(CapsuleORMEntity.id == capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return normalize_registry_record(
            row,
            extras={
                "qualname": row.get("qualname"),
                "name": row.get("name"),
                "capsule_version": row.get("capsule_version"),
                "checksum": row.get("checksum"),
                "source_file": row.get("source_file"),
                "tags": row.get("tags"),
            },
        )

    def search(self, name: Optional[str] = None, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = sa.select(
            CapsuleORMEntity.id,
            CapsuleORMEntity.qualname,
            CapsuleORMEntity.name,
            CapsuleORMEntity.capsule_version,
            CapsuleORMEntity.checksum,
            CapsuleORMEntity.source_file,
            CapsuleORMEntity.manifest,
            CapsuleORMEntity.tags,
            CapsuleORMEntity.created_at,
            CapsuleORMEntity.updated_at,
        )
        if name:
            stmt = stmt.where(CapsuleORMEntity.name.ilike(f"%{name}%"))
        stmt = stmt.order_by(CapsuleORMEntity.name)
        result = self._db.orm_execute(stmt, readonly=True)
        rows = result.to_list(row_fmt="dict") if result else []
        if tag:
            rows = [row for row in rows if row.get("tags") and tag in row["tags"]]
        return rows

    def exists(self, capsule_id: str) -> bool:
        stmt = CapsuleORMEntity.exists_stmt(capsule_id)
        result = self._db.orm_execute(stmt, readonly=True)
        return bool(result.scalar()) if result else False

    def add(self, capsule: Dict[str, Any], tags: Optional[List[str]] = None) -> str:
        with self._write_lock:
            capsule_id = capsule["capsule_id"]
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            manifest = capsule.get("manifest", {})

            created_at = self._get_created_at(capsule_id) or now
            if tags is None:
                tags = self._get_tags(capsule_id)

            entity = CapsuleORMEntity(
                id=capsule_id,
                qualname=manifest.get("qualname", manifest.get("name", "")),
                name=manifest.get("name", ""),
                capsule_version=capsule.get("capsule_version", "1.0"),
                checksum=capsule.get("checksum"),
                source_file=manifest.get("source_file"),
                manifest=manifest,
                schema=capsule.get("schema"),
                payload=_compress_payload(capsule),
                tags=tags,
                created_at=created_at,
                updated_at=now,
            )
            for stmt in entity.upsert_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return capsule_id

    def delete(self, capsule_id: str) -> None:
        with self._write_lock:
            for stmt in CapsuleORMEntity.remove_stmts(id=capsule_id):
                self._db.orm_execute(stmt, autocommit=True)

    def remove(self, capsule_id: str) -> None:
        self.delete(capsule_id)

    def clear(self) -> int:
        with self._write_lock:
            count_stmt = sa.select(sa.func.count()).select_from(CapsuleORMEntity.__table__)
            count_result = self._db.orm_execute(count_stmt, readonly=True)
            count = count_result.scalar() if count_result else 0
            for stmt in CapsuleORMEntity.clear_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return count

    def stale(self) -> List[Dict[str, Any]]:
        stale_items: List[Dict[str, Any]] = []
        for item in self.list_items():
            source_file = item.get("source_file")
            if source_file and (not exists_path(source_file)):
                stale_items.append(item)
        return stale_items


_store_instance: Optional[CapsuleStore] = None
_store_lock = threading.Lock()


def get_capsule_store() -> CapsuleStore:
    """Return the process-wide ``CapsuleStore`` singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = CapsuleStore()
    return _store_instance


# -- CapsuleManager --------------------------------------------------- #


class CapsuleManager:
    """High-level capsule management facade.

    Returns ``Capsule`` objects for capsule retrieval APIs and exposes
    dedicated ``*_info`` methods for registry summary dicts.
    """

    def __init__(self, store: Optional[CapsuleStore] = None):
        self._store = store

    @property
    def store(self) -> CapsuleStore:
        if self._store is None:
            self._store = get_capsule_store()
        return self._store

    @staticmethod
    def _to_capsule(capsule_data: Optional[Dict[str, Any]]) -> Optional["Capsule"]:
        if capsule_data is None:
            return None
        from .core import Capsule

        return Capsule.from_dict(capsule_data)

    def _capsules_from_registry_rows(self, rows: List[Dict[str, Any]]) -> List["Capsule"]:
        capsules: List["Capsule"] = []
        for row in rows:
            capsule_id = row.get("id")
            normalized_id: Optional[str]
            if capsule_id is None:
                normalized_id = None
            elif isinstance(capsule_id, int):
                normalized_id = str(capsule_id).zfill(40)
            else:
                normalized_id = str(capsule_id)
                if normalized_id.isdigit() and len(normalized_id) < 40:
                    normalized_id = normalized_id.zfill(40)

            cap = self._to_capsule(self.store.get(normalized_id)) if normalized_id else None
            if cap is None:
                qualname = row.get("qualname")
                cap = self._to_capsule(self.store.get_by_qualname(str(qualname))) if qualname else None
            if cap is not None:
                capsules.append(cap)
        return capsules

    def get(self, id_or_qualname: str) -> Optional["Capsule"]:
        """Get a capsule by id or qualname."""
        cap = self._to_capsule(self.store.get(id_or_qualname))
        if cap is not None:
            return cap
        return self._to_capsule(self.store.get_by_qualname(id_or_qualname))

    def add(self, func_or_capsule, *, tags: Optional[List[str]] = None) -> str:
        """Add a function or capsule to the store.

        Accepts a callable (auto-encapsulated), a Capsule object, or a raw
        capsule dict.
        """
        from .core import Capsule

        if callable(func_or_capsule) and not isinstance(func_or_capsule, dict):
            if isinstance(func_or_capsule, Capsule):
                cap = func_or_capsule.to_dict()
            else:
                cap = Capsule.from_func(func_or_capsule).to_dict()
        elif isinstance(func_or_capsule, dict):
            cap = func_or_capsule
        else:
            raise TypeError(f"Expected callable, Capsule, or dict, got {type(func_or_capsule)}")
        return self.store.add(cap, tags=tags)

    def list(self, tag: Optional[str] = None) -> List["Capsule"]:
        """List capsules as Capsule objects."""
        rows = self.store.list(tag=tag)
        return self._capsules_from_registry_rows(rows)

    def search(self, name: Optional[str] = None, tag: Optional[str] = None) -> List["Capsule"]:
        """Search capsules by name or tag and return Capsule objects."""
        rows = self.store.search(name=name, tag=tag)
        return self._capsules_from_registry_rows(rows)

    def list_info(self, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """List capsule registry summaries."""
        return self.store.list(tag=tag)

    def search_info(self, name: Optional[str] = None, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search capsule registry summaries by name or tag."""
        return self.store.search(name=name, tag=tag)

    def remove(self, capsule_id: str) -> None:
        """Delete a capsule."""
        self.store.delete(capsule_id)

    def info(self, capsule_id: str) -> Optional[Dict[str, Any]]:
        """Get capsule registry info."""
        return self.store.info(capsule_id)

    def stale(self) -> List["Capsule"]:
        """List stale capsules as Capsule objects."""
        rows = self.store.stale()
        return self._capsules_from_registry_rows(rows)

    def stale_info(self) -> List[Dict[str, Any]]:
        """List stale capsule registry summaries."""
        return self.store.stale()

    def exists(self, capsule_id: str) -> bool:
        """Check whether a capsule exists."""
        return self.store.exists(capsule_id)

    def clear(self) -> int:
        """Remove all capsules. Returns the count deleted."""
        return self.store.clear()


# -- singleton -------------------------------------------------------- #

_manager_instance: Optional[CapsuleManager] = None
_manager_lock = threading.Lock()


def get_capsule_manager() -> CapsuleManager:
    """Return the process-wide ``CapsuleManager`` singleton."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = CapsuleManager()
    return _manager_instance


class _LazyCP:
    """Module-level proxy so ``CP_AHVN`` can be imported at the top of a
    module without triggering DB initialization on import."""

    def __getattr__(self, name):
        return getattr(get_capsule_manager(), name)

    def __repr__(self):
        return repr(get_capsule_manager())


CP_AHVN: CapsuleManager = _LazyCP()  # type: ignore[assignment]
