"""\
Persistence layer for translations.

``TranslationStore`` is a thin DB wrapper that handles CRUD for the
normalized translation tables plus optional runtime index snapshots.
No runtime matching logic lives here.
"""

__all__ = [
    "TranslationStore",
]

import datetime
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

from ..basic.hash_utils import md5hash
from ..basic.log_utils import get_logger
from ..registry.contracts import normalize_registry_record
from .translate_schema import (
    TranslationIndexEntity,
    TranslationNamespaceEntity,
    TranslationTemplateEntity,
    TranslationValueEntity,
)
from .translate_match import TemplateSpec

logger = get_logger(__name__)

_ALL_ENTITIES = [
    TranslationNamespaceEntity,
    TranslationTemplateEntity,
    TranslationValueEntity,
    TranslationIndexEntity,
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _template_id(namespace: str, source_key: str) -> int:
    return md5hash(f"{namespace}\x00{source_key}")


def _value_id(template_id: int, lang: str) -> int:
    return md5hash(f"{template_id}\x00{lang}")


class TranslationStore:
    """Database-backed persistence for translations."""

    CACHE_POLICY = {
        "mode": "runtime-index-snapshot",
        "add_on": True,
    }

    def __init__(self, **config):
        from ..db import Database

        if not config:
            try:
                from ..basic.config_utils import CM_AHVN

                config = CM_AHVN.get("registry.translation.storage", default={})
                if not config:
                    config = CM_AHVN.get("prompts.translation.storage", default={})
            except Exception:
                config = {}
        if not config:
            config = {"provider": "sqlite", "database": "file:%/translations.db"}

        self._db = Database(**config)
        self._db.create_tabs(_ALL_ENTITIES)
        self._write_lock = threading.RLock()

    @contextmanager
    def write_tx(self):
        """Return a DB context manager for atomic translation writes."""
        with self._write_lock:
            with self._db(readonly=False) as db:
                yield db

    @contextmanager
    def tx(self, write: bool = True):
        """Context manager for grouped store operations."""
        if write:
            with self.write_tx() as db:
                yield db
            return

        with self._db(readonly=True) as db:
            yield db

    @staticmethod
    def template_id(namespace: str, source_key: str) -> int:
        """Return deterministic template id for (namespace, source_key)."""
        return _template_id(namespace, source_key)

    # -- namespace ---------------------------------------------------- #

    def ensure_namespace(self, namespace: str, main_lang: str) -> None:
        with self._write_lock:
            now = _now()
            entity = TranslationNamespaceEntity(
                id=namespace,
                main_lang=main_lang,
                created_at=now,
                updated_at=now,
            )
            for stmt in entity.upsert_stmts():
                self._db.orm_execute(stmt)

    def get_namespace(self, namespace: str) -> Optional[Dict[str, Any]]:
        stmt = sa.select(TranslationNamespaceEntity).where(TranslationNamespaceEntity.id == namespace)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.first() if result else None

    def delete_namespace(self, namespace: str) -> None:
        """Delete namespace, all its templates, and all values."""
        with self._write_lock:
            # Get template ids first
            stmt = sa.select(TranslationTemplateEntity.id).where(TranslationTemplateEntity.namespace == namespace)
            result = self._db.orm_execute(stmt, readonly=True)
            tmpl_ids = [r["id"] for r in result.to_list(row_fmt="dict")] if result else []

            # Delete values for those templates
            if tmpl_ids:
                del_vals = sa.delete(TranslationValueEntity).where(TranslationValueEntity.template_id.in_(tmpl_ids))
                self._db.orm_execute(del_vals)

            # Delete templates
            for stmt in TranslationTemplateEntity.remove_stmts(namespace=namespace):
                self._db.orm_execute(stmt)

            # Delete namespace
            for stmt in TranslationNamespaceEntity.remove_stmts(id=namespace):
                self._db.orm_execute(stmt)

            # Delete persisted runtime index snapshot
            for stmt in TranslationIndexEntity.remove_stmts(id=namespace):
                self._db.orm_execute(stmt)

    def list_namespaces(self) -> List[Dict[str, Any]]:
        stmt = sa.select(TranslationNamespaceEntity).order_by(TranslationNamespaceEntity.id)
        result = self._db.orm_execute(stmt, readonly=True)
        return result.to_list(row_fmt="dict") if result else []

    def list(self) -> List[Dict[str, Any]]:
        """List namespace records under the shared registry contract."""
        items: List[Dict[str, Any]] = []
        for row in self.list_namespaces():
            namespace = str(row["id"])
            items.append(
                normalize_registry_record(
                    row,
                    extras={
                        "main_lang": row.get("main_lang", "en"),
                        "languages": self.list_languages(namespace),
                        "entry_count": self.entry_count(namespace),
                    },
                )
            )
        return items

    def info(self, namespace: str) -> Optional[Dict[str, Any]]:
        """Return namespace metadata under the shared registry contract."""
        row = self.get_namespace(namespace)
        if row is None:
            return None
        return normalize_registry_record(
            row,
            extras={
                "main_lang": row.get("main_lang", "en"),
                "languages": self.list_languages(namespace),
                "entry_count": self.entry_count(namespace),
            },
        )

    def exists(self, namespace: str) -> bool:
        return self.get_namespace(namespace) is not None

    def remove(self, namespace: str) -> None:
        self.delete_namespace(namespace)

    def stale(self) -> List[Dict[str, Any]]:
        """Namespaces kept in DB but with no translated values."""
        items: List[Dict[str, Any]] = []
        for row in self.list():
            if int(row.get("entry_count") or 0) == 0:
                items.append(row)
        return items

    def get_index_snapshot(self, namespace: str) -> Optional[Dict[str, Any]]:
        """Load persisted matcher runtime index snapshot for a namespace.

        Snapshot payloads are compacted in storage using template ids.
        """
        stmt = sa.select(TranslationIndexEntity).where(TranslationIndexEntity.id == namespace)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        if row is None:
            return None
        return {
            "version": int(row.get("index_version") or 1),
            "gram_index": row.get("gram_index_json") or {},
            "residual_keys": row.get("residual_keys_json") or [],
            "pattern_keys_by_lang": row.get("pattern_keys_by_lang_json") or {},
        }

    def save_index_snapshot(self, namespace: str, snapshot: Dict[str, Any]) -> None:
        """Persist matcher runtime index snapshot for a namespace.

        Snapshot payloads should use compact template-id lists.
        """
        with self._write_lock:
            now = _now()
            entity = TranslationIndexEntity(
                id=namespace,
                index_version=int(snapshot.get("version") or 1),
                gram_index_json=snapshot.get("gram_index") or {},
                residual_keys_json=snapshot.get("residual_keys") or [],
                pattern_keys_by_lang_json=snapshot.get("pattern_keys_by_lang") or {},
                created_at=now,
                updated_at=now,
            )
            for stmt in entity.upsert_stmts():
                self._db.orm_execute(stmt)

    # -- template + value writes -------------------------------------- #

    def save_entry(self, namespace: str, source_key: str, lang: str, value: str) -> None:
        """Upsert one template + one value row."""
        with self._write_lock:
            now = _now()
            spec = TemplateSpec.parse(source_key)
            tid = _template_id(namespace, source_key)
            vid = _value_id(tid, lang)

            tmpl = TranslationTemplateEntity(
                id=tid,
                namespace=namespace,
                source_key=source_key,
                created_at=now,
                updated_at=now,
                **spec.to_db_dict(),
            )
            val = TranslationValueEntity(
                id=vid,
                template_id=tid,
                lang=lang,
                target_value=value,
                created_at=now,
                updated_at=now,
            )
            for stmt in tmpl.upsert_stmts():
                self._db.orm_execute(stmt)
            for stmt in val.upsert_stmts():
                self._db.orm_execute(stmt)

    def save_entries_batch(self, namespace: str, entries: List[Dict[str, str]]) -> int:
        """Batch-save ``[{"key", "lang", "value"}, ...]``. Returns count saved."""
        with self._write_lock:
            now = _now()
            count = 0
            for entry in entries:
                source_key, lang, value = entry["key"], entry["lang"], entry["value"]
                spec = TemplateSpec.parse(source_key)
                tid = _template_id(namespace, source_key)
                vid = _value_id(tid, lang)

                tmpl = TranslationTemplateEntity(
                    id=tid,
                    namespace=namespace,
                    source_key=source_key,
                    created_at=now,
                    updated_at=now,
                    **spec.to_db_dict(),
                )
                val = TranslationValueEntity(
                    id=vid,
                    template_id=tid,
                    lang=lang,
                    target_value=value,
                    created_at=now,
                    updated_at=now,
                )
                for stmt in tmpl.upsert_stmts():
                    self._db.orm_execute(stmt)
                for stmt in val.upsert_stmts():
                    self._db.orm_execute(stmt)
                count += 1
            return count

    def delete_entry(self, namespace: str, source_key: str, lang: str) -> None:
        """Delete a single value row. Garbage-collect orphan template if no values remain."""
        with self._write_lock:
            tid = _template_id(namespace, source_key)
            vid = _value_id(tid, lang)
            for stmt in TranslationValueEntity.remove_stmts(id=vid):
                self._db.orm_execute(stmt)

            # Check if template has any remaining values
            cnt = sa.select(sa.func.count()).select_from(TranslationValueEntity).where(TranslationValueEntity.template_id == tid)
            result = self._db.orm_execute(cnt, readonly=True)
            remaining = result.scalar() if result else 0
            if remaining == 0:
                for stmt in TranslationTemplateEntity.remove_stmts(id=tid):
                    self._db.orm_execute(stmt)

    def delete_language(self, namespace: str, lang: str) -> None:
        """Delete all values for a language in a namespace."""
        with self._write_lock:
            stmt = sa.select(TranslationTemplateEntity.id).where(TranslationTemplateEntity.namespace == namespace)
            result = self._db.orm_execute(stmt, readonly=True)
            tmpl_ids = [r["id"] for r in result.to_list(row_fmt="dict")] if result else []

            if tmpl_ids:
                del_stmt = sa.delete(TranslationValueEntity).where(
                    TranslationValueEntity.template_id.in_(tmpl_ids),
                    TranslationValueEntity.lang == lang,
                )
                self._db.orm_execute(del_stmt)

    # -- reads -------------------------------------------------------- #

    def load_namespace(self, namespace: str):
        """Load all templates and values for a namespace.

        Returns ``(templates: list[dict], values: list[dict])`` where each
        template dict has a ``"source_key"`` and each value dict has
        ``"source_key"``, ``"lang"``, ``"target_value"``.
        """
        # Templates
        stmt = sa.select(TranslationTemplateEntity).where(TranslationTemplateEntity.namespace == namespace)
        result = self._db.orm_execute(stmt, readonly=True)
        templates = result.to_list(row_fmt="dict") if result else []

        # Build id → source_key map
        id_to_key = {t["id"]: t["source_key"] for t in templates}

        # Values
        if id_to_key:
            stmt = sa.select(TranslationValueEntity).where(TranslationValueEntity.template_id.in_(list(id_to_key.keys())))
            result = self._db.orm_execute(stmt, readonly=True)
            raw_values = result.to_list(row_fmt="dict") if result else []
        else:
            raw_values = []

        # Attach source_key to each value for convenience
        values = []
        for v in raw_values:
            v["source_key"] = id_to_key.get(v["template_id"], "")
            values.append(v)

        return templates, values

    def load_namespace_bundle(self, namespace: str):
        """Load templates, values, and optional runtime index snapshot."""
        templates, values = self.load_namespace(namespace)
        snapshot = self.get_index_snapshot(namespace)
        return templates, values, snapshot

    def list_languages(self, namespace: str) -> List[str]:
        """Return distinct language codes for a namespace."""
        stmt = sa.select(TranslationTemplateEntity.id).where(TranslationTemplateEntity.namespace == namespace)
        result = self._db.orm_execute(stmt, readonly=True)
        tmpl_ids = [r["id"] for r in result.to_list(row_fmt="dict")] if result else []
        if not tmpl_ids:
            return []

        stmt = sa.select(sa.distinct(TranslationValueEntity.lang)).where(TranslationValueEntity.template_id.in_(tmpl_ids)).order_by(TranslationValueEntity.lang)
        result = self._db.orm_execute(stmt, readonly=True)
        return [r["lang"] for r in result.to_list(row_fmt="dict")] if result else []

    def entry_count(self, namespace: str, lang: Optional[str] = None) -> int:
        stmt = sa.select(TranslationTemplateEntity.id).where(TranslationTemplateEntity.namespace == namespace)
        result = self._db.orm_execute(stmt, readonly=True)
        tmpl_ids = [r["id"] for r in result.to_list(row_fmt="dict")] if result else []
        if not tmpl_ids:
            return 0

        cnt = sa.select(sa.func.count()).select_from(TranslationValueEntity).where(TranslationValueEntity.template_id.in_(tmpl_ids))
        if lang is not None:
            cnt = cnt.where(TranslationValueEntity.lang == lang)
        result = self._db.orm_execute(cnt, readonly=True)
        return result.scalar() if result else 0

    def get_entries(self, namespace: str, lang: Optional[str] = None, prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        """Load entries with source_key, lang, target_value. Optionally filter."""
        stmt = sa.select(TranslationTemplateEntity).where(TranslationTemplateEntity.namespace == namespace)
        if prefix:
            like_pattern = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
            stmt = stmt.where(TranslationTemplateEntity.source_key.like(like_pattern))

        result = self._db.orm_execute(stmt, readonly=True)
        templates = result.to_list(row_fmt="dict") if result else []
        if not templates:
            return []

        id_to_key = {t["id"]: t["source_key"] for t in templates}
        v_stmt = sa.select(TranslationValueEntity).where(TranslationValueEntity.template_id.in_(list(id_to_key.keys())))
        if lang is not None:
            v_stmt = v_stmt.where(TranslationValueEntity.lang == lang)

        result = self._db.orm_execute(v_stmt, readonly=True)
        raw = result.to_list(row_fmt="dict") if result else []

        out = []
        for v in raw:
            out.append(
                {
                    "key": id_to_key.get(v["template_id"], ""),
                    "lang": v["lang"],
                    "value": v["target_value"],
                }
            )
        return out

    def get_value(self, namespace: str, lang: str, key: str) -> Optional[str]:
        """Get a single translated value."""
        tid = _template_id(namespace, key)
        vid = _value_id(tid, lang)
        stmt = TranslationValueEntity.get_stmt(vid)
        result = self._db.orm_execute(stmt, readonly=True)
        row = result.first() if result else None
        return row["target_value"] if row else None

    def clear(self) -> int:
        """Clear all translation namespaces/templates/values/indexes.

        Returns:
            int: Number of translation value rows removed.
        """
        with self.write_tx():
            cnt_stmt = sa.select(sa.func.count()).select_from(TranslationValueEntity.__table__)
            cnt_result = self._db.orm_execute(cnt_stmt, readonly=True)
            count = int(cnt_result.scalar() if cnt_result else 0)

            # Delete in dependency order.
            for entity in (
                TranslationValueEntity,
                TranslationIndexEntity,
                TranslationTemplateEntity,
                TranslationNamespaceEntity,
            ):
                for stmt in entity.clear_stmts():
                    self._db.orm_execute(stmt)
        return count
