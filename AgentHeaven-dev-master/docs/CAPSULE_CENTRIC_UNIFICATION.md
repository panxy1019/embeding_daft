## Capsule-Centric Unification (Latest)

### Objective

Use `Capsule` as the single callable persistence payload across:
- `BaseUKF` callable fields (`triggers`, `content_composers`)
- `ToolSpec` / `ToolUKFT`
- Toolkit-level tool persistence
- JSON callable encoding paths

UKF remains the envelope; Capsule is the callable payload.

---

### Final Interface Decisions

1. `Capsule` public interface stays minimal:
- keep `to_tool(...)`
- no public `to_callable(...)`
- keep internal callable restoration helper private

2. `Capsule` is callable:
- `cap(*args, **kwargs)` restores via `to_tool()` and executes
- positional args are mapped by input-schema order
- ambiguous/invalid mappings raise explicit errors

3. Layer selection uses ordered `layers=[...]` only:
- no `prefer` argument

4. MCP connection naming is standardized to `transport`:
- `client_config` is treated as legacy/read compatibility only

5. `ToolUKFT` is capsule-first:
- tool capsule stored in `content_resources["capsule"]`
- schema and description remain duplicated in `content_resources` for indexing/composition

---

### Stage Status

#### Stage 1: Capsule runtime core cleanup
- Added private callable restoration path in Capsule core
- Added positional-arg support in `Capsule.call/__call__`
- Kept `to_tool` as canonical explicit restore API

#### Stage 2: BaseUKF + serializer integration
- `BaseUKF.triggers` / `BaseUKF.content_composers` now serialize callables as capsules
- `AhvnJsonEncoder` now writes callable payloads as capsules
- `AhvnJsonDecoder` supports:
  - capsule callable payloads (new)
  - legacy function descriptors (read fallback)

#### Stage 3: ToolSpec + ToolUKFT unification
- `ToolSpec.to_ukf(...)` now uses `transport` naming
- `ToolUKFT.from_tool(...)` embeds capsule payload (`content_resources["capsule"]`)
- `ToolUKFT` restore paths are capsule-first with compatibility fallbacks

#### Stage 4: Toolkit/MCP unification + bundle persistence
- Added `Toolkit.to_capsules()` / `Toolkit.from_capsules()`
- Added manager helpers:
  - `ToolkitManager.save_as_capsules(...)`
  - `ToolkitManager.load_from_capsules(...)`
- Removed duplicated CLI kv-parser code (`mcp` and `capsule` now share `cli/tool_cli_utils.py`)
- `capsule serve` now restores tools via `Toolkit.from_capsules(...)`

#### Stage 5: Skill refs + docs + cleanup
- `SkillUKFT` tools now support normalized refs:
  - `{"name": "<tool_name>", "capsule_id": "<optional_id>"}`
- legacy plain string tool names remain accepted
- `BaseUKF.tools` now returns tool-name list from either legacy or normalized refs
- capsule docs updated for callable Capsule behavior and current API surface

---

### Coding Style Rules Applied

- Paths normalized at ingress (`pj(..., abs=True)`)
- Extension/path transforms use `path_utils` helpers
- JSON serialization uses ahvn serializers (`dumps_json`, `loads_json`)
- Persistence stays on ahvn abstractions (`Database`, stores)
- Public exports controlled via `__all__`
- Private helpers remain internal-only

---

### Compatibility Policy

- New writes use capsule payload format.
- Legacy callable payloads remain readable in decode/validation paths where low-cost.
- No legacy public wrapper APIs are reintroduced.
