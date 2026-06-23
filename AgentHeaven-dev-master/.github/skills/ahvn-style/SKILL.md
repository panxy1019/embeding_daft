---
name: ahvn-style
version: 1.0.0
description: 'AgentHeaven (AHVN) code-style guideline. Follow for all Python code in AHVN ecosystem projects: use ahvn.utils.basic instead of stdlib, route config through CM_AHVN, use canonical OOP names, keep naming short, no backward compatibility, no embedded SQL.'
argument-hint: 'What files, diff, or feature should be handled with AHVN style?'
---

# AHVN Style

Execution workflow for the AgentHeaven code style — not just a checklist.
Applies to AgentHeaven itself and all downstream packages (RubikSQL, etc.),
using source-only evidence from the current workspace.

## References

Load these only when detailed rules are needed:

- **Full style guide**: [CODE_STYLE.md](./references/CODE_STYLE.md) — all 16 sections, glossary, review checklist
- **Compact instruction profile**: [code-style.instructions.md](./references/code-style.instructions.md) — blocking rules only, suitable as a quick refresher

## Golden Rules (always in context)

1. **No stdlib for covered concerns.** Use `ahvn.utils.basic.*` instead of `os.path`, `pathlib`, `json`, `yaml`, `pickle`, `subprocess`, `shutil`, `hashlib`, `logging`, `random`.
2. **No hard-coded config.** Tunables go through `CM_AHVN` (or `CM_<PKG>` in downstream). Prompts/templates/SQL go in `src/<pkg>/resources/`.
3. **No backward compatibility.** One API version, delete deprecated code, rename freely.
4. **Short names, AHVN glossary.** `KLStore`, `pj`, `sig2func`, `emb`, `n_*`, `max_*`.
5. **Canonical OOP vocabulary.** `from_dict`, `to_dict`, `to_str`, `clone`, `get`, `set` — no synonyms.
6. **Compact Python.** Comprehensions, ternaries, guard clauses, chained ops.
7. **Minimal error handling.** Raise with context; catch only at system boundaries.
8. **SQL via SQLAlchemy only.** ORM by default; raw `.sql` in `resources/` if unavoidable.

## Key Import Patterns

```python
# ✅ Canonical imports (ahvn.utils.basic re-exports typing, dataclass, etc.)
from ahvn.utils.basic import dataclass, field, Dict, List, Optional
from ahvn.utils.basic.serialize_utils import load_json, dump_json
from ahvn.utils.basic.path_utils import pj
from ahvn.utils.basic.config_utils import CM_AHVN
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.file_utils import touch_dir, list_files
from ahvn.utils.basic.hash_utils import md5hash
from ahvn.utils.basic.cmd_utils import cmd

# ✅ Top-level re-exports also work
from ahvn.utils import CM_AHVN, pj, load_json, dump_json

# ❌ Banned
import json, os, pathlib, subprocess, yaml, pickle, shutil, hashlib
```

**Import order:** `ahvn.utils.basic` re-exports → `ahvn.*` submodules → first-party → third-party → stdlib (only uncovered concerns).

## Quick Example: Before / After

```python
# ❌ BEFORE (stdlib, hard-coded, verbose)
import os, json
def load_config(name):
    path = os.path.join(os.path.expanduser("~"), ".ahvn", "cache", name + ".json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = []
    for item in data["items"]:
        if item.get("active"):
            results.append(item["name"].lower())
    return results

# ✅ AFTER (AHVN style)
from ahvn.utils.basic.serialize_utils import load_json
from ahvn.utils.basic.config_utils import CM_AHVN

def load_config(name):
    data = load_json(CM_AHVN.pj(f"cache/{name}.json", abs=True))
    return [item["name"].lower() for item in data["items"] if item.get("active")]
```

## Workflow

### Step 1. Source-Only Baseline

1. Confirm target files and language.
2. Discover conventions from the current workspace source:
   - `CM_AHVN` (or `CM_<PKG>`) symbols and config access patterns.
   - `ahvn.utils.basic` usage patterns already in the codebase.
   - Lint/test entrypoints: look for `scripts/flake.bash`, `scripts/test.bash`, `Makefile`, `pyproject.toml`.
3. Non-Python files: apply portable rules only (config centralization, naming, no compat shims).

### Step 2. Build A Rule Map Per File

For each target file, map violations to actions:

1. Replace banned stdlib operations with `ahvn.utils.basic` equivalents.
2. Move tunable literals to `CM_AHVN.get(...)` / `CM_AHVN.pj(...)`.
3. Rename symbols to AHVN glossary terms (see §6.2 in CODE_STYLE.md).
4. Normalize method vocabulary to canonical OOP names.
5. Remove compatibility branches, aliases, and deprecation shims.
6. Replace embedded SQL strings with SQLAlchemy or `resources/*.sql`.

### Step 3. Implement Minimal Diffs

1. Smallest safe edits for compliance — preserve behavior.
2. Keep frequent symbols short; prefer compact Python.
3. When compactness hurts clarity, choose clarity and note the tradeoff.

### Step 4. Decision Branches

- **Missing utility helper + editable utility layer:** add helper to `ahvn.utils.basic` first, then reuse.
- **Non-editable utility layer:** add a project-local wrapper; keep business code clean.
- **Backward compatibility explicitly requested:** keep one shim, call it out in summary.
- **Unavoidable raw SQL:** move to `src/<pkg>/resources/*.sql`, load via `CM_*.pj(...)`.
- **Verification commands:** prefer repo wrappers (`scripts/*`); direct tools only as fallback.

### Step 5. Verification Gates

All required before completion:

1. **Static scan:** grep for banned imports (`import os.path`, `import json`, `import pathlib`, etc.).
2. **Format + lint:** `bash scripts/flake.bash -b -f` (or repo-declared entrypoint).
3. **Tests:** `bash scripts/test.bash` (or repo-declared entrypoint). Never use `pytest` directly if a wrapper exists.
4. **Naming check:** confirm canonical method vocabulary and AHVN glossary compliance.

### Step 6. Completion Checklist

Task is done only when **all** are true:

- [ ] No banned stdlib imports for covered concerns.
- [ ] No hard-coded defaults/prompts/model names/paths — routed through `CM_*` and `resources/`.
- [ ] No backward-compat leftovers (unless explicitly approved).
- [ ] Canonical OOP names used (`from_dict`, `to_dict`, `clone`, etc.).
- [ ] No embedded SQL in Python source.
- [ ] `black` + `flake8` clean.
- [ ] Tests pass via repo test entrypoint.

## Output Format

Return:

1. **What changed** — grouped by rule category.
2. **Verification** — commands executed and outcomes.
3. **Risks** — remaining issues, fallbacks used, or explicit waivers.
