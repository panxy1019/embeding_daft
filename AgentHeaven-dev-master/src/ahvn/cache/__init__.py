"""\
Cache backends for AgentHeaven.

Includes in-memory, on-disk, and JSON-file caches, plus a no-op cache.
"""

import importlib

__all__ = [
    "CacheEntry",
    "BaseCache",
    "NoCache",
    "DiskCache",
    "JsonCache",
    "InMemCache",
    "CallbackCache",
    "DatabaseCache",
    "MongoCache",
]

from .base import CacheEntry, BaseCache
from .no_cache import NoCache
from .disk_cache import DiskCache
from .json_cache import JsonCache
from .in_mem_cache import InMemCache
from .callback_cache import CallbackCache

_EXPORT_MAP = {
    "DatabaseCache": ".db_cache",
    "MongoCache": ".mongo_cache",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
