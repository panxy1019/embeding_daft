# Update Log

## v0.9.4.dev0 (2026-04-10)

- **_Major Refactor_: AgentHeaven is now migrated to a unified database-backed configuration system. Compared to the previous file-based config, the new system provides better multi-user support, versioning and serverless context management.** Use `CM_AHVN` for new configurations, ``HEAVEN_CM`` is now deprecated.

- **_Major Refactor_: `LLM` is moved to `ahvn.utils.llm` and refactored using the new `ConfigEngine`. Config resolution behaviors are changed (e.g., `default_model` is deprecated, OmegaConf is used for resolving environment variables, LLM configs parsing are now idempotent, etc.), but the LLM API is mostly compatible with the previous version.**

- **_Major Refactor_: `Database` class is completely refactored using the new `ConfigEngine`, together with multiple stability fixes and new features including: global connection pooling, better multi-backend support, superuser connections, comments, ORM table creation, enhanced error handling, enhanced transactions, and more built-in feature functions and dialects.**

- **_Major Refactor_: `ahvn` CLI now uses typer instead of click, with a more intuitive command structure and better user experience. Aliases are added for common commands.**

- **_Feature_: new `Capsule` system for function persistence, replacing the legacy `serialize_func` as a more robust and flexible solution. Refactored `UKF`, `ToolSpec`, and added new `Toolkit` via Capsule-based management.**

- **_Major Refactor_: new prompt system `PromptSpec` for defining and managing prompts with versioning, translation support, and template-based generation. Completely replaced the legacy Jinja + Babel solution via the aforementioned Capsule system.**

- **_Feature_: new `ahvn tr` CLI command for translation management, allowing users to register terms and their translations in different languages, and retrieve them in their code or prompts. Human and LLM elicitation are both supported for missing translations.**

- **_Feature_: new `ahvn mcp` CLI command for managing MCP toolkits and serving MCP servers (supported via the refactored `ToolSpec` and `Toolkit` class). Currently supporting `db`, `llm` and `config`. More toolkits to come.**

- _Enhancement_: Introduced `pyi` files for type hinting and IDE support for lazy-loaded packages. The dependency management and lazy loading mechanism would be refactored soon.

- _Enhancement_: `Parallelized` now supports async parallelization.

- _Enhancement_: LLM `embed` now supports `num_threads` and `batch_size` to control parallel and batch embedding (can be combined) for better performance. It also deduplictes in-batch inputs to avoid redundant embedding calls, and reconstructs the output in the original order.

- _Feature_: `KnowledgeUKFT` construction now supports `from_desc` to construct from text with links to other UKFTs.

- _Enhancement_: `DAACKLEngine` now supports parallel rebuild/query using via duplicating ac.

- _Deprecate_: Unified all function-related interfaces to use `func` instead of `function` and `sig` instead of `signature` for consistency and brevity.

- _Deprecate_: `prefer_backticks=True` is now the default for SQL prettification and transpilation when the backend supports it, as it is safer for JSON-based function calling used by LLM tool use (quotes lead to escaping issues and harder parsing). The `prefer_backticks` parameter is still supported to override this behavior when necessary.

<br/>

## v0.9.3.dev1 (2026-02-05)

- **_Feature_: Integrated support for `SkillUKFT`.**

- **_Feature_: Refactored `AgentSpec` for ease of extension and the use of skills. More built-in agent specs to come.**

- **_Deprecate_: Refactored the entire `rnd_utils` for efficiency, stability and batching. Now `Philox` is the default RNG backend, with `StableRNG` as a high-level wrapper for stable random generation.**

- **_Deprecate_: KLEngine now not only checks its own `condition`, but also `full_condition` from all its associated storages before performing upsert/insert operations. This ensures that KLEngines respect the conditions defined at the storage level as well.**

- **_Enhancement_: UKF descriptions and short descriptions are both upgraded to use UKFLongTextType.**

- _Deprecate_: `md5hash` now skips serialization for basic python types for performance improvement. This may cause certain hash mismatches if the previous version hashed the serialized form of basic types.

- _Enhancement_: `Parallelized` with `num_threads <= 0` now completely avoids using threads, behaving exactly like sequential function calls without entering `ThreadPoolExecutor`.

- _Enhancement_: duckdb now uses `SET preserve_insertion_order=false;` by default to speedup inserts and reduce memory usage.

- _Deprecate_: `Parallelized` now defaults to `progress=NoProgress` to avoid unnecessary overhead when progress reporting is not needed.

- _Deprecate_: `KLEngine.search` now returns KL search metadata that contains `args_repr` and `kwargs_repr` instead of the actual args and kwargs to avoid potential serialization issues.

- _Feature_: `lreshape` utility added for reshaping lists of arbitrary depth and size.

- _Feature_: `dset` now supports using `[-]` to append to a list.

- _Feature_: `Database.browse` to support filtering, ordering, limit and offset for robust data browsing.

- _Feature_: `Database` now supports more built-in feature functions like `col_percentile`, `col_agg`, etc.

- _Feature_: preparing `Database` for connecting to oracle (e.g., empty strings treated as NULLs), under development, not ready for production use yet.

- _Fix_: fixed `DatabaseKLStore` creating multiple schemas with shared metadata across adapters bug.

- _Fix_: updated `ExperienceUKFT` to use the new `prompt_instance.jinja` template for correct prompt generation.

- _Fix_: fixed `Database.tab_cols(tab_id, full_info=True)` returning dictionary with wrong "col_name" key.

- _Fix_: fixed json serialization and deserialization of `date` type.

- _Fix_: fixed `ConfigManager` not creating config file on first run bug.

<br/>

## v0.9.3.dev0 (2026-01-04)

- **_Feature_: LLM now supports non-streaming inference for structured output and tool use, by setting the key `enforce_non_stream_structured: true` as provider default args to be compatible with providers that do not support streaming with tools or structured outputs. The API still yields results as a generator for compatibility.**

- _Enhancement_: hpj now supports `%` to get package folder path.

- _Enhancement_: rnd utils are now truly random when enforcing `seed=None` (get seed from time), otherwise deterministic, and no matter what, does not interfere with the global random state. Also renamed `stable_rndint` -> `stable_rnd_int`; changed interface to `stable_rnd_vector`; added `stable_rnd_str`.

- _Fix_: robust `autotask` output parsing

- _Fix_: llamaindex vdb parallel initialization dummy conflict bugfix

<br/>

## v0.9.2 (2025-12-23)

- **_Feature_: A preliminary version for `AgentSpec` is added (temporarily) for fast prototyping of agents with tools, to be standardized in the following releases**

- **_Feature_: `LLM` now supports `structured` for structured outputs (requires backend to support streaming for now) and `delta_messages`. Meanwhile, `messages` is now compatible with streaming as well**

- **_Feature_: `LLM` inference with tool use now supports kwargs `repair_tool_calls=True` (default behavior), which automatically fixes malformed tool call arguments based on the provided `ToolSpec`s' arguments**

- **_Feature_: `auto*` now supports ExperienceUKFT, KLStore, KLEngine, and KLBase as inputs, with new `search_args`, `ExampleType`, and `ExampleSource` interfaces**

- **_Feature_: `ahvn config` CLI now supports `--cwd/-c` to specify which working directory to use for local config operations**

- **_Feature_: `ahvn config show` CLI now supports adding optional positional argument to specify a sub-key to show only that part of the config**

- **_Feature_: `BaseUKF` now supports `set`, `unset`, `setdef` besides `get`**

- **_Feature_: `BaseUKF.clone` now supports `upd_<field>` for updating iterable fields instead of overwriting**

- _Enhancement_: `Database` now supports `pool` args for robsut connection management

- _Feature_: `DAACKLEngine` now supports custom file (for storing metadata and synonyms) encoding via `encoding` parameter

- _Feature_: `HEAVEN_KB.get_prompt` now supports specifying additional search facets via `**kwargs` to disambiguate prompts with the same name

- _Feature_: `delta_messages` and `gather_stream` utilities added to facilitate streaming LLM queries

- _Feature_: `ahvn pj <path>` CLI command added to view the `hpj` path

- _Feature_: `ORMUKFAdaptor` now supports `main_table_name()`, `dims_table_name(dim)`, and `table_names()` methods

- _Feature_: config now adds `llm.litellm_debug` option to control LiteLLM debug mode separately (only effective when `core.debug` is also `True`)

- _Deprecate_: `raise_mismatch` with `mode='ignore'` now returns the original value directly, while ``mode='match'`` returns the suggestion

- _Deprecate_: `LLMChunk` is no longer exposed (renamed to `_LLMChunk`) as it is an internal state for implementing `LLM.stream`

- _Deprecate_: renamed `Cache.remove(func, **kwargs)` -> `Cache.unset(func, **kwargs)` to correspond to `Cache.set`, with the new `Cache.remove(entry)` corresponding to `Cache.add`

- _Deprecate_: `auto*` prompt composers' defaults now come before user-provided descriptions/instructions instead of after

- _Deprecate_: changed `dset/dunset` behavior when `key_path=None`

- _Fix_: default encoding now correctly reads from `core.encoding` in `HEAVEN_CM` instead of `encoding`

- _Fix_: fixed database adaptor index creation when some fields are aliased, expecting tremendous speedup when using `FacetKLEngine` on large datasets

- _Fix_: milvus vdb `alias` connection support

- _Fix_: improved system prompts translation

- _Fix_: system prompts now correctly handle `input_schema` and `output_schema`

- _Fix_: fixed `copy_dir` errors when `mode='skip'`

- _Fix_: `parse_md` now correctly handles incomplete streaming and nested markdown structures

<br/>

## v0.9.2.dev1 (2025-12-09)

- **_Feature_: new `ScanKLEngine`, which brute-force scans all entries in the KLStore for search, useful for small datasets and testing**

- **_Feature_: `HEAVEN_KB` which stores built-in ahvn ukfs, as a first attempt towards AgentHeaven's self-containment**

- **_Feature_: `dmerge` (used in ConfigManager) now supports overwriting nested dictionaries with the special key `_OVERWRITE_`**

- **_Feature_: Progress bar utils is added to AgentHeaven utils for a unified callback progress reporting system**

- **_Feature_: `system/prompt.jinja` now supports `toolspecs` to render tool specifications in prompts with instruction on text-based function calling**

- **_Feature_: `autocode/autofunc/autotask` prompts are converted to `PromptUKFT`, which now supports `format` and `bind` and have altered composers**

- **_Feature_: `BaseUKF` now supports `get` to retrieve nested values from `content_resources` using dot-separated key paths**

- **_Fix_: LLM tool calling now properly parses `index` (missing for backends like `vllm`) for merging tool call deltas**

- _Feature_: `ToolSpec` now supports `to_func` to convert a `ToolSpec` back to a callable function with proper signature

- _Feature_: `funcwrap` utility added to wrap a function with the signature and metadata of another function

- _Feature_: `KLBase` now supports `default_engine` which is used when no engine is specified in `search`

- _Deprecate_: `KLBase` now interprets CRUD to `storages` and `engines` separately; if both are None, all storages and engines are used; if one is None, it is set to empty list if the other is non-empty, otherwise all.

- _Deprecate_: `klengine.batch_size` -> `klengine.sync_batch_size` for sync operations to clarify usage

- _Fix_: fixed `auto*` creating a different function signature than expected when `bind` is used, causing `Cache.memoize` to fail

- _Fix_: `system/prompt.jinja` now guarantees two blank lines between sections, even when some sections are omitted

- _Fix_: updated default LLM presets

- _Fix_: `ahvn chat` and `ahvn session` now default to appropriate presets (`chat`) if none specified

<br/>

## v0.9.2.dev0 (2025-12-02)

- **_Feature_: `utils.exts.auto*` functions now use a dynamic examples list, enabling cache-based imitation**

- **_Feature_: `KLEngine` now stores search args and returns in `r['kl'].metadata['search']` for each search result**

- _Feature_: `config copy` now supports copying all configs with user confirmation by passing no keyword arguments

- _Deprecate_: `ToolSpec.jsonschema` disabled strict mode to be compatible with optional parameters

- _Fix_: `BaseKLEngine.search` now respects the `_search` defaults when `include=None`

- _Fix_: `DAACKLEngine` now defaults to return `["id", "kl", "strs"]`, and correctly parses `strs`

- _Fix_: `VectorKLEngine` with custom `k_encoder` now properly skips the new `DummyUKFT` during encoding

- _Fix_: `VectorKLEngine` and `MongoKLEngine` now has safer batch encode/embed methods that handle empty lists

<br/>

## v0.9.1.dev1 (2025-11-26)

- **_Feature_: `LLM` now supports tool-based interactions and `LLM.tooluse` which is compatible with `ToolSpec`**

- **_Feature_: `ToolSpec` now supports decorating functions like `@ToolSpec(name="func")`**

- **_Deprecate_: `LLM`'s `n` parameter for batch inference is temporarily removed**

- **_Optimize_: Refactored dependency management with lazy imports**

- _Feature_: `KLStore` now supports `batch_get`, with `DatabaseKLStore` and `MongoKLStore` having efficient implementations

- _Feature_: `FacetKLEngine` and `MongoKLEngine` now supports `orderby` parameter in search methods

- _Feature_: `ConfigManager` now supports `config copy` and inheritance by other packages

- _Feature_: `ahvn session` bug fixes, safeguards, defaults and user experience improvements

- _Feature_: `UKF*TextType` now supports `max_length()`

- _Optimize_: Better inheritance behavior: ukf tags & type default

- _Optimize_: BaseUKF defaults to empty `content_composers` and `triggers` dict to reduce memory usage

- _Fix_: milvus vdb store collection was not fully loaded before dummy removal

- _Fix_: `babel init` now creates an empty `_locales.jinja` file if not existing

- _Fix_: `babel translate` now correctly handles multi-line strings in jinja templates

<br/>

## v0.9.1.dev0 (2025-11-21)

- Initial release

<br/>
