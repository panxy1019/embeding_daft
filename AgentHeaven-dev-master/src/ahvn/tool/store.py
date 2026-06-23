"""Database-backed persistence for capsule-based toolkits."""

from __future__ import annotations

__all__ = [
    "ToolkitStore",
    "get_toolkit_store",
]

from copy import deepcopy
from contextlib import contextmanager
import datetime
import gzip
import threading
import warnings
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy import Column, Index, LargeBinary

from ..utils.basic.file_utils import exists_path
from ..utils.basic.hash_utils import fmt_hash, md5hash
from ..utils.basic.log_utils import get_logger
from ..utils.registry.contracts import normalize_registry_record
from ..utils.basic.serialize_utils import dumps_json, loads_json
from ..utils.db.types import DatabaseJsonType, DatabaseTextType, ExportableEntity

TOOLKIT_VERSION = "1.0"
logger = get_logger(__name__)


class ToolkitORMEntity(ExportableEntity):
    """ORM entity for persisted toolkits."""

    __tablename__ = "toolkits"

    id = Column(DatabaseTextType(length=255), primary_key=True)
    name = Column(DatabaseTextType(length=255), nullable=False, index=True)
    toolkit_version = Column(DatabaseTextType(length=63), nullable=False, server_default=TOOLKIT_VERSION)
    checksum = Column(DatabaseTextType(length=63), nullable=True)
    manifest = Column(DatabaseJsonType(), nullable=False)
    payload = Column(LargeBinary, nullable=False)
    created_at = Column(DatabaseTextType(length=63), nullable=True)
    updated_at = Column(DatabaseTextType(length=63), nullable=True)

    __table_args__ = (
        Index("idx_toolkit_name", "name"),
        {"extend_existing": True},
    )


def _compress_payload(payload: Dict[str, Any]) -> bytes:
    return gzip.compress(dumps_json(payload, indent=None).encode("utf-8"))


def _decompress_payload(data: bytes) -> Dict[str, Any]:
    return loads_json(gzip.decompress(data).decode("utf-8"))


def _payload_checksum(payload: Dict[str, Any]) -> str:
    checksum_data = {
        "toolkit_name": payload.get("toolkit_name"),
        "manifest": payload.get("manifest", {}),
        "capsules": payload.get("capsules", []),
    }
    return fmt_hash(md5hash(checksum_data))


def _capsule_lossless(capsule: Dict[str, Any]) -> bool:
    if not isinstance(capsule, dict):
        return False
    toolspec_meta = capsule.get("toolspec")
    if isinstance(toolspec_meta, dict):
        return bool(toolspec_meta.get("lossless", True))
    return True


class ToolkitStore:
    """Database-backed capsule-bundle toolkit persistence."""

    CACHE_POLICY = {
        "mode": "manager-runtime-cache",
        "add_on": True,
    }

    def __init__(self, **config):
        from ..utils.db import Database

        if not config:
            from ..utils.basic.config_utils import CM_AHVN

            config = CM_AHVN.get("registry.toolkit.storage", default={})
            if not config:
                config = CM_AHVN.get("tool.toolkit.storage", default={})
        if not config:
            config = {"provider": "sqlite", "database": "file:%/toolkits.db"}

        self._db = Database(**config)
        self._db.create_tabs([ToolkitORMEntity])
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

    @staticmethod
    def _build_payload(
        *,
        name: str,
        short_description: str,
        description: str,
        instructions: Optional[Dict[str, List[str]]],
        capsules: List[Dict[str, Any]],
        runtime_type: str = "session",
        tool_enabled: Optional[Dict[str, bool]] = None,
        source: Optional[Dict[str, Any]] = None,
        mcp_entry: Optional[Dict[str, Any]] = None,
        version: str = TOOLKIT_VERSION,
    ) -> Dict[str, Any]:
        tool_names = sorted([capsule.get("manifest", {}).get("tool_name", "") for capsule in capsules if isinstance(capsule, dict)])
        lossy_tools = sorted(
            [capsule.get("manifest", {}).get("tool_name", "") for capsule in capsules if isinstance(capsule, dict) and (not _capsule_lossless(capsule))]
        )

        manifest: Dict[str, Any] = {
            "name": name,
            "short_description": short_description or "",
            "description": description or "",
            "instructions": deepcopy(instructions) if isinstance(instructions, dict) else {},
            "tool_names": tool_names,
            "n_tools": len(capsules),
            "lossless": len(lossy_tools) == 0,
            "runtime_type": runtime_type if isinstance(runtime_type, str) else "session",
            "tool_enabled": deepcopy(tool_enabled) if isinstance(tool_enabled, dict) else {},
        }
        if lossy_tools:
            manifest["lossy_tools"] = lossy_tools
        if isinstance(source, dict) and source:
            manifest["source"] = deepcopy(source)
        if isinstance(mcp_entry, dict) and mcp_entry:
            manifest["mcp_entry"] = deepcopy(mcp_entry)

        payload = {
            "toolkit_version": version,
            "toolkit_name": name,
            "manifest": manifest,
            "capsules": capsules,
        }
        payload["checksum"] = _payload_checksum(payload)
        return payload

    def _get_created_at(self, toolkit_name: str) -> Optional[str]:
        stmt = sa.select(ToolkitORMEntity.created_at).where(ToolkitORMEntity.id == toolkit_name)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.scalar() if result else None

    def exists(self, toolkit_name: str) -> bool:
        stmt = ToolkitORMEntity.exists_stmt(toolkit_name)
        result = self._db.orm_execute(stmt, readonly=True)
        return bool(result.scalar()) if result else False

    def get(self, toolkit_name: str) -> Optional[Dict[str, Any]]:
        stmt = ToolkitORMEntity.get_stmt(toolkit_name)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return _decompress_payload(row["payload"])

    def save(self, payload: Dict[str, Any]) -> str:
        with self._write_lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()

            data = deepcopy(payload)
            toolkit_name = str(data.get("toolkit_name") or data.get("manifest", {}).get("name") or data.get("name") or "")
            if not toolkit_name:
                raise ValueError("Toolkit payload must include 'toolkit_name'.")

            manifest = data.get("manifest", {})
            if not isinstance(manifest, dict):
                manifest = {}
            manifest.setdefault("name", toolkit_name)
            runtime_type = manifest.get("runtime_type", "session")
            if runtime_type not in ("session", "persistent", "stateless"):
                runtime_type = "session"
            manifest["runtime_type"] = runtime_type
            tool_enabled = manifest.get("tool_enabled", {})
            if not isinstance(tool_enabled, dict):
                tool_enabled = {}
            manifest["tool_enabled"] = {str(k): bool(v) for k, v in tool_enabled.items()}
            capsules = data.get("capsules", [])
            if not isinstance(capsules, list):
                capsules = []
            lossy_tools = sorted(
                [capsule.get("manifest", {}).get("tool_name", "") for capsule in capsules if isinstance(capsule, dict) and (not _capsule_lossless(capsule))]
            )
            manifest["lossless"] = len(lossy_tools) == 0
            if lossy_tools:
                manifest["lossy_tools"] = lossy_tools
            else:
                manifest.pop("lossy_tools", None)
            data["toolkit_name"] = toolkit_name
            data["manifest"] = manifest
            data["toolkit_version"] = str(data.get("toolkit_version", TOOLKIT_VERSION))
            data["capsules"] = capsules
            data["checksum"] = _payload_checksum(data)

            created_at = self._get_created_at(toolkit_name) or now

            entity = ToolkitORMEntity(
                id=toolkit_name,
                name=toolkit_name,
                toolkit_version=data["toolkit_version"],
                checksum=data["checksum"],
                manifest=manifest,
                payload=_compress_payload(data),
                created_at=created_at,
                updated_at=now,
            )
            for stmt in entity.upsert_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return toolkit_name

    def save_toolkit(self, toolkit, source: Optional[Dict[str, Any]] = None, **capsule_kwargs) -> str:
        mcp_entry = getattr(toolkit, "_mcp_entry", None)
        if not isinstance(mcp_entry, dict):
            mcp_entry = None
        if mcp_entry is None:
            remote_url = getattr(toolkit, "_remote_url", None)
            if isinstance(remote_url, str) and remote_url.strip():
                mcp_entry = {
                    "url": remote_url.strip(),
                    "transport": "http",
                }

        payload = self._build_payload(
            name=toolkit.name,
            short_description=toolkit.short_description,
            description=toolkit.description,
            instructions=toolkit.instructions,
            capsules=toolkit.to_capsules(**capsule_kwargs),
            runtime_type=getattr(toolkit, "runtime_type", "session"),
            tool_enabled=getattr(toolkit, "tool_enabled", {}),
            source=source,
            mcp_entry=mcp_entry,
        )
        return self.save(payload)

    def load(self, toolkit_name: str):
        from .toolkit import Toolkit

        payload = self.get(toolkit_name)
        if payload is None:
            raise KeyError(f"Toolkit '{toolkit_name}' not found.")

        manifest = payload.get("manifest", {})
        if isinstance(manifest, dict) and (not bool(manifest.get("lossless", True))):
            lossy_tools = manifest.get("lossy_tools", [])
            warning_msg = f"Toolkit '{toolkit_name}' contains lossy ToolSpec state; " f"affected tools: {lossy_tools or '[unknown]'}."
            warnings.warn(warning_msg, UserWarning, stacklevel=2)
            logger.warning(warning_msg)
        toolkit = Toolkit.from_capsules(
            name=manifest.get("name", toolkit_name),
            capsules=payload.get("capsules", []),
            short_description=manifest.get("short_description", ""),
            description=manifest.get("description", ""),
            instructions=manifest.get("instructions"),
            runtime_type=manifest.get("runtime_type", "session"),
            tool_enabled=manifest.get("tool_enabled"),
        )
        mcp_entry = manifest.get("mcp_entry")
        if isinstance(mcp_entry, dict) and mcp_entry:
            toolkit._mcp_entry = deepcopy(mcp_entry)
            url = mcp_entry.get("url")
            if isinstance(url, str) and url.strip():
                toolkit._remote_url = url.strip()
        return toolkit

    def list(self) -> List[Dict[str, Any]]:
        stmt = sa.select(
            ToolkitORMEntity.id,
            ToolkitORMEntity.name,
            ToolkitORMEntity.toolkit_version,
            ToolkitORMEntity.checksum,
            ToolkitORMEntity.manifest,
            ToolkitORMEntity.created_at,
            ToolkitORMEntity.updated_at,
        ).order_by(ToolkitORMEntity.name)
        result = self._db.orm_execute(stmt, readonly=True)
        rows = result.to_list(row_fmt="dict") if result else []

        items: List[Dict[str, Any]] = []
        for row in rows:
            manifest = row.get("manifest") if isinstance(row.get("manifest"), dict) else {}
            source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
            items.append(
                normalize_registry_record(
                    row,
                    extras={
                        "name": row.get("name") or row.get("id"),
                        "toolkit_version": row.get("toolkit_version", TOOLKIT_VERSION),
                        "checksum": row.get("checksum"),
                        "short_description": manifest.get("short_description", ""),
                        "description": manifest.get("description", ""),
                        "tools": manifest.get("tool_names", []),
                        "n_tools": manifest.get("n_tools", 0),
                        "lossless": manifest.get("lossless", True),
                        "lossy_tools": manifest.get("lossy_tools", []),
                        "runtime_type": manifest.get("runtime_type", "session"),
                        "tool_enabled": manifest.get("tool_enabled", {}),
                        "source_factory": source.get("factory", ""),
                        "source_args": source.get("args", {}),
                    },
                )
            )
        return items

    def list_items(self) -> List[Dict[str, Any]]:
        return self.list()

    def info(self, toolkit_name: str) -> Optional[Dict[str, Any]]:
        for item in self.list_items():
            if item["id"] == toolkit_name:
                return item
        return None

    def list_names(self) -> List[str]:
        stmt = sa.select(ToolkitORMEntity.name).order_by(ToolkitORMEntity.name)
        result = self._db.orm_execute(stmt, readonly=True)
        return [row["name"] for row in result] if result else []

    def delete(self, toolkit_name: str) -> None:
        with self._write_lock:
            for stmt in ToolkitORMEntity.remove_stmts(id=toolkit_name):
                self._db.orm_execute(stmt, autocommit=True)

    def remove(self, toolkit_name: str) -> None:
        self.delete(toolkit_name)

    def rename(self, old_name: str, new_name: str) -> None:
        with self._write_lock:
            payload = self.get(old_name)
            if payload is None:
                raise KeyError(f"Toolkit '{old_name}' not found.")

            payload["toolkit_name"] = new_name
            manifest = payload.get("manifest", {}) if isinstance(payload.get("manifest"), dict) else {}
            manifest["name"] = new_name
            payload["manifest"] = manifest
            payload["checksum"] = _payload_checksum(payload)

            self.save(payload)
            if old_name != new_name:
                self.delete(old_name)

    def clear(self) -> int:
        with self._write_lock:
            count_stmt = sa.select(sa.func.count()).select_from(ToolkitORMEntity.__table__)
            count_result = self._db.orm_execute(count_stmt, readonly=True)
            count = count_result.scalar() if count_result else 0
            for stmt in ToolkitORMEntity.clear_stmts():
                self._db.orm_execute(stmt, autocommit=True)
            return count

    @staticmethod
    def _source_paths_from_args(args: Dict[str, Any]) -> List[str]:
        if not isinstance(args, dict):
            return []

        paths: List[str] = []
        for key, value in args.items():
            if key not in {"database", "path", "uri", "file", "source_file"}:
                continue
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate:
                continue
            if "://" in candidate and (not candidate.startswith("file://")):
                continue
            if candidate.startswith("file://"):
                candidate = candidate[7:]
            if candidate.startswith("file:"):
                candidate = candidate[5:]
            if candidate.startswith("%"):
                continue
            if candidate.lower() in {":memory:", "memory"}:
                continue
            paths.append(candidate)
        return paths

    def stale(self) -> List[Dict[str, Any]]:
        """Toolkits whose recorded source paths no longer exist."""
        items: List[Dict[str, Any]] = []
        for item in self.list_items():
            args = item.get("source_args", {})
            missing = []
            for raw_path in self._source_paths_from_args(args):
                if not exists_path(raw_path):
                    missing.append(raw_path)
            if missing:
                row = dict(item)
                row["missing_paths"] = missing
                items.append(row)
        return items


_store_instance: Optional[ToolkitStore] = None
_store_lock = threading.Lock()


def get_toolkit_store() -> ToolkitStore:
    """Return the process-wide ``ToolkitStore`` singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = ToolkitStore()
    return _store_instance
