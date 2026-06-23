import importlib

__all__ = [
    "BaseKLEngine",
    "ScanKLEngine",
    "FacetKLEngine",
    "DAACKLEngine",
    "GRAMKLEngine",
    "ShardedDAACKLEngine",
    "VectorKLEngine",
    "MongoKLEngine",
]

from .base import *

_EXPORT_MAP = {
    "ScanKLEngine": ".scan_engine",
    "FacetKLEngine": ".facet_engine",
    "DAACKLEngine": ".daac_engine",
    "GRAMKLEngine": ".gram_engine",
    "ShardedDAACKLEngine": ".sharded_daac_engine",
    "VectorKLEngine": ".vector_engine",
    "MongoKLEngine": ".mongo_engine",
}

_SUBMODULES = ["scan_engine", "facet_engine", "vector_engine", "mongo_engine", "daac_engine", "sharded_daac_engine", "gram_engine"]


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
