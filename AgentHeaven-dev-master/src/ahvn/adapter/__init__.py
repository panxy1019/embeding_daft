import importlib

__all__ = [
    "BaseUKFAdapter",
    "parse_ukf_include",
    "ORMUKFAdapter",
    "VdbUKFAdapter",
    "MongoUKFAdapter",
]

from .base import *

_EXPORT_MAP = {
    "ORMUKFAdapter": ".db",
    "VdbUKFAdapter": ".vdb",
    "MongoUKFAdapter": ".mdb",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
