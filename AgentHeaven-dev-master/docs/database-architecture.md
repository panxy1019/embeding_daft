# Database Module Architecture

## 1. Overview
<br/>

The Database module (`ahvn/utils/db/`) provides a universal relational database connector built on SQLAlchemy. It supports SQLite, DuckDB, PostgreSQL, and MySQL through a unified configuration-driven architecture.

**Key design principles:**
- **No persistent connections** — connections exist only inside context managers or are created-and-closed for standalone calls.
- **Engine sharing** — engines (and their connection pools) are cached globally via `DatabaseEngineRegistry`.
- **Config-driven** — all provider-specific settings (pool class, pragmas, connect_args) are resolved from a layered config system, not hardcoded.
- **3-option autocommit** — `None` (auto-resolve), `True` (commit), `False` (no commit).

<br/>

## 2. Module Structure
<br/>

```
ahvn/utils/db/
├── base.py        # Database, SQLResponse, table_display
├── spec.py        # DatabaseConfigSpec, DatabaseConfigEngine, POOL_CLASS_MAP
├── db_utils.py    # DatabaseEngineRegistry, create_database_engine, SQL utilities
└── __init__.py    # Public API exports
```

<br/>

## 3. Core Classes
<br/>

### 3.1. `DatabaseConfigSpec` (spec.py)
<br/>

A frozen dataclass representing fully-resolved database configuration:

| Field | Description |
|-------|-------------|
| `provider` | Canonical provider name (`sqlite`, `pg`, `duckdb`, `mysql`) |
| `dialect` | SQLAlchemy dialect string |
| `driver` | DBAPI driver |
| `database` | Database name or file path |
| `host`, `port`, `user`, `password` | Connection credentials (server DBs) |
| `pool` | Pool configuration dict (pool_class, pool_size, etc.) |
| `pragmas` | List of SQL pragmas to execute on each new connection |
| `connect_args` | Driver-level connection arguments |
| `args` | Additional kwargs (proxy settings, etc.) |

The spec is **hashable** and used as a cache key in `DatabaseEngineRegistry`.

<br/>

### 3.2. `DatabaseConfigEngine` (spec.py)
<br/>

The configuration engine that resolves raw user input into a `DatabaseConfigSpec`. It has two main operations:

- **`resolve(raw_dict)`** — Merges user input with provider defaults from `default_config.yaml`, resolves aliases, and validates. Returns a `DatabaseConfigSpec`.
- **`materialize(spec, mode)`** — Converts a spec back into a dict for different purposes:
  - `"spec"` — full spec dict (for re-creating a Database instance)
  - `"engine"` — SQLAlchemy `create_engine()` kwargs
  - `"superuser"` — superuser connection URL
  - `"pragmas"` — list of pragma statements
  - `"key"` — cache key for engine registry
  - `"url"` — connection URL string

**Pool class mapping** (`POOL_CLASS_MAP`):

| Config value | SQLAlchemy class |
|---|---|
| `"static"` | `StaticPool` |
| `"null"` | `NullPool` |
| `"queue"` | `QueuePool` |
| `"singleton"` | `SingletonThreadPool` |
| `"assertion"` | `AssertionPool` |

<br/>

### 3.3. `DatabaseEngineRegistry` (db_utils.py)
<br/>

A thread-safe LRU cache (max 2048) for SQLAlchemy engines. Engines are keyed by the materialized `"key"` mode of a spec.

**API:**
- `get_engine(spec)` — return cached or create new engine
- `dispose(spec)` — dispose engine and mark key as disposed
- `clear_disposed(spec)` — allow a spec to obtain a new engine after disposal
- `clear()` — dispose all engines

Disposal tracking prevents accidental reuse of disposed engines.

<br/>

### 3.4. `Database` (base.py)
<br/>

The main user-facing class.

**Lifecycle:**
1. `__init__` resolves config via `DatabaseConfigEngine` but does **not** create an engine.
2. The `engine` property lazily fetches the shared engine from `DatabaseEngineRegistry`.
3. Connections are obtained only via:
   - **Context manager** (`with db:`) — opens a connection + transaction, commits on clean exit, rolls back on exception.
   - **Standalone calls** — each `execute()`/`orm_execute()` creates a temporary connection, commits, and closes it.

**Key attributes:**
- `spec` — resolved `DatabaseConfigSpec`
- `config` — materialized spec dict
- `dialect` — dialect string for SQL processing
- `sql_processor` — handles SQL transpilation between dialects
- `_conn` — active connection (only set inside context manager)

<br/>

### 3.5. `SQLResponse` (base.py)
<br/>

Unified result wrapper for all SQL operations — success and error.

**Core state:**
- `ok` — boolean indicating success/failure; `bool(result)` also works
- `columns` — column name list
- `rows` — eagerly fetched list of dicts
- `shape` / `size` — `(row_count, column_count)` tuple
- `row_count` — number of rows affected
- `lastrowid` — last inserted row ID
- `query` — the executed SQL (tracked on both success and error)
- `params` — the parameters used
- `elapsed` — wall-clock execution time in seconds

**Data access:**
- `to_list(row_fmt, columns)` — export as list of dicts, tuples, or lists; optional column subset
- `fetchall()` — alias for `to_list()` (backward compat)
- `scalar()` — first column of first row (e.g., `COUNT(*)`)
- `first()` — first row as dict, or `None`
- `column(col)` — all values for a column (series)
- `to_pd()` — export as `pandas.DataFrame`
- `clone()` — deep copy

**Indexing (numpy/pandas-style):**
- `result[i]` — row by index (dict)
- `result[i:j]` — row slice (list of dicts)
- `result["col"]` — column series (list of values)
- `result[i, "col"]` — cell by row + column name
- `result[i, j]` — cell by row + column index
- `result[i:j, "col"]` — column slice
- `result[[0, 2, 5]]` — row subset by indices
- `result[["col1", "col2"]]` — column subset (list of dicts)

**Display:**
- `__str__` — `table_display()` for success, structured error for failure
- `__repr__` — compact debug representation
- `to_str()` — text summary (success) or structured error info

**Error access (when `ok=False`):**
- `error_type`, `error_message` — classified error info
- `exception` — original exception with traceback preserved
- `raise_on_error()` — re-raise stored exception
- `traceback()` — formatted traceback string

<br/>

## 4. Autocommit Semantics
<br/>

The `autocommit` parameter uses a **3-option** system:

| Value | Context Manager | Standalone |
|-------|----------------|------------|
| `None` (default) | No commit (transaction managed by `__exit__`) | Auto-commit + close |
| `True` | Commit immediately inline | Commit + close |
| `False` | No commit | **Raises `DatabaseError`** |

This means:
- Most callers can omit `autocommit` entirely — `None` does the right thing in both contexts.
- Use `autocommit=True` when you need to force an immediate commit inside a context manager.
- Use `autocommit=False` only inside a context manager when you want explicit control.

<br/>

## 5. Connection Flow
<br/>

### 5.1. Standalone Execution
<br/>

```
db.execute("SELECT ...")
    └─ _exec_sql(query, autocommit=None)
        ├─ self._conn is None → standalone path
        ├─ engine.connect() → new connection
        ├─ conn.begin() → start transaction
        ├─ conn.execute(query)
        ├─ conn.commit()
        └─ conn.close()
```

<br/>

### 5.2. Context Manager Execution
<br/>

```
with db:
    db.execute("INSERT ...")   # autocommit=None → no commit
    db.execute("UPDATE ...")   # autocommit=None → no commit
# __exit__ → commit (or rollback on exception)
```

```
__enter__:
    engine.connect() → self._conn
    self._conn.begin()

execute():
    _exec_sql(query, autocommit=None)
        └─ self._conn is not None → context-manager path
            └─ self._conn.execute(query)  # no commit

__exit__:
    if no exception → self._conn.commit()
    else → self._conn.rollback()
    self._conn.close()
    self._conn = None
```

<br/>

## 6. Engine Creation Pipeline
<br/>

```
User input (provider, database, pool, ...)
    │
    ▼
DatabaseConfigEngine.resolve()
    ├─ Merge with default_config.yaml provider settings
    ├─ Resolve aliases (pg → postgresql, etc.)
    ├─ Deep-merge pool config
    └─ Return DatabaseConfigSpec (frozen, hashable)
    │
    ▼
DatabaseEngineRegistry.get_engine(spec)
    ├─ Materialize spec → engine kwargs (URL, poolclass, connect_args, ...)
    ├─ create_engine(**kwargs)
    ├─ Register pragma event listeners
    └─ Cache engine by key
```

<br/>

## 7. Method Groups
<br/>

### 7.1. Execution
<br/>

| Method | Purpose |
|--------|---------|
| `execute(query, ...)` | Execute raw SQL string (with optional transpilation) |
| `orm_execute(query, ...)` | Execute SQLAlchemy ORM statements (`select`, `insert`, etc.) |
| `_exec_sql(query, ...)` | Internal execution dispatcher (standalone vs context manager) |

<br/>

### 7.2. Transaction Control
<br/>

| Method | Purpose |
|--------|---------|
| `__enter__` / `__exit__` | Context manager for transactional blocks |
| `commit()` | Manual commit (context manager only) |
| `rollback()` | Manual rollback (context manager only) |
| `in_transaction()` | Check if inside an active context manager |

<br/>

### 7.3. Inspection
<br/>

| Method | Purpose |
|--------|---------|
| `db_tabs()` | List table names |
| `db_views()` | List view names |
| `tab_cols(tab)` | Column names/info for a table |
| `tab_pks(tab)` | Primary key columns |
| `tab_fks(tab)` | Foreign key relationships |
| `row_count(tab)` | Row count for a table |
| `col_agg(tab, col, agg)` | Aggregate function on a column |
| `col_type(tab, col)` | Column data type |
| `col_distincts(tab, col)` | Distinct values |
| `col_enums(tab, col)` | Enum values (low-cardinality columns) |
| `col_freqs(tab, col)` | Value frequency distribution |
| `col_freqk(tab, col, k)` | Top-K frequent values |
| `col_nonnulls(tab, col)` | Non-null values |

<br/>

### 7.4. Data Access
<br/>

| Method | Purpose |
|--------|---------|
| `browse(tab, limit, offset, orderby)` | Browse table rows |
| `row_sample(tab, n)` | Random sample of rows |
| `col_percentile(tab, col, ...)` | Percentile statistics |
| `col_lengths(tab, col)` | String length statistics |

<br/>

### 7.5. Manipulation
<br/>

| Method | Purpose |
|--------|---------|
| `clear_tab(tab)` | Delete all rows from a table |
| `drop(tab)` | Drop table(s) or entire database |
| `drop_view(view)` | Drop a view |

<br/>

### 7.6. Utility
<br/>

| Method | Purpose |
|--------|---------|
| `clone()` | Create independent Database instance (shared engine) |
| `close()` | No-op (engine managed by registry) |

<br/>

## 8. Thread Safety
<br/>

- **Standalone calls** are thread-safe — each creates its own connection from the pool.
- **Context manager** is **not** thread-safe — `self._conn` is stored on the instance 。For parallel context-manager usage, use `clone()` to create separate instances.
- `DatabaseEngineRegistry` is thread-safe (uses `threading.Lock`).

<br/>

## 9. `:memory:` Databases
<br/>

Using `:memory:` databases is **not recommended**. In-memory databases cannot share state across connections, which breaks:
- Connection pooling (each connection gets a fresh empty database)
- Context manager semantics
- `clone()` (clones don't share data)

Use file-based databases instead (e.g., `"./tmp/my.db"` for SQLite).

A `UserWarning` is emitted when `:memory:` is used.

<br/>
