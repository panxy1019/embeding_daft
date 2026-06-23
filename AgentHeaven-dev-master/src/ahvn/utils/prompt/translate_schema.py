"""\
ORM entities for the translation subsystem.

Four normalized tables:
- ``translation_namespaces``  — one row per namespace
- ``translation_templates``   — one row per source key (pattern metadata lives here)
- ``translation_values``      — one row per (template, lang) translated value
- ``translation_indexes``     — one row per namespace runtime index snapshot
"""

__all__ = [
    "TranslationNamespaceEntity",
    "TranslationTemplateEntity",
    "TranslationValueEntity",
    "TranslationIndexEntity",
]

import sqlalchemy as sa
from sqlalchemy import Column, Index

from ..db.types import (
    DatabaseIdType,
    DatabaseTextType,
    DatabaseJsonType,
    DatabaseTimestampType,
    ExportableEntity,
)


class TranslationNamespaceEntity(ExportableEntity):
    """Metadata for a translation namespace."""

    __tablename__ = "translation_namespaces"

    id = Column(DatabaseTextType(length=255), primary_key=True)
    main_lang = Column(DatabaseTextType(length=63), nullable=False, server_default="en")
    created_at = Column(DatabaseTimestampType(), nullable=True)
    updated_at = Column(DatabaseTimestampType(), nullable=True)

    __table_args__ = ({"extend_existing": True},)


class TranslationTemplateEntity(ExportableEntity):
    """One row per source key — stores pattern metadata once."""

    __tablename__ = "translation_templates"

    id = Column(DatabaseIdType(), primary_key=True)
    namespace = Column(DatabaseTextType(length=255), nullable=False)
    source_key = Column(DatabaseTextType(length=65535), nullable=False)
    is_pattern = Column(sa.Boolean, nullable=False, default=False)
    literals_json = Column(DatabaseJsonType(), nullable=True)
    placeholders_json = Column(DatabaseJsonType(), nullable=True)
    probe = Column(DatabaseTextType(length=65535), nullable=True)
    structurally_ambiguous = Column(sa.Boolean, nullable=False, default=False)
    created_at = Column(DatabaseTimestampType(), nullable=True)
    updated_at = Column(DatabaseTimestampType(), nullable=True)

    __table_args__ = (
        Index("idx_tt_ns_key", "namespace", "source_key", unique=True),
        Index("idx_tt_ns_pattern", "namespace", "is_pattern"),
        {"extend_existing": True},
    )


class TranslationValueEntity(ExportableEntity):
    """One row per (template, lang) — the actual translated string."""

    __tablename__ = "translation_values"

    id = Column(DatabaseIdType(), primary_key=True)
    template_id = Column(DatabaseIdType(), nullable=False)
    lang = Column(DatabaseTextType(length=63), nullable=False)
    target_value = Column(DatabaseTextType(length=65535), nullable=False)
    created_at = Column(DatabaseTimestampType(), nullable=True)
    updated_at = Column(DatabaseTimestampType(), nullable=True)

    __table_args__ = (
        Index("idx_tv_tmpl_lang", "template_id", "lang", unique=True),
        {"extend_existing": True},
    )


class TranslationIndexEntity(ExportableEntity):
    """Persisted runtime index snapshot for a namespace."""

    __tablename__ = "translation_indexes"

    id = Column(DatabaseTextType(length=255), primary_key=True)
    index_version = Column(sa.Integer, nullable=False, server_default="1")
    gram_index_json = Column(DatabaseJsonType(), nullable=True)
    residual_keys_json = Column(DatabaseJsonType(), nullable=True)
    pattern_keys_by_lang_json = Column(DatabaseJsonType(), nullable=True)
    created_at = Column(DatabaseTimestampType(), nullable=True)
    updated_at = Column(DatabaseTimestampType(), nullable=True)

    __table_args__ = ({"extend_existing": True},)
