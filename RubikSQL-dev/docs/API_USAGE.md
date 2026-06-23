# RubikSQL Python API Documentation

This document provides comprehensive usage information for all RubikSQL Python APIs.

## Table of Contents

- [Database Management APIs](#database-management-apis)
- [Active Database Management APIs](#active-database-management-apis)
- [Database Information APIs](#database-information-apis)
- [Knowledge Base APIs](#knowledge-base-apis)
- [CLI to API Mapping](#cli-to-api-mapping)

---

## Database Management APIs

All database management APIs are in `rubiksql.api.database`.

### `list_dbs()`

List all registered database names.

**Equivalent CLI:** `rubiksql list`

```python
from rubiksql.api.database import list_dbs

databases = list_dbs()
# Returns: ['mydb', 'prod', 'test_db']
```

**Returns:**
- `List[str]`: List of database names sorted alphabetically

---

### `add_db(name, test=False, **kwargs)`

Add a new database connection.

**Equivalent CLI:** `rubiksql add -n <name> -p <provider> [options]`

```python
from rubiksql.api.database import add_db

# Add SQLite database
config = add_db(
    name="mydb",
    provider="sqlite",
    database="./data.db",
    test=True  # Test connection before saving
)

# Add PostgreSQL database
config = add_db(
    name="prod",
    provider="pg",
    host="localhost",
    database="my_pg_db",
    username="admin",
    password="secret",
    port=5432
)
```

**Parameters:**
- `name` (str): Display name for the database (used as folder name)
- `test` (bool): Whether to test the connection before saving (default: False)
- `**kwargs`: Connection parameters
  - `provider` (str): Database provider (`sqlite`, `duckdb`, `pg`, `mysql`, `mssql`)
  - `database` (str): Physical database name or path
  - `host` (str): Hostname (optional)
  - `port` (int): Port number (optional)
  - `username` (str): Username (optional)
  - `password` (str): Password (optional)

**Returns:**
- `DatabaseConfig`: The created database configuration

**Raises:**
- `ValueError`: If name already exists or parameters are invalid
- `ConnectionError`: If test=True and connection fails

---

### `remove_db(db_id)`

Remove a database configuration.

**Equivalent CLI:** `rubiksql remove <db_id>`

```python
from rubiksql.api.database import remove_db

success = remove_db("mydb")
# Returns: True
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `bool`: True if removed successfully

**Raises:**
- `ValueError`: If database not found

---

### `load_db(db_id)`

Get a Database connection for a registered database.

**Equivalent CLI:** N/A (internal API)

```python
from rubiksql.api.database import load_db

db = load_db("mydb")
# Use Database object to execute queries
result = db.query("SELECT * FROM users LIMIT 5")
db.close_conn()
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `Database`: Database connection instance from `ahvn.utils.db`

**Raises:**
- `ValueError`: If database not found

---

### `get_db_config(db_id)`

Get database configuration without connecting.

**Equivalent CLI:** N/A (used by `rubiksql show` - now deprecated)

```python
from rubiksql.api.database import get_db_config

config = get_db_config("mydb")
if config:
    print(f"Provider: {config.provider}")
    print(f"Database: {config.database}")
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `DatabaseConfig` or `None`: Database configuration if found

---

### `db_exists(db_id)`

Check if a database exists.

**Equivalent CLI:** N/A (internal check used by commands)

```python
from rubiksql.api.database import db_exists

if db_exists("mydb"):
    print("Database exists")
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `bool`: True if database exists

---

## Active Database Management APIs

### `get_active_db()`

Get the currently activated database name.

**Equivalent CLI:** N/A (internal check)

```python
from rubiksql.api.database import get_active_db

active_db = get_active_db()
if active_db:
    print(f"Active database: {active_db}")
else:
    print("No active database")
```

**Returns:**
- `str` or `None`: Database name or None if no database is activated

---

### `activate_db(db_id)`

Activate a database as the default.

**Equivalent CLI:** `rubiksql activate <db_id>`

```python
from rubiksql.api.database import activate_db

activate_db("mydb")
# All subsequent CLI commands can now omit --name/-n
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Raises:**
- `ValueError`: If database doesn't exist

---

### `deactivate_db()`

Deactivate the current active database.

**Equivalent CLI:** `rubiksql deactivate`

```python
from rubiksql.api.database import deactivate_db

deactivate_db()
# All CLI commands now require --name/-n argument
```

---

## Database Information APIs

### `get_kb_path(db_id)`

Get the knowledge base path for a database.

**Equivalent CLI:** N/A (internal API)

```python
from rubiksql.api.database import get_kb_path

kb_path = get_kb_path("mydb")
# Returns: ~/.rubiksql/databases/mydb/kb/
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `str`: Path to the knowledge base directory

---

### `init_db_info(db_id, reset=False)`

Initialize database metadata.

**Equivalent CLI:** `rubiksql info -n <db_id> --reset`

```python
from rubiksql.api.database import init_db_info

# Initialize database info
db_info = init_db_info("mydb")

# Force reinitialize (clear and rebuild)
db_info = init_db_info("mydb", reset=True)
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `reset` (bool): Whether to clear existing info before initializing

**Returns:**
- `DatabaseInfo`: Initialized database information

**Raises:**
- `ValueError`: If database not found or initialization fails

---

### `load_db_info(db_id)`

Load database metadata from storage.

**Equivalent CLI:** N/A (internal API used by `rubiksql info`)

```python
from rubiksql.api.database import load_db_info

db_info = load_db_info("mydb")
if db_info:
    print(f"Tables: {db_info.n_tabs}")
    print(f"Columns: {db_info.n_cols}")
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `DatabaseInfo` or `None`: Database info if initialized, None otherwise

---

### `update_db_info(db_id, tab_id=None, col_id=None, progress=None)`

Update database metadata with hierarchical scope control.

**Equivalent CLI:**
- `rubiksql info -n <db_id>` (database-level)
- `rubiksql info -n <db_id> -t <table>` (table-level)
- `rubiksql info -n <db_id> -t <table> -c <column>` (column-level)

```python
from rubiksql.api.database import update_db_info

# Update entire database
db_info = update_db_info("mydb")

# Update specific table
db_info = update_db_info("mydb", tab_id="users")

# Update specific column
db_info = update_db_info("mydb", tab_id="users", col_id="name")

# Update with custom progress callback
def my_progress(stage, progress, message):
    print(f"[{stage}] {progress*100:.0f}% - {message}")

db_info = update_db_info("mydb", progress=my_progress)
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `tab_id` (str, optional): Table ID to scope update to
- `col_id` (str, optional): Column ID to scope update to (requires tab_id)
- `progress` (callback, optional): Progress callback function

**Returns:**
- `DatabaseInfo`: Updated database information

**Raises:**
- `ValueError`: If database info not initialized or object not found

**Hierarchical Scope:**
- Database-level only: `update_db_info("mydb")`
- Table-level: `update_db_info("mydb", tab_id="users")`
- Column-level: `update_db_info("mydb", tab_id="users", col_id="name")`

---

### `set_description(db_id, desc, tab_id=None, col_id=None)`

Set description for database objects with hierarchical scope.

**Equivalent CLI:**
- `rubiksql update desc <db_id> <description>`
- `rubiksql update desc -t <table> <description>`
- `rubiksql update desc -t <table> -c <column> <description>`

```python
from rubiksql.api.database import set_description

# Set database description
set_description("mydb", "My production database")

# Set table description
set_description("mydb", "User information table", tab_id="users")

# Set column description
set_description("mydb", "Primary key identifier", tab_id="users", col_id="id")
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `desc` (str): Description text
- `tab_id` (str, optional): Table ID
- `col_id` (str, optional): Column ID (requires tab_id)

**Raises:**
- `ValueError`: If database info not initialized or object not found

---

### `set_disabled(db_id, disabled=True, tab_id=None, col_id=None)`

Enable or disable database objects with hierarchical scope.

**Equivalent CLI:**
- `rubiksql update disable <db_id>` or `rubiksql update enable <db_id>`
- `rubiksql update disable -t <table>` or `rubiksql update enable -t <table>`
- `rubiksql update disable -t <table> -c <column>` or `rubiksql update enable -t <table> -c <column>`

```python
from rubiksql.api.database import set_disabled

# Disable entire database (exclude from KB building)
set_disabled("mydb", disabled=True)

# Enable entire database
set_disabled("mydb", disabled=False)

# Disable specific table
set_disabled("mydb", disabled=True, tab_id="sensitive_data")

# Disable specific column
set_disabled("mydb", disabled=True, tab_id="users", col_id="password")
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `disabled` (bool): True to disable, False to enable (default: True)
- `tab_id` (str, optional): Table ID
- `col_id` (str, optional): Column ID (requires tab_id)

**Raises:**
- `ValueError`: If database info not initialized or object not found

---

## Knowledge Base APIs

All knowledge base APIs are in `rubiksql.api.knowledge`.

### `load_kb(db_id)`

Load knowledge base for a database.

**Equivalent CLI:** N/A (internal API used by tools and agent)

```python
from rubiksql.api.knowledge import load_kb

kb = load_kb("mydb")
# Use KLBase for knowledge operations
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `RubikSQLKLBase`: Knowledge base instance

**Raises:**
- `ValueError`: If KB not built or loading fails

---

### `build_kb(db_id, force=False, stages=None, progress_class=None)`

Build knowledge base for a database.

**Equivalent CLI:** `rubiksql build -n <db_id>`

```python
from rubiksql.api.knowledge import build_kb

# Build entire knowledge base
for event in build_kb("mydb"):
    print(f"Progress: {event['progress']*100:.0f}% - {event['message']}")

# Force rebuild
for event in build_kb("mydb", force=True):
    print(f"Progress: {event['progress']*100:.0f}% - {event['message']}")

# Build specific stages only
for event in build_kb("mydb", stages=["tables", "columns"]):
    print(f"Progress: {event['progress']*100:.0f}% - {event['message']}")
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `force` (bool): Force rebuild (default: False)
- `stages` (List[str], optional): Specific stages to build
- `progress_class` (class, optional): Custom progress class

**Yields:**
- `Dict`: Build events with progress information

**Raises:**
- `ValueError`: If database not found or build fails

---

### `kb_status(db_id)`

Get knowledge base build status.

**Equivalent CLI:** N/A (integrated into `rubiksql info`)

```python
from rubiksql.api.knowledge import kb_status

status = kb_status("mydb")
print(f"Exists: {status['exists']}")
print(f"Built: {status['built']}")
print(f"Status: {status['status']}")
print(f"Progress: {status['progress']*100:.0f}%")
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `Dict`: Status information
  - `exists` (bool): Whether KB directory exists
  - `built` (bool): Whether KB is built
  - `status` (str): Build status (`completed`, `running`, `cancelled`, etc.)
  - `progress` (float): Progress 0.0-1.0
  - `step` (str): Current step name
  - `stages` (dict): Per-stage status if available

**Raises:**
- `ValueError`: If database not found

---

### `list_kb_stages()`

List all knowledge base build stages.

**Equivalent CLI:** `rubiksql build --list-stages`

```python
from rubiksql.api.knowledge import list_kb_stages

stages = list_kb_stages()
# Returns: ['COUNT', 'DATABASE', 'TABLES', 'COLUMNS', 'ENUMS', ...]
```

**Returns:**
- `List[str]`: List of stage names in execution order

---

### `build_kb_stages(db_id, stages, force=False, progress_class=None)`

Build specific knowledge base stages.

**Equivalent CLI:** `rubiksql build -n <db_id> -s <stages>`

```python
from rubiksql.api.knowledge import build_kb_stages

# Build only tables and columns stages
for event in build_kb_stages("mydb", ["TABLES", "COLUMNS"]):
    print(f"Progress: {event['progress']*100:.0f}%")
```

**Parameters:**
- `db_id` (str): Database name/identifier
- `stages` (List[str]): Stage names to build
- `force` (bool): Force rebuild (default: False)
- `progress_class` (class, optional): Custom progress class

**Yields:**
- `Dict`: Build events with progress information

---

### `purge_kb(db_id)`

Remove knowledge base for a database.

**Equivalent CLI:** N/A (maintenance API)

```python
from rubiksql.api.knowledge import purge_kb

purge_kb("mydb")
# Removes KB directory for mydb
```

**Parameters:**
- `db_id` (str): Database name/identifier

---

### `purge_all_kb()`

Remove all knowledge bases.

**Equivalent CLI:** N/A (maintenance API)

```python
from rubiksql.api.knowledge import purge_all_kb

purge_all_kb()
# Removes all KB directories
```

---

### `kb_cache_stats(db_id)`

Get knowledge base cache statistics.

**Equivalent CLI:** N/A (diagnostic API)

```python
from rubiksql.api.knowledge import kb_cache_stats

stats = kb_cache_stats("mydb")
print(f"Cache hits: {stats['cache_hits']}")
print(f"Cache misses: {stats['cache_misses']}")
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- `Dict`: Cache statistics

---

### `load_toolkit(db_id)`

Load toolkit for agent use.

**Equivalent CLI:** N/A (internal API)

```python
from rubiksql.api.knowledge import load_toolkit

toolkit = load_toolkit("mydb")
# Returns toolkit for agent
```

**Parameters:**
- `db_id` (str): Database name/identifier

**Returns:**
- Tool toolkit instance

---

## CLI to API Mapping

| CLI Command | API Function | Module |
|------------|-------------|---------|
| **Database Management** |||
| `rubiksql add` | `add_db()` | `rubiksql.api.database` |
| `rubiksql list` | `list_dbs()` | `rubiksql.api.database` |
| `rubiksql remove` | `remove_db()` | `rubiksql.api.database` |
| `rubiksql edit` | `RUBIK_DBM.update_database()` | `rubiksql.db.manager` |
| `rubiksql test` | `RUBIK_DBM.test_connection()` | `rubiksql.db.manager` |
| **Active Database** |||
| `rubiksql activate` | `activate_db()` | `rubiksql.api.database` |
| `rubiksql deactivate` | `deactivate_db()` | `rubiksql.api.database` |
| **Information** |||
| `rubiksql info` | `update_db_info()` | `rubiksql.api.database` |
| `rubiksql update desc` | `set_description()` | `rubiksql.api.database` |
| `rubiksql update enable` | `set_disabled(db_id, False)` | `rubiksql.api.database` |
| `rubiksql update disable` | `set_disabled(db_id, True)` | `rubiksql.api.database` |
| **Execution** |||
| `rubiksql exec` | `load_db()` + `exec_sql()` | `rubiksql.api.database` + tools |
| `rubiksql ask` | (agent-based) | See agent APIs |
| **Knowledge Base** |||
| `rubiksql build` | `build_kb()` | `rubiksql.api.knowledge` |
| **Tools** |||
| `rubiksql tool db_info` | `tools.db_info.db_info()` | See individual tools |
| `rubiksql tool tab_info` | `tools.tab_info.tab_info()` | See individual tools |
| `rubiksql tool col_info` | `tools.col_info.col_info()` | See individual tools |
| `rubiksql tool fd_check` | `tools.fd_check.fd_check()` | See individual tools |
| `rubiksql tool fuzzy_enum` | `tools.fuzzy_enum.fuzzy_enum()` | See individual tools |

---

## Usage Patterns

### Basic Database Workflow

```python
from rubiksql.api.database import add_db, activate_database, list_dbs
from rubiksql.api.knowledge import build_kb

# Add database
config = add_db(
    name="mydb",
    provider="sqlite",
    database="./data.db"
)

# List databases
dbs = list_dbs()
print(f"Available databases: {dbs}")

# Activate database
activate_db("mydb")

# Build knowledge base
for event in build_kb("mydb"):
    print(f"Building: {event['progress']*100:.0f}% - {event['message']}")
```

### Information Query Workflow

```python
from rubiksql.api.database import update_db_info, load_db_info

# Update database info
db_info = update_db_info("mydb")

# Load info
db_info = load_db_info("mydb")
print(f"Tables: {db_info.n_tabs}")
print(f"Columns: {db_info.n_cols}")

# Access table info
for tab_id, tab_info in db_info.tables.items():
    print(f"  {tab_id}: {tab_info.n_rows} rows, {tab_info.n_cols} cols")
```

### Metadata Management Workflow

```python
from rubiksql.api.database import set_description, set_disabled

# Set descriptions
set_description("mydb", "Production database")
set_description("mydb", "User table", tab_id="users")
set_description("mydb", "User ID", tab_id="users", col_id="id")

# Disable sensitive data
set_disabled("mydb", disabled=True, tab_id="users", col_id="password")
set_disabled("mydb", disabled=True, tab_id="sensitive_logs")
```

### Query Execution Workflow

```python
from rubiksql.api.database import load_db
from rubiksql.tools.exec_sql import exec_sql

# Get database connection
db = load_db("mydb")

# Execute SQL
result = exec_sql(db, "SELECT * FROM users LIMIT 10")

# Check result
if result["output"]:
    for row in result["output"]:
        print(row)

# Close connection
db.close_conn()
```
