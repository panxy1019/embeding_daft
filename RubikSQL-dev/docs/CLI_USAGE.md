# RubikSQL CLI Usage Documentation

This document provides comprehensive usage information for all RubikSQL CLI commands.

## Table of Contents

- [Database Management Commands](#database-management-commands)
  - [add](#rubiksql-add)
  - [list](#rubiksql-list)
  - [remove](#rubiksql-remove)
  - [edit](#rubiksql-edit)
  - [test](#rubiksql-test)
  - [activate](#rubiksql-activate)
  - [deactivate](#rubiksql-deactivate)
- [Information Commands](#information-commands)
  - [info](#rubiksql-info)
  - [update](#rubiksql-update)
- [Execution Commands](#execution-commands)
  - [exec](#rubiksql-exec)
  - [ask](#rubiksql-ask)
- [Knowledge Base Commands](#knowledge-base-commands)
  - [build](#rubiksql-build)
- [Tool Commands](#tool-commands)
  - [tool](#rubiksql-tool)

---

## Database Management Commands

### `rubiksql add`

Add a new database connection to RubikSQL.

**Usage:**
```bash
rubiksql add -n <name> -p <provider> [options]
```

**Required Options:**
- `-n, --name <name>`: Display name for the database (used as folder name)
- `-p, --provider <provider>`: Database provider (`sqlite`, `duckdb`, `pg`, `mysql`, `mssql`)

**Connection Options:**
- `-db, --database <path/name>`: Physical database name or path
  - SQLite: `/path/to/file.db`
  - PostgreSQL/MySQL: The database name on server
- `-H, --host <host>`: Hostname (default: localhost for most)
- `-P, --port <port>`: Port number
- `-u, --username <user>`: Username
- `-w, --password <pass>`: Password
- `-t, --test`: Test connection immediately after adding

**Examples:**
```bash
# Add SQLite database
rubiksql add -n mydb -p sqlite -db ./data.db

# Add PostgreSQL database with connection test
rubiksql add -n prod -p pg -H localhost -db my_pg_db -u admin -w secret -t
```

---

### `rubiksql list`

List all registered databases.

**Usage:**
```bash
rubiksql list
rubiksql ls
```

**Examples:**
```bash
rubiksql list
# Output:
# mydb
# prod
# test_db
```

---

### `rubiksql remove`

Remove a registered database.

**Usage:**
```bash
rubiksql remove [name] [options]
rubiksql rm [name] [options]
```

**Options:**
- `[name]`: Database identifier (positional argument)
- `-n, --name <name>`: Database identifier (option flag)
- `-y, --yes`: Skip confirmation prompt
- If no name specified, uses the active database

**Examples:**
```bash
rubiksql remove -n mydb
rubiksql remove mydb
rubiksql remove              # Uses active database
rubiksql rm mydb -y          # Skip confirmation
```

---

### `rubiksql edit`

Edit connection parameters of a registered database.

**Usage:**
```bash
rubiksql edit [name] [options]
```

**Options:**
- `[name]`: Database identifier (positional argument)
- `-n, --name <name>`: Database identifier (option flag)
- Same connection options as `add` (except provider)
- If no name specified, uses the active database

**Examples:**
```bash
rubiksql edit -n mydb --host new-host
rubiksql edit mydb --host new-host
rubiksql edit --host new-host    # Uses active database
rubiksql edit mydb -db new_db_name
```

---

### `rubiksql test`

Test connection to a registered database.

**Usage:**
```bash
rubiksql test [name] [options]
```

**Options:**
- `[name]`: Database identifier (positional argument)
- `-n, --name <name>`: Database identifier (option flag)
- If no name specified, uses the active database

**Examples:**
```bash
rubiksql test -n mydb
rubiksql test mydb
rubiksql test                 # Uses active database
```

---

### `rubiksql activate`

Activate a database as the default.

After activation, all commands can omit the `--name/-n` argument and will use this database by default.

**Usage:**
```bash
rubiksql activate [name] [options]
```

**Options:**
- `[name]`: Database identifier (positional argument)
- `-n, --name <name>`: Database identifier (option flag)

**Examples:**
```bash
rubiksql activate -n mydb
rubiksql activate mydb
```

---

### `rubiksql deactivate`

Deactivate the current active database.

After deactivation, all commands will require the `--name/-n` argument.

**Usage:**
```bash
rubiksql deactivate
```

**Examples:**
```bash
rubiksql deactivate
```

---

## Information Commands

### `rubiksql info`

Display database/table/column metadata (includes KB status at database level).

**Usage:**
```bash
rubiksql info [name] [options]
```

**Options:**
- `[name]`: Database identifier (or use active database)
- `-n, --name <name>`: Database identifier
- `-t, --table <table>`: Table ID to show (optional)
- `-c, --column <column>`: Column ID to show (requires `--table`)
- `--reset`: Remove and recompute metadata from database
- `--update`: Refresh metadata from database (preserves desc/disabled)
- If no database name specified, uses the active database

**Scope Hierarchy:**
- Database-level: Shows all tables, columns, KB status
- Table-level (`-t`): Shows specific table with all columns
- Column-level (`-t -c`): Shows specific column details

**Examples:**
```bash
rubiksql info -n mydb                    # Database-level (includes KB status)
rubiksql info                            # Uses active database
rubiksql info -n mydb -t users           # Table-level
rubiksql info -n mydb -t users -c name   # Column-level
rubiksql info -n mydb --reset            # Force reinitialize
```

---

### `rubiksql update`

Update database metadata or properties.

**Usage:**
```bash
rubiksql update <subcommand> [options]
```

**Subcommands:**

#### `rubiksql update desc`

Update description for database objects.

**Usage:**
```bash
rubiksql update desc [name] [options] <description>
```

**Options:**
- `[name]`: Database identifier (or use active database)
- `-n, --name <name>`: Database identifier
- `-t, --table <table>`: Table ID (optional)
- `-c, --column <column>`: Column ID (requires `--table`)

**Examples:**
```bash
rubiksql update desc mydb "My production database"
rubiksql update desc -t users "User information table"
rubiksql update desc -t users -c id "Primary key"
```

#### `rubiksql update enable`

Enable database objects (included in knowledge base building).

**Usage:**
```bash
rubiksql update enable [name] [options]
```

**Options:**
- `[name]`: Database identifier (or use active database)
- `-n, --name <name>`: Database identifier
- `-t, --table <table>`: Table ID (optional)
- `-c, --column <column>`: Column ID (requires `--table`)

**Examples:**
```bash
rubiksql update enable mydb              # Enable all tables
rubiksql update enable -t users          # Enable specific table
rubiksql update enable -t users -c pass  # Enable specific column
```

#### `rubiksql update disable`

Disable database objects (excluded from knowledge base building).

**Usage:**
```bash
rubiksql update disable [name] [options]
```

**Options:**
- Same as `enable`

**Examples:**
```bash
rubiksql update disable mydb
rubiksql update disable -t users
rubiksql update disable -t users -c pass
```

---

## Execution Commands

### `rubiksql exec`

Execute a raw SQL query.

**Usage:**
```bash
rubiksql exec [args...] [options]
```

**Options:**
- `-n, --name <name>`: Database identifier
- `-s, --sql <sql>`: SQL query string
- If no database name specified, uses the active database
- **Smart parsing**: When using positional arguments, automatically detects which is the database name and which is the SQL based on SQL keywords and spaces

**Smart Detection:**
- Single argument: Detects if SQL (contains space or SQL keywords) or database name
- Two arguments: Intelligently determines database name vs SQL
- Multiple arguments: First is database name, rest joined as SQL

**SQL Keywords Detected:**
`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ALTER`, `WITH`, `TRUNCATE`, `GRANT`, `REVOKE`

**Examples:**
```bash
rubiksql exec -n mydb "SELECT count(*) FROM users"
rubiksql exec mydb "SELECT count(*) FROM users"
rubiksql exec "SELECT count(*) FROM users"      # Uses active DB
rubiksql exec mydb SELECT * FROM users          # Auto-detects both
rubiksql exec SELECT * FROM users               # Uses active DB
```

---

### `rubiksql ask`

Ask a question in natural language.

**Usage:**
```bash
rubiksql ask [name] "<question>" [options]
```

**Options:**
- `[name]`: Database identifier (or use positional argument)
- `-n, --name <name>`: Database identifier
- `--agent <agent>`: Select specific agent
- If no database name specified, uses the active database

**Examples:**
```bash
rubiksql ask -n mydb "How many users are there?"
rubiksql ask mydb "How many users are there?"
rubiksql ask "How many users are there?"        # Uses active database
```

---

## Knowledge Base Commands

### `rubiksql build`

Build or rebuild the knowledge base for a database.

**Usage:**
```bash
rubiksql build [options]
```

**Options:**
- `-n, --name <name>`: **Required**. The database identifier
- `-r, --rebuild`: Force rebuild
- `-s, --stages <list>`: Comma-separated list of stages (e.g., `tables,columns`)
- `--list-stages`: List available build stages

**Examples:**
```bash
rubiksql build -n mydb
rubiksql build -n mydb --rebuild
rubiksql build -n mydb -s tables,columns
rubiksql build --list-stages
```

---

## Tool Commands

### `rubiksql tool`

Access internal tools directly.

**Usage:**
```bash
rubiksql tool <subcommand> [options]
```

**Options:**
- `[name]`: Database identifier (or use positional argument)
- `-n, --name <name>`: Database identifier
- If no database name specified, uses the active database

**Subcommands:**

#### `rubiksql tool db_info`

Get database schema information.

**Usage:**
```bash
rubiksql tool db_info [name] [options]
```

**Examples:**
```bash
rubiksql tool db_info -n mydb
rubiksql tool db_info                    # Uses active database
```

#### `rubiksql tool tab_info`

Get table schema information.

**Usage:**
```bash
rubiksql tool tab_info [name] -t <table> [options]
```

**Options:**
- `-t, --table <table>`: Table name(s) (required, multiple allowed)

**Examples:**
```bash
rubiksql tool tab_info -n mydb -t users
rubiksql tool tab_info -t users          # Uses active database
rubiksql tool tab_info -t users -t orders  # Multiple tables
```

#### `rubiksql tool col_info`

Get column information.

**Usage:**
```bash
rubiksql tool col_info [name] -t <table> -c <column> [options]
```

**Options:**
- `-t, --table <table>`: Table name (required)
- `-c, --column <column>`: Column name(s) (required, multiple allowed)

**Examples:**
```bash
rubiksql tool col_info -n mydb -t users -c name
rubiksql tool col_info -t users -c name  # Uses active database
rubiksql tool col_info -t users -c id -c name  # Multiple columns
```

#### `rubiksql tool fd_check`

Check functional dependencies (X → Y).

**Usage:**
```bash
rubiksql tool fd_check [name] -t <table> -x <cols> -y <cols> [options]
```

**Options:**
- `-t, --table <table>`: Table name (required)
- `-x, --x-cols <cols>`: Determinant columns X (required, multiple allowed)
- `-y, --y-cols <cols>`: Dependent columns Y (required, multiple allowed)

**Examples:**
```bash
rubiksql tool fd_check -n mydb -t heroes -x id -y name
rubiksql tool fd_check -t heroes -x id -y name            # Uses active DB
rubiksql tool fd_check -t heroes -x id name -y age height  # Multiple columns
```

#### `rubiksql tool fuzzy_enum`

Fuzzy search for values in database columns.

**Usage:**
```bash
rubiksql tool fuzzy_enum [name] "<value>" [options]
```

**Options:**
- `-t, --table <table>`: Limit search to specific tables (multiple allowed)

**Examples:**
```bash
rubiksql tool fuzzy_enum -n mydb "riverside"
rubiksql tool fuzzy_enum "riverside"        # Uses active database
rubiksql tool fuzzy_enum -n mydb "riverside" -t schools
```

---

## Global Patterns

### Active Database Support

Most commands support the active database feature:
1. Activate a database: `rubiksql activate mydb`
2. Use commands without specifying database: `rubiksql info`, `rubiksql test`, etc.
3. Deactivate when done: `rubiksql deactivate`

### Positional vs Option Arguments

Most commands support both styles:
- **Option style**: `rubiksql info -n mydb -t users`
- **Positional style**: `rubiksql info mydb -t users`
- **Active database**: `rubiksql info -t users` (when database is activated)

### Hierarchical Scope

Commands like `info` and `update` support hierarchical scope:
- **Database level**: Affects entire database
- **Table level**: Affects specific table (`-t/--table`)
- **Column level**: Affects specific column (`-t table -c column`)
