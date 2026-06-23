# Function Capsule Proposal

## 1. Motivation
<br/>

The current AgentHeaven tool ecosystem has strong capabilities for **creating** tools (`ToolSpec.from_func`, `from_code`, `from_mcp`, `from_client`) and **utilizing** them (FastMCP servers, LLM tool-calling, ToolUKFT). However, a critical gap exists: **portable persistence of executable tool implementations**.

|                     | Stores Code | Portable | Lightweight | Auto-Recoverable |
|---------------------|:-----------:|:--------:|:-----------:|:-----------------:|
| `serialize_func`    |     ✓       |    ✗¹    |      ✓      |        ✗          |
| `ToolUKFT`          |     ✗       |    ✓     |      ✓      |        ✗²         |
| Containers (Docker) |     ✓       |    ✓     |      ✗      |        ✓          |
| **Function Capsule**|     ✓       |    ✓     |      ✓      |        ✓          |

¹ cloudpickle is Python-version-sensitive; source breaks on file changes.
² Requires a running MCP server; no fallback cascade.

A **Function Capsule** bridges this gap: a self-describing, JSON-serializable artifact that stores **multiple recovery strategies** for a single function, enabling deterministic restoration with progressive degradation.

<br/>

## 2. Design Principles
<br/>

1. **Minimal Core, Extensible Layers** — The capsule is a flat JSON dict with optional layers. No overhead for simple functions; heavy layers only when needed.
2. **ToolSpec-Centric** — Every capsule is created from, and restores to, a `ToolSpec`. This connects to the full tool ecosystem: Python functions, MCP, FastMCP, ToolUKFT.
3. **Reuse Existing Utilities** — `md5hash` for content-addressing (with `sha256hash` added to `hash_utils` for future migration), `serialize_func`/`deserialize_func` as the core source+cloudpickle engine, `serialize_path`/`deserialize_path` (shared with `ResourceUKFT`) for module snapshots.
4. **JSON-First Storage** — The capsule is a plain dict storable as a JSON string, a database column, or a file. No custom binary formats except optional compression for transfer.
5. **Progressive Recovery** — Layers are stored as an **ordered list** and tried sequentially. The first success wins. Degradation is transparent to the caller.
6. **Minimal Interface** — The public API should be as small and easy as possible: `encapsulate()` to create, `restore()` to recover, `@capsule` to decorate. Advanced options are kwargs, not separate functions.

<br/>

## 3. Architecture Overview
<br/>

```
                       ┌─────────────────────────┐
                       │    CREATION SOURCES      │
                       ├─────────────────────────┤
                       │  Python function         │
                       │  Code string             │
                       │  ToolSpec                │
                       │  MCP/FastMCP server      │
                       └───────────┬─────────────┘
                                   │
                          encapsulate() / @capsule
                                   │
                                   ▼
                       ┌─────────────────────────┐
                       │    FUNCTION CAPSULE      │
                       │                          │
                       │  manifest  (identity)    │
                       │  schema    (API contract)│
                       │  layers    (exec strats) │
                       │  capsule_id (md5hash)    │
                       └───────────┬─────────────┘
                                   │
                     ┌─────────────┼─────────────┐
                     │             │              │
                     ▼             ▼              ▼
               ┌──────────┐ ┌──────────┐ ┌──────────────┐
               │   JSON   │ │    DB    │ │  File (.fcap)│
               │  string  │ │  record  │ │  or tar.gz   │
               └──────┬───┘ └────┬─────┘ └──────┬───────┘
                      │          │               │
                      └──────────┼───────────────┘
                                 │
                        restore() / Capsule.to_tool()
                                 │
                                 ▼
                       ┌─────────────────────────┐
                       │       ToolSpec           │
                       │  (fully executable)      │
                       └─────────────────────────┘
```

<br/>

## 4. Capsule Data Structure
<br/>

A capsule is a **flat JSON-serializable dict**. This is the canonical schema:

```json
{
  "capsule_version": "1.0",
  "capsule_id": "00000000000000000000000000000000000012345678",

  "manifest": {
    "name": "fibonacci",
    "entrypoint": "fibonacci",
    "python_version": "3.11.5",
    "created_at": "2026-03-05T12:00:00Z",
    "dependencies": {
      "pip": [],
      "python": ">=3.10"
    }
  },

  "schema": {
    "description": "Return the n-th Fibonacci number.",
    "input_schema": {
      "type": "object",
      "properties": {
        "n": {"type": "integer", "description": "Fibonacci index (0-indexed)"}
      },
      "required": ["n"]
    },
    "output_schema": {
      "properties": {
        "result": {"type": "integer"}
      }
    }
  },

  "layers": [
    {"type": "source", ...},
    {"type": "cloudpickle", ...},
    {"type": "snapshot", ...},
    {"type": "runner", ...}
  ]
}
```

**Field summary:**

| Field              | Type   | Required | Description                                               |
|--------------------|--------|----------|-----------------------------------------------------------|
| `capsule_version`  | str    | ✓        | Schema version for forward compatibility (`"1.0"`)        |
| `capsule_id`       | str    | ✓        | `fmt_hash(md5hash(layers))` — content-addressed identity  |
| `manifest`         | dict   | ✓        | Identity, runtime requirements, dependencies              |
| `schema`           | dict   | ✓        | API contract: description, input/output JSON schemas      |
| `layers`           | list   | ✓        | Ordered recovery strategies (at least one required)       |

<br/>

## 5. Layer Definitions
<br/>

Layers are ordered by preference. The recovery cascade tries each in order, using the first that succeeds.

### 5.1 Layer: `source`

The most portable layer. Stores the function's source code as a plain string, plus a content hash for integrity and an optional simple-globals snapshot.

```json
{
  "type": "source",
  "code": "def fibonacci(n: int) -> int:\n    ...",
  "func_name": "fibonacci",
  "sha256": "9c13d1d6a3f1...",
  "globals": {"PI": 3.14}
}
```

**Creation**: `dill.source.getsource(func)` (via existing `_patched_getsource` in `serialize_func`). The `sha256` is computed from the `code` string. The `globals` dict captures **only JSON-serializable simple values** (int, float, str, bool, list, dict) from the function's `__globals__` that are referenced in the source — NOT modules, classes, or other complex objects.

**Recovery**: `code2func(code, func_name, env=globals)` → `ToolSpec.from_func(func)`.

**Strengths**: Human-readable, Python-version-independent, editable. Content hash enables corruption/tampering detection.

**Weaknesses**: Fails if the source uses imports not available in the restoration environment, or if `getsource` fails (e.g. lambdas, `exec`-defined functions). Globals snapshot only covers simple values.

### 5.2 Layer: `cloudpickle`

Binary serialization of the complete function object including closures.

```json
{
  "type": "cloudpickle",
  "hex_dumps": "80059532000000...",
  "python_version": "3.11.5",
  "cloudpickle_version": "3.0.0"
}
```

**Creation**: `cloudpickle.dumps(func).hex()` (via existing `serialize_func`).

**Recovery**: `cloudpickle.loads(bytes.fromhex(hex_dumps))` → `ToolSpec.from_func(func)`. **No version pre-check** — always attempt deserialization via try-catch. If the Python version or cloudpickle version differs, emit a warning but still try. If the function is runnable, accept it.

**Strengths**: Captures closures, lambdas, dynamically generated functions. Full fidelity.

**Weaknesses**: Python-version-sensitive. Not meant for long-term storage. May silently produce incorrect results across major Python version changes (we guarantee runnability, not correctness).

### 5.3 Layer: `snapshot`

A self-contained module bundle for functions with local imports.

```json
{
  "type": "snapshot",
  "data": {
    "my_module.py": "aW1wb3J0IG1hdGg...",
    "helpers/": null,
    "helpers/utils.py": "ZGVmIGhlbHBlci..."
  },
  "entrypoint_file": "my_module.py",
  "func_name": "fibonacci"
}
```

**Creation**: `serialize_path(module_dir)` — identical to `ResourceUKFT.from_path()`.

**Recovery**: `deserialize_path(data, temp_dir)` → **isolated import via `importlib.util`** → `ToolSpec.from_func(func)`. The module is loaded into an isolated namespace to avoid polluting `sys.modules` or the global interpreter:

```python
import importlib.util
spec = importlib.util.spec_from_file_location(module_name, file_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
func = getattr(module, func_name)
```

Temp directories are cleaned up after the function is extracted.

**Strengths**: Solves the "file changed / moved" problem completely. Self-contained. No global namespace pollution.

**Weaknesses**: Larger payload. Only covers local dependencies, not pip packages.

### 5.4 Layer: `runner`

Fallback to MCP server execution. Supports three modes: connecting to an existing HTTP server, spawning an ephemeral stdio MCP process, or running a process command.

**HTTP** — connect to an already-running server:

```json
{
  "type": "runner",
  "transport": "http",
  "url": "http://localhost:8321/math/mcp",
  "tool_name": "fibonacci"
}
```

**Stdio** — spawn an ephemeral MCP server process (preferred for on-demand execution):

```json
{
  "type": "runner",
  "transport": "stdio",
  "script": "from ahvn.tool import ToolkitManager; ToolkitManager().get('math').serve(transport='stdio')",
  "tool_name": "fibonacci"
}
```

**Process** — spawn a generic command:

```json
{
  "type": "runner",
  "transport": "process",
  "command": ["python", "-m", "my_tool_server"],
  "tool_name": "fibonacci"
}
```

**Creation**: From `ToolUKFT.client_config`, `Toolkit.to_mcp_json()`, or user-specified.

**Recovery**: `ToolUKFT._create_client_from_config(runner)` → `ToolSpec.from_client(client, tool_name)`. For stdio/process transports, the process is spawned on demand and terminated after use (ephemeral execution).

**Strengths**: Maximum compatibility. Works with arbitrarily complex tools. No serialization risk. Stdio mode enables fully ephemeral tool execution without a persistent server.

**Weaknesses**: HTTP requires a running server. Stdio/process has startup latency (~30–80ms). Network latency for HTTP.

<br/>

## 6. Recovery Cascade
<br/>

```
restore(capsule)
    │
    for layer in capsule["layers"]:  # ordered list
    │
    ├─ type="source"
    │   verify sha256 (warn if mismatch)
    │   code2func(code, func_name, env=globals) → ToolSpec.from_func(func)
    │
    ├─ type="cloudpickle"
    │   warn if python_version differs
    │   cloudpickle.loads(hex_dumps) → ToolSpec.from_func(func)
    │
    ├─ type="snapshot"
    │   deserialize_path → importlib isolated import → ToolSpec.from_func(func)
    │
    ├─ type="runner"
    │   _create_client_from_config → ToolSpec.from_client(client, tool_name)
    │
    └─ all layers exhausted → CapsuleRestorationError
```

The cascade is **always try-catch based**, never version-gated. If a layer produces a runnable function, it is accepted regardless of version discrepancies (with a warning). The cascade reuses existing ahvn functions at every step. No new execution engine is needed.

After successful recovery, the restored `ToolSpec` has full capabilities: `call()`, `acall()`, `to_fastmcp()`, `to_jsonschema()`, etc.

<br/>

## 7. Relationship to Existing Components
<br/>

### 7.1 Relationship to `serialize_func` / `deserialize_func`

The capsule **wraps** the existing serialization primitives:

| `serialize_func` field | Capsule layer      | Usage                      |
|------------------------|--------------------|----------------------------|
| `code`                 | `layers.source`    | Source recovery             |
| `hex_dumps`            | `layers.cloudpickle` | Binary recovery           |
| `name`, `qualname`     | `manifest`         | Identity                   |
| `doc`                  | `schema`           | Description (via docstring) |
| `annotations`          | `schema`           | Input/output schemas        |

The capsule adds **schema** (from ToolSpec) and **snapshot/runner** layers that `serialize_func` cannot provide.

### 7.2 Relationship to `ToolSpec`

Bidirectional conversion:

```
ToolSpec  ─── encapsulate() ───▶  Capsule
Capsule  ─── to_tool()  ───▶  ToolSpec
```

Because `ToolSpec` is the hub connecting functions, MCP, FastMCP, and ToolUKFT, the capsule inherits connectivity to the entire ecosystem:

```
Python function ──┐
Code string ──────┤
MCP tool ─────────┼──▶ ToolSpec ◀──▶ Capsule ──▶ JSON / DB / File
FastMCP tool ─────┤
ToolUKFT ─────────┘
```

### 7.3 Relationship to `ToolUKFT`

`ToolUKFT` serializes **schemas + MCP client config** but NOT function code. The capsule and `ToolUKFT` are complementary:

| Capability               | ToolUKFT | Capsule |
|--------------------------|:--------:|:-------:|
| Schema serialization     |    ✓     |    ✓    |
| Function code storage    |    ✗     |    ✓    |
| MCP client config        |    ✓     |    ✓ (runner layer) |
| Pydantic BaseModel       |    ✓     |    ✗ (plain dict)   |
| UKF integration          |    ✓     |    ✗ (standalone)   |
| Offline execution        |    ✗     |    ✓    |

A `ToolUKFT` can be **produced from** a capsule (take schema + runner layer as client_config), and a capsule can **consume** a ToolUKFT's client_config as its runner layer.

### 7.4 Relationship to `ResourceUKFT` / `serialize_path`

The snapshot layer reuses `serialize_path` / `deserialize_path` verbatim — the same base64-encoded file dict used by `ResourceUKFT.from_path()`.

If `serialize_path` is ever refactored or extracted to a shared utility, both `ResourceUKFT` and capsule snapshots benefit automatically.

### 7.5 Relationship to `md5hash` / `sha256hash`

Content-addressed identity uses `md5hash` (existing):

```python
from ahvn.utils import md5hash, fmt_hash

capsule_id = fmt_hash(md5hash(capsule["layers"]))
```

A new `sha256hash` function will be added to `hash_utils` alongside `md5hash` for layer-level integrity (source code hash). The capsule ID itself continues to use `md5hash` for consistency with the rest of ahvn; a future migration to `sha256hash` may follow after performance benchmarking.

This provides:
- **Deduplication**: identical tools produce the same `capsule_id`.
- **Integrity**: tampering changes the hash.
- **Caching**: fast lookup by `capsule_id`.

<br/>

## 8. API Surface
<br/>

### 8.1 `encapsulate(func_or_toolspec, ...) → dict`

Primary creation function. Accepts a Python function, code string, or ToolSpec.

```python
from ahvn.tool.capsule import encapsulate

# From function
cap = encapsulate(fibonacci)

# From function with snapshot
cap = encapsulate(fibonacci, snapshot_modules=["mypackage/"])

# From ToolSpec
cap = encapsulate(tool_spec)

# From ToolSpec with runner config
cap = encapsulate(tool_spec, runner={"type": "http", "url": "http://..."})
```

### 8.2 `@capsule` Decorator

Convenience decorator that wraps a function and attaches its capsule.

```python
from ahvn.tool.capsule import capsule

@capsule
def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number."""
    if n <= 0: return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b

# fibonacci is still callable
fibonacci(10)  # → 55

# Access the capsule
fibonacci.__capsule__  # → dict

# Convert to ToolSpec
from ahvn.tool.capsule import restore
spec = restore(fibonacci.__capsule__)
```

### 8.3 `restore(capsule_dict, prefer=None) → ToolSpec`

Restore a ToolSpec from a capsule dict using the recovery cascade.

```python
from ahvn.tool.capsule import restore

spec = restore(capsule_dict)
result = spec(n=10)  # → 55
```

Optional `prefer` parameter forces a specific layer:

```python
spec = restore(capsule_dict, prefer="source")       # Source only
spec = restore(capsule_dict, prefer="cloudpickle")   # Cloudpickle only
spec = restore(capsule_dict, prefer="runner")        # MCP only
```

### 8.4 Integration with `ToolSpec`

```python
# ToolSpec → Capsule
cap = tool_spec.to_capsule()

# Capsule → ToolSpec
spec = ToolSpec.from_capsule(capsule_dict)
```

### 8.5 Integration with `CapsuleStore`

Capsules are persisted via a **standalone `CapsuleStore`** — a dedicated database-backed store modelled after `ConfigStorage` and `ToolkitStore`. It uses a proper ORM entity (`CapsuleORMEntity`) with a `LargeBinary` / `BLOB` column for capsule payloads, avoiding field-length limits across database providers (SQLite, PostgreSQL, MySQL, Oracle).

```python
from ahvn.tool.capsule import CapsuleStore

store = CapsuleStore()                             # uses config default db
cap = encapsulate(fibonacci)
store.save(cap)                                     # upsert by capsule_id
store.save(cap, tags=["math", "fibonacci"])          # with optional tags

loaded = store.get(cap["capsule_id"])                # → dict
all_caps = store.list()                              # → [summary, ...]
store.delete(cap["capsule_id"])
```

The store is independent of `ToolkitStore` and can be used standalone or composed into higher-level systems (CLI, ToolkitManager, future Capsule Registry).

<br/>

## 9. Serialization Formats
<br/>

### 9.1 JSON String (primary)

The capsule is natively JSON-serializable. Store in any JSON-compatible medium:

```python
import json
json_str = json.dumps(capsule_dict)         # → string
capsule_dict = json.loads(json_str)          # → dict
```

### 9.2 Database Record

Store as a TEXT column (JSON string) in SQLite/PostgreSQL:

```sql
INSERT INTO toolkit_configs (name, capsules_json)
VALUES ('math', '{"fibonacci": {...}, "add": {...}}');
```

### 9.3 Compressed File (`.fcap`)

For transfer across machines, compress the JSON:

```python
import gzip, json

# Write
with gzip.open("fibonacci.fcap", "wt") as f:
    json.dump(capsule_dict, f)

# Read
with gzip.open("fibonacci.fcap", "rt") as f:
    capsule_dict = json.load(f)
```

The `.fcap` extension is a convention, not a requirement.

### 9.4 Base64 for Embedding

When embedding in another JSON structure or sending via API:

```python
import base64, gzip, json

payload = base64.b64encode(gzip.compress(json.dumps(capsule_dict).encode())).decode()
# → single base64 string, safe for any JSON field
```

<br/>

## 10. Layer Selection Heuristics
<br/>

Not all layers are needed for every function. The `encapsulate()` function decides which layers to generate based on function complexity:

```
                          ┌─────────────────────────────────────┐
                          │  Can getsource() extract source?    │
                          │                                     │
                          │  YES → include source layer         │
                          │  NO  → skip (lambda, exec-defined)  │
                          └──────────────┬──────────────────────┘
                                         │
                          ┌──────────────▼──────────────────────┐
                          │  Is cloudpickle available?          │
                          │                                     │
                          │  YES → include cloudpickle layer    │
                          │  NO  → skip                         │
                          └──────────────┬──────────────────────┘
                                         │
                          ┌──────────────▼──────────────────────┐
                          │  Does source import local modules?  │
                          │  (ast.parse → ImportFrom analysis)  │
                          │                                     │
                          │  YES → include snapshot layer       │
                          │  NO  → skip                         │
                          └──────────────┬──────────────────────┘
                                         │
                          ┌──────────────▼──────────────────────┐
                          │  Is there a runner config?          │
                          │  (user-provided or from ToolUKFT)   │
                          │                                     │
                          │  YES → include runner layer         │
                          │  NO  → skip                         │
                          └─────────────────────────────────────┘
```

Simple functions (no imports, no closures) → **source + cloudpickle** (~2–5 KB).

Functions with local imports → **source + cloudpickle + snapshot** (~10–100 KB).

Complex / remote tools → **source + cloudpickle + runner** or **runner only**.

<br/>

## 11. Implementation Strategy
<br/>

Implementation follows an incremental, test-driven approach. Each phase is standalone and testable before moving to the next.

### 11.1 Phase 1: Core Standalone Module + Tests

**New file**: `src/ahvn/tool/capsule.py`

**Public API** (minimal):

```python
def encapsulate(
    func_or_spec,                     # Callable | ToolSpec | str (code)
    *,
    include_cloudpickle: bool = True,
    snapshot_modules: list[str] | None = None,
    runner: dict | None = None,
    dependencies: dict | None = None,
) -> dict:
    """Create a capsule dict from a function, ToolSpec, or code string."""

def restore(
    capsule: dict,
    *,
    prefer: str | None = None,        # Force a specific layer type
) -> ToolSpec:
    """Restore a ToolSpec from a capsule dict."""

def capsule(func: Callable) -> Callable:
    """Decorator that attaches a capsule to a function."""
```

**Also in Phase 1**: Add `sha256hash` to `hash_utils` (alongside existing `md5hash`).

**Test plan** (standalone, no codebase changes required):

```python
# tests/unit/tool/test_capsule.py

# 1. encapsulate a simple function → verify dict structure
# 2. encapsulate → restore → call → verify result
# 3. source-only restore (prefer="source")
# 4. cloudpickle-only restore (prefer="cloudpickle")
# 5. encapsulate a lambda → source layer skipped, cloudpickle works
# 6. encapsulate a closure → cloudpickle captures closed-over values
# 7. encapsulate with globals (e.g., PI=3.14) → source layer uses them
# 8. capsule_id is deterministic (same function → same id)
# 9. sha256 integrity check in source layer
# 10. @capsule decorator preserves callability
# 11. JSON round-trip: dumps → loads → restore → call
```

**Dependencies**: Only existing ahvn utilities. No new pip packages.

**Pseudocode**:

```python
def encapsulate(func_or_spec, *, include_cloudpickle=True, ...):
    # Normalize input to (func, tool_spec)
    if isinstance(func_or_spec, ToolSpec):
        tool_spec = func_or_spec
        func = _extract_func(tool_spec)  # tool.tool.fn if available
    elif isinstance(func_or_spec, str):
        func = code2func(func_or_spec)
        tool_spec = ToolSpec.from_func(func)
    elif callable(func_or_spec):
        func = func_or_spec
        tool_spec = ToolSpec.from_func(func)

    # Build schema from ToolSpec
    schema = {
        "description": tool_spec.binded.description,
        "input_schema": tool_spec.input_schema,
        "output_schema": tool_spec.output_schema,
    }

    # Build layers (ordered list)
    layers = []
    blob = None

    # L1: Source (first priority)
    try:
        blob = serialize_func(func)
        if blob.get("code"):
            import hashlib
            code = blob["code"]
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            simple_globals = _extract_simple_globals(func, code)
            layer = {"type": "source", "code": code,
                     "func_name": blob["name"], "sha256": code_hash}
            if simple_globals:
                layer["globals"] = simple_globals
            layers.append(layer)
    except Exception:
        pass

    # L2: Cloudpickle (second priority)
    if include_cloudpickle:
        try:
            if blob is None:
                blob = serialize_func(func)
            if blob.get("hex_dumps"):
                import sys, cloudpickle
                layers.append({
                    "type": "cloudpickle",
                    "hex_dumps": blob["hex_dumps"],
                    "python_version": sys.version.split()[0],
                    "cloudpickle_version": cloudpickle.__version__,
                })
        except Exception:
            pass

    # L3: Snapshot (only if requested)
    if snapshot_modules:
        snapshot_data = {}
        for mod_path in snapshot_modules:
            snapshot_data.update(serialize_path(mod_path))
        layers.append({
            "type": "snapshot",
            "data": snapshot_data,
            "entrypoint_file": ...,
            "func_name": func.__name__,
        })

    # L4: Runner (only if provided)
    if runner:
        layers.append({"type": "runner"} | runner)

    # Build manifest
    import sys, datetime
    manifest = {
        "name": func.__name__,
        "entrypoint": func.__name__,
        "python_version": sys.version.split()[0],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dependencies": dependencies or {},
    }

    capsule = {
        "capsule_version": "1.0",
        "manifest": manifest,
        "schema": schema,
        "layers": layers,
    }
    capsule["capsule_id"] = fmt_hash(md5hash(capsule["layers"]))
    return capsule


def _extract_simple_globals(func, code):
    """Extract only JSON-serializable simple values from func.__globals__
    that are referenced in the source code."""
    import ast, json
    try:
        tree = ast.parse(code)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    except Exception:
        return {}
    result = {}
    for name in names:
        if name in func.__globals__ and name != func.__name__:
            val = func.__globals__[name]
            if isinstance(val, (int, float, str, bool, type(None))):
                result[name] = val
            elif isinstance(val, (list, dict)):
                try:
                    json.dumps(val)  # verify serializable
                    result[name] = val
                except (TypeError, ValueError):
                    pass
    return result
```

### 11.2 Phase 2: Callable Compatibility + ToolSpec Integration

Ensure capsules are compatible as callables, and add `to_capsule()` / `from_capsule()` to `ToolSpec`:

```python
# In src/ahvn/tool/base.py
class ToolSpec:
    def to_capsule(self, **kwargs) -> dict:
        from .capsule import encapsulate
        return encapsulate(self, **kwargs)

    @classmethod
    def from_capsule(cls, capsule: dict, **kwargs) -> "ToolSpec":
        from .capsule import restore
        return restore(capsule, **kwargs)
```

### 11.3 Phase 3: Replace `serialize_func` Usages

Gradually migrate existing consumers of `serialize_func` to use capsules:

- **`AhvnJsonEncoder`**: Support capsule dicts for function serialization.
- **UKF function storage**: Store functions as capsules instead of raw `serialize_func` output.
- **ToolUKFT**: Optionally embed a capsule for offline execution capability.

### 11.4 Phase 4: Standalone CapsuleStore

Build a dedicated, database-backed capsule persistence layer. Closely imitates the coding style of `ToolkitStore` (SQLAlchemy table + `Database` class) and `CacheORMEntity` (ORM entity for structured storage).

**New file**: `src/ahvn/tool/capsule_store.py` (or co-located in `capsule.py`)

**ORM Entity**:

```python
from ahvn.utils.db.types import ExportableEntity, DatabaseIdType, DatabaseTextType, DatabaseJsonType
from sqlalchemy import Column, Index, LargeBinary, DateTime
import datetime


class CapsuleORMEntity(ExportableEntity):
    """Database record for a single Function Capsule."""

    __tablename__ = "capsules"

    # capsule_id (fmt_hash string) as primary key
    id = Column(DatabaseIdType(), primary_key=True)

    # Human-readable name (from manifest.name)
    name = Column(DatabaseTextType(length=255), nullable=False, index=True)

    # Schema version
    capsule_version = Column(DatabaseTextType(length=16), nullable=False, server_default="1.0")

    # manifest + schema as JSON (lightweight, queryable)
    manifest = Column(DatabaseJsonType(), nullable=False)
    schema = Column(DatabaseJsonType(), nullable=True)

    # The full capsule payload as compressed binary (gzip JSON).
    # Using LargeBinary / BLOB avoids TEXT field-length limits
    # across providers (MySQL max_allowed_packet, Oracle CLOB, etc.)
    payload = Column(LargeBinary, nullable=False)

    # Optional tags for browse/filter (stored as JSON list)
    tags = Column(DatabaseJsonType(), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (
        Index("idx_capsule_name", "name"),
        Index("idx_capsule_tags", "tags"),
        {"extend_existing": True},
    )
```

**Design decisions for BLOB storage**:

| Provider     | Strategy                                                    |
|-------------|-------------------------------------------------------------|
| SQLite      | `BLOB` — native, no size limit                             |
| PostgreSQL  | `BYTEA` — up to 1 GB per field                             |
| MySQL       | `LONGBLOB` via `LargeBinary(length=2**32-1)` — up to 4 GB  |
| Oracle      | `BLOB` — up to 4 GB (auto-mapped by SQLAlchemy)            |
| DuckDB      | `BLOB` — native                                            |

The `payload` column stores `gzip(json.dumps(capsule_dict).encode())`. On read, `json.loads(gzip.decompress(payload))`. This keeps the queryable metadata (name, manifest, schema, tags) in indexed columns while the heavy layers blob is compressed.

**CapsuleStore class** (follows `ToolkitStore` style):

```python
class CapsuleStore:
    """Database-backed persistence for Function Capsules."""

    def __init__(self, **config):
        from ..utils.db import Database
        if not config:
            from ..utils.basic.config_utils import CM_AHVN
            config = CM_AHVN.get("tool.capsule.storage", default={})
        self._db = Database(**config)
        with self._db:
            for stmt in CapsuleORMEntity.create_stmts():
                self._db.orm_execute(stmt)

    # ── read ──────────────────────────────────────────────────

    def get(self, capsule_id: str) -> dict | None:
        """Load a capsule by its id. Returns the full capsule dict."""
        stmt = CapsuleORMEntity.get_stmt(capsule_id)
        rows = self._db.orm_execute(stmt).to_list(row_fmt="dict")
        if not rows:
            return None
        return _decompress_payload(rows[0]["payload"])

    def list(self, tag: str = None) -> list[dict]:
        """List capsule summaries (id, name, tags, created_at). No payload."""
        ...

    def search(self, name: str = None, tag: str = None) -> list[dict]:
        """Search capsules by name pattern or tag."""
        ...

    def exists(self, capsule_id: str) -> bool:
        """Check if a capsule exists."""
        ...

    # ── write ─────────────────────────────────────────────────

    def save(self, capsule: dict, tags: list[str] = None) -> str:
        """Upsert a capsule. Returns the capsule_id."""
        entity = CapsuleORMEntity(
            id=capsule["capsule_id"],
            name=capsule["manifest"]["name"],
            capsule_version=capsule["capsule_version"],
            manifest=capsule["manifest"],
            schema=capsule.get("schema"),
            payload=_compress_payload(capsule),
            tags=tags,
        )
        for stmt in entity.upsert_stmts():
            self._db.orm_execute(stmt)
        return capsule["capsule_id"]

    def delete(self, capsule_id: str) -> None:
        """Delete a capsule by id."""
        for stmt in CapsuleORMEntity.remove_stmts(id=capsule_id):
            self._db.orm_execute(stmt)

    def clear(self) -> int:
        """Delete all capsules. Returns count deleted."""
        ...

    # ── helpers ───────────────────────────────────────────────

    def load(self, capsule_id: str) -> "ToolSpec":
        """Load and restore a capsule to a ToolSpec in one step."""
        cap = self.get(capsule_id)
        if cap is None:
            raise KeyError(f"Capsule not found: {capsule_id}")
        from .capsule import restore
        return restore(cap)


def _compress_payload(capsule: dict) -> bytes:
    import gzip
    from ahvn.utils.basic.serialize_utils import dumps_json
    return gzip.compress(dumps_json(capsule).encode())

def _decompress_payload(data: bytes) -> dict:
    import gzip
    from ahvn.utils.basic.serialize_utils import loads_json
    return loads_json(gzip.decompress(data).decode())
```

**Test plan** (`tests/unit/tool/test_capsule_store.py`):

```python
# 1. save + get round-trip (simple function capsule)
# 2. save + list (verify summary fields, no payload)
# 3. save + delete + get returns None
# 4. upsert (save twice, get returns latest)
# 5. clear (save multiple, clear, list empty)
# 6. search by name pattern
# 7. search/filter by tag
# 8. large capsule (snapshot layer ~100KB) save + get round-trip
# 9. save + load → ToolSpec → call → verify result
# 10. exists() check
# 11. JSON integrity: decompress payload matches original capsule dict
```

### 11.5 Phase 5: CLI Integration

Extend the existing `McpCLI` with capsule subcommands (integrated under the `mcp` group, not a separate CLI). Follows the same `do_*` + `register_click` / `register_typer` pattern.

**New commands** (under `ahvn mcp capsule`):

| Command                          | Description                                  |
|----------------------------------|----------------------------------------------|
| `ahvn mcp capsule list`          | List all stored capsules (summary table)     |
| `ahvn mcp capsule info <id>`     | Show capsule metadata (manifest + schema)    |
| `ahvn mcp capsule show <id>`     | Show full capsule including layer details    |
| `ahvn mcp capsule run <id>`      | Restore + execute with key=value args        |
| `ahvn mcp capsule serve <id...>` | Restore + serve as MCP (http or stdio)       |
| `ahvn mcp capsule import <file>` | Import a `.fcap` file into the store         |
| `ahvn mcp capsule export <id>`   | Export a capsule to `.fcap` file             |
| `ahvn mcp capsule rm <id>`       | Delete a capsule from the store              |

**Implementation approach**:

```python
# In mcp_cli.py — add capsule subgroup inside register_click / register_typer

def _register_capsule_subgroup(self, mcp_group):
    """Register capsule subcommands under the mcp group."""
    import click
    ref = self

    @mcp_group.group("capsule", help="Browse, run, and manage stored capsules.")
    def capsule_group():
        pass

    @capsule_group.command("list", help="List all stored capsules.")
    @click.option("--tag", "-t", default=None, help="Filter by tag.")
    def cap_list(tag):
        ref.do_capsule_list(tag=tag)

    @capsule_group.command("info", help="Show capsule metadata.")
    @click.argument("capsule_id")
    def cap_info(capsule_id):
        ref.do_capsule_info(capsule_id)

    @capsule_group.command("run", help="Restore and execute a capsule.")
    @click.argument("capsule_id")
    @click.argument("args", nargs=-1)
    def cap_run(capsule_id, args):
        ref.do_capsule_run(capsule_id, list(args))

    @capsule_group.command("serve", help="Serve capsule(s) as MCP.")
    @click.argument("capsule_ids", nargs=-1, required=True)
    @click.option("--stdio", is_flag=True)
    @click.option("--host", default="127.0.0.1")
    @click.option("--port", default=7002, type=int)
    def cap_serve(capsule_ids, stdio, host, port):
        ref.do_capsule_serve(list(capsule_ids), stdio=stdio, host=host, port=port)

    @capsule_group.command("import", help="Import a .fcap file.")
    @click.argument("file_path")
    @click.option("--tag", "-t", multiple=True, help="Tags to attach.")
    def cap_import(file_path, tag):
        ref.do_capsule_import(file_path, tags=list(tag))

    @capsule_group.command("export", help="Export a capsule to .fcap file.")
    @click.argument("capsule_id")
    @click.option("--output", "-o", default=None)
    def cap_export(capsule_id, output):
        ref.do_capsule_export(capsule_id, output=output)

    @capsule_group.command("rm", help="Delete a capsule.")
    @click.argument("capsule_id")
    @click.option("--yes", "-y", is_flag=True)
    def cap_rm(capsule_id, yes):
        ref.do_capsule_rm(capsule_id, skip_confirm=yes)
```

**`do_*` methods** delegate to `CapsuleStore` + `restore()`. The `do_capsule_serve` method restores capsules to `ToolSpec`s, builds a `Toolkit`, and calls `serve()`.

<br/>

## 12. Size Estimates
<br/>

| Function Type                | Layers                    | Approx. Size |
|------------------------------|---------------------------|-------------|
| Simple math function         | source + cloudpickle      | 1–5 KB      |
| Medium utility with imports  | source + cloudpickle      | 5–20 KB     |
| Function with local modules  | source + cloudpickle + snapshot | 20–200 KB |
| LLM-generated tool           | source + cloudpickle      | 2–10 KB     |
| Complex remote tool          | runner only               | < 1 KB      |

<br/>

## 13. Security Considerations
<br/>

1. **Code execution risk**: Both source and cloudpickle layers use `exec()` / `cloudpickle.loads()`. This is the same trust model as the existing `deserialize_func`, `code2func`, and Python's `pickle` module. **Capsules are untrusted code artifacts — only restore capsules from trusted sources.**

2. **Integrity verification**: The source layer includes a `sha256` hash of the code. On restore, verify the hash and warn if it doesn't match (indicating corruption or tampering).

3. **Snapshot layer**: Extracted to a **temporary directory** and imported via `importlib.util` into an **isolated namespace** — not added to `sys.path` or `sys.modules`. Temp directories are cleaned up after function extraction.

<br/>

## 14. Future Extensions (Out of Scope for v1.0)
<br/>

These are **not** part of the initial implementation but the architecture supports them:

1. **SHA256 migration**: Switch `capsule_id` from `md5hash` to `sha256hash` after performance benchmarking across the ahvn ecosystem.

2. **WASM layer**: Compile pure-Python functions to WASM for cross-platform sandboxed execution. Heavy; only for advanced use cases.

3. **Deduplicated layer storage**: Content-addressed layer storage (like Docker image layers) where identical snapshot modules are stored only once in `CapsuleStore`.

4. **Capsule Registry**: Remote capsule fetching via HTTP (similar to PyPI / Docker Hub). `CapsuleStore` becomes the local cache.

5. **Automatic dependency tracing**: AST-based import analysis to auto-build the snapshot layer and `dependencies.pip` list without user specification.

6. **CapsuleUKFT**: A UKF template wrapping capsules for KLStore integration, combining `ToolUKFT` schemas with capsule execution layers.

7. **Capsule Runtime**: Direct execution mode (`capsule_runtime.execute(capsule, input)`) without ToolSpec conversion — for lightweight ephemeral tool invocation.

8. **Factory-less ToolkitManager**: `ToolkitStore` and `ToolkitManager` evolve to store toolkits as collections of capsules instead of factory configs. Each toolkit becomes a named group of capsules, servable as a single MCP server (toolkit-wise) or individually (capsule-wise). Factories become optional convenience wrappers for initial capsule creation, not a runtime dependency. The recovery path becomes: `ToolkitManager.get(name)` → `CapsuleStore.list(tag=name)` → `restore()` each → `Toolkit`. This eliminates the "factory not registered" problem entirely.

<br/>

## 15. Summary
<br/>

| Aspect              | Design Decision                                                  |
|----------------------|------------------------------------------------------------------|
| **Storage format**   | Plain JSON dict                                                  |
| **Identity**         | `md5hash(layers)` via `ahvn.utils.hash_utils` (sha256 future)   |
| **Source layer**     | `serialize_func` → `code` + `sha256` hash + simple `globals`    |
| **Binary layer**     | `serialize_func` → `hex_dumps` (cloudpickle, try-catch recovery)|
| **Snapshot layer**   | `serialize_path` / `deserialize_path` + `importlib` isolation   |
| **Runner layer**     | http / stdio / process (ephemeral MCP server support)            |
| **Layers format**    | Ordered list `[{"type": ..., ...}, ...]`                        |
| **Recovery**         | Cascade: source → cloudpickle → snapshot → runner (try-catch)   |
| **Integration hub**  | `ToolSpec` (to_capsule / from_capsule)                           |
| **Persistence**      | Standalone `CapsuleStore` (ORM + BLOB, own table) — Phase 4      |
| **CLI**              | `ahvn mcp capsule` subcommands (list/info/run/serve/import/export) — Phase 5 |
| **New code**         | `src/ahvn/tool/capsule.py`, `capsule_store.py` + `sha256hash` in `hash_utils` |
| **New dependencies** | None (all utilities already exist)                               |
| **Phases**           | 1: Core+Tests → 2: ToolSpec → 3: Replace serialize_func → 4: CapsuleStore → 5: CLI |
