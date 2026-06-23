"""\
Knowledge base build CLI commands for RubikSQL.
"""

import click


def register_build_commands(cli):
    """\
    Register all KB build commands to the CLI.
    """

    @cli.group(
        "build",
        help="""\
Build knowledge base components.

Subcommands:
  database     Build DatabaseUKFT knowledge for the database
  table        Build TableUKFT knowledge for database tables
  column       Build ColumnUKFT knowledge for database columns
  column-type  Deduce and update column datatypes
  enum         Build EnumUKFT knowledge for enum values
  database-desc   Build database descriptions
  table-desc      Build table descriptions
  column-desc     Build column descriptions
  database-syn    Build database synonyms
  table-syn       Build table synonyms
  column-syn      Build column synonyms

Examples:
  rubiksql build database -n mydb                  # Build database knowledge
  rubiksql build database -u                       # Force update (uses active DB)
  rubiksql build table -n mydb                     # Build all tables
  rubiksql build table -n mydb -t users            # Build single table
  rubiksql build column -n mydb                    # Build all columns
  rubiksql build column -n mydb -t users -c name   # Build single column
  rubiksql build column-type -n mydb               # Deduce types for all columns
  rubiksql build column-type -t users -u           # Force re-deduce types for table
  rubiksql build enum -n mydb                      # Build enums for eligible columns
  rubiksql build enum -t users -c status           # Build enums for single column
  rubiksql build database-desc -n mydb             # Build database description
  rubiksql build table-desc -n mydb                # Build table descriptions
  rubiksql build table-desc -n mydb -t users       # Build description for single table
  rubiksql build column-desc -n mydb               # Build column descriptions
  rubiksql build column-desc -n mydb -t users -c name  # Build description for single column
  rubiksql build database-syn -n mydb              # Build database synonyms
  rubiksql build table-syn -n mydb                 # Build table synonyms
  rubiksql build table-syn -n mydb -t users        # Build synonyms for single table
  rubiksql build column-syn -n mydb                # Build column synonyms
  rubiksql build column-syn -n mydb -t users -c name   # Build synonyms for single column
""",
    )
    def build_cmd():
        """\
        Build knowledge base components.
        """
        pass

    @build_cmd.command(
        "column",
        help="""\
Build ColumnUKFT knowledge for database columns.

Supports hierarchical building:
- No -t/-c: Build all columns in all tables
- With -t only: Build all columns in that table
- With -t and -c: Build single column

By default, skips columns that already have knowledge.
Use --update/-u to force rebuild.

Examples:
  rubiksql build column -n mydb                    # All columns in database
  rubiksql build column -n mydb -t users           # All columns in 'users' table
  rubiksql build column -n mydb -t users -c name   # Single column
  rubiksql build column -t users                   # Use active database
  rubiksql build column -t users -u                # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for scoped building (optional).")
    @click.option("--column", "-c", default=None, help="Column ID for single column building (requires --table).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if knowledge exists.")
    def build_column_cmd(name, table, column, update):
        """\
        Build ColumnUKFT knowledge for database columns.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_column, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        # Determine database name
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        # Validate hierarchy: column requires table
        if column is not None and table is None:
            click.echo(color_error("Error: --column/-c requires --table/-t to be specified."), err=True)
            raise SystemExit(1)

        # Build scope description
        if table is None:
            scope = f"all columns in database '{name}'"
        elif column is None:
            scope = f"all columns in table '{table}'"
        else:
            scope = f"column '{table}.{column}'"

        click.echo(f"Building {scope}{'  (updating)' if update else ''}...")

        try:
            # Build with progress bar
            with RubikSQLRichProgress() as progress:
                count = build_column(
                    db_id=name,
                    tab_id=table,
                    col_id=column,
                    update=update,
                    progress=progress,
                )

            # Success message
            if count == 0:
                click.echo(color_grey("No columns to build (all up to date)."))
            else:
                click.echo(color_success(f"✓ Built {count} column{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "column-type",
        help="""\
Deduce and update column datatypes in the knowledge base.

Uses deduction rules and LLM calls (for datetime parsing) to infer column types.
Updates both the ColumnUKFT knowledge and ensures consistency with db_info.yaml.

Deduction logic:
1. If existing type is not UNKNOWN and not updating, skip.
2. If updating, always prefer user datatype (datatype_anno) if provided.
3. Apply deduction rules: datetime, identifier, categorical, integer, float, text.

Supports hierarchical building:
- No -t/-c: Deduce types for all columns in all tables
- With -t only: Deduce types for all columns in that table
- With -t and -c: Deduce type for single column

By default, skips columns that already have types deduced.
Use --update/-u to force re-deduction.

Examples:
  rubiksql build column-type -n mydb                    # All columns in database
  rubiksql build column-type -n mydb -t users           # All columns in 'users' table
  rubiksql build column-type -n mydb -t users -c name   # Single column
  rubiksql build column-type -t users                   # Use active database
  rubiksql build column-type -t users -u                # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for scoped building (optional).")
    @click.option("--column", "-c", default=None, help="Column ID for single column building (requires --table).")
    @click.option("--update", "-u", is_flag=True, help="Force re-deduction even if type already deduced.")
    def build_column_type_cmd(name, table, column, update):
        """\
        Deduce and update column datatypes in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_column_type, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        # Determine database name
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        # Validate hierarchy: column requires table
        if column is not None and table is None:
            click.echo(color_error("Error: --column/-c requires --table/-t to be specified."), err=True)
            raise SystemExit(1)

        # Build scope description
        if table is None:
            scope = f"all columns in database '{name}'"
        elif column is None:
            scope = f"all columns in table '{table}'"
        else:
            scope = f"column '{table}.{column}'"

        click.echo(f"Deducing types for {scope}{'  (updating)' if update else ''}...")

        try:
            # Build with progress bar
            with RubikSQLRichProgress() as progress:
                count = build_column_type(
                    db_id=name,
                    tab_id=table,
                    col_id=column,
                    update=update,
                    progress=progress,
                )

            # Success message
            if count == 0:
                click.echo(color_grey("No columns to process (all types already deduced)."))
            else:
                click.echo(color_success(f"✓ Deduced types for {count} column{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "enum",
        help="""\
Build EnumUKFT knowledge for enum values in database columns.

Only builds enums for columns where:
- enum_index_enabled is True (explicit), OR
- enum_index_enabled is None (default) AND datatype is TEXT or CATEGORICAL

Supports hierarchical building:
- No -t/-c: Build enums for all eligible columns in all tables
- With -t only: Build enums for all eligible columns in that table
- With -t and -c: Build enums for single column (if eligible)

By default, skips columns that already have enums built.
Use --update/-u to force rebuild.

Examples:
  rubiksql build enum -n mydb                    # All eligible columns in database
  rubiksql build enum -n mydb -t users           # All eligible columns in 'users' table
  rubiksql build enum -n mydb -t users -c status # Single column
  rubiksql build enum -t users                   # Use active database
  rubiksql build enum -t users -u                # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for scoped building (optional).")
    @click.option("--column", "-c", default=None, help="Column ID for single column building (requires --table).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if enums exist.")
    def build_enum_cmd(name, table, column, update):
        """\
        Build EnumUKFT knowledge for enum values in database columns.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_enum, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        # Determine database name
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        # Validate hierarchy: column requires table
        if column is not None and table is None:
            click.echo(color_error("Error: --column/-c requires --table/-t to be specified."), err=True)
            raise SystemExit(1)

        # Build scope description
        if table is None:
            scope = f"all eligible columns in database '{name}'"
        elif column is None:
            scope = f"all eligible columns in table '{table}'"
        else:
            scope = f"column '{table}.{column}'"

        click.echo(f"Building enums for {scope}{'  (updating)' if update else ''}...")

        try:
            # Build with progress bar
            with RubikSQLRichProgress() as progress:
                count = build_enum(
                    db_id=name,
                    tab_id=table,
                    col_id=column,
                    update=update,
                    progress=progress,
                )

            # Success message
            if count == 0:
                click.echo(color_grey("No enums to build (all up to date or no eligible columns)."))
            else:
                click.echo(color_success(f"✓ Built {count} enum{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "database-desc",
        help="""\
Build database description in the knowledge base.

By default, skips if description already exists.
Use --update/-u to force rebuild.

Examples:
  rubiksql build database-desc -n mydb             # Build database description
  rubiksql build database-desc                     # Use active database
  rubiksql build database-desc -u                  # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if description exists.")
    def build_database_desc_cmd(name, update):
        """\
        Build database description in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_database_desc, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        click.echo(f"Building database description for '{name}'{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_database_desc(
                    db_id=name,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("Database description already exists (up to date)."))
            else:
                click.echo(color_success("✓ Built database description."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "table-desc",
        help="""\
Build table descriptions in the knowledge base.

Supports hierarchical building:
- No -t: Build descriptions for all tables
- With -t: Build description for a single table

By default, skips tables with existing descriptions.
Use --update/-u to force rebuild.

Examples:
  rubiksql build table-desc -n mydb                # All tables in database
  rubiksql build table-desc -n mydb -t users       # Single table
  rubiksql build table-desc -t users               # Use active database
  rubiksql build table-desc -t users -u            # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for single table building (optional).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if description exists.")
    def build_table_desc_cmd(name, table, update):
        """\
        Build table descriptions in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_table_desc, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        scope = f"all tables in database '{name}'" if table is None else f"table '{table}'"
        click.echo(f"Building descriptions for {scope}{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_table_desc(
                    db_id=name,
                    tab_id=table,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("No tables to process (all descriptions already exist)."))
            else:
                click.echo(color_success(f"✓ Built descriptions for {count} table{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "column-desc",
        help="""\
Build column descriptions in the knowledge base.

Supports hierarchical building:
- No -t/-c: Build descriptions for all columns in all tables
- With -t only: Build descriptions for all columns in that table
- With -t and -c: Build description for a single column

By default, skips columns with existing descriptions.
Use --update/-u to force rebuild.

Examples:
  rubiksql build column-desc -n mydb               # All columns in database
  rubiksql build column-desc -n mydb -t users      # All columns in 'users' table
  rubiksql build column-desc -n mydb -t users -c name   # Single column
  rubiksql build column-desc -t users              # Use active database
  rubiksql build column-desc -t users -u           # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for scoped building (optional).")
    @click.option("--column", "-c", default=None, help="Column ID for single column building (requires --table).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if description exists.")
    def build_column_desc_cmd(name, table, column, update):
        """\
        Build column descriptions in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_column_desc, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        if column is not None and table is None:
            click.echo(color_error("Error: --column/-c requires --table/-t to be specified."), err=True)
            raise SystemExit(1)

        if table is None:
            scope = f"all columns in database '{name}'"
        elif column is None:
            scope = f"all columns in table '{table}'"
        else:
            scope = f"column '{table}.{column}'"

        click.echo(f"Building descriptions for {scope}{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_column_desc(
                    db_id=name,
                    tab_id=table,
                    col_id=column,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("No columns to process (all descriptions already exist)."))
            else:
                click.echo(color_success(f"✓ Built descriptions for {count} column{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "database-syn",
        help="""\
Build database synonyms in the knowledge base.

By default, skips if synonyms already exist.
Use --update/-u to force rebuild.

Examples:
  rubiksql build database-syn -n mydb              # Build database synonyms
  rubiksql build database-syn                      # Use active database
  rubiksql build database-syn -u                   # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if synonyms exist.")
    def build_database_syn_cmd(name, update):
        """\
        Build database synonyms in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_database_synonyms, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        click.echo(f"Building database synonyms for '{name}'{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_database_synonyms(
                    db_id=name,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("Database synonyms already exist (up to date)."))
            else:
                click.echo(color_success("✓ Built database synonyms."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "table-syn",
        help="""\
Build table synonyms in the knowledge base.

Supports hierarchical building:
- No -t: Build synonyms for all tables
- With -t: Build synonyms for a single table

By default, skips tables with existing synonyms.
Use --update/-u to force rebuild.

Examples:
  rubiksql build table-syn -n mydb                 # All tables in database
  rubiksql build table-syn -n mydb -t users        # Single table
  rubiksql build table-syn -t users                # Use active database
  rubiksql build table-syn -t users -u             # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for single table building (optional).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if synonyms exist.")
    def build_table_syn_cmd(name, table, update):
        """\
        Build table synonyms in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_table_synonyms, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        scope = f"all tables in database '{name}'" if table is None else f"table '{table}'"
        click.echo(f"Building synonyms for {scope}{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_table_synonyms(
                    db_id=name,
                    tab_id=table,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("No tables to process (all synonyms already exist)."))
            else:
                click.echo(color_success(f"✓ Built synonyms for {count} table{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "column-syn",
        help="""\
Build column synonyms in the knowledge base.

Supports hierarchical building:
- No -t/-c: Build synonyms for all columns in all tables
- With -t only: Build synonyms for all columns in that table
- With -t and -c: Build synonyms for a single column

By default, skips columns with existing synonyms.
Use --update/-u to force rebuild.

Examples:
  rubiksql build column-syn -n mydb                # All columns in database
  rubiksql build column-syn -n mydb -t users       # All columns in 'users' table
  rubiksql build column-syn -n mydb -t users -c name   # Single column
  rubiksql build column-syn -t users               # Use active database
  rubiksql build column-syn -t users -u            # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for scoped building (optional).")
    @click.option("--column", "-c", default=None, help="Column ID for single column building (requires --table).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if synonyms exist.")
    def build_column_syn_cmd(name, table, column, update):
        """\
        Build column synonyms in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_column_synonyms, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        if column is not None and table is None:
            click.echo(color_error("Error: --column/-c requires --table/-t to be specified."), err=True)
            raise SystemExit(1)

        if table is None:
            scope = f"all columns in database '{name}'"
        elif column is None:
            scope = f"all columns in table '{table}'"
        else:
            scope = f"column '{table}.{column}'"

        click.echo(f"Building synonyms for {scope}{'  (updating)' if update else ''}...")

        try:
            with RubikSQLRichProgress() as progress:
                count = build_column_synonyms(
                    db_id=name,
                    tab_id=table,
                    col_id=column,
                    update=update,
                    progress=progress,
                )

            if count == 0:
                click.echo(color_grey("No columns to process (all synonyms already exist)."))
            else:
                click.echo(color_success(f"✓ Built synonyms for {count} column{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "table",
        help="""\
Build TableUKFT knowledge for database tables.

Supports hierarchical building:
- No -t: Build all tables in the database
- With -t: Build single table

By default, skips tables that already have knowledge.
Use --update/-u to force rebuild.

Examples:
  rubiksql build table -n mydb                     # All tables in database
  rubiksql build table -n mydb -t users            # Single table
  rubiksql build table -t users                    # Use active database
  rubiksql build table -t users -u                 # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for single table building (optional).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if knowledge exists.")
    def build_table_cmd(name, table, update):
        """\
        Build TableUKFT knowledge for database tables.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_table, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        # Determine database name
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        # Build scope description
        if table is None:
            scope = f"all tables in database '{name}'"
        else:
            scope = f"table '{table}'"

        click.echo(f"Building {scope}{'  (updating)' if update else ''}...")

        try:
            # Build with progress bar
            with RubikSQLRichProgress() as progress:
                count = build_table(
                    db_id=name,
                    tab_id=table,
                    update=update,
                    progress=progress,
                )

            # Success message
            if count == 0:
                click.echo(color_grey("No tables to build (all up to date)."))
            else:
                click.echo(color_success(f"✓ Built {count} table{'s' if count != 1 else ''}."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)

    @build_cmd.command(
        "database",
        help="""\
Build DatabaseUKFT knowledge for the database.

By default, skips if database knowledge already exists.
Use --update/-u to force rebuild.

Examples:
  rubiksql build database -n mydb                  # Build database knowledge
  rubiksql build database                          # Use active database
  rubiksql build database -u                       # Force update
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--update", "-u", is_flag=True, help="Force rebuild even if knowledge exists.")
    def build_database_cmd(name, update):
        """\
        Build DatabaseUKFT knowledge for the database.
        """
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey
        from rubiksql.api import build_database, get_active_db, db_exists
        from rubiksql.utils.progress_utils import RubikSQLRichProgress

        # Determine database name
        if name is None:
            name = get_active_db()
            if name is None:
                click.echo(color_error("Error: No active database. Use -n/--name to specify a database."), err=True)
                raise SystemExit(1)

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        click.echo(f"Building database '{name}'{'  (updating)' if update else ''}...")

        try:
            # Build with progress bar
            with RubikSQLRichProgress() as progress:
                count = build_database(
                    db_id=name,
                    update=update,
                    progress=progress,
                )

            # Success message
            if count == 0:
                click.echo(color_grey("Database knowledge already exists (up to date)."))
            else:
                click.echo(color_success("✓ Built database knowledge."))

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)