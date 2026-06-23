"""\
Knowledge search CLI commands for RubikSQL.
"""

import click


def register_search_commands(cli):
    """\
    Register search command to the CLI.
    """

    @cli.command(
        "search",
        help="""\
Search for knowledge in the database.

Uses hierarchical -n/-t/-c/-e interface to retrieve entity knowledge.
The search uses the "facet" mode which calls get_entity() to retrieve
database, table, column, or enum knowledge based on the specified hierarchy.

Hierarchy:
  -n only:        Retrieve database knowledge
  -n -t:          Retrieve table knowledge
  -n -t -c:       Retrieve column knowledge
  -n -t -c -e:    Retrieve enum knowledge

Examples:
  rubiksql search -n mydb                          # Database knowledge
  rubiksql search -n mydb -t users                 # Table knowledge
  rubiksql search -n mydb -t users -c email        # Column knowledge
  rubiksql search -n mydb -t users -c status -e active  # Enum knowledge
  rubiksql search -t users                         # Use active database
  rubiksql search -t users -c email -m facet       # Explicit mode (default)
""",
    )
    @click.option("--name", "-n", required=False, help="Database identifier (defaults to active database).")
    @click.option("--table", "-t", default=None, help="Table ID for table/column/enum search.")
    @click.option("--column", "-c", default=None, help="Column ID for column/enum search (requires --table).")
    @click.option("--enum", "-e", default=None, help="Enum value for enum search (requires --table and --column).")
    @click.option("--mode", "-m", default="facet", help="Search mode (default: facet).")
    def search_cmd(name, table, column, enum, mode):
        """\
        Search for knowledge in the database.
        """
        from ahvn.utils.basic.color_utils import color_error, color_grey
        from rubiksql.api import search_knowledge, get_active_db, db_exists

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
        
        # Validate hierarchy: enum requires table and column
        if enum is not None and (table is None or column is None):
            click.echo(color_error("Error: --enum/-e requires both --table/-t and --column/-c to be specified."), err=True)
            raise SystemExit(1)

        try:
            # Search for knowledge
            result = search_knowledge(
                db_id=name,
                tab_id=table,
                col_id=column,
                enum_val=enum,
                mode=mode,
            )

            # Display result
            if result is None:
                # Describe what wasn't found
                if table is None:
                    entity = f"database '{name}'"
                elif column is None:
                    entity = f"table '{table}'"
                elif enum is None:
                    entity = f"column '{table}.{column}'"
                else:
                    entity = f"enum '{table}.{column}={enum}'"
                click.echo(color_grey(f"No knowledge found for {entity}."))
            else:
                click.echo(result)

        except ValueError as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)
