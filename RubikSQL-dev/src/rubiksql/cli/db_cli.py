"""\
Database management CLI commands for RubikSQL.
"""

import click


def register_db_commands(cli):
    """\
    Register all database management commands to the CLI.
    """

    @cli.command("list", help="List all registered databases.")
    @click.option("--verbose", "-v", is_flag=True, help="Show detailed information.")
    def list_dbs_cmd(verbose):
        """\
        List all registered databases.
        """
        from ahvn.utils.basic.color_utils import color_success, color_warning, color_grey
        from rubiksql.api import list_dbs, get_db_config, get_active_db

        db_names = list_dbs()
        active_db = get_active_db()

        if not db_names:
            click.echo(color_warning("No databases registered."))
            click.echo(color_grey("Use 'rubiksql add' to add a database."))
            return

        click.echo("Registered databases:")
        maxlen = max((len(name) for name in db_names), default=0)

        for name in db_names:
            config = get_db_config(name)
            if not config:
                continue

            # Show active indicator
            is_active = name == active_db
            active_str = color_success("(active)") if is_active else "        "

            name_str = name.ljust(maxlen)
            conn = config.connection
            provider = conn.get("provider", "?")
            provider_str = color_grey(f"[{provider}]")

            if provider in ("sqlite", "duckdb"):
                loc_str = color_grey(conn.get("database", ""))
            else:
                loc_parts = []
                if conn.get("host"):
                    loc_parts.append(conn["host"])
                if conn.get("port"):
                    loc_parts.append(str(conn["port"]))
                if conn.get("database"):
                    loc_parts.append(conn["database"])
                loc_str = color_grey(":".join(loc_parts) if loc_parts else "")

            click.echo(f"  {color_success('●')} {name_str} {active_str} {provider_str} {loc_str}")

            if verbose and config.created_at:
                click.echo(color_grey(f"      Created: {config.created_at}"))

    @cli.command(
        "add",
        help="""\
Add a new database connection.

Common options are provided as flags. Additional kwargs can be passed
after -- and will be forwarded directly to ahvn.Database.

Examples:
  rubiksql add -n mydb --provider sqlite -db ./data.db
  rubiksql add -n mydb --provider sqlite -db ./data.db --test
  rubiksql add -n prod --provider pg --host localhost --port 5432 -db mydb --username user --password pass
""",
        context_settings={"allow_extra_args": True, "allow_interspersed_args": True},
    )
    @click.option("--name", "-n", required=True, help="Name for the registered database (identifier).")
    @click.option(
        "--provider",
        "-p",
        type=click.Choice(["sqlite", "duckdb", "pg", "postgresql", "mysql", "mssql"], case_sensitive=False),
        required=True,
        help="Database provider.",
    )
    @click.option("--database", "-db", default=None, help="Physical database name or path (e.g. filename for sqlite, db name for pg).")
    @click.option("--host", "-H", default=None, help="Database host.")
    @click.option("--port", "-P", type=int, default=None, help="Database port.")
    @click.option("--username", "-u", default=None, help="Database username.")
    @click.option("--password", "-w", default=None, help="Database password.")
    @click.option("--test", "-t", is_flag=True, help="Test connection after adding.")
    @click.pass_context
    def add_db(ctx, name, provider, database, host, port, username, password, test):
        """\
        Add a new database connection.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error

        from rubiksql.api import add_db

        # Build connection kwargs from explicit options
        connection_kwargs = {"provider": provider}
        if database is not None:
            connection_kwargs["database"] = database
        if host is not None:
            connection_kwargs["host"] = host
        if port is not None:
            connection_kwargs["port"] = port
        if username is not None:
            connection_kwargs["username"] = username
        if password is not None:
            connection_kwargs["password"] = password

        # Parse extra args for additional kwargs
        extra = _parse_extra_args(ctx.args)
        connection_kwargs.update(extra)

        try:
            config = add_db(name=name, test=test, **connection_kwargs)
            click.echo(color_success(f"Database '{config.name}' added successfully."))
            if test:
                click.echo(color_success("Connection test passed."))
        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except ConnectionError as e:
            click.echo(color_error(f"Connection test failed: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @cli.command("remove", help="Remove a registered database.")
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
    def remove_db(name_pos, name_opt, yes):
        """\
        Remove a registered database by name.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_warning
        from rubiksql.api import remove_db, db_exists, get_active_db

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        if not yes:
            click.echo(color_warning(f"This will remove database '{name}' from the registry."))
            if not click.confirm("Are you sure you want to continue?"):
                click.echo("Aborted.")
                return

        try:
            remove_db(name)
            click.echo(color_success(f"Database '{name}' removed."))
        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @cli.command(
        "edit",
        help="""\
Edit a database connection parameters.

Examples:
  rubiksql edit -n mydb --host newhost
  rubiksql edit mydb --port 5433 --password newpass
  rubiksql edit --host newhost  # Uses active database
""",
        context_settings={"allow_extra_args": True, "allow_interspersed_args": True},
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--database", "-db", default=None, help="Physical database name or path.")
    @click.option("--host", "-H", default=None, help="Database host.")
    @click.option("--port", "-P", type=int, default=None, help="Database port.")
    @click.option("--username", "-u", default=None, help="Database username.")
    @click.option("--password", "-w", default=None, help="Database password.")
    @click.pass_context
    def edit_db(ctx, name_pos, name_opt, database, host, port, username, password):
        """\
        Edit a database configuration.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error
        from rubiksql.api import RUBIK_DBM, get_active_db

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        # Collect updates from explicit options
        updates = {}
        if database is not None:
            updates["database"] = database
        if host is not None:
            updates["host"] = host
        if port is not None:
            updates["port"] = port
        if username is not None:
            updates["username"] = username
        if password is not None:
            updates["password"] = password

        # Parse extra args for additional updates
        extra = _parse_extra_args(ctx.args)
        updates.update(extra)

        if not updates:
            click.echo(color_error("No changes specified."), err=True)
            raise SystemExit(1)

        try:
            RUBIK_DBM.update_db(name, **updates)
            click.echo(color_success(f"Database '{name}' updated."))
        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @cli.command("test", help="Test connection to a registered database.")
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    def test_db(name_pos, name_opt):
        """\
        Test connection to a registered database.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error
        from rubiksql.db.manager import RUBIK_DBM
        from rubiksql.api import db_exists, get_active_db

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            RUBIK_DBM.test_connection(name)
            click.echo(color_success(f"Connection to '{name}' successful."))
        except ConnectionError as e:
            click.echo(color_error(f"Connection failed: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    # Register exec command at CLI level (not under db group)
    @cli.command(
        "exec",
        help="""\
Execute SQL on a registered database.

Examples:
  rubiksql exec -n mydb "SELECT * FROM users"
  rubiksql exec -n mydb -s "SELECT * FROM users LIMIT 10"
  rubiksql exec "SELECT * FROM users"              # Uses active database
  rubiksql exec mydb "SELECT * FROM users"          # Auto-detects db and SQL
  rubiksql exec mydb SELECT                         # Single SQL keyword
""",
    )
    @click.argument("args", nargs=-1, required=False)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--sql", "-s", default=None, help="SQL statement to execute.")
    def exec_sql_cmd(args, name_opt, sql):
        """\
        Execute SQL on a registered database.
        """
        from ahvn.utils.basic.color_utils import color_error
        from ahvn.utils.db import table_display
        from rubiksql.api import load_db, db_exists, get_active_db
        from rubiksql.tools.exec_sql import exec_sql
        from rubiksql.utils.config_utils import RUBIK_CM

        # Parse positional arguments intelligently
        name_pos = None
        sql_arg = None

        if args:
            if len(args) == 1:
                # Single argument: check if it looks like SQL
                arg = args[0]
                # SQL contains SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, or space
                sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH", "TRUNCATE", "GRANT", "REVOKE"]
                if " " in arg or any(kw in arg.upper() for kw in sql_keywords):
                    sql_arg = arg
                else:
                    # Assume it's a database name
                    name_pos = arg
            elif len(args) == 2:
                # Two arguments: intelligently determine which is db name and which is SQL
                arg1, arg2 = args
                # SQL contains SELECT or space - more likely to be SQL
                sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH", "TRUNCATE", "GRANT", "REVOKE"]
                if " " in arg1 or any(kw in arg1.upper() for kw in sql_keywords):
                    sql_arg = arg1
                    name_pos = arg2
                elif " " in arg2 or any(kw in arg2.upper() for kw in sql_keywords):
                    sql_arg = arg2
                    name_pos = arg1
                else:
                    # Both look like db names, assume first is db, second is SQL
                    name_pos = arg1
                    sql_arg = arg2
            else:
                # More than 2 arguments - unusual, but treat first as db name, rest as SQL
                name_pos = args[0]
                sql_arg = " ".join(args[1:])

        # Determine database name: positional arg, then option, then active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        # Determine SQL: option, then positional arg
        sql = sql or sql_arg
        if not sql:
            click.echo(color_error("Error: SQL statement is required (use -s/--sql or positional arg)."), err=True)
            raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            db = load_db(name)
            result = exec_sql(db, sql)
            db.close_conn()

            if result["msg"]:
                click.echo(f"[WARNING] {result['msg']}")

            if result["err"]:
                click.echo(color_error(f"[ERROR] {result['err']}"), err=True)
                raise SystemExit(1)

            if not result["output"]:
                click.echo("Query executed successfully. No rows returned.")
            else:
                max_rows = RUBIK_CM.get("tools.exec_sql.max_rows", 32)
                max_width = RUBIK_CM.get("tools.exec_sql.max_width", 64)
                style = RUBIK_CM.get("tools.exec_sql.style", "DEFAULT")
                click.echo(table_display(result["output"], max_rows=max_rows, max_width=max_width, style=style))

        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)


def _parse_extra_args(args):
    """Parse extra CLI arguments into a dictionary."""
    kwargs = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            if "=" in arg:
                key, value = arg[2:].split("=", 1)
                kwargs[key] = _parse_value(value)
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                key = arg[2:]
                kwargs[key] = _parse_value(args[i + 1])
                i += 1
            else:
                kwargs[arg[2:]] = True
        i += 1
    return kwargs


def _parse_value(value: str):
    """Parse a string value to appropriate type."""
    # Try integer
    try:
        return int(value)
    except ValueError:
        pass
    # Try float
    try:
        return float(value)
    except ValueError:
        pass
    # Boolean-like
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    # Return as string
    return value
