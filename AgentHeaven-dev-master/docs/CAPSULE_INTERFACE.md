# Capsule Interface

This document defines the current Capsule API in AgentHeaven.

## Minimal Usage

```python
from ahvn.utils.capsule import Capsule


def add(a: int, b: int) -> int:
    return a + b


cap = Capsule.from_func(add)
result = cap(1, 2)
```

Decorator-first minimal usage:

```python
from ahvn.utils.capsule import Capsule


@Capsule.capsule
def add(a: int, b: int) -> int:
    return a + b


result = add(1, 2)
cap_payload = add.__capsule__
```

## API Surface

### Class Methods

- `Capsule.from_func(func_or_spec, *, layers=None, snapshot_modules=None, transport=None, dependencies=None, identifier=None) -> Capsule`
- `Capsule.from_code(code, *, func_name=None, env=None, layers=None, snapshot_modules=None, transport=None, dependencies=None, identifier=None) -> Capsule`
- `Capsule.from_dict(data: dict) -> Capsule`
- `Capsule.from_file(path: str) -> Capsule`
- `Capsule.load(path: str) -> Capsule` (same as `from_file`)
- `Capsule.to_tool(capsule: dict | Capsule, *, layers=None) -> ToolSpec`
- `Capsule.capsule(func=None, *, identifier=None, **kwargs) -> decorator`
- `Capsule.capsule_id(identity_key: str) -> str`

### Instance Methods

- `to_dict() -> dict`
- `to_tool(*, layers=None) -> ToolSpec`
- `dump(path: str) -> str`
- `save(path: str) -> str` (alias of `dump`, same logic by duplicated code)
- `call(*args, **kwargs) -> Any`
- `__call__(*args, **kwargs) -> Any`
- `__str__() -> str` summary

## Parameters

### `Capsule.from_func(...)`

- `func_or_spec`: `Callable | ToolSpec | Capsule`
  - `Callable`: normal function
  - `ToolSpec`: tool spec instance
  - `Capsule`: copies existing capsule object
- `layers: Optional[List[Literal["source","cloudpickle","snapshot","runner"] | str]]`
  - Layer build order.
  - `None` means use the current recommended default order for this capsule version.
- `snapshot_modules: Optional[List[str]]`
  - Files/directories included for `snapshot` layer.
- `transport: Optional[dict]`
  - Configuration used by `runner` layer. Typical shape:
  - `{"transport":"http","url":"...","tool_name":"..."}`
  - `{"transport":"stdio","script":"...","tool_name":"..."}`
  - `{"transport":"process","command":"...","tool_name":"..."}`
- `dependencies: Optional[dict]`
  - Dependency metadata persisted in manifest.
- `identifier: Optional[str]`
  - Custom identity key source for deterministic capsule id.

### `Capsule.from_code(...)`

- `code`: Python code string that defines at least one callable.
- `func_name`: optional callable name when multiple callables exist in the code.
- `env`: optional globals environment for executing the code string.
- Other parameters are the same as `from_func`.

### `Capsule.to_tool(...)`

- `capsule`: capsule dict or `Capsule` object.
- `layers`: optional restore cascade order/filter.
  - Example: `layers=["source","cloudpickle"]`.

### `Capsule.__call__(...)`

- Default execution path restores a `ToolSpec` via `to_tool()` and executes it.
- Positional arguments are mapped to input-schema parameter order.
- Explicit errors are raised for ambiguous mapping (for example same parameter from both positional and keyword args).

## Path and File Behavior

- All paths are normalized via `pj(...)`.
- `dump`/`save` default extension is `.fcap`:
  - If no extension is provided, `.fcap` is appended automatically.
- `.fcap` is gzip-compressed JSON payload.
- `.json` is supported when extension is explicitly `.json`.

## fcap vs json

- `.fcap` (recommended default):
  - Smaller files due to gzip compression.
  - Better for transfer/storage of large layer payloads (especially cloudpickle/snapshot).
- `.json`:
  - Plain text and easy to inspect manually.
  - Larger on disk; better for quick debugging only.

Recommended:
- Use `.fcap` for normal persistence and sharing.
- Use `.json` only for explicit human inspection workflows.

## Typical Examples

### 1) Function

```python
cap = Capsule.from_func(add)
tool = cap.to_tool()
```

### 2) Code String

```python
code = '''
def square(x: int) -> int:
    return x * x
'''
cap = Capsule.from_code(code)
tool = cap.to_tool()
```

### 3) Dynamic Code (runtime-generated string)

```python
name = "pow2"
code = f"def {name}(x: int) -> int:\\n    return x ** 2\\n"
cap = Capsule.from_code(code, func_name=name)
```

### 4) Lambda

Lambdas are supported by cloudpickle layer but not recommended for long-term portability.
For durable storage, use named functions.

```python
fn = lambda x: x * 2
cap = Capsule.from_func(fn, layers=["cloudpickle"])
```

### 5) Nested Function / Closure

```python
def make_mul(k: int):
    def mul(x: int) -> int:
        return x * k
    return mul

cap = Capsule.from_func(make_mul(7), layers=["cloudpickle", "source"])
```

### 6) Function with External Package Imports

```python
def norm(v):
    import numpy as np
    return float(np.linalg.norm(v))

cap = Capsule.from_func(norm)
```

Note:
- Runtime environment must still provide required packages (for source/cloudpickle execution).

### 7) ToolSpec

```python
from ahvn.tool import ToolSpec

spec = ToolSpec.from_func(add)
cap = Capsule.from_func(spec)
tool = Capsule.to_tool(cap.to_dict())
```

### 8) MCP / Runner Transport

```python
cap = Capsule.from_func(
    add,
    layers=["runner"],
    transport={
        "transport": "http",
        "url": "http://127.0.0.1:7001/mcp",
        "tool_name": "add",
    },
)
tool = cap.to_tool()
```

### 9) Toolkit (bundle all tools as capsules)

```python
from ahvn.tool.toolkit import Toolkit

capsules = toolkit.to_capsules()  # List[dict], one capsule per tool
restored = Toolkit.from_capsules(name="restored-toolkit", capsules=capsules)
result = restored.run("add", a=1, b=2)
```

## Toolkit State and DatabaseToolkit

- `Toolkit.to_capsules()` serializes each `ToolSpec` as a capsule payload.
- When a tool is stateful, capsule creation uses cloudpickle for the `ToolSpec` object if needed, so bound state/closures are preserved when serializable.
- For `DatabaseToolkit`, the `exec_sql` tool usually captures a specific `Database` instance. Capsule restore keeps that behavior for cloudpickle-capable environments.

Practical recommendation:
- For portability across machines/runtimes, prefer runner transport (`layers` includes `runner`) so restore reconnects to a live service.
- Use pure cloudpickle recovery for same-environment persistence and fast local snapshot-style reuse.

## ToolUKFT + Capsule

`ToolUKFT` is now capsule-first:
- `ToolUKFT.from_tool(...)` embeds a canonical capsule in `content_resources["capsule"]`.
- `ToolUKFT.to_tool()/to_atool()` restore from the embedded capsule first.
- UKF metadata (`description`, `input_schema`, `output_schema`, tags, etc.) remains the envelope.
- Transport config is read from `transport` and feeds capsule runner-layer restoration when available.
- If runner transport restoration fails, recovery falls through to other capsule layers in order.

Typical flow:
1. Build a `ToolSpec` from a function or MCP client.
2. Convert to `ToolUKFT` (UKF envelope with capsule payload).
3. Serialize/transfer/store as UKF JSON.
4. Restore executable tool via `to_tool()` or `to_atool()`.

Example:

```python
from ahvn.tool.base import ToolSpec
from ahvn.ukf.templates.basic.tool import ToolUKFT


spec = ToolSpec.from_func(add)
tool_ukft = ToolUKFT.from_tool(
    spec,
    transport={"transport": "http", "url": "http://127.0.0.1:7001/mcp", "tool_name": "add"},
)

payload = tool_ukft.model_dump_json()
restored = ToolUKFT.model_validate_json(payload)
tool = restored.to_tool()
```

Notes:
- Use `transport` as the primary interface name.
- Legacy `client_config` is compatibility-oriented and not recommended for new code.

## Recommended Usage

- Prefer `@Capsule.capsule` for first-party tool functions.
- Prefer `Capsule.from_func(...).to_dict()` for DB/network payloads.
- Prefer `cap.save("name")` (auto `.fcap`) for filesystem persistence.
- Use `layers=[...]` explicitly only when you need deterministic layer behavior; otherwise keep `layers=None`.

## Testing Notes

- `to_dict`/`from_dict` round-trip is covered in unit tests.
- Capsule restore cascades and store round-trips are covered in:
  - `tests/unit/tool/test_capsule.py`
  - `tests/unit/tool/test_capsule_store.py`
