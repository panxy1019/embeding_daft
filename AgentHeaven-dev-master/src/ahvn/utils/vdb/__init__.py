import importlib

from .vdb_utils import *

__all__ = [
    # vdb_utils
    "parse_encoder_embedder",
    "resolve_vdb_config",
    # types
    "BaseVdbType",
    "VdbIdType",
    "VdbTextType",
    "VdbIntegerType",
    "VdbBooleanType",
    "VdbDurationType",
    "VdbTimestampType",
    "VdbJsonType",
    "VdbVectorType",
    "VdbTagsType",
    "VdbSynonymsType",
    "VdbRelatedType",
    "VdbAuthsType",
    # compiler
    "VectorCompiler",
    # base
    "VectorDatabase",
]

_EXPORT_MAP = {
    "BaseVdbType": ".types",
    "VdbIdType": ".types",
    "VdbTextType": ".types",
    "VdbIntegerType": ".types",
    "VdbBooleanType": ".types",
    "VdbDurationType": ".types",
    "VdbTimestampType": ".types",
    "VdbJsonType": ".types",
    "VdbVectorType": ".types",
    "VdbTagsType": ".types",
    "VdbSynonymsType": ".types",
    "VdbRelatedType": ".types",
    "VdbAuthsType": ".types",
    "VectorCompiler": ".compiler",
    "VectorDatabase": ".base",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
