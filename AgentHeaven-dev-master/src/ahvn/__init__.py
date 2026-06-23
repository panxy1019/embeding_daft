"""\
Top-level AgentHeaven package.

This package re-exports commonly used utilities and LLM helpers for convenience.
Subpackages are lazy-loaded — only `ahvn.utils.basic` and `ahvn.utils.klop` are
imported eagerly.

Note: Public API is defined primarily via subpackages. Import submodules directly
when you need fine-grained control.
"""

# Suppress deprecation warnings from third-party LLM dependencies
# These are internal warnings from litellm/llama_index that users shouldn't see
import warnings
from pydantic.warnings import PydanticDeprecatedSince211

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"litellm.*")
warnings.filterwarnings("ignore", category=PydanticDeprecatedSince211, module=r"litellm.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"llama_index.*")

import importlib

from .version import __version__

from . import utils
from .utils.klop import *

# ---------------------------------------------------------------------------
# Lazy imports: submodules accessible as `ahvn.<name>`
# ---------------------------------------------------------------------------
_SUBMODULES = [
    "adapter",
    "agent",
    "cache",
    "cli",
    "klbase",
    "klengine",
    "klstore",
    "resources",
    "tool",
    "ukf",
    "utils",
]

# ---------------------------------------------------------------------------
# Lazy imports: names -> relative submodule
# All heavy classes / functions are resolved on first access.
# ---------------------------------------------------------------------------
_EXPORT_MAP = {
    # --- klstore ---
    "BaseKLStore": ".klstore",
    "CacheKLStore": ".klstore",
    "CascadeKLStore": ".klstore",
    "DatabaseKLStore": ".klstore",
    "MongoKLStore": ".klstore",
    "VectorKLStore": ".klstore",
    # --- klengine ---
    "BaseKLEngine": ".klengine",
    "ScanKLEngine": ".klengine",
    "FacetKLEngine": ".klengine",
    "DAACKLEngine": ".klengine",
    "ShardedDAACKLEngine": ".klengine",
    "VectorKLEngine": ".klengine",
    "MongoKLEngine": ".klengine",
    # --- cache ---
    "CacheEntry": ".cache",
    "BaseCache": ".cache",
    "NoCache": ".cache",
    "DiskCache": ".cache",
    "JsonCache": ".cache",
    "InMemCache": ".cache",
    "CallbackCache": ".cache",
    "DatabaseCache": ".cache",
    "MongoCache": ".cache",
    # --- tool ---
    "ToolSpec": ".tool",
    # --- ukf ---
    "default_trigger": ".ukf",
    "default_composer": ".ukf",
    "BaseUKF": ".ukf",
    "UKFTypeRegistry": ".ukf",
    "HEAVEN_UR": ".ukf",
    "register_ukft": ".ukf",
    "UKF_TYPES": ".ukf",
    "UKFIdType": ".ukf",
    "UKFIntegerType": ".ukf",
    "UKFBooleanType": ".ukf",
    "UKFShortTextType": ".ukf",
    "UKFMediumTextType": ".ukf",
    "UKFLongTextType": ".ukf",
    "UKFTimestampType": ".ukf",
    "UKFDurationType": ".ukf",
    "UKFJsonType": ".ukf",
    "UKFSetType": ".ukf",
    "UKFTagsType": ".ukf",
    "tag_s": ".ukf",
    "tag_v": ".ukf",
    "tag_t": ".ukf",
    "ptags": ".ukf",
    "gtags": ".ukf",
    "has_tag": ".ukf",
    "has_related": ".ukf",
    "next_ver": ".ukf",
    "DummyUKFT": ".ukf",
    "KnowledgeUKFT": ".ukf",
    "ExperienceUKFT": ".ukf",
    "ResourceUKFT": ".ukf",
    "DocumentUKFT": ".ukf",
    "TemplateUKFT": ".ukf",
    "PromptUKFT": ".ukf",
    "ToolUKFT": ".ukf",
    # --- klbase ---
    "KLBase": ".klbase",
    # --- adapter ---
    "BaseUKFAdapter": ".adapter",
    "parse_ukf_include": ".adapter",
    "ORMUKFAdapter": ".adapter",
    "VdbUKFAdapter": ".adapter",
    "MongoUKFAdapter": ".adapter",
    # --- utils.llm ---
    "LLM": ".utils.llm",
    "LLMSpec": ".utils.llm",
    "LLMConfigEngine": ".utils.llm",
    "LLMResponse": ".utils.llm",
    "LLMIncludeType": ".utils.llm",
    "Message": ".utils.llm",
    "Messages": ".utils.llm",
    "normalize_tool_call": ".utils.llm",
    "parse_tool_args": ".utils.llm",
    "format_tool_call": ".utils.llm",
    "format_tool_calls": ".utils.llm",
    "exec_tool_calls": ".utils.llm",
    "repair_tool_call": ".utils.llm",
    "gather_assistant_message": ".utils.llm",
    "gather_stream": ".utils.llm",
    "format_messages": ".utils.llm",
    "get_litellm": ".utils.llm",
    "get_litellm_retryable_exceptions": ".utils.llm",
    # --- utils.db ---
    "DatabaseConfigSpec": ".utils.db",
    "DatabaseConfigEngine": ".utils.db",
    "DatabaseEngineRegistry": ".utils.db",
    "create_database_engine": ".utils.db",
    "create_database": ".utils.db",
    "split_sqls": ".utils.db",
    "transpile_sql": ".utils.db",
    "prettify_sql": ".utils.db",
    "compare_sqls": ".utils.db",
    "load_builtin_sql": ".utils.db",
    "SQLProcessor": ".utils.db",
    "ExportableEntity": ".utils.db",
    "DatabaseIdType": ".utils.db",
    "DatabaseTextType": ".utils.db",
    "DatabaseIntegerType": ".utils.db",
    "DatabaseBooleanType": ".utils.db",
    "DatabaseDurationType": ".utils.db",
    "DatabaseTimestampType": ".utils.db",
    "DatabaseJsonType": ".utils.db",
    "DatabaseSetType": ".utils.db",
    "DatabaseNfType": ".utils.db",
    "DatabaseVectorType": ".utils.db",
    "get_base": ".utils.db",
    "SQLCompiler": ".utils.db",
    "SQLResponse": ".utils.db",
    "Database": ".utils.db",
    "table_display": ".utils.db",
    # --- utils.vdb ---
    "parse_encoder_embedder": ".utils.vdb",
    "resolve_vdb_config": ".utils.vdb",
    "VectorDatabase": ".utils.vdb",
    "VectorCompiler": ".utils.vdb",
    # --- utils.mdb ---
    "resolve_mdb_config": ".utils.mdb",
    "MongoDatabase": ".utils.mdb",
    "MongoCompiler": ".utils.mdb",
    # --- utils.exts ---
    "ExampleType": ".utils.exts",
    "ExampleSource": ".utils.exts",
    "normalize_examples": ".utils.exts",
    "autoi18n": ".utils.exts",
    "autotask": ".utils.exts",
    "autofunc": ".utils.exts",
    "autocode": ".utils.exts",
    # --- agent ---
    "BaseAgentSpec": ".agent",
    "BasePromptAgentSpec": ".agent",
    "AgentStreamChunk": ".agent",
    # --- prompt ---
    "PM_AHVN": ".utils.prompt",
    "TR_AHVN": ".utils.prompt",
    "setup_system_prompts": ".utils.prompt",
    "ensure_system_prompts": ".utils.prompt",
    "get_system_prompt_spec": ".utils.prompt",
    # --- resources ---
    "AhvnKLBase": ".resources.ahvn_kb",
    "HEAVEN_KB": ".resources.ahvn_kb",
    "setup_heaven_kb": ".resources.ahvn_kb",
}


def __getattr__(name):
    # 1) Check basic utils (delegated to ahvn.utils.basic lazy map)
    from .utils.basic import _lazy_modules as _basic_map  # noqa: lazy ref

    if name in _basic_map:
        mod = importlib.import_module(_basic_map[name], "ahvn.utils.basic")
        return getattr(mod, name)

    # 2) Check subpackage-level exports
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)

    # 3) Check submodule access (e.g. `ahvn.klstore`)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
