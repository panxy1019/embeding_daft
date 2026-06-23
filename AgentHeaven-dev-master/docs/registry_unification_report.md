# Registry Unification Report (2026-03-25)

## Scope
This report covers unification of:

- Translation registry (`tr`, `TranslationStore`)
- Capsule registry (`capsule`, `CapsuleStore`)
- Toolkit registry (`mcp`, `ToolkitStore` + `ToolkitManager`)

The goal is a shared registry mindset and CLI/API pattern, while keeping each feature's storage/config path bundled with its own domain.

## Unified Design Decisions

1. Unified config aliases are supported via `registry.<domain>.storage`, with legacy domain keys retained as fallback.
2. Shared mandatory record fields are:
   - `id`
   - `created_at`
   - `updated_at`
3. Domain-specific fields remain optional add-ons (for example: `checksum`, `manifest`, runtime metadata).
4. Cache policy is modular per registry (`CACHE_POLICY` add-on), not forced globally.
5. Registry operations follow a shared mindset:
   - register by stable `id`
   - mutate only through explicit write operations
   - persisted records survive original source/file deletion
   - stale items can be listed and removed manually

## Comparison Table

| Area | Translation | Capsule | Toolkit |
|---|---|---|---|
| Python standard record API | `list()`, `info(id)`, `exists(id)`, `remove(id)`, `stale()` | `list_items()/list()`, `info(id)`, `remove(id)`, `stale()` | `list_items()/list()`, `info(id)`, `remove(id)`, `stale()` |
| Store accessor pattern | `get_translation_store()` singleton | `get_capsule_store()` singleton | `get_toolkit_store()` singleton (manager defaults to it) |
| Domain writes | `save_entry`, `save_entries_batch`, `delete_entry`, `delete_namespace` | `save`, `delete`, `load` | `save`, `save_toolkit`, `rename`, manager `create/save/remove` |
| Transaction API | `write_tx()` + `tx(write=True)` | `tx(write=True)` | `tx(write=True)` |
| CLI core verbs | `list`, `info`, `get`, `set`, `remove`, `clear`, `import`, `export`, `stale` | `list`, `info`, `get`, `set`, `remove`, `clear`, `import`, `export`, `stale` | `list`, `info`, `get`, `set`, `remove`, `clear`, `import`, `export`, `stale` |
| CLI domain add-ons | `missing`, `fill` | `run`, `serve`, `show` | `create`, `show`, `rename`, `reset`, `serve`, `run` |
| Setup integration (`ahvn setup`) | initialized/reset via `AhvnConfigManager.setup` | initialized/reset via `AhvnConfigManager.setup` | initialized/reset via `AhvnConfigManager.setup` |
| Cache policy | matcher index snapshot add-on | none | manager runtime cache add-on |
| Parallelism/locking | write lock + transactional `write_tx()` | write lock for mutating ops | write lock for mutating ops; manager lock for runtime cache/process map |
| Stale detection | namespaces with zero entries | missing `source_file` | missing persisted source paths from factory args |

## Discrepancies Resolved

- Setup flow split removed: capsule store setup/reset moved into the same orchestration path as toolkit/translation in `AhvnConfigManager.setup`.
- Shared record contract added through `ahvn.utils.registry.contracts.normalize_registry_record`.
- Store-level write-locking made consistent across all three registries.
- Store transaction shape unified with `tx(write=True)` available across translation/capsule/toolkit stores.
- Store lifecycle accessor parity completed with `get_toolkit_store()` for toolkit persistence.
- Unified registry config aliases supported: `registry.translation.storage`, `registry.capsule.storage`, `registry.toolkit.storage` (legacy keys preserved as fallback).
- CLI output backend alignment completed: translation CLI now uses `CLIOutput` like capsule/toolkit CLIs.
- Toolkit storage legacy config fallback (`tool.manager.storage`) removed; toolkit storage uses only `tool.toolkit.storage`.
- CLI core verbs normalized with shared `get/set/import/export/stale` availability across registry CLIs.

## Breaking Changes

- `ahvn setup` no longer prints separate capsule-db init/reset messages from `ConfigCLI`; capsule lifecycle is managed by unified setup orchestration.
- `ToolkitStore` no longer falls back to `tool.manager.storage`; configure toolkit persistence via `tool.toolkit.storage`.
- `CapsuleCLI.do_rm` compatibility shim removed; use `do_remove` / CLI `remove` (`rm` alias still works via alias registry).

## Reference for Future Registries

When adding a new registry, follow this checklist:

1. Keep config path under feature namespace (not a generic registry namespace).
2. Expose shared record fields (`id`, `created_at`, `updated_at`) in list/info APIs.
3. Put extra fields (checksums/manifests/runtime internals) behind add-on metadata.
4. Provide explicit write methods only; no implicit mutation.
5. Add write locking for mutating operations.
6. Implement stale discovery as an explicit maintenance operation.
7. Expose the unified CLI core verbs and keep domain-specific commands as modular add-ons.
