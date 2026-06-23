---
description: "Use when writing, reviewing, refactoring, or generating Python code in any repository built upon AgentHeaven. Enforces AHVN style with source-only evidence: no stdlib os.path/json/pathlib/subprocess for covered concerns (use ahvn.utils.basic), no hard-coded tunables (use CM_*), no backward compatibility unless requested, short naming, canonical OOP vocabulary, compact Python, no embedded SQL strings, and repository-native lint/test gates."
applyTo: "**/*.py"
---

# AHVN Code Style (enforced)

Canonical guide: [CODE_STYLE.md](./CODE_STYLE.md) - **read it before writing or reviewing Python**.

These rules are **blocking**. Do not produce or approve code that violates them.

Operate in source-only mode: infer conventions from code and config files in the current workspace.

## Non-negotiable rules

1. **Never import `os.path`, `pathlib`, `json`, `yaml`, `pickle`, `subprocess`, `shutil`, or `hashlib` directly for paths / files / serialization / shell / hashing.** Use `ahvn.utils.basic.{path_utils, file_utils, serialize_utils, cmd_utils, hash_utils, log_utils, rnd_utils, request_utils}`.
    - Paths: `CM_*.pj("...", abs=True)`, `pj(...)`, `get_file_ext`, ...
    - JSON/YAML/Pickle: `load_json`/`dump_json`, `load_yaml`/`dump_yaml`, `load_pkl`/`dump_pkl` (and `loads_*`/`dumps_*` for strings).
    - Shell: `cmd(...)`.
2. **Never hard-code** examples, prompts, model names, paths, numeric thresholds, table/index names, codes, SQLs, or any "reasonable default" a user might later tweak. Route through `CM_AHVN` (or `CM_<PKG>` in downstream packages). Prompts, few-shots, SQL, and similar assets live in `src/<pkg>/resources/`.
3. **No backward compatibility.** Delete deprecated code, rename freely, break signatures, and update all call sites in the same PR. No `warnings.warn` shims, no "v1/v2" duplicates, unless the user explicitly asks.
4. **Short, consistent names.** Use the AHVN glossary: `KLStore` (not `KnowledgeStorage`), `KLEngine`, `KLBase`, `UKF`, `pj`, `sig2func`, `func2sig`, `db`, `vdb`, `mdb`, `emb`, `n_*`, `max_*`. Frequent symbols must be terse; invent no synonyms.
5. **Canonical method vocabulary only.** Use exactly these names where the concept applies — no synonyms:
    - `@classmethod from_dict`, `from_str`, `from_<other>`
    - `to_dict`, `to_str`
    - `clone(self, **updates)`
    - `get(key, default=None)` / `set(key, value)`
    - `__contains__`, `__iter__`, `__len__`, `__repr__`, `__str__`
    - Banned: `serialize`, `deserialize`, `as_dict`, `asdict`, `toJSON`, `fromJSON`, `dump`, `load`, `duplicate`, `copy_of`, `fetch`, `retrieve`.
6. **Compact Pythonic code.** Prefer comprehensions, ternaries, unpacking, guard clauses, `filter`/`map`/chained ops over explicit loops and nested `if`/`else`. Trade a little readability for fewer lines — but never compress regex/SQL/prompt literals (those go to `resources/`).
7. **Minimal error handling.** No broad `except`, no defensive `try/except` around one-liners, no `print` diagnostics. Use `ahvn.utils.basic.log_utils`; gate debug on `CM_AHVN.get("core.debug")`. Raise with context: `raise ValueError(f"unknown backend: {backend!r}")`.
8. **SQL via SQLAlchemy only** (ORM by default). Never embed SQL strings in `.py` files; if unavoidable, store the `.sql` under `src/<pkg>/resources/`.
9. **Always pass lint/format gates** via the repository-declared entrypoint discovered from source (`scripts/*`, `Makefile`, `pyproject.toml`, `README*`). Use direct `black` + `flake8` only when no wrapper exists.
10. **Always run tests via the repository-declared test entrypoint**. Use direct `pytest` only if it is the only declared entrypoint.

## OOP mental model

A user should predict a class's API from its name + one sentence. Public surface = minimum viable verbs; hide internals with `_underscore`. Do not leak framework types (SQLAlchemy rows, raw dicts) across module boundaries — return domain objects.

## Review gate

Before emitting code, mentally run §15 of [CODE_STYLE.md](./CODE_STYLE.md).
If any box fails, fix the code — do not ship it.

When unsure: obey the shorter rule, and prefer patterns already established in this repository's source modules under `src/**`.
