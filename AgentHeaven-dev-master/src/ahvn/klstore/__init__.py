import importlib

__all__ = [
    "BaseKLStore",
    "CacheKLStore",
    "CascadeKLStore",
    "DatabaseKLStore",
    "MongoKLStore",
    "VectorKLStore",
]

from .base import *

_EXPORT_MAP = {
    "CacheKLStore": ".cache_store",
    "CascadeKLStore": ".cascade_store",
    "DatabaseKLStore": ".db_store",
    "MongoKLStore": ".mdb_store",
    "VectorKLStore": ".vdb_store",
}

_SUBMODULES = ["cache_store", "cascade_store", "db_store", "mdb_store", "vdb_store"]


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
