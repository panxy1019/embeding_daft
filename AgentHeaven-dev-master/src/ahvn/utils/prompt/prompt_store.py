"""\
Persistence layer for versioned prompt specifications.

``PromptStore`` follows the unified registry pattern with version-aware
CRUD.  Each prompt is identified by ``(prompt_id, version)``; the DB
primary key is a deterministic hash of that pair.
"""

__all__ = [
    "PromptStore",
]

import datetime
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

from ..basic.hash_utils import md5hash
from ..basic.log_utils import get_logger
from ..registry.contracts import normalize_registry_record
from .prompt_schema import PromptSpecEntity

logger = get_logger(__name__)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _row_id(prompt_id: str, version: int) -> int:
    """Deterministic PK for a (prompt_id, version) pair."""
    return md5hash(f"{prompt_id}\x00{version}")


_PROMPT_CAPSULE_KEY = "__prompt_capsule__"


def _validate_capsule_metadata(
    *,
    prompt_id: str,
    version: int,
    checksum: str,
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = dict(metadata or {})
    capsule_data = payload.get(_PROMPT_CAPSULE_KEY)
    if not isinstance(capsule_data, dict):
        raise ValueError("PromptStore.save requires metadata['__prompt_capsule__'] with capsule payload.")
    prompt_payload = capsule_data.get("prompt_spec")
    if not isinstance(prompt_payload, dict):
        raise ValueError("PromptStore.save requires capsule prompt_spec metadata for consistency checks.")
    capsule_prompt_id = str(prompt_payload.get("id") or "")
    capsule_version = prompt_payload.get("version")
    capsule_checksum = str(prompt_payload.get("checksum") or "")
    if capsule_prompt_id and capsule_prompt_id != prompt_id:
        raise ValueError(f"PromptStore.save mismatch: prompt_id='{prompt_id}' but capsule prompt_spec.id='{capsule_prompt_id}'.")
    if capsule_version is not None and int(capsule_version) != int(version):
        raise ValueError(f"PromptStore.save mismatch: version={version} but capsule prompt_spec.version={capsule_version}.")
    if capsule_checksum and capsule_checksum != str(checksum):
        raise ValueError(f"PromptStore.save mismatch: checksum='{checksum}' but capsule prompt_spec.checksum='{capsule_checksum}'.")
    return payload


class PromptStore:
    """Database-backed persistence for versioned prompt specs."""

    CACHE_POLICY = {"mode": "none", "add_on": True}

    def __init__(self, **config):
        from ..db import Database

        if not config:
            try:
                from ..basic.config_utils import CM_AHVN

                config = CM_AHVN.get("prompts.storage", default={})
            except Exception:
                config = {}
        if not config:
            config = {"provider": "sqlite", "database": "file:%/prompts.db"}

        self._db = Database(**config)
        self._db.create_tabs([PromptSpecEntity])
        self._write_lock = threading.RLock()

    # -- transaction helpers ------------------------------------------ #

    @contextmanager
    def write_tx(self):
        with self._write_lock:
            with self._db(readonly=False) as db:
                yield db

    @contextmanager
    def tx(self, write: bool = True):
        if write:
            with self.write_tx() as db:
                yield db
            return
        with self._db(readonly=True) as db:
            yield db

    # -- versioned CRUD ----------------------------------------------- #

    def save(
        self,
        prompt_id: str,
        version: int,
        checksum: str,
        qualname: str = "",
        source_file: str = "",
        source_code: str = "",
        td_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persist a prompt spec version. Returns the version number."""
        with self._write_lock:
            metadata_payload = _validate_capsule_metadata(
                prompt_id=prompt_id,
                version=version,
                checksum=checksum,
                metadata=metadata,
            )
            now = _now()
            row_pk = _row_id(prompt_id, version)
            entity = PromptSpecEntity(
                id=row_pk,
                prompt_id=prompt_id,
                version=version,
                checksum=checksum,
                qualname=qualname,
                source_file=source_file,
                source_code=source_code,
                td_refs=td_refs or [],
                metadata_json=metadata_payload,
                created_at=now,
                updated_at=now,
            )
            for stmt in entity.upsert_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return version

    def get(
        self,
        prompt_id: str,
        version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a prompt spec. *version*=None returns latest."""
        if version is not None:
            row_pk = _row_id(prompt_id, version)
            stmt = sa.select(PromptSpecEntity).where(PromptSpecEntity.id == row_pk)
        else:
            stmt = sa.select(PromptSpecEntity).where(PromptSpecEntity.prompt_id == prompt_id).order_by(PromptSpecEntity.version.desc()).limit(1)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return dict(row)

    def get_latest_version(self, prompt_id: str) -> Optional[int]:
        """Return the highest version number for *prompt_id*, or None."""
        stmt = sa.select(sa.func.max(PromptSpecEntity.version)).where(PromptSpecEntity.prompt_id == prompt_id)
        result = self._db.orm_execute(stmt, readonly=True)
        val = result.scalar() if result else None
        return int(val) if val is not None else None

    def get_checksum(
        self,
        prompt_id: str,
        version: Optional[int] = None,
    ) -> Optional[str]:
        """Return checksum of *version* (or latest). None if not found."""
        row = self.get(prompt_id, version)
        return row.get("checksum") if row else None

    def list_versions(self, prompt_id: str) -> List[int]:
        """Return sorted list of all version numbers for *prompt_id*."""
        stmt = sa.select(PromptSpecEntity.version).where(PromptSpecEntity.prompt_id == prompt_id).order_by(PromptSpecEntity.version)
        result = self._db.orm_execute(stmt, readonly=True)
        return [int(r["version"]) for r in result.to_list(row_fmt="dict")] if result else []

    # -- registry contract -------------------------------------------- #

    def list(self) -> List[Dict[str, Any]]:
        """List all prompt ids with latest version info."""
        stmt = (
            sa.select(
                PromptSpecEntity.prompt_id,
                sa.func.max(PromptSpecEntity.version).label("latest_version"),
                sa.func.count(PromptSpecEntity.version).label("version_count"),
            )
            .group_by(PromptSpecEntity.prompt_id)
            .order_by(PromptSpecEntity.prompt_id)
        )
        result = self._db.orm_execute(stmt, readonly=True)
        rows = result.to_list(row_fmt="dict") if result else []
        items = []
        for row in rows:
            pid = str(row["prompt_id"])
            latest = self.get(pid)
            if latest is None:
                continue
            items.append(
                normalize_registry_record(
                    latest,
                    id_key="prompt_id",
                    extras={
                        "version": int(row["latest_version"]),
                        "version_count": int(row["version_count"]),
                        "checksum": latest.get("checksum"),
                        "qualname": latest.get("qualname"),
                    },
                )
            )
        return items

    def info(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Return metadata for all versions of *prompt_id*."""
        versions = self.list_versions(prompt_id)
        if not versions:
            return None
        latest = self.get(prompt_id)
        return normalize_registry_record(
            latest,
            id_key="prompt_id",
            extras={
                "versions": versions,
                "latest_version": max(versions),
                "checksum": latest.get("checksum"),
                "qualname": latest.get("qualname"),
                "td_refs": latest.get("td_refs"),
            },
        )

    def exists(self, prompt_id: str) -> bool:
        return self.get_latest_version(prompt_id) is not None

    def remove(self, prompt_id: str, version: Optional[int] = None) -> None:
        """Remove a specific version, or all versions if *version* is None."""
        with self._write_lock:
            if version is not None:
                row_pk = _row_id(prompt_id, version)
                for stmt in PromptSpecEntity.remove_stmts(id=row_pk):
                    self._db.orm_execute(stmt, autocommit=True)
            else:
                stmt = sa.delete(PromptSpecEntity).where(PromptSpecEntity.prompt_id == prompt_id)
                self._db.orm_execute(stmt, autocommit=True)

    def clear(self) -> int:
        """Delete all prompt spec rows. Returns the count removed."""
        with self._write_lock:
            count_stmt = sa.select(sa.func.count()).select_from(PromptSpecEntity.__table__)
            count_result = self._db.orm_execute(count_stmt, readonly=True)
            count = count_result.scalar() if count_result else 0
            for stmt in PromptSpecEntity.clear_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return count

    def stale(self) -> List[Dict[str, Any]]:
        """Prompts whose source_file no longer exists on disk."""
        items = []
        for entry in self.list():
            pid = str(entry["id"])
            latest = self.get(pid)
            if latest is None:
                continue
            sf = latest.get("source_file") or ""
            if sf and not os.path.isfile(sf):
                items.append(entry)
        return items


# -- singleton -------------------------------------------------------- #

_store_instance: Optional[PromptStore] = None
_store_lock = threading.Lock()


def get_prompt_store() -> PromptStore:
    """Return the process-wide ``PromptStore`` singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = PromptStore()
    return _store_instance
