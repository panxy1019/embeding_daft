"""\
Tool CLI commands for RubikSQL.

Provides direct CLI access to individual tools like db_info, tab_info, col_info, fd_check, and fuzzy_enum.
"""

import click
from typing import List, Optional


def _format_tool_result(result: dict) -> str:
    """Format a tool result for display."""
    parts = []
    if result.get("msg"):
        parts.append(f"[INFO] {result['msg']}")
    if result.get("err"):
        parts.append(f"[ERROR] {result['err']}")
    if result.get("output") is not None:
        output = result["output"]
        # Handle different output types
        if isinstance(output, str):
            parts.append(output)
        elif isinstance(output, bool):
            parts.append(str(output))
        elif isinstance(output, list):
            # Format list results (e.g., from fuzzy_enum)
            for item in output:
                if hasattr(item, "name") and hasattr(item, "metadata"):
                    from ahvn.utils.basic.config_utils import dget

                    score = dget(item.metadata, "search.returns.score", 0.0)
                    parts.append(f"{item.name} (score: {score:.3f})")
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    parts.append(str(item))
            if not output:
                parts.append("No results found.")
        else:
            parts.append(str(output))
    return "\n".join(parts) if parts else "No output."


def register_tool_commands(cli):
    """\
    Register the tool commands to the CLI.
    """

    @cli.group(
        "tool",
        help="""\
Direct tool access commands.

These commands provide direct CLI access to individual RubikSQL tools,
allowing you to query database and table information without using the agent.

Examples:
  rubiksql tool db_info -n superhero
  rubiksql tool tab_info -n superhero -tab heroes
  rubiksql tool col_info -n superhero -tab heroes -col name
  rubiksql tool fd_check -n superhero -tab heroes -X id -Y name
  rubiksql tool fuzzy_enum -n superhero "riverside" --tab schools
""",
    )
    @click.pass_context
    def tool(ctx):
        """\
        Direct tool access commands.
        """
        pass

    @tool.command(
        "db_info",
        help="""\
Get database schema information.

Displays the database schema including all tables and their relationships.

Example:
  rubiksql tool db_info -n superhero
  rubiksql tool db_info  # Uses active database
""",
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.pass_context
    def db_info_cmd(ctx, name_pos, name_opt):
        """\
        Get database schema information.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from ahvn.utils.basic.path_utils import pj
        from ahvn.utils.basic.file_utils import exists_file
        from ahvn.utils.basic.serialize_utils import load_json
        from rubiksql.db import RUBIK_DBM
        from rubiksql.klbase import RubikSQLKLBase
        from rubiksql.tools.db_info import db_info
        from rubiksql.api import get_active_db

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        # Check database exists
        if not RUBIK_DBM.db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            from rubiksql.api import load_kb

            kb = load_kb(name)
            result = db_info(kb, name)
            click.echo(_format_tool_result(result))

        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @tool.command(
        "tab_info",
        help="""\
Get table schema information.

Displays detailed information about specific tables.

Example:
  rubiksql tool tab_info -n superhero -t heroes
  rubiksql tool tab_info -t heroes  # Uses active database
""",
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--table", "-t", multiple=True, required=True, help="Table name(s).")
    def tab_info_cmd(name_pos, name_opt, table):
        """\
        Get table information.
        """
        from ahvn.utils.basic.color_utils import color_error
        from rubiksql.api import load_db, RUBIK_DBM, get_active_db
        from rubiksql.tools.tab_info import tab_info

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not RUBIK_DBM.db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            from rubiksql.api import load_kb

            kb = load_kb(name)
            # tab_info expects single table, not list - iterate if multiple
            for tab in table:
                result = tab_info(kb, name, tab)
                click.echo(_format_tool_result(result))
                if len(table) > 1:
                    click.echo()
        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @tool.command(
        "col_info",
        help="""\
Get column information.

Displays information about specific columns in a table.

Example:
  rubiksql tool col_info -n superhero -t heroes -c name
  rubiksql tool col_info -t heroes -c name  # Uses active database
""",
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--table", "-t", required=True, help="Table name.")
    @click.option("--column", "-c", multiple=True, required=True, help="Column name(s).")
    def col_info_cmd(name_pos, name_opt, table, column):
        """\
        Get column information.
        """
        from ahvn.utils.basic.color_utils import color_error
        from rubiksql.api import load_db, RUBIK_DBM, get_active_db
        from rubiksql.tools.col_info import col_info

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not RUBIK_DBM.db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            from rubiksql.api import load_kb

            kb = load_kb(name)
            # col_info expects single column, not list - iterate if multiple
            for col in column:
                result = col_info(kb, name, table, col)
                click.echo(_format_tool_result(result))
                if len(column) > 1:
                    click.echo()
        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @tool.command(
        "fd_check",
        help="""\
Check functional dependencies.

Tests if column set X determines column set Y in a table.

Example:
  rubiksql tool fd_check -n superhero -t heroes -x id -y name
  rubiksql tool fd_check -t heroes -x id -y name  # Uses active database
  rubiksql tool fd_check -t heroes -x id name -y age height  # Multiple columns
""",
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.option("--table", "-t", required=True, help="Table name.")
    @click.option("--x-cols", "-x", multiple=True, required=True, help="Determinant columns (X).")
    @click.option("--y-cols", "-y", multiple=True, required=True, help="Dependent columns (Y).")
    def fd_check_cmd(name_pos, name_opt, table, x_cols, y_cols):
        """\
        Check functional dependency X -> Y.
        """
        from ahvn.utils.basic.color_utils import color_error
        from rubiksql.api import load_kb, load_db, RUBIK_DBM, get_active_db
        from rubiksql.tools.fd_check import fd_check

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not RUBIK_DBM.db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        try:
            kb = load_kb(name)
            db = load_db(name)
            result = fd_check(kb, db, name, table, list(x_cols), list(y_cols))
            db.close_conn()
            click.echo(_format_tool_result(result))
        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)

    @tool.command(
        "fuzzy_enum",
        help="""\
Fuzzy search for values in database columns.

Example:
  rubiksql tool fuzzy_enum -n superhero "riverside"
  rubiksql tool fuzzy_enum "riverside"  # Uses active database
  rubiksql tool fuzzy_enum -n superhero "riverside" -t schools
""",
    )
    @click.argument("name_pos", required=False, default=None)
    @click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier.")
    @click.argument("value", required=True)
    @click.option("--table", "-t", multiple=True, help="Limit search to specific tables.")
    def fuzzy_enum_cmd(name_pos, name_opt, value, table):
        """\
        Fuzzy search for value.
        """
        from ahvn.utils.basic.color_utils import color_error, color_warning, color_grey
        from rubiksql.api import load_db, RUBIK_DBM, kb_status, get_active_db
        from rubiksql.tools.fuzzy_enum import fuzzy_enum

        # Use positional argument if provided, otherwise use option, otherwise use active database
        name = name_pos or name_opt
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use '<name>' or -n/--name to specify a database, or activate a database first."), err=True)
                raise SystemExit(1)

        if not RUBIK_DBM.db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        # Check if KB is built, as fuzzy_enum often relies on indices/KB
        # (Assuming implementation detail - but safer to warn if not built)
        st = kb_status(name)
        if not st["built"]:
            click.echo(color_warning(f"KB for '{name}' is not built. Fuzzy search may be limited or fail."))
            click.echo(color_grey(f"Run 'rubiksql build -n {name}' first."))

        try:
            from rubiksql.api import load_kb

            kb = load_kb(name)
            # fuzzy_enum expects table list or None
            tabs = list(table) if table else None
            result = fuzzy_enum(kb, value, tabs)
            click.echo(_format_tool_result(result))
        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
