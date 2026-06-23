import importlib

from .mdb_utils import *

__all__ = [
    # mdb_utils
    "resolve_mdb_config",
    # types
    "BaseMongoType",
    "MongoIdType",
    "MongoTextType",
    "MongoIntegerType",
    "MongoBooleanType",
    "MongoDurationType",
    "MongoTimestampType",
    "MongoJsonType",
    "MongoVectorType",
    "MongoTagsType",
    "MongoSynonymsType",
    "MongoRelatedType",
    "MongoAuthsType",
    "MONGO_FIELD_TYPES",
    "MONGO_VIRTUAL_FIELD_TYPES",
    # compiler
    "MongoCompiler",
    # base
    "MongoDatabase",
]

_EXPORT_MAP = {
    "BaseMongoType": ".types",
    "MongoIdType": ".types",
    "MongoTextType": ".types",
    "MongoIntegerType": ".types",
    "MongoBooleanType": ".types",
    "MongoDurationType": ".types",
    "MongoTimestampType": ".types",
    "MongoJsonType": ".types",
    "MongoVectorType": ".types",
    "MongoTagsType": ".types",
    "MongoSynonymsType": ".types",
    "MongoRelatedType": ".types",
    "MongoAuthsType": ".types",
    "MONGO_FIELD_TYPES": ".types",
    "MONGO_VIRTUAL_FIELD_TYPES": ".types",
    "MongoCompiler": ".compiler",
    "MongoDatabase": ".base",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
