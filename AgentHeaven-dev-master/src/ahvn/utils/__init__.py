import importlib

from .basic import *
from .klop import *

_EXPORT_MAP = {
    # db
    "DatabaseConfigSpec": ".db",
    "DatabaseConfigEngine": ".db",
    "DatabaseEngineRegistry": ".db",
    "create_database_engine": ".db",
    "create_database": ".db",
    "split_sqls": ".db",
    "transpile_sql": ".db",
    "prettify_sql": ".db",
    "compare_sqls": ".db",
    "load_builtin_sql": ".db",
    "SQLProcessor": ".db",
    "ExportableEntity": ".db",
    "DatabaseIdType": ".db",
    "DatabaseTextType": ".db",
    "DatabaseIntegerType": ".db",
    "DatabaseBooleanType": ".db",
    "DatabaseDurationType": ".db",
    "DatabaseTimestampType": ".db",
    "DatabaseJsonType": ".db",
    "DatabaseSetType": ".db",
    "DatabaseNfType": ".db",
    "DatabaseVectorType": ".db",
    "get_base": ".db",
    "SQLCompiler": ".db",
    "SQLResponse": ".db",
    "Database": ".db",
    "table_display": ".db",
    # vdb
    "parse_encoder_embedder": ".vdb",
    "resolve_vdb_config": ".vdb",
    "BaseVdbType": ".vdb",
    "VdbIdType": ".vdb",
    "VdbTextType": ".vdb",
    "VdbIntegerType": ".vdb",
    "VdbBooleanType": ".vdb",
    "VdbDurationType": ".vdb",
    "VdbTimestampType": ".vdb",
    "VdbJsonType": ".vdb",
    "VdbVectorType": ".vdb",
    "VdbTagsType": ".vdb",
    "VdbSynonymsType": ".vdb",
    "VdbRelatedType": ".vdb",
    "VdbAuthsType": ".vdb",
    "VectorCompiler": ".vdb",
    "VectorDatabase": ".vdb",
    # mdb
    "resolve_mdb_config": ".mdb",
    "BaseMongoType": ".mdb",
    "MongoIdType": ".mdb",
    "MongoTextType": ".mdb",
    "MongoIntegerType": ".mdb",
    "MongoBooleanType": ".mdb",
    "MongoDurationType": ".mdb",
    "MongoTimestampType": ".mdb",
    "MongoJsonType": ".mdb",
    "MongoVectorType": ".mdb",
    "MongoTagsType": ".mdb",
    "MongoSynonymsType": ".mdb",
    "MongoRelatedType": ".mdb",
    "MongoAuthsType": ".mdb",
    "MONGO_FIELD_TYPES": ".mdb",
    "MONGO_VIRTUAL_FIELD_TYPES": ".mdb",
    "MongoCompiler": ".mdb",
    "MongoDatabase": ".mdb",
    # exts
    "ExampleType": ".exts",
    "ExampleSource": ".exts",
    "normalize_examples": ".exts",
    "autoi18n": ".exts",
    "autotask": ".exts",
    "autofunc": ".exts",
    "autocode": ".exts",
    # llm
    "LLM": ".llm",
    "LLMSpec": ".llm",
    "LLMConfigEngine": ".llm",
    "LLMResponse": ".llm",
    "LLMIncludeType": ".llm",
    "Message": ".llm",
    "Messages": ".llm",
    "normalize_tool_call": ".llm",
    "parse_tool_args": ".llm",
    "format_tool_call": ".llm",
    "format_tool_calls": ".llm",
    "exec_tool_calls": ".llm",
    "repair_tool_call": ".llm",
    "gather_assistant_message": ".llm",
    "gather_stream": ".llm",
    "format_messages": ".llm",
    "get_litellm": ".llm",
    "get_litellm_retryable_exceptions": ".llm",
    # capsule
    "Capsule": ".capsule",
    "CapsuleStore": ".capsule",
    "CapsuleORMEntity": ".capsule",
    "CapsuleError": ".capsule",
    "CapsuleCreationError": ".capsule",
    "CapsuleRestorationError": ".capsule",
    "CAPSULE_VERSION": ".capsule",
    "SUPPORTED_VERSIONS": ".capsule",
    "register_layer": ".capsule",
    "get_capsule_store": ".capsule",
    # prompt
    "fast_prompt_section": ".prompt",
}

_SUBMODULES = ["db", "vdb", "mdb", "exts", "llm", "capsule", "prompt"]


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
