"""\
ORM entity for versioned prompt specifications.

Single table ``prompt_specs`` where each row is one (prompt_id, version)
pair.  The primary key is a deterministic hash of that composite key so
the standard ``ExportableEntity`` upsert/remove helpers work unchanged.
"""

__all__ = [
    "PromptSpecEntity",
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


class PromptSpecEntity(ExportableEntity):
    """One row per (prompt_id, version)."""

    __tablename__ = "prompt_specs"

    # PK = md5hash(prompt_id + "\x00" + str(version))
    id = Column(DatabaseIdType(), primary_key=True)
    prompt_id = Column(DatabaseTextType(length=255), nullable=False)
    version = Column(sa.Integer, nullable=False)
    checksum = Column(DatabaseTextType(length=63), nullable=True)
    qualname = Column(DatabaseTextType(length=2047), nullable=True)
    source_file = Column(DatabaseTextType(length=2047), nullable=True)
    source_code = Column(DatabaseTextType(length=65535), nullable=True)
    td_refs = Column(DatabaseJsonType(), nullable=True)
    metadata_json = Column(DatabaseJsonType(), nullable=True)
    created_at = Column(DatabaseTimestampType(), nullable=True)
    updated_at = Column(DatabaseTimestampType(), nullable=True)

    __table_args__ = (
        Index("idx_ps_pid_ver", "prompt_id", "version", unique=True),
        Index("idx_ps_pid", "prompt_id"),
        {"extend_existing": True},
    )
