# AHVN Downstream Code Style Guide

> Mandatory style guide for **every** engineer — human or AI — writing, reviewing,
> or auto-generating code in any repository built upon AgentHeaven (AHVN),
> including downstream packages, adapters, tools, experiments, and private repos.
>
> This guide is **source-first**: infer conventions from the code and config
> visible in the current workspace, without requiring external docs.
>
> This guide is **prescriptive and enforced**. Reviewers MUST block any PR that
> violates these rules unless the PR description contains an explicit, written
> waiver from the repository owner.

---

## 1. Golden Rules (TL;DR)

1. **No stdlib shortcuts for paths / files / JSON / shell.** Use `ahvn.utils.basic`.
2. **No hard-coded config.** Everything tunable lives behind a `CM_*` config manager.
3. **No backward compatibility.** Keep exactly one — the newest — version of every API.
4. **Think like the user.** A class name + one sentence must tell anyone what it does.
5. **Short names, consistent abbreviations.** `KLStore`, not `KnowledgeStorage`.
6. **Compact, idiomatic Python.** Comprehensions, ternaries, chained ops over loops.
7. **Minimal logging & `try/except`.** Raise loudly; handle only at system boundaries.
8. **Always `black` + `flake8` before commit.** Non-negotiable.

Everything below expands on these eight rules. When in doubt, obey the shorter rule.

---

## 2. Utilities: Never Touch the Standard Library Directly

The AHVN `utils.basic` layer wraps stdlib modules with *saner defaults, encoding
handling, alias resolution, and config awareness*. Using stdlib directly produces
bugs that never show up in dev but blow up in production (Windows paths, UTF‑8
BOMs, cwd drift, JSON float weirdness, shell-escaping, etc.).

### 2.1 Banned stdlib imports (and their replacements)

| ❌ Banned (stdlib)                      | ✅ Use instead                                     |
| -------------------------------------- | ------------------------------------------------- |
| `import os.path` / `os.path.join` / `os.makedirs` / `os.listdir` / `os.remove` | `ahvn.utils.basic.path_utils`, `ahvn.utils.basic.file_utils` |
| `import pathlib` / `Path(...)`         | `ahvn.utils.basic.path_utils.pj` (+ friends)      |
| `import json` / `json.load(s)` / `json.dump(s)` | `ahvn.utils.basic.serialize_utils` (`load_json`, `dump_json`, `loads_json`, `dumps_json`, plus `yaml`/`pkl`/`hex`/`b64` variants) |
| `import yaml`, `import pickle`         | `ahvn.utils.basic.serialize_utils`                |
| `import subprocess` / `os.system`      | `ahvn.utils.basic.cmd_utils`                      |
| `import shutil.copy*` / `shutil.rmtree`| `ahvn.utils.basic.file_utils` (`copy_file`, `copy_dir`, `delete_*`) |
| `import hashlib` / ad-hoc digests      | `ahvn.utils.basic.hash_utils`                     |
| `import logging` / `print` for diagnostics | `ahvn.utils.basic.log_utils`                   |
| `import random` / `uuid` (for non-crypto IDs) | `ahvn.utils.basic.rnd_utils`               |
| `import tempfile`, bespoke tmp dirs    | `ahvn.utils.basic.file_utils` (scratch dirs resolved via `CM_*.pj`) |
| Bare `requests.get(...)`               | `ahvn.utils.basic.request_utils`                  |
| `datetime.datetime.now()` scattered    | `ahvn.utils.basic` re-exports (`datetime`) with project-wide conventions |

**Rule of thumb:** before `import <stdlib>`, check the utility layer available
in the current workspace (for example `src/ahvn/utils/basic/` when present)
for a `*_utils.py` with the same concern. If one exists, use it. If none exists
and the utility layer is editable, add one there; otherwise add a project-local
wrapper helper rather than sprinkling raw stdlib across business code.

### 2.2 Canonical path construction

```python
# ❌ Forbidden
import os
p = os.path.join(os.path.expanduser("~"), ".ahvn", "cache", "x.json")

# ✅ Required
from ahvn.utils.basic.config_utils import CM_AHVN
p = CM_AHVN.pj("cache/x.json", abs=True)
```

`pj` (= "path join") is the single canonical way to build paths. It handles
aliases (`&`, `@`, `~`), absolute resolution, and cross-platform separators.

### 2.3 Canonical (de)serialization

```python
# ❌ Forbidden
import json
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

# ✅ Required
from ahvn.utils.basic.serialize_utils import load_json, dump_json
data = load_json(path)
dump_json(data, path)                 # atomic write, utf-8, indent=4 by default
```

The same rule applies to YAML, Pickle, Hex, and Base64 — use the paired
`load_*` / `dump_*` (or `save_*`) helpers.

### 2.4 Shell & subprocess

```python
# ❌ Forbidden
os.system("pytest -x")
subprocess.check_output(["git", "status"], shell=True)

# ✅ Required
from ahvn.utils.basic.cmd_utils import cmd
cmd("pytest -x")                  # handles quoting, logging, exit codes
```

---

## 3. Configuration: One Source of Truth, Zero Magic Numbers

Anything a reader might later want to tweak — a default, a prompt template,
a model name, a numeric threshold, a path, an example string, a feature flag
— **must not be a literal in code**.

### 3.1 The `CM_*` pattern

Each package exposes exactly one config manager named `CM_<PKG>`:

| Package        | Manager     |
| -------------- | ----------- |
| AgentHeaven    | `CM_AHVN`   |
| RubikSQL       | `CM_RUBIK`  |
| (downstream)   | `CM_<PKG>`  |

Usage:

```python
# CM_AHVN is the canonical config manager for the AgentHeaven package.
# Downstream packages follow the same pattern (CM_RUBIK, CM_<PKG>, etc.).
timeout   = CM_AHVN.get("llm.timeout", 60)      # typed get with default
CM_AHVN.set("llm.timeout", 120)                 # programmatic override
root      = CM_AHVN.pj("workspace", abs=True)   # path aliasing
is_debug  = CM_AHVN.get("core.debug")
```

### 3.2 Rules

1. **No literal defaults in hot code.** If a value appears in `def foo(x=42)`,
   ask: "would a user ever want to change 42?". If yes → move to config.
2. **No hard-coded example strings or prompt fragments** in `.py` files.
   Prompts, few-shots, and templates belong in `src/<pkg>/resources/` and
  are loaded via the config manager and in-repo resource loaders.
3. **No hard-coded paths.** Every path goes through `CM_AHVN.pj(...)` (or
   `CM_<PKG>.pj(...)` in downstream) with aliases; never concatenate raw strings.
4. **No hard-coded model / provider / table / index names.** All naming
   lives in config or a typed registry.
5. **Changing the default config template** requires running the repository's
  bootstrap/sync setup entrypoint (for example `bash secret/setup.bash` or
  equivalent) so local user config is regenerated.

For detailed usage of the config system, inspect `CM_AHVN` implementation at
`ahvn.utils.basic.config_utils` and call sites in the current source tree.

---

## 4. OOP Mental Model: Design for the User's First Guess

A new user should be able to read a class name and a one-line description and
**predict the API within 3 sentences**. If they can't, rename or redesign.

### 4.1 Class design checklist

- **Name = role.** `KLStore`, `ToolSpec`, `CacheEntry`, `PromptSpec` — not
  `StorageHandlerManager` or `AbstractThingFactoryImpl`.
- **Public surface = minimum viable verbs.** Hide internals behind `_underscore`.
- **No leaking framework types.** A `KLStore` returns domain objects, not
  raw SQLAlchemy rows or raw JSON blobs.
- **Composition over inheritance** except where a clear `is-a` hierarchy
  matches the user's mental model (e.g. `Cache` → `JsonCache`, `DiskCache`).

### 4.2 Unified method vocabulary

Every class that conceptually supports one of these operations **must** use
the canonical name — no synonyms, no prefixes, no suffixes.

| Concept                          | Canonical method                           |
| -------------------------------- | ------------------------------------------ |
| Construct from a dict           | `@classmethod from_dict(cls, data, ...)`   |
| Construct from a string/JSON    | `@classmethod from_str(cls, s, ...)`       |
| Construct from another instance | `@classmethod from_X(cls, x, ...)`         |
| Export to a dict                | `to_dict(self) -> Dict[str, Any]`          |
| Export to a string              | `to_str(self, ...) -> str`                 |
| Export to JSON                  | prefer `to_dict()` + `dumps_json(...)`     |
| Shallow / parametric copy        | `clone(self, **updates) -> Self`          |
| Read / write a field             | `get(key, default=None)` / `set(key, value)` |
| Membership / presence            | `__contains__` (i.e. `key in obj`)         |
| Iteration                        | `__iter__` over the most natural unit      |
| Length                           | `__len__` over the most natural count      |
| String representation            | `__repr__` (debug) and `__str__` (user)    |

**Banned synonyms:** `serialize`, `deserialize`, `as_dict`, `asdict`,
`toJSON`, `fromJSON`, `dump`, `load`, `duplicate`, `copy_of`, `fetch`,
`retrieve`, `lookup`. Pick the canonical name above.

### 4.3 Constructors and factories

- `__init__` takes the *authoritative, minimal* set of fields.
- All other construction paths are `@classmethod from_*`.
- Never require more than ~5 positional args; use keyword-only args past that.

---

## 5. No Backward Compatibility — Ever

Unless the repo owner explicitly says "keep the old API around":

- **Delete deprecated code.** No `# DEPRECATED`, no `warnings.warn(...)` shims,
  no duplicate "v1 / v2" modules.
- **Rename freely** when a better name emerges. Fix all call sites in the same
  PR. CI must be green on the new name.
- **Break signatures freely.** Update every call site. Do not accept
  `*args, **kwargs` just to "not break callers".
- **One configuration schema at a time.** When the schema changes, bump the
  default config file and update the repository's config bootstrap entrypoint.
- **Migrations, when truly necessary,** are one-shot scripts under
  `scripts/`, not permanent branches in the library.

The cost of carrying legacy is paid by every future reader. We do not pay it.

---

## 6. Naming: Short, Consistent, Predictable

Brevity is a feature. Reviewers will shorten overly verbose names on sight.

### 6.1 General rules

- **The more frequent the symbol, the shorter its name.** Top-level classes
  and hot-path functions must be terse; leaf helpers may be longer.
- **Use project-wide abbreviations consistently** (see §6.2). No synonyms.
- **snake_case** for functions / modules / variables; **PascalCase** for
  classes; **SCREAMING_SNAKE_CASE** for module-level constants & singletons
  like `CM_X`, `<PKG>_KB`.
- **No Hungarian notation, no type suffixes** (`user_list`, `user_dict`).
  Use types for types.
- **No underscores-as-spaces** in user-facing strings.

### 6.2 Canonical abbreviations (AHVN glossary)

| Long form                 | Abbreviation       | Example                          |
| ------------------------- | ------------------ | -------------------------------- |
| Knowledge                 | `kl`               | `klstore`, `klengine`, `klbase`  |
| Knowledge Base            | `KLBase`           | `HEAVEN_KB`                      |
| Knowledge Store           | `KLStore`          | not `KnowledgeStorage`           |
| Knowledge Engine          | `KLEngine`         |                                  |
| Unified Knowledge Format  | `UKF`              | `BaseUKF`, `ukf_to_dict`         |
| Configuration Manager     | `CM_*`             | `CM_AHVN`, `CM_RUBIK`            |
| Database                  | `db` / `DB`        | `db_cache`, `DbAdapter`          |
| Vector Database           | `vdb` / `VDB`      | `vdb_adapter`                    |
| MongoDB                   | `mdb` / `MDB`      | `mdb_cache`                      |
| Path join                 | `pj`               | `CM_*.pj(...)`                   |
| Signature → function      | `sig2func`         | not `convert_signature_to_function` |
| Function → signature      | `func2sig`         |                                  |
| Specification             | `Spec` / `_spec`   | `ToolSpec`, `PromptSpec`         |
| Configuration             | `cfg` (var), `config` (module) |                        |
| Embedding                 | `emb`              |                                  |
| Number / count            | `n_*`              | `n_retries`, `n_tokens`          |
| Maximum                   | `max_*`            | `max_len`, never `maximum_length`|
| Temporary                 | `tmp`              |                                  |
| Result / response         | `res` / `resp`     |                                  |

For translations (EN <-> ZH), consult a repository-local glossary when
available (for example files matching `**/i18n*.md` or `**/*glossary*`).
If no glossary exists, reuse terms already present in source code/docs.
If you invent an abbreviation, add it here in the same PR.

### 6.3 Functions

- Verb first: `load_json`, `parse_spec`, `render_prompt`, `sig2func`.
- Pure transforms: `<src>_to_<dst>` or `<src>2<dst>` for short, e.g. `dict2yaml`.
- Predicates: `is_*`, `has_*`, `can_*`, returning `bool`.
- Side-effecting I/O: `load_*` / `dump_*` / `save_*` / `delete_*` / `copy_*`.

---

## 7. Compact Python

Write dense, idiomatic Python. Lines of code are a liability; clarity per line
is the metric. If compressing costs significant readability, don't — but aim
to compress first, expand only on review feedback.

### 7.1 Prefer comprehensions & chained ops

```python
# ❌
out = []
for x in items:
    if x.active:
        out.append(x.name.lower())

# ✅
out = [x.name.lower() for x in items if x.active]
```

### 7.2 Prefer ternaries over tiny if/else

```python
# ❌
if flag:
    mode = "fast"
else:
    mode = "safe"

# ✅
mode = "fast" if flag else "safe"
```

### 7.3 Prefer `filter` / `map` / generator chains where they read cleanly

```python
total = sum(e.cost for e in entries if e.billable)
names = ",".join(sorted({u.name for u in users}))
```

### 7.4 Use unpacking, not indexing

```python
first, *rest = xs
a, b = point
cfg = {**defaults, **overrides}
```

### 7.5 Guard clauses, not nested `if`

```python
def handle(req):
    if not req.valid:
        return None
    if req.cached:
        return req.cache
    ...
```

### 7.6 Use `ahvn.utils.basic.*` "shortcut" re-exports

`ahvn.utils.basic` already re-exports `datetime`, `deepcopy`, `dataclass`,
`field`, `ABC`, `abstractmethod`, `defaultdict`, and the typing primitives.
Prefer `from ahvn.utils.basic import dataclass, Dict, List` over sprinkling
half a dozen separate imports.

### 7.7 What *not* to compress

- Complex boolean expressions → name them: `is_ready = ... and ...`.
- Nested comprehensions deeper than two levels → unroll.
- Regex / SQL / prompt strings → move to `resources/` (see §3).

---

## 8. Errors, Logging, and Exception Handling

- **Raise loudly, catch narrowly.** Only catch exceptions you can genuinely
  recover from, at a well-defined system boundary (CLI entry, HTTP handler,
  tool execution). Never `except Exception: pass`.
- **No defensive `try/except` around one-liners.** Let it crash; the
  traceback is information.
- **No `print` for diagnostics.** Use `ahvn.utils.basic.log_utils`. Debug
  output is gated on `CM_*.get("core.debug")`.
- **Errors carry context.** `raise ValueError(f"unknown backend: {backend!r}")`,
  not `raise ValueError("error")`.
- **No silent fallbacks** for user-provided inputs (paths, configs, model
  names). Fail fast.

---

## 9. Database & SQL

- **Never embed SQL strings in Python source.** Use SQLAlchemy Core / ORM.
- **ORM by default.** Drop to Core only when ORM is demonstrably insufficient.
- **If a raw query is truly needed**, put the SQL in `src/<pkg>/resources/`
  as a `.sql` file and load it via the resource registry.
- Connection strings, table names, index names → config (§3), never literals.

---

## 10. Resources, Prompts, and Examples

- Every prompt template, few-shot example, seed dataset, JSON schema,
  SQL snippet, or similar asset goes under `src/<pkg>/resources/`.
- Code loads resources through the config manager (paths via `CM_*.pj`,
  values via `CM_*.get`), never via `open(__file__/...)` tricks.
- Resources are treated as source code: reviewed, versioned, and subject
  to this style guide (naming, brevity, no redundancy).

---

## 11. Tests

- Unit tests go under `tests/unit/<module>/test_*.py`.
- **Never invoke `pytest` directly** when the repository already provides a
  test wrapper script or task.
- Use the repository test entrypoint discovered from source
  (`scripts/test*`, `Makefile`, `pyproject.toml`, `README*`) so environment,
  markers, and coverage are consistent.
- If no wrapper/entrypoint exists, use `pytest` and record this fallback
  explicitly in the PR summary.
- Tests obey the same style: AHVN utilities, no hard-coded paths, no
  stdlib `json`, canonical method names.
- Fixtures live in `tests/fixtures/` and are imported, not duplicated.

---

## 12. Documentation

- Every public class / function gets a short docstring — one sentence summary,
  followed by args / returns when non-obvious. No novels.
- User-facing Markdown docs:
  - All sections at **every level are numbered** (except the root `#`).
  - Every section ends with `<br/>`.
  - **All content must faithfully match the code.** If the code doesn't do
    it, the doc doesn't claim it.
- Bilingual docs (EN / ZH) follow repository-local glossary terminology.

---

## 13. Tooling and Pre-commit

- **`black` + `flake8` must pass** before every commit. Use the repository's
  declared lint/format entrypoint when present (for example scripts or tasks).
  If none exists, run `black` and `flake8` directly.
- Line length: whatever `black`'s current project setting is — do not fight it.
- Imports:
  1. `ahvn.utils.basic` re-exports first,
  2. then `ahvn.*` submodules,
  3. then first-party project imports,
  4. then third-party,
  5. then stdlib (only where actually needed and not replaced by AHVN utils).
  Within each group, sorted alphabetically.
- No unused imports, no `from x import *` (except inside `ahvn.utils.basic`
  re-export boilerplate).

---

## 14. Commit Hygiene

- One logical change per commit. Mixed refactor + feature commits are rejected.
- Major features, refactors, or schema changes → prefix the commit message
  with `[major]` to trigger the full CI suite.
- Every PR description states:
  1. What changed,
  2. Which config keys were added / removed,
  3. Any `CM_*` default-config updates needed (run the repository bootstrap/sync setup entrypoint).

---

## 15. Review Checklist (copy into every PR)

```markdown
- [ ] No stdlib `os.path`, `pathlib`, `json`, `yaml`, `pickle`, `subprocess`,
      `shutil`, or `hashlib` used directly (AHVN utils used instead).
- [ ] No hard-coded paths / prompts / defaults / model names / examples
      (routed through CM_* and resources/).
- [ ] No backward-compat shims; deprecated code deleted.
- [ ] Class / function names short, consistent with the AHVN glossary.
- [ ] Canonical methods only: from_dict / to_dict / to_str / clone / get / set.
- [ ] Code is compact (comprehensions, ternaries, chaining) where reasonable.
- [ ] No SQL strings embedded in Python; SQLAlchemy used.
- [ ] No stray print / broad except / defensive try-except.
- [ ] `black` + `flake8` clean.
- [ ] Tests run via the repository test entrypoint and pass.
- [ ] Docs updated: numbered sections, `<br/>` endings, content matches code.
```

---

## 16. When This Guide Is Silent

Default to the spirit of the golden rules (§1). If a situation genuinely
isn't covered, open an issue — don't invent a private convention. Any
agreed-upon addition is merged into this document in the same PR as the
first code that uses it.

— *Maintainers of the adopting repository*
