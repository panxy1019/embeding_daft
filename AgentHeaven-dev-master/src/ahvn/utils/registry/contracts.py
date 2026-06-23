"""Shared contracts for persisted registries.

The registry contract intentionally keeps a minimal mandatory surface:

- `id`
- `created_at`
- `updated_at`

Everything else (checksum, manifests, cache hints, domain metadata) is
registry-specific and treated as an optional add-on field.
"""

from __future__ import annotations

__all__ = [
    "REGISTRY_STANDARD_FIELDS",
    "VERSIONED_REGISTRY_FIELDS",
    "normalize_registry_record",
    "next_version",
    "resolve_version",
]

from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

REGISTRY_STANDARD_FIELDS = ("id", "created_at", "updated_at")
VERSIONED_REGISTRY_FIELDS = ("id", "version", "checksum", "created_at", "updated_at")


def normalize_registry_record(
    data: Mapping[str, Any],
    *,
    id_key: str = "id",
    extras: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize a registry record to the shared minimal contract."""
    record: Dict[str, Any] = {
        "id": data.get(id_key),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }
    if extras:
        for key, value in extras.items():
            if key in REGISTRY_STANDARD_FIELDS:
                continue
            record[key] = value
    return record


def next_version(existing_versions: Sequence[int]) -> int:
    """Return the next integer version (max + 1, or 1 if empty)."""
    return max(existing_versions) + 1 if existing_versions else 1


def resolve_version(
    version: Union[int, str, None],
    available: Sequence[int],
) -> Optional[int]:
    """Resolve a version specifier to a concrete integer.

    - ``"latest"`` or ``None`` → max of *available*
    - ``int`` → validated against *available*
    - Returns ``None`` when *available* is empty or the int is not found.
    """
    if not available:
        return None
    if version is None or (isinstance(version, str) and version == "latest"):
        return max(available)
    version = int(version)
    return version if version in available else None
