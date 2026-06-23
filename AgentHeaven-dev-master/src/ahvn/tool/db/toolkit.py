from __future__ import annotations

__all__ = [
    "DatabaseToolkitFactory",
]

from typing import Dict, Any, Optional, List, ClassVar

from ..toolkit import Toolkit, ToolkitFactory, register_factory
from .exec_sql import toolspec_factory_builtins_execute_sql


@register_factory
class DatabaseToolkitFactory(ToolkitFactory):
    """\
    Factory for creating database toolkits.

    Creates a Toolkit containing database tools (exec_sql, etc.)
    bound to a specific Database instance.

    Example:
        >>> from ahvn.tool import get_factory
        >>> factory = get_factory("db")
        >>> toolkit = factory.create("my-db", provider="sqlite", database="test.db")
        >>> toolkit.run("exec_sql", query="SELECT 1")

        >>> # With pragmas and params
        >>> toolkit = factory.create(
        ...     "my-db",
        ...     provider="sqlite",
        ...     database="test.db",
        ...     pragmas=["journal_mode=WAL", "busy_timeout=5000"],
        ...     params={"check_same_thread": "false"},
        ... )
    """

    name: ClassVar[str] = "db"
    description: ClassVar[str] = (
        "Database toolkit with SQL execution and schema inspection. "
        "Connects to any SQLAlchemy-supported database (SQLite, PostgreSQL, MySQL, "
        "MSSQL, DuckDB, Oracle, etc.) and exposes an exec_sql tool for running "
        "queries and inspecting schemas. Supports dialect-specific pragmas, "
        "display formatting options, and result row/width limits."
    )

    @classmethod
    def args_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Database provider (sqlite, pg, mysql, mssql, duckdb, oracle, etc.)",
                },
                "database": {
                    "type": "string",
                    "description": "Database name or path",
                },
                "host": {
                    "type": "string",
                    "description": "Database host",
                },
                "port": {
                    "type": "integer",
                    "description": "Database port",
                },
                "username": {
                    "type": "string",
                    "description": "Database username",
                },
                "password": {
                    "type": "string",
                    "description": "Database password",
                },
                "params": {
                    "type": "object",
                    "description": "Connection URL params (e.g. {'check_same_thread': 'false'})",
                },
                "pragmas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dialect-specific pragmas (e.g. ['journal_mode=WAL', 'busy_timeout=5000'])",
                },
                "heal_sql": {
                    "type": "boolean",
                    "description": "Enable one-shot SQL healing fallback for exec_sql when a query fails.",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Maximum rows to display (default: from config)",
                },
                "max_width": {
                    "type": "integer",
                    "description": "Maximum column width (default: from config)",
                },
                "style": {
                    "type": "string",
                    "description": "Table display style (DEFAULT, MARKDOWN, PLAIN_COLUMNS, etc.)",
                },
            },
            "required": ["provider", "database"],
        }

    @classmethod
    def create(
        cls,
        toolkit_name: str,
        provider: str = "sqlite",
        database: str = "",
        params: Optional[Dict[str, Any]] = None,
        pragmas: Optional[List[str]] = None,
        max_rows: Optional[int] = None,
        max_width: Optional[int] = None,
        style: Optional[str] = None,
        heal_sql: bool = True,
        **db_kwargs,
    ) -> Toolkit:
        """\
        Create a database toolkit bound to a specific database.

        Args:
            toolkit_name (str): Unique name for this toolkit.
            provider (str): Database provider.
            database (str): Database name or path.
            params (Dict[str, Any], optional): Connection URL params.
            pragmas (List[str], optional): Dialect-specific pragmas.
            heal_sql (bool): Whether exec_sql should auto-attempt SQL healing on failure.
            max_rows (int, optional): Max rows for display.
            max_width (int, optional): Max column width for display.
            style (str, optional): Table display style.
            **db_kwargs: Additional args passed to Database constructor (host, port, username, password, etc.).

        Returns:
            Toolkit: A toolkit with database tools.
        """
        from ...utils.db import Database

        # Build Database kwargs including params and pragmas
        init_kwargs = dict(**db_kwargs)
        if params:
            init_kwargs["params"] = params
        if pragmas:
            init_kwargs["pragmas"] = pragmas

        db = Database(provider=provider, database=database, **init_kwargs)

        # Build display kwargs
        display_kwargs = {}
        if max_rows is not None:
            display_kwargs["max_rows"] = max_rows
        if max_width is not None:
            display_kwargs["max_width"] = max_width
        if style is not None:
            display_kwargs["style"] = style

        exec_sql_tool = toolspec_factory_builtins_execute_sql(db, heal_sql=heal_sql, **display_kwargs)

        # Sanitize args for persistence (exclude password)
        persist_args: Dict[str, Any] = {"provider": provider, "database": database}
        for k in ("host", "port", "username"):
            if k in db_kwargs:
                persist_args[k] = db_kwargs[k]
        if params:
            persist_args["params"] = params
        if pragmas:
            persist_args["pragmas"] = pragmas
        if heal_sql:
            persist_args["heal_sql"] = True
        if max_rows is not None:
            persist_args["max_rows"] = max_rows
        if max_width is not None:
            persist_args["max_width"] = max_width
        if style is not None:
            persist_args["style"] = style

        dialect = db.dialect
        short_description = (
            f"Toolkit for executing SQL queries against a {provider} database (`{database}`), " f"dialect `{dialect}`, with formatted table output."
        )
        description = (
            f"The `{toolkit_name}` Toolkit provides database tools for executing SQL queries "
            f"against a {provider} database (`{database}`), using the `{dialect}` SQL dialect. "
            f"Results are returned as formatted tables."
        )

        return Toolkit(
            name=toolkit_name,
            short_description=short_description,
            description=description,
            tools={"exec_sql": exec_sql_tool},
        )

    @classmethod
    def _register_create_typer(cls, create_app, cli_ref):
        import typer

        @create_app.command(
            "db",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create db my-sqlite -P sqlite -D ./test.db\n"
                "  ahvn mcp create db my-pg -P pg -D mydb -H localhost -p 5432 -u postgres\n"
                "  ahvn mcp create db my-sqlite-wal -P sqlite -D ./test.db "
                "--pragmas journal_mode=WAL --pragmas busy_timeout=5000"
            ),
        )
        def cmd(
            name: str = typer.Argument(..., help="Unique name for the toolkit."),
            provider: str = typer.Option(..., "-P", "--provider", help="Database provider (sqlite, pg, mysql, mssql, duckdb, oracle, etc.)."),
            database: str = typer.Option(..., "-D", "--database", help="Database name or path."),
            host: Optional[str] = typer.Option(None, "-H", "--host", help="Database host."),
            port: Optional[int] = typer.Option(None, "-p", "--port", help="Database port."),
            username: Optional[str] = typer.Option(None, "-u", "--username", help="Database username."),
            password: Optional[str] = typer.Option(None, "--password", help="Database password."),
            pragmas: Optional[List[str]] = typer.Option(None, "--pragmas", help="Dialect-specific pragmas (repeatable)."),
            heal_sql: bool = typer.Option(False, "--heal-sql/--no-heal-sql", help="Enable SQL healing fallback in exec_sql."),
            max_rows: Optional[int] = typer.Option(None, "--max-rows", help="Maximum rows to display."),
            max_width: Optional[int] = typer.Option(None, "--max-width", help="Maximum column width."),
            style: Optional[str] = typer.Option(None, "--style", help="Table display style (DEFAULT, MARKDOWN, PLAIN_COLUMNS, etc.)."),
        ):
            args = [f"provider={provider}", f"database={database}"]
            if host:
                args.append(f"host={host}")
            if port is not None:
                args.append(f"port={port}")
            if username:
                args.append(f"username={username}")
            if password:
                args.append(f"password={password}")
            for p in pragmas or []:
                args.append(f"pragmas={p}")
            args.append(f"heal_sql={heal_sql}")
            if max_rows is not None:
                args.append(f"max_rows={max_rows}")
            if max_width is not None:
                args.append(f"max_width={max_width}")
            if style:
                args.append(f"style={style}")
            cli_ref.do_create("db", name, args)

    @classmethod
    def _register_create_click(cls, create_group, cli_ref):
        import click

        @create_group.command(
            "db",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create db my-sqlite -P sqlite -D ./test.db\n"
                "  ahvn mcp create db my-pg -P pg -D mydb -H localhost -p 5432 -u postgres\n"
                "  ahvn mcp create db my-sqlite-wal -P sqlite -D ./test.db "
                "--pragmas journal_mode=WAL --pragmas busy_timeout=5000"
            ),
        )
        @click.argument("name")
        @click.option("-P", "--provider", required=True, help="Database provider.")
        @click.option("-D", "--database", required=True, help="Database name or path.")
        @click.option("-H", "--host", default=None, help="Database host.")
        @click.option("-p", "--port", default=None, type=int, help="Database port.")
        @click.option("-u", "--username", default=None, help="Database username.")
        @click.option("--password", default=None, help="Database password.")
        @click.option("--pragmas", multiple=True, help="Dialect-specific pragmas (repeatable).")
        @click.option("--heal-sql/--no-heal-sql", default=False, help="Enable SQL healing fallback in exec_sql.")
        @click.option("--max-rows", default=None, type=int, help="Maximum rows to display.")
        @click.option("--max-width", default=None, type=int, help="Maximum column width.")
        @click.option("--style", default=None, help="Table display style.")
        def cmd(name, provider, database, host, port, username, password, pragmas, heal_sql, max_rows, max_width, style):
            args = [f"provider={provider}", f"database={database}"]
            if host:
                args.append(f"host={host}")
            if port is not None:
                args.append(f"port={port}")
            if username:
                args.append(f"username={username}")
            if password:
                args.append(f"password={password}")
            for p in pragmas:
                args.append(f"pragmas={p}")
            args.append(f"heal_sql={heal_sql}")
            if max_rows is not None:
                args.append(f"max_rows={max_rows}")
            if max_width is not None:
                args.append(f"max_width={max_width}")
            if style:
                args.append(f"style={style}")
            cli_ref.do_create("db", name, args)
