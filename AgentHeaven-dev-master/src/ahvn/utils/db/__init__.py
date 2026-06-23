import importlib

from .db_utils import *
from .sql_processor import *  # noqa: F401,F403
from .sql_healer import *  # noqa: F401,F403

__all__ = [
    # spec
    "DatabaseConfigSpec",
    "DatabaseConfigEngine",
    # db_utils
    "SchemaIndex",
    "DatabaseEngineRegistry",
    "create_database_engine",
    "create_database",
    "drop_database",
    "split_sqls",
    "transpile_sql",
    "prettify_sql",
    "compare_sqls",
    "load_builtin_sql",
    "escape_sql_binds",
    "strip_sql_comments",
    "validate_sql",
    # sql_processor
    "SA_TO_SQLGLOT",
    "SQLGLOT_TO_SA",
    "sa_dialect_to_sqlglot",
    "sqlglot_dialect_to_sa",
    "SQLProcessor",
    "SQLGlotProcessor",
    "create_sql_processor",
    "SQLHealer",
    "create_sql_healer",
    # types
    "ExportableEntity",
    "DatabaseIdType",
    "DatabaseTextType",
    "DatabaseIntegerType",
    "DatabaseBooleanType",
    "DatabaseDurationType",
    "DatabaseTimestampType",
    "DatabaseJsonType",
    "DatabaseSetType",
    "DatabaseNfType",
    "DatabaseVectorType",
    "get_base",
    # compiler
    "SQLCompiler",
    # base
    "SQLResponse",
    "Database",
    "table_display",
]

_EXPORT_MAP = {
    "DatabaseConfigSpec": ".spec",
    "DatabaseConfigEngine": ".spec",
    "ExportableEntity": ".types",
    "DatabaseIdType": ".types",
    "DatabaseTextType": ".types",
    "DatabaseIntegerType": ".types",
    "DatabaseBooleanType": ".types",
    "DatabaseDurationType": ".types",
    "DatabaseTimestampType": ".types",
    "DatabaseJsonType": ".types",
    "DatabaseSetType": ".types",
    "DatabaseNfType": ".types",
    "DatabaseVectorType": ".types",
    "get_base": ".types",
    "SQLCompiler": ".compiler",
    "SQLResponse": ".base",
    "Database": ".base",
    "table_display": ".base",
    "SQLHealer": ".sql_healer",
    "create_sql_healer": ".sql_healer",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
