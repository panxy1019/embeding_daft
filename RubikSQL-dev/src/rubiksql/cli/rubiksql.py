"""\
RubikSQL CLI
"""

import click
from ahvn.cli.utils import AliasedGroup
from ahvn.utils.basic.log_utils import set_log_level
from rubiksql.version import __version__

from rubiksql.utils.config_utils import RUBIK_CM, HEAVEN_CM

# Configure logging early before any other imports trigger logging
# Suppress INFO/DEBUG messages from library code by default
set_log_level("WARNING", loggers=["ahvn", "rubiksql", "lance", "lancedb"])

# Import command registration functions
from .config_cli import register_config_commands
from .db_cli import register_db_commands
from .build_cli import register_build_commands
from .search_cli import register_search_commands

# from .kb_cli import register_kb_commands
from .ask_cli import register_ask_commands
from .tool_cli import register_tool_commands
from .skill_cli import register_skill_commands


class RubikSQLAliasedGroup(AliasedGroup):
    """\
    RubikSQL CLI group with custom aliases.
    """

    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv

        # RubikSQL specific aliases
        aliases = {
            "ls": "list",
            "rm": "remove",
            # Add more aliases if needed
        }
        if cmd_name in aliases:
            return click.Group.get_command(self, ctx, aliases[cmd_name])

        return super().get_command(ctx, cmd_name)


@click.group(
    cls=RubikSQLAliasedGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
    help="""\
RubikSQL CLI

Manage RubikSQL configurations and tasks.
""",
)
@click.version_option(__version__, "-v", "--version", message="v%(version)s", help="Show the RubikSQL version and exit.")
@click.option("--verbose", "-V", is_flag=True, help="Enable verbose logging output.")
@click.pass_context
def cli(ctx, verbose):
    """\
    RubikSQL CLI.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if verbose:
        # Re-enable detailed logging when verbose flag is set
        set_log_level("DEBUG", loggers=["ahvn", "rubiksql"])


@cli.command(
    help="""\
Initialize or reset the global RubikSQL configuration.

This command sets up RubikSQL for all projects on your system, similar to 'conda init'.
Use --reset to restore default configuration values.
"""
)
@click.option("--reset", "-r", is_flag=True, help="Reset the global RubikSQL configuration to default values.")
def setup(reset):
    """\
    Initialize or reset the global RubikSQL configuration.
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.utils.config_utils import RUBIK_CM

    try:
        if RUBIK_CM.setup(reset=reset):
            click.echo(color_success(f"RubikSQL globally initialized{' (reset)' if reset else ''}."))
        else:
            click.echo(color_error("Failed to initialize RubikSQL globally."), err=True)
    except Exception as e:
        click.echo(color_error(f"Error: {e}"), err=True)


@cli.command(
    help="""\
Join path strings using RubikSQL's path utility.

This command uses the rpj() function to join path components with proper
handling of special characters like ~, &, >, and forward slashes.
The result is returned as an absolute path.
"""
)
@click.argument("string", metavar="STRING", nargs=-1, required=True)
def pj(string):
    """\
    Join path strings using rpj utility.
    """
    from rubiksql.utils.config_utils import rpj
    from ahvn.utils.basic.color_utils import color_error

    try:
        # Join all arguments into a single string
        path_str = " ".join(string)
        result = rpj(path_str, abs=True)
        click.echo(result)
    except Exception as e:
        click.echo(color_error(f"Error: {e}"), err=True)


# Register config commands
register_config_commands(cli, cm=RUBIK_CM, name="rubiksql")

# Register db commands
register_db_commands(cli)

# Register build commands
register_build_commands(cli)

# Register search commands
register_search_commands(cli)

# Register kb commands
# register_kb_commands(cli)

# Register ask commands
register_ask_commands(cli)

# Register tool commands
register_tool_commands(cli)

# Register skill commands
register_skill_commands(cli)


# =========================================================================
# Database Info Commands (Root-level with unified interface)
# =========================================================================


@cli.command("info", help="Display database/table/column metadata.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", default=None, help="Table ID to show (optional).")
@click.option("--column", "-c", default=None, help="Column ID to show (requires --table).")
@click.option("--reset", "-r", is_flag=True, help="Remove and recompute metadata from database (includes PK/FK extraction).")
@click.option("--update", "-u", is_flag=True, help="Refresh metadata from database (preserves desc/disabled/PKs/FKs).")
def info_cmd(name, table, column, reset, update):
    """\
    Display database/table/column metadata with optional refresh.

    Examples:
        rubiksql info -n mydb                    # Show database info (no update)
        rubiksql info -n mydb -u                 # Update metadata, preserve user edits
        rubiksql info -n mydb -r                 # Reset all (clear user edits, extract from DB)
        rubiksql info -n mydb -t users           # Show table info
        rubiksql info -n mydb -t users -c name   # Show column info
        rubiksql info                            # Show active database info
    """
    from ahvn.utils.basic.color_utils import color_success, color_error, color_warning, color_grey
    from ahvn.utils.basic.progress_utils import NoProgress
    from rubiksql.api import update_db_info, get_active_db, set_description, set_disabled
    from rubiksql.db.info import DatabaseInfo, TableInfo, ColumnInfo
    from rubiksql.cli.progress import RubikSQLCLIProgress

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # Determine if we should update or just display
        if not column:
            if reset:
                # Reset mode: Clear and rebuild, extracting all metadata from DB
                if table:
                    # Clear table: remove from db_info to force rebuild
                    from rubiksql.api import load_db_info

                    db_info = load_db_info(name)
                    if db_info and table in db_info.tables:
                        # Remove table to force full rebuild
                        db_info.tables.pop(table, None)
                        from rubiksql.db.manager import RUBIK_DBM

                        RUBIK_DBM.save_db_info(name, db_info)
                else:
                    # Clear entire database
                    from rubiksql.api import init_db_info

                    init_db_info(name, reset=True)

                # Update with reset=True to extract all metadata
                db_info = update_db_info(name, tab_id=table, col_id=column, reset=True, progress=RubikSQLCLIProgress)
            elif update:
                # Update mode: Refresh metadata but preserve user edits
                db_info = update_db_info(name, tab_id=table, col_id=column, reset=False, progress=RubikSQLCLIProgress)
            else:
                # Display only mode: Just load existing data
                from rubiksql.api import load_db_info

                db_info = load_db_info(name)
        else:
            # Column-level: just load without updating
            from rubiksql.api import load_db_info

            db_info = load_db_info(name)

        if db_info is None:
            click.echo(color_warning(f"No metadata found for '{name}'. Run 'rubiksql info -n {name} --update' to initialize."))
            return

        # Display based on scope
        if column:
            # Show column info
            if table not in db_info.tables:
                click.echo(color_error(f"Error: Table '{table}' not found in database info."), err=True)
                raise SystemExit(1)
            if column not in db_info.tables[table].columns:
                click.echo(color_error(f"Error: Column '{column}' not found in table '{table}' info."), err=True)
                raise SystemExit(1)

            tab_info = db_info.tables[table]
            col_info = tab_info.columns[column]

            click.echo(f"Column: {color_success(column)}")
            click.echo(color_grey("-" * 40))
            click.echo(f"Table: {tab_info.tab_id}")
            click.echo(f"Database: {db_info.name}")
            click.echo()
            click.echo(f"Data type (original): {col_info.datatype_orig}")
            click.echo(f"Data type (annotated): {col_info.datatype_anno or '(none)'}")
            click.echo(f"Is Primary Key: {color_success('Yes') if col_info.is_pk else 'No'}")
            click.echo(f"Enum index enabled: {col_info.enum_index_enabled if col_info.enum_index_enabled is not None else '(none)'}")
            click.echo(f"Description: {col_info.desc or '(none)'}")
            click.echo(f"Disabled: {col_info.disabled}")

        elif table:
            # Show table info
            if table not in db_info.tables:
                click.echo(color_error(f"Error: Table '{table}' not found in database info."), err=True)
                raise SystemExit(1)

            tab_info = db_info.tables[table]

            click.echo(f"Table: {color_success(table)}")
            click.echo(color_grey("-" * 40))
            click.echo(f"Database: {db_info.name}")
            click.echo()
            click.echo(f"Rows: {tab_info.n_rows}")
            click.echo(f"Columns: {tab_info.n_cols} ({tab_info.n_cols_enabled} enabled)")

            # Display Primary Keys
            if tab_info.pks:
                pk_list = ", ".join(tab_info.pks)
                click.echo(f"Primary Key(s): {color_success(pk_list)}")
            else:
                click.echo(f"Primary Key(s): {color_grey('(none)')}")

            # Display Foreign Keys
            if tab_info.fks:
                click.echo("Foreign Keys:")
                for fk in tab_info.fks:
                    fk_str = f"{fk['col_name']} → {fk['tab_ref']}.{fk['col_ref']}"
                    if fk.get("name"):
                        fk_str += f" ({fk['name']})"
                    click.echo(f"  {color_grey('→')} {fk_str}")
            else:
                click.echo(f"Foreign Keys: {color_grey('(none)')}")

            click.echo(f"Description: {tab_info.desc or '(none)'}")
            click.echo(f"Disabled: {tab_info.disabled}")
            click.echo()
            click.echo("Columns:")
            # Sort columns: enabled first, then by original order
            sorted_cols = sorted(tab_info.columns.items(), key=lambda item: (item[1].disabled, list(tab_info.columns.keys()).index(item[0])))
            for col_id, col_info in sorted_cols:
                if col_info.disabled:
                    # Grey name and grey bullet for disabled columns
                    bullet = color_grey("●")
                    name_str = color_grey(col_id)
                    type_str = color_grey(col_info.datatype_orig)
                    disabled_str = color_grey(" (disabled)")
                else:
                    # White name with green bullet for enabled columns
                    bullet = color_success("●")
                    name_str = col_id
                    type_str = col_info.datatype_orig
                    # Show annotated type if different from original
                    if col_info.datatype_anno and col_info.datatype_anno != col_info.datatype_orig:
                        type_str = f"{type_str} ({color_grey(col_info.datatype_anno)})"
                    disabled_str = ""

                # Add PK indicator
                pk_str = color_success(" [PK]") if col_info.is_pk else ""
                # Add enum index indicator if explicitly enabled
                enum_str = ""
                if col_info.enum_index_enabled is True:
                    enum_str = color_grey(" [enum]")
                desc_str = f" - {col_info.desc}" if col_info.desc else ""
                click.echo(f"  {bullet} {name_str}: {type_str}{pk_str}{enum_str}{desc_str}{disabled_str}")

        else:
            # Show database info
            click.echo(f"Database: {color_success(db_info.name)}")
            click.echo(color_grey("-" * 40))
            click.echo(f"Description: {db_info.desc or '(none)'}")
            click.echo(f"Disabled: {db_info.disabled}")
            click.echo()
            click.echo(f"Tables: {db_info.n_tabs} ({db_info.n_tabs_enabled} enabled)")
            click.echo(f"Columns: {db_info.n_cols} ({db_info.n_cols_enabled} enabled)")
            click.echo(f"Total rows: {db_info.get_total_row_count()}")
            click.echo()

            # TODO: Show KB status when kb_status is implemented
            # from rubiksql.api import kb_status
            # TODO: Show KB status when kb_status is implemented
            # from rubiksql.api import kb_status
            # kb_info = kb_status(name)
            # if not kb_info["exists"]:
            #     click.echo(color_grey("Knowledge Base: Not initialized"))
            # elif not kb_info["built"]:
            #     click.echo(color_warning("Knowledge Base: Not built"))
            #     click.echo(color_grey(f"  Run 'rubiksql build -n {name}' to build."))
            # else:
            #     status = kb_info["status"]
            #     progress = kb_info["progress"]
            #     if status == "completed":
            #         click.echo(color_success("Knowledge Base: Built"))
            #     elif status == "running":
            #         click.echo(color_warning(f"Knowledge Base: Building ({progress * 100:.0f}%)"))
            #     else:
            #         click.echo(f"Knowledge Base: {status} ({progress * 100:.0f}%)")
            # click.echo()

            click.echo("Tables:")
            # Sort tables: enabled first, then by original order
            sorted_tables = sorted(db_info.tables.items(), key=lambda item: (item[1].disabled, list(db_info.tables.keys()).index(item[0])))
            for tab_id, tab_info in sorted_tables:
                if tab_info.disabled:
                    # Grey name and grey bullet for disabled tables
                    bullet = color_grey("●")
                    name_str = color_grey(tab_id)
                    disabled_str = color_grey(" (disabled)")
                else:
                    # White name with green bullet for enabled tables
                    bullet = color_success("●")
                    name_str = tab_id
                    disabled_str = ""
                enabled_cols_str = f" ({tab_info.n_cols_enabled} enabled)" if tab_info.n_cols_enabled != tab_info.n_cols else ""
                desc_str = f" - {tab_info.desc}" if tab_info.desc else ""
                click.echo(f"  {bullet} {name_str}: {tab_info.n_rows} rows, {tab_info.n_cols} cols{enabled_cols_str}{desc_str}{disabled_str}")

    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@cli.group("update", help="Update database metadata.")
@click.pass_context
def update_group(ctx):
    """\
    Update database metadata.
    """
    pass


@update_group.command("desc", help="Update description for database objects.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", default=None, help="Table ID (optional).")
@click.option("--column", "-c", default=None, help="Column ID (requires --table).")
@click.option("--description", "-d", default=None, help="Description text.")
@click.argument("description_arg", required=False, default=None)
def update_desc_cmd(name, table, column, description, description_arg):
    """\
    Update description for database objects.

    Examples:
        rubiksql update desc -n mydb -d "My database"                # Set database description
        rubiksql update desc -n mydb "My database"                   # Set database description (positional)
        rubiksql update desc -n mydb -t users -d "User table"         # Set table description
        rubiksql update desc -n mydb -t users "User table"           # Set table description (positional)
        rubiksql update desc -n mydb -t users -c name -d "User name" # Set column description
        rubiksql update desc -d "My database"                        # Set active database description
        rubiksql update desc "My database"                           # Set active database description (positional)
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_description, get_active_db, update_db_info

    # Use positional argument if --description/-d not provided
    desc = description or description_arg
    if not desc:
        click.echo(color_error("Error: Description is required (use -d/--description or positional argument)."), err=True)
        raise SystemExit(1)

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # Set description
        set_description(name, tab_id=table, col_id=column, description=desc)

        # Trigger update to refresh metadata
        update_db_info(name, tab_id=table, col_id=column)

        # Show success message
        if column:
            click.echo(color_success(f"Updated description for column '{column}' in table '{table}'."))
        elif table:
            click.echo(color_success(f"Updated description for table '{table}'."))
        else:
            click.echo(color_success(f"Updated description for database '{name}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("enable", help="Enable database objects (included in knowledge base building).")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", default=None, help="Table ID to enable (optional).")
@click.option("--column", "-c", default=None, help="Column ID to enable (requires --table).")
def update_enable_cmd(name, table, column):
    """\
    Enable database objects.

    Examples:
        rubiksql update enable -n mydb                              # Enable all tables
        rubiksql update enable -n mydb -t users                       # Enable single table
        rubiksql update enable -n mydb -t users -c pass               # Enable single column
        rubiksql update enable                                       # Enable all tables in active database
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_disabled, get_active_db

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # API call handles all logic including updating enabled counts
        set_disabled(name, tab_id=table, col_id=column, disabled=False)

        # Show success message
        if column:
            click.echo(color_success(f"Enabled column '{column}' in table '{table}'."))
        elif table:
            click.echo(color_success(f"Enabled table '{table}'."))
        else:
            click.echo(color_success(f"Enabled all tables in database '{name}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("disable", help="Disable database objects (excluded from knowledge base building).")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", default=None, help="Table ID to disable (optional).")
@click.option("--column", "-c", default=None, help="Column ID to disable (requires --table).")
def update_disable_cmd(name, table, column):
    """\
    Disable database objects.

    Examples:
        rubiksql update disable -n mydb                              # Disable all tables
        rubiksql update disable -n mydb -t users                       # Disable single table
        rubiksql update disable -n mydb -t users -c pass               # Disable single column
        rubiksql update disable                                       # Disable all tables in active database
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_disabled, get_active_db

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # API call handles all logic including updating enabled counts
        set_disabled(name, tab_id=table, col_id=column, disabled=True)

        # Show success message
        if column:
            click.echo(color_success(f"Disabled column '{column}' in table '{table}'."))
        elif table:
            click.echo(color_success(f"Disabled table '{table}'."))
        else:
            click.echo(color_success(f"Disabled all tables in database '{name}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("type", help="Update human-annotated datatype for a column.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", required=True, help="Table ID.")
@click.option("--column", "-c", required=True, help="Column ID.")
@click.option(
    "--datatype",
    "-d",
    default=None,
    help="Annotated datatype (LONGTEXT, DATETIME, IDENTIFIER, CATEGORICAL, INTEGER, FLOAT, TEXT, UNKNOWN, null). Case-insensitive.",
)
@click.argument("datatype_arg", required=False, default=None)
def update_type_cmd(name, table, column, datatype, datatype_arg):
    """\
    Update human-annotated datatype for a column.

    The datatype must be one of: LONGTEXT, DATETIME, IDENTIFIER, CATEGORICAL, INTEGER, FLOAT, TEXT, UNKNOWN, or null.
    Input is case-insensitive and will be converted to uppercase. Use "null" or empty string to clear the annotation.

    Examples:
        rubiksql update type -n mydb -t users -c name -d TEXT           # Set datatype
        rubiksql update type -t users -c status CATEGORICAL              # Set datatype (positional, active db)
        rubiksql update type -t users -c name null                       # Clear datatype annotation (using null)
        rubiksql update type -t users -c name -d ""                      # Clear datatype annotation (empty string)
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_datatype_anno, get_active_db

    # Use positional argument if --datatype/-d not provided
    datatype_anno = datatype or datatype_arg
    if datatype_anno is None:
        click.echo(color_error("Error: Datatype is required (use -d/--datatype or positional argument)."), err=True)
        raise SystemExit(1)

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # Set datatype annotation (will be validated and normalized)
        set_datatype_anno(name, tab_id=table, col_id=column, datatype_anno=datatype_anno)

        # Show success message
        if datatype_anno and datatype_anno.lower() != "null":
            click.echo(color_success(f"Set datatype annotation for column '{column}' in table '{table}' to '{datatype_anno.upper()}'."))
        else:
            click.echo(color_success(f"Cleared datatype annotation for column '{column}' in table '{table}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("enable-enum", help="Enable enum indexing for a column.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", required=True, help="Table ID.")
@click.option("--column", "-c", required=True, help="Column ID.")
def update_enable_enum_cmd(name, table, column):
    """\
    Enable enum indexing for a column.

    When enabled, enum values for this column will be indexed during knowledge base building.

    Examples:
        rubiksql update enable-enum -n mydb -t users -c status       # Enable enum indexing
        rubiksql update enable-enum -t users -c status               # Enable enum indexing (active db)
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_enum_index_enabled, get_active_db

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # Enable enum indexing
        set_enum_index_enabled(name, tab_id=table, col_id=column, enum_index_enabled=True)

        # Show success message
        click.echo(color_success(f"Enabled enum indexing for column '{column}' in table '{table}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("disable-enum", help="Disable enum indexing for a column.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", required=True, help="Table ID.")
@click.option("--column", "-c", required=True, help="Column ID.")
def update_disable_enum_cmd(name, table, column):
    """\
    Disable enum indexing for a column.

    When disabled, enum values for this column will not be indexed during knowledge base building.

    Examples:
        rubiksql update disable-enum -n mydb -t users -c status      # Disable enum indexing
        rubiksql update disable-enum -t users -c status              # Disable enum indexing (active db)
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import set_enum_index_enabled, get_active_db

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        # Disable enum indexing
        set_enum_index_enabled(name, tab_id=table, col_id=column, enum_index_enabled=False)

        # Show success message
        click.echo(color_success(f"Disabled enum indexing for column '{column}' in table '{table}'."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


# NOTE: Primary keys are read-only and extracted from the database schema.
# They cannot be manually added or removed through CLI/API - use the database's
# ALTER TABLE commands to modify PKs, then run `rubiksql info -r` to update metadata.


@update_group.command("add-fk", help="Add a foreign key to a table.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", required=True, help="Table ID.")
@click.option("--column", "-c", required=True, help="Column ID in this table.")
@click.option("--ref-table", "-rt", required=True, help="Referenced table name.")
@click.option("--ref-column", "-rc", required=True, help="Referenced column name.")
@click.option("--fk-name", "-fk", default=None, help="Optional constraint name.")
def update_add_fk_cmd(name, table, column, ref_table, ref_column, fk_name):
    """\
    Add a foreign key to a table.

    Examples:
        rubiksql update add-fk -n mydb -t orders -c user_id --ref-table users --ref-column id
        rubiksql update add-fk -t orders -c user_id --ref-table users --ref-column id --fk-name fk_orders_users
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import get_active_db
    from rubiksql.db.manager import RUBIK_DBM

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        RUBIK_DBM.add_fk(name, table, column, ref_table, ref_column, fk_name)
        fk_str = f"{column} → {ref_table}.{ref_column}"
        if fk_name:
            fk_str += f" ({fk_name})"
        click.echo(color_success(f"Added foreign key: {fk_str}"))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@update_group.command("remove-fk", help="Remove a foreign key from a table.")
@click.option("--name", "-n", required=False, help="Registered database identifier (defaults to active database).")
@click.option("--table", "-t", required=True, help="Table ID.")
@click.option("--column", "-c", default=None, help="Column ID in this table (optional filter).")
@click.option("--ref-table", "-rt", default=None, help="Referenced table name (optional filter).")
@click.option("--ref-column", "-rc", default=None, help="Referenced column name (optional filter).")
@click.option("--fk-name", "-fk", default=None, help="FK constraint name (optional filter).")
def update_remove_fk_cmd(name, table, column, ref_table, ref_column, fk_name):
    """\
    Remove a foreign key from a table.

    Filtering logic:
    - Specify --fk-name alone to remove by constraint name
    - Specify -c/-rt/-rc to filter by column/table/column criteria
    - Combine both to match all criteria

    Examples:
        rubiksql update remove-fk -t orders --fk-name fk_user                            # Remove FK by name
        rubiksql update remove-fk -t orders -c user_id                                   # Remove all FKs on column
        rubiksql update remove-fk -t orders -c user_id -rt users                         # Remove FKs to specific table
        rubiksql update remove-fk -t orders -c user_id -rt users -rc id                  # Remove specific FK
        rubiksql update remove-fk -t orders -c user_id --fk-name fk_user                 # Remove by both criteria
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import get_active_db
    from rubiksql.db.manager import RUBIK_DBM

    # Use active database if name not provided
    if name is None:
        name = get_active_db()
        if name is None:
            click.echo(color_error("Error: No active database. Use -n/--name to specify a database, or activate a database first."), err=True)
            raise SystemExit(1)

    try:
        RUBIK_DBM.remove_fk(name, table, column, ref_table, ref_column, fk_name)

        # Build description of what was removed
        criteria = []
        if column:
            criteria.append(f"column '{column}'")
        if ref_table and ref_column:
            criteria.append(f"to '{ref_table}.{ref_column}'")
        elif ref_table:
            criteria.append(f"to table '{ref_table}'")
        if fk_name:
            criteria.append(f"named '{fk_name}'")

        fk_str = "Foreign key"
        if criteria:
            fk_str += " (" + ", ".join(criteria) + ")"

        click.echo(color_success(f"Removed {fk_str}"))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@cli.command(
    "activate",
    help="""\
Activate a database as the default.

After activation, all commands can omit the --name/-n argument and will
use this database by default.

Examples:
  rubiksql activate mydb               # Activate mydb as default (positional)
  rubiksql activate -n mydb            # Activate mydb as default (option)
""",
)
@click.argument("name_pos", required=False, default=None)
@click.option("--name", "-n", "name_opt", required=False, help="Registered database identifier to activate.")
def activate_cmd(name_pos, name_opt):
    """\
    Activate a database as the default.
    """
    from ahvn.utils.basic.color_utils import color_success, color_error
    from rubiksql.api import activate_db

    # Use positional argument if provided, otherwise use option
    name = name_pos or name_opt

    if not name:
        click.echo(color_error("Error: Database name is required. Use 'rubiksql activate <name>' or 'rubiksql activate -n <name>'."), err=True)
        raise SystemExit(1)

    try:
        actual_name = activate_db(name)
        click.echo(color_success(f"Activated '{actual_name}' as default database."))
    except ValueError as e:
        click.echo(color_error(f"Error: {e}"), err=True)
        raise SystemExit(1)


@cli.command(
    "deactivate",
    help="""\
Deactivate the current active database.

After deactivation, all commands will require the --name/-n argument.

Examples:
  rubiksql deactivate                    # Deactivate active database
""",
)
def deactivate_cmd():
    """\
    Deactivate the current active database.
    """
    from ahvn.utils.basic.color_utils import color_success, color_warning
    from rubiksql.api import deactivate_db, get_active_db

    current = get_active_db()
    if current is None:
        click.echo(color_warning("No active database to deactivate."))
        return

    deactivate_db()
    click.echo(color_success(f"Deactivated '{current}'."))


def main():
    cli()


if __name__ == "__main__":
    main()
