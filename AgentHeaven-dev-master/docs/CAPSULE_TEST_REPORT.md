# Capsule Comprehensive Test Report

Date: 2026-03-09

## Scope

This report covers:

- `Capsule.from_code` API and dynamic-code usage.
- Stateful `ToolSpec`/`Toolkit` capsule round-trip (including `DatabaseToolkit`).
- Capsule impact on UKF payload size.
- MCP runner transport behavior (`http`, `stdio`) under capsule restore.
- Corner-case behavior and current limitations.

## Implemented Fixes

1. Added `Capsule.from_code(...)` in [src/ahvn/utils/capsule/core.py](/D:/projects/Rubik/AgentHeaven-dev/src/ahvn/utils/capsule/core.py).
2. Hardened cloudpickle layer construction:
   - no hard dependency on `serialize_func(...)` success,
   - tool-state path tries `ToolSpec` cloudpickle first and falls back to function cloudpickle.
3. Refactored runner restore:
   - use fresh MCP client per call for robustness,
   - fixed stdio reconnect behavior.
4. Updated interface doc examples to use `Capsule.from_code` for code-string/dynamic-code flows:
   - [docs/CAPSULE_INTERFACE.md](/D:/projects/Rubik/AgentHeaven-dev/docs/CAPSULE_INTERFACE.md)
5. Added regression tests:
   - stateful `ToolSpec` cloudpickle round-trip,
   - `DatabaseToolkit` capsule round-trip after tool execution,
   - stdio runner restore + repeated invocation.

## Test Commands and Results

All test commands were run in Git Bash with:

- `source /c/ProgramData/miniforge3/etc/profile.d/conda.sh`
- `conda activate rubik`

### Unit Test Suites

Command:

```bash
pytest -q tests/unit/tool/test_capsule.py \
  tests/unit/tool/test_capsule_store.py \
  tests/unit/tool/test_toolkit.py \
  tests/unit/tool/test_toolspec.py \
  tests/unit/ukf/test_tool_ukft.py \
  tests/unit/utils/test_serialize_utils.py
```

Result:

- `177 passed, 1 warning in 8.99s`

Warning:

- expected warning for `:memory:` SQLite usage in one stateful tool test.

### Lint/Style Gate

Command:

```bash
bash scripts/flake.bash -a
```

Result:

- Black: pass (1 file reformatted)
- Flake8: pass

## Integration Probes

### MCP Runner Transport

- HTTP runner probe:
  - restored capsule tool executed twice successfully (`5`, `17`).
- STDIO runner probe:
  - restored capsule tool executed twice successfully (`5`, `17`).
  - this specifically validates the fresh-client-per-call runner fix.

### DatabaseToolkit Stateful Capsule Behavior

Backends tested against running Docker services from:

- [docker-compose.yml](/D:/databases/docker-compose.yml)

Backends checked:

- `sqlite`
- `pg` (Postgres)
- `mysql`
- `mssql`
- `oracle`

Probe behavior per backend:

- create `DatabaseToolkit`,
- execute `SELECT 1 AS value`,
- serialize via `toolkit.to_capsules()`,
- restore via `Toolkit.from_capsules(...)`,
- execute `SELECT 1 AS value` again.

Results:

- SQLite: pass
- Postgres: pass
- MySQL: pass
- MSSQL: pass
- Oracle: pass

Observed payload metrics:

- SQLite:
  - default capsule JSON length: `16642`
  - cloudpickle-only capsule JSON length: `16689`
  - ToolUKFT JSON length: `20796`
- Postgres:
  - default capsule JSON length: `17068`
  - cloudpickle-only capsule JSON length: `17115`
  - ToolUKFT JSON length: `21222`
- MySQL:
  - default capsule JSON length: `16822`
  - cloudpickle-only capsule JSON length: `16869`
  - ToolUKFT JSON length: `20976`

Interpretation:

- DB toolkit capsules are cloudpickle-dominant (`default_layers = ["cloudpickle"]`).
- ToolUKFT adds envelope overhead vs capsule-only payload.

## `.fcap` vs `.json` Size Check

Measured by saving the same capsule as `.fcap` and `.json`:

- simple function capsule:
  - `.fcap`: `1631` bytes
  - `.json`: `4190` bytes
- `DatabaseToolkit.exec_sql` capsule:
  - `.fcap`: `3856` bytes
  - `.json`: `12399` bytes

Conclusion:

- `.fcap` is significantly smaller and should remain default for persistence.

## Corner Cases Verified

1. Dynamic code via `Capsule.from_code(...)` with explicit `func_name`.
2. Stateful `ToolSpec` cloudpickle restore.
3. Capsule callable positional-argument mapping path.
4. Corrupted-layer fallback behavior in capsule restore cascade.
5. `Toolkit.to_capsules()` after tool execution (runtime state mutated).
6. Runner transport repeated invocation after restore (`stdio`, `http`).

## Current Limitations

1. Source-only capsule for DB wrapped tools:
   - `layers=["source"]` may fail with `CapsuleCreationError` for wrapped/stateful tools like `exec_sql`.
   - Practical path is cloudpickle/runner layers for those tools.
2. Postgres autocreate warning:
   - observed superuser autocreate warning from existing DB config fallback, even though direct runtime queries pass for `rubik` user.
   - does not block capsule usability in tested flow.
3. MSSQL/Oracle autocreate warnings:
   - warnings were observed from provider-specific autocreate helper paths (ODBC driver/service-name config mismatch messages).
   - direct toolkit execution and capsule round-trip still passed.
4. Cross-environment portability:
   - cloudpickle-heavy capsules are best for same/compatible runtime environments.
   - for cross-machine robustness, runner transport remains the preferred path.
