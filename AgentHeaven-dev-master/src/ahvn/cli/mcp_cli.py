"""\
MCP Toolkit CLI for AgentHeaven.

Provides a modular :class:`McpCLI` class with backend-agnostic ``do_*``
methods and ``register_click`` / ``register_typer`` / ``register`` dispatchers
following the same modular CLI pattern used across this package.

Commands:
    create       Create a new toolkit from a registered factory.
    list (ls)    List all toolkits.
    show         Display toolkit metadata and MCP client config.
    add          Add a toolkit from JSON string, URL, or file.
    import       Import toolkit payload from JSON file (alias for add --path).
    reset        Reset runtime state of a toolkit.
    rename (rn)  Rename a toolkit.
    remove (rm, del)  Remove a toolkit.
    clear (clr)  Remove all toolkits.
    stale        List toolkits with missing source paths.
    serve        Start MCP server for a toolkit.
    run          Execute a tool by qualified name.
    export       Export a toolkit as a Skills package.
"""

__all__ = ["McpCLI"]

from typing import List, Literal, Optional

from .tool_cli_utils import parse_kv_args


class McpCLI:
    """\
    Modular MCP Toolkit CLI.

    Core operations are in the ``do_*`` methods; ``register_click``,
    ``register_typer``, and ``register`` attach them to a CLI framework.
    """

    def __init__(self):
        from ..utils.basic.cli_utils import CLIOutput

        self.out = CLIOutput()
        self._manager = None

    @property
    def manager(self):
        """\
        Lazy-loaded ToolkitManager singleton.
        """
        if self._manager is None:
            from ..tool.manager import ToolkitManager

            self._manager = ToolkitManager()
        return self._manager

    # =====================================================================
    # Core operations (backend-agnostic)
    # =====================================================================

    def do_create(
        self,
        factory_name: str,
        name: str,
        args: Optional[List[str]] = None,
    ):
        """\
        Create a new toolkit from a registered factory.

        Args are passed as key=value pairs (e.g., provider=sqlite database=test.db).
        List-valued args can use the key multiple times (e.g., pragmas=journal_mode=WAL pragmas=busy_timeout=5000).
        The factory must be imported and registered in the current process.
        """
        parsed = parse_kv_args(args or [])

        try:
            from ..tool.toolkit import get_factory

            factory = get_factory(factory_name)
            toolkit = factory.create(name, **parsed)
            self.manager.add(
                toolkit,
                overwrite=False,
                source={
                    "factory": factory_name,
                    "args": parsed,
                },
            )
            self.out.success(f"Created toolkit '{name}' ({factory_name})")
            tools = toolkit.list_tools()
            self.out.info(f"Tools: {', '.join(tools)}")
        except Exception as e:
            self.out.error(str(e))

    def do_list(self):
        """\
        List all toolkits.
        """
        items = self.manager.list()
        if not items:
            self.out.info("No toolkits registered.")
            return

        rows = []
        for item in items:
            tools = item.get("tools", "?")
            if isinstance(tools, list):
                tools = ", ".join(tools)
            serving = "\u25cf" if item.get("serving") else ""
            rows.append(
                [
                    item["name"],
                    str(tools),
                    serving,
                ]
            )
        self.out.table("Toolkits", ["Name", "Tools", "Serving"], rows)

    def do_show(self, name: str):
        """\
        Display toolkit metadata and MCP client config JSON.

        Shows registry metadata (name, id, checksum, tools, dates, etc.)
        followed by the MCP client config for easy copy-paste.
        """
        try:
            info = self.manager.info(name)
        except KeyError as e:
            self.out.error(str(e))
            return

        # ── metadata table ──
        rows = []
        rows.append(["Name", info.get("name", name)])
        rows.append(["ID", str(info.get("id", ""))])
        rows.append(["Checksum", str(info.get("checksum", ""))])
        rows.append(["Description", info.get("short_description") or info.get("description", "")])
        tools = info.get("tools", [])
        rows.append(["Tools", ", ".join(tools) if isinstance(tools, list) else str(tools)])
        rows.append(["Runtime", info.get("runtime_type", "")])
        rows.append(["Factory", info.get("source_factory", "")])
        rows.append(["Serving", "yes" if info.get("serving") else "no"])
        rows.append(["Created", str(info.get("created_at", ""))[:19]])
        rows.append(["Updated", str(info.get("updated_at", ""))[:19]])
        self.out.table(f"Toolkit: {name}", ["Field", "Value"], rows)

        # ── MCP client config ──
        try:
            toolkit = self.manager.get(name)
            self.out.echo("")
            self.out.info("MCP client config (stdio):")
            self.out.echo(toolkit.to_mcp_json(transport="stdio"))
        except Exception:
            pass  # metadata was already shown; MCP config is best-effort

    def do_reset(self, name: str):
        """\
        Reset runtime state for a toolkit.
        """
        try:
            self.manager.reset(name)
            self.out.success(f"Reset runtime state for '{name}'")
        except (KeyError, RuntimeError) as e:
            self.out.error(str(e))

    def do_rename(self, old_name: str, new_name: str):
        """\
        Rename a toolkit.
        """
        try:
            self.manager.rename(old_name, new_name)
            self.out.success(f"Renamed '{old_name}' -> '{new_name}'")
        except (KeyError, RuntimeError) as e:
            self.out.error(str(e))

    def do_remove(self, name: str, skip_confirm: bool = False):
        """\
        Remove a toolkit.
        """
        if not skip_confirm:
            confirm = input(f"Remove toolkit '{name}'? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return
        try:
            self.manager.remove(name)
            self.out.success(f"Removed toolkit '{name}'")
        except KeyError as e:
            self.out.error(str(e))

    def do_clear(self, skip_confirm: bool = False):
        """\
        Remove all toolkits.
        """
        items = self.manager.list()
        if not items:
            self.out.info("No toolkits to remove.")
            return

        if not skip_confirm:
            confirm = input(f"Remove all {len(items)} toolkit(s)? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return

        count = self.manager.clear()
        self.out.success(f"Removed {count} toolkit(s).")

    def do_stale(self):
        """List toolkits with missing source paths."""
        items = self.manager.stale()
        if not items:
            self.out.info("No stale toolkits.")
            return
        rows = []
        for item in items:
            rows.append(
                [
                    item["id"],
                    item.get("source_factory", ""),
                    ", ".join(item.get("missing_paths", [])),
                    str(item.get("updated_at", ""))[:19],
                ]
            )
        self.out.table("Stale Toolkits", ["ID", "Factory", "Missing Paths", "Updated"], rows)

    def do_serve(
        self,
        names: List[str],
        stdio: bool = False,
        host: str = "127.0.0.1",
        port: int = 7001,
        copy: bool = False,
    ):
        """\
        Start MCP server(s) or generate MCP client config.

        Default transport is ``http`` on ``127.0.0.1:7001``.
        Use ``--stdio`` to generate a JSON config for client-spawned stdio mode
        (no server process is started; the MCP client launches the process itself).
        """
        if not names:
            self.out.error("No toolkit names specified.")
            return

        transport = "stdio" if stdio else "http"

        try:
            from ahvn.utils.basic.serialize_utils import dumps_json

            infos = self.manager.serve_many(names, transport=transport, host=host, port=port)
            merged: dict = {"mcpServers": {}}
            for info in infos:
                merged["mcpServers"].update(info.get("mcp_config", {}).get("mcpServers", {}))

            json_text = dumps_json(merged, indent=2)
            self.out.info("MCP client config (copy to your MCP client settings):")
            self.out.echo(json_text)
            self.out.echo("")

            if copy:
                from ..utils.basic.cmd_utils import clipboard

                if clipboard(json_text):
                    self.out.success("JSON config copied to clipboard.")
                else:
                    self.out.warn("Could not find a clipboard tool (pbcopy/clip/xclip/xsel).")

            if transport == "stdio":
                # Build a single-line command for VS Code MCP: Add Server → stdio
                # Each entry has "command" and "args" keys.
                cli_lines = []
                for entry in merged["mcpServers"].values():
                    cmd = entry.get("command", "")
                    args = entry.get("args", [])
                    parts = [cmd] + args
                    cli_lines.append(" ".join(parts))
                if cli_lines:
                    self.out.info("Single-line command:")
                    for line in cli_lines:
                        self.out.echo(line)
                    self.out.echo("")

                # stdio: no server process needed, just print config and exit
                self.out.info("stdio mode: no server started. " "Paste the config above into your MCP client settings.")
                return

            # HTTP: report running processes and block in foreground
            for info in infos:
                self.out.success(f"Serving '{info['name']}' via {info['transport']} (pid={info['pid']})")
                if "url" in info:
                    self.out.info(f"Endpoint: {info['url']}")

            self.out.info("Supervisor running in foreground. Press Ctrl+C to stop all toolkit servers.")
            self.manager.wait_forever()
            self.out.success("All toolkit servers stopped.")
        except (KeyError, RuntimeError) as e:
            self.out.error(str(e))

    @staticmethod
    def do_stdio(name: str):
        """\
        Run a toolkit as an MCP stdio server (blocking).

        This is a convenience entry point for MCP clients or manual use
        in an already-activated environment.  For cold-spawn scenarios
        (LM Studio, VS Code, etc.), the generated ``-c`` one-liner in
        ``ahvn mcp serve --stdio`` handles conda DLL PATH internally.
        """
        from ahvn.tool import TK_AHVN

        TK_AHVN.get(name).serve(transport="stdio")

    def do_run(self, qualified_name: str, args: Optional[List[str]] = None):
        """\
        Execute a tool by qualified name (toolkit_name.tool_name).

        Args are passed as key=value pairs (e.g., query="SELECT 1").
        """
        parsed = parse_kv_args(args or [])

        try:
            result = self.manager.run(qualified_name, **parsed)
            self.out.echo(str(result))
        except Exception as e:
            self.out.error(str(e))

    def do_export(self, name: str, output: Optional[str] = None):
        """\
        Export a toolkit as a Skills package directory.

        A subfolder named after the toolkit is always created inside ``output``.
        For example, ``ahvn mcp export ahvn -o ./skills/`` creates
        ``./skills/ahvn/SKILL.md``.

        Args:
            name: Toolkit name.
            output: Parent output directory (defaults to ``./``).
        """
        output = output or "./"
        try:
            abs_path = self.manager.export(name, output)
            self.out.success(f"Exported '{name}' to {abs_path}")
        except Exception as e:
            self.out.error(str(e))

    def do_add(
        self,
        name: Optional[str] = None,
        json_str: Optional[str] = None,
        url: Optional[str] = None,
        path: Optional[str] = None,
        overwrite: bool = False,
    ):
        """\
        Add a toolkit from one of three sources (exactly one required):

        * ``--json/-j``  inline MCP client config JSON string
        * ``--url/-u``   remote MCP server HTTP URL
        * ``--path/-p``  local JSON payload file path
        """
        import json as _json
        from ..tool.toolkit import Toolkit

        sources = [s for s in (json_str, url, path) if s is not None]
        if len(sources) != 1:
            self.out.error("Exactly one of --json/-j, --url/-u, or --path/-p is required.")
            return

        try:
            if json_str is not None:
                config = _json.loads(json_str)
                if not isinstance(config, dict):
                    raise ValueError("JSON input must be a dictionary.")
                toolkit = Toolkit.from_mcp_config(config, name=name)

            elif url is not None:
                toolkit = Toolkit.from_url(url, name=name)

            else:  # path
                from ..utils.basic.serialize_utils import load_json

                payload = load_json(path, strict=True)
                if not isinstance(payload, dict):
                    raise ValueError("Toolkit payload must be a dictionary.")
                manifest = payload.get("manifest", {})
                if not isinstance(manifest, dict):
                    manifest = {}
                target_name = name or payload.get("toolkit_name") or payload.get("name") or manifest.get("name")
                if not isinstance(target_name, str) or not target_name.strip():
                    raise ValueError("Toolkit payload must provide a toolkit name.")
                toolkit = Toolkit.from_capsules(
                    name=target_name.strip(),
                    capsules=payload.get("capsules", []),
                    short_description=manifest.get("short_description", ""),
                    description=manifest.get("description", ""),
                    instructions=manifest.get("instructions"),
                    runtime_type=manifest.get("runtime_type", "session"),
                    tool_enabled=manifest.get("tool_enabled"),
                )

            self.manager.add(toolkit, overwrite=overwrite)
            self.out.success(f"Added toolkit '{toolkit.name}'")
        except Exception as e:
            self.out.error(str(e))

    # =====================================================================
    # Click backend
    # =====================================================================

    def register_click(self, parent, group_name: str = "mcp"):
        """\
        Register MCP subcommands on a **Click** group.
        """
        import click
        from ..utils.basic.cli_utils import AliasedGroup, apply_cli_aliases

        ref = self

        @parent.group(group_name, cls=AliasedGroup, help="MCP toolkit operations: create, list, serve, run, etc.")
        def mcp_group():
            pass

        # ── create subgroup (auto-register factory subcommands) ──
        _create_help = (
            "Create a new toolkit from a registered factory category.\n\n"
            "Each category provides typed options specific to its toolkit type. "
            "Run 'create <category> --help' to see available options and examples."
        )

        @mcp_group.group("create", cls=AliasedGroup, help=_create_help)
        def create_group():
            pass

        from ..tool.toolkit import list_factories, get_factory

        for fname in list_factories():
            factory = get_factory(fname)
            factory.register_create_cli(create_group, ref, backend="click")

        @mcp_group.command("list", help="List all toolkits.")
        def list_cmd():
            ref.do_list()

        @mcp_group.command("show", help="Display toolkit details, tools, and signatures.")
        @click.argument("name")
        def show_cmd(name):
            ref.do_show(name)

        @mcp_group.command("reset", help="Reset runtime state of a toolkit.")
        @click.argument("name")
        def reset_cmd(name):
            ref.do_reset(name)

        @mcp_group.command("rename", help="Rename a toolkit.")
        @click.argument("old_name")
        @click.argument("new_name")
        def rename_cmd(old_name, new_name):
            ref.do_rename(old_name, new_name)

        @mcp_group.command("remove", help="Remove a toolkit.")
        @click.argument("name")
        @click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
        def remove_cmd(name, yes):
            ref.do_remove(name, skip_confirm=yes)

        @mcp_group.command("clear", help="Remove all toolkits.")
        @click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
        def clear_cmd(yes):
            ref.do_clear(skip_confirm=yes)

        @mcp_group.command("stale", help="List toolkits with missing source paths.")
        def stale_cmd():
            ref.do_stale()

        @mcp_group.command("serve", help="Start MCP server for one or more toolkits.")
        @click.argument("names", nargs=-1, required=True)
        @click.option("--stdio", "-std", is_flag=True, help="Use stdio transport instead of default HTTP service mode.")
        @click.option("--host", "-H", default="127.0.0.1", help="Host to bind for HTTP mode.")
        @click.option("--port", "-p", default=7001, type=int, help="Port to bind for HTTP mode.")
        @click.option("--copy", "-cp", is_flag=True, help="Copy MCP JSON config to clipboard.")
        def serve_cmd(names, stdio, host, port, copy):
            ref.do_serve(list(names), stdio=stdio, host=host, port=port, copy=copy)

        @mcp_group.command("stdio", help="Run a toolkit as MCP stdio server (used by MCP clients).", hidden=True)
        @click.argument("name")
        def stdio_cmd(name):
            ref.do_stdio(name)

        @mcp_group.command("run", help="Execute a tool by qualified name (toolkit.tool).")
        @click.argument("qualified_name")
        @click.argument("args", nargs=-1)
        def run_cmd(qualified_name, args):
            ref.do_run(qualified_name, list(args))

        @mcp_group.command("export", help="Export a toolkit as a Skills package.")
        @click.argument("name")
        @click.option("--output", "-o", default=None, help="Output directory (default: ./<name>/).")
        def export_cmd(name, output):
            ref.do_export(name, output)

        @mcp_group.command("import", help="Import toolkit payload from JSON.")
        @click.argument("file_path")
        @click.option("--name", "-n", default=None, help="Override toolkit name.")
        @click.option("--overwrite", "-w", is_flag=True, help="Overwrite existing toolkit.")
        def import_cmd(file_path, name, overwrite):
            ref.do_add(path=file_path, name=name, overwrite=overwrite)

        @mcp_group.command("add", help="Add a toolkit from JSON string, URL, or file.")
        @click.option("--json", "-j", "json_str", default=None, help="Inline MCP client config JSON string.")
        @click.option("--url", "-u", default=None, help="Remote MCP server HTTP URL.")
        @click.option("--path", "-p", default=None, help="Local JSON payload file path.")
        @click.option("--name", "-n", default=None, help="Override toolkit name.")
        @click.option("--overwrite", "-w", is_flag=True, help="Overwrite existing toolkit.")
        def add_cmd(json_str, url, path, name, overwrite):
            ref.do_add(name=name, json_str=json_str, url=url, path=path, overwrite=overwrite)

        apply_cli_aliases(create_group, "mcp.create")
        apply_cli_aliases(mcp_group, "mcp")

        return mcp_group

    # =====================================================================
    # Typer backend
    # =====================================================================

    def register_typer(self, parent, group_name: str = "mcp"):
        """\
        Register MCP subcommands on a **Typer** app.
        """
        import typer
        from typing import Annotated
        from ..utils.basic.cli_utils import AliasedTyper, apply_cli_aliases

        mcp_app = AliasedTyper(
            help="MCP toolkit operations: create, list, serve, run, etc.",
            no_args_is_help=True,
        )
        parent.add_typer(mcp_app, name=group_name)
        ref = self

        # ── create subgroup (auto-register factory subcommands) ──
        _create_help = (
            "Create a new toolkit from a registered factory category.\n\n"
            "Each category provides typed options specific to its toolkit type. "
            "Run 'create <category> --help' to see available options and examples."
        )
        create_app = AliasedTyper(
            help=_create_help,
            no_args_is_help=True,
        )
        mcp_app.add_typer(create_app, name="create")

        from ..tool.toolkit import list_factories, get_factory

        for fname in list_factories():
            factory = get_factory(fname)
            factory.register_create_cli(create_app, ref, backend="typer")

        @mcp_app.command("list", help="List all toolkits.")
        def list_cmd():
            ref.do_list()

        @mcp_app.command("show", help="Display toolkit details, tools, and signatures.")
        def show_cmd(
            name: Annotated[str, typer.Argument(help="Toolkit name to show.")],
        ):
            ref.do_show(name)

        @mcp_app.command("reset", help="Reset runtime state of a toolkit.")
        def reset_cmd(
            name: Annotated[str, typer.Argument(help="Toolkit name to reset.")],
        ):
            ref.do_reset(name)

        @mcp_app.command("rename", help="Rename a toolkit.")
        def rename_cmd(
            old_name: Annotated[str, typer.Argument(help="Current toolkit name.")],
            new_name: Annotated[str, typer.Argument(help="New toolkit name.")],
        ):
            ref.do_rename(old_name, new_name)

        @mcp_app.command("remove", help="Remove a toolkit.")
        def remove_cmd(
            name: Annotated[str, typer.Argument(help="Toolkit name to remove.")],
            yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation.")] = False,
        ):
            ref.do_remove(name, skip_confirm=yes)

        @mcp_app.command("clear", help="Remove all toolkits.")
        def clear_cmd(
            yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation.")] = False,
        ):
            ref.do_clear(skip_confirm=yes)

        @mcp_app.command("stale", help="List toolkits with missing source paths.")
        def stale_cmd():
            ref.do_stale()

        @mcp_app.command("serve", help="Start MCP server for one or more toolkits.")
        def serve_cmd(
            names: Annotated[List[str], typer.Argument(help="Toolkit name(s) to serve.")],
            stdio: Annotated[bool, typer.Option("--stdio", "-std", help="Use stdio transport instead of default HTTP service mode.")] = False,
            host: Annotated[str, typer.Option("-H", "--host", help="Host to bind for HTTP mode.")] = "127.0.0.1",
            port: Annotated[int, typer.Option("-p", "--port", help="Port to bind for HTTP mode.")] = 7001,
            copy: Annotated[bool, typer.Option("--copy", "-cp", help="Copy MCP JSON config to clipboard.")] = False,
        ):
            ref.do_serve(list(names), stdio=stdio, host=host, port=port, copy=copy)

        @mcp_app.command("stdio", help="Run a toolkit as MCP stdio server (used by MCP clients).", hidden=True)
        def stdio_cmd(
            name: Annotated[str, typer.Argument(help="Toolkit name to run.")],
        ):
            ref.do_stdio(name)

        @mcp_app.command("run", help="Execute a tool by qualified name (toolkit.tool).")
        def run_cmd(
            qualified_name: Annotated[str, typer.Argument(help="toolkit_name.tool_name")],
            args: Annotated[Optional[List[str]], typer.Argument(help="key=value args for the tool.")] = None,
        ):
            ref.do_run(qualified_name, args)

        @mcp_app.command("export", help="Export a toolkit as a Skills package.")
        def export_cmd(
            name: Annotated[str, typer.Argument(help="Toolkit name to export.")],
            output: Annotated[Optional[str], typer.Option("-o", "--output", help="Output directory.")] = None,
        ):
            ref.do_export(name, output)

        @mcp_app.command("import", help="Import toolkit payload from JSON file.")
        def import_cmd(
            file_path: Annotated[str, typer.Argument(help="Path to toolkit payload JSON.")],
            name: Annotated[Optional[str], typer.Option("-n", "--name", help="Override toolkit name.")] = None,
            overwrite: Annotated[bool, typer.Option("-w", "--overwrite", help="Overwrite existing toolkit.")] = False,
        ):
            ref.do_add(path=file_path, name=name, overwrite=overwrite)

        @mcp_app.command("add", help="Add a toolkit from JSON string, URL, or file.")
        def add_cmd(
            json_str: Annotated[Optional[str], typer.Option("-j", "--json", help="Inline MCP client config JSON string.")] = None,
            url: Annotated[Optional[str], typer.Option("-u", "--url", help="Remote MCP server HTTP URL.")] = None,
            path: Annotated[Optional[str], typer.Option("-p", "--path", help="Local JSON payload file path.")] = None,
            name: Annotated[Optional[str], typer.Option("-n", "--name", help="Override toolkit name.")] = None,
            overwrite: Annotated[bool, typer.Option("-w", "--overwrite", help="Overwrite existing toolkit.")] = False,
        ):
            ref.do_add(name=name, json_str=json_str, url=url, path=path, overwrite=overwrite)

        apply_cli_aliases(create_app, "mcp.create")
        apply_cli_aliases(mcp_app, "mcp")

        return mcp_app

    # =====================================================================
    # Argparse backend
    # =====================================================================

    def register_argparse(self, parent, group_name: str = "mcp"):
        """\
        Register MCP subcommands on an **argparse** (sub)parser.

        ``parent`` can be either:
        - an ``argparse._SubParsersAction`` (preferred), or
        - a top-level ``argparse.ArgumentParser``.
        """
        from ..utils.basic.cli_utils import add_aliased_parser

        if hasattr(parent, "add_parser"):
            mcp_group = add_aliased_parser(parent, (), group_name, help="MCP toolkit operations: create, list, serve, run, etc.")
        elif hasattr(parent, "add_subparsers"):
            root_sub = parent.add_subparsers(dest="root_cmd")
            mcp_group = add_aliased_parser(root_sub, (), group_name, help="MCP toolkit operations: create, list, serve, run, etc.")
        else:
            raise TypeError("parent must be an argparse parser or subparsers action.")

        mcp_parser = mcp_group.add_subparsers(dest="mcp_command")
        ref = self

        def _add(name, help_text, setup_fn):
            p = add_aliased_parser(mcp_parser, "mcp", name, help=help_text)
            setup_fn(p)
            return p

        def _setup_list(p):
            p.set_defaults(_handler=lambda a: ref.do_list())

        _add("list", "List all toolkits.", _setup_list)

        def _setup_show(p):
            p.add_argument("name", help="Toolkit name.")
            p.set_defaults(_handler=lambda a: ref.do_show(a.name))

        _add("show", "Display toolkit details, tools, and signatures.", _setup_show)

        def _setup_reset(p):
            p.add_argument("name", help="Toolkit name to reset.")
            p.set_defaults(_handler=lambda a: ref.do_reset(a.name))

        _add("reset", "Reset runtime state of a toolkit.", _setup_reset)

        def _setup_rename(p):
            p.add_argument("old_name", help="Current toolkit name.")
            p.add_argument("new_name", help="New toolkit name.")
            p.set_defaults(_handler=lambda a: ref.do_rename(a.old_name, a.new_name))

        _add("rename", "Rename a toolkit.", _setup_rename)

        def _setup_remove(p):
            p.add_argument("name", help="Toolkit name to remove.")
            p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")
            p.set_defaults(_handler=lambda a: ref.do_remove(a.name, skip_confirm=a.yes))

        _add("remove", "Remove a toolkit.", _setup_remove)

        def _setup_clear(p):
            p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")
            p.set_defaults(_handler=lambda a: ref.do_clear(skip_confirm=a.yes))

        _add("clear", "Remove all toolkits.", _setup_clear)

        def _setup_stale(p):
            p.set_defaults(_handler=lambda a: ref.do_stale())

        _add("stale", "List toolkits with missing source paths.", _setup_stale)

        def _setup_serve(p):
            p.add_argument("names", nargs="+", help="Toolkit name(s) to serve.")
            p.add_argument("--stdio", "-std", action="store_true", help="Use stdio transport instead of default HTTP service mode.")
            p.add_argument("-H", "--host", default="127.0.0.1", help="Host to bind for HTTP mode.")
            p.add_argument("-p", "--port", type=int, default=7001, help="Port to bind for HTTP mode.")
            p.add_argument("--copy", "-cp", action="store_true", help="Copy MCP JSON config to clipboard.")
            p.set_defaults(
                _handler=lambda a: ref.do_serve(
                    a.names,
                    stdio=a.stdio,
                    host=a.host,
                    port=a.port,
                    copy=a.copy,
                )
            )

        _add("serve", "Start MCP server for one or more toolkits.", _setup_serve)

        def _setup_run(p):
            p.add_argument("qualified_name", help="toolkit_name.tool_name")
            p.add_argument("args", nargs="*", help="key=value args for the tool.")
            p.set_defaults(_handler=lambda a: ref.do_run(a.qualified_name, a.args))

        _add("run", "Execute a tool by qualified name (toolkit.tool).", _setup_run)

        def _setup_export(p):
            p.add_argument("name", help="Toolkit name to export.")
            p.add_argument("-o", "--output", default=None, help="Output directory (default: ./<name>/).")
            p.set_defaults(_handler=lambda a: ref.do_export(a.name, a.output))

        _add("export", "Export a toolkit as a Skills package.", _setup_export)

        def _setup_import(p):
            p.add_argument("file_path", help="Path to toolkit payload JSON.")
            p.add_argument("-n", "--name", default=None, help="Override toolkit name.")
            p.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing toolkit.")
            p.set_defaults(_handler=lambda a: ref.do_add(path=a.file_path, name=a.name, overwrite=a.overwrite))

        _add("import", "Import toolkit payload from JSON file.", _setup_import)

        def _setup_add(p):
            p.add_argument("-j", "--json", dest="json_str", default=None, help="Inline MCP client config JSON string.")
            p.add_argument("-u", "--url", default=None, help="Remote MCP server HTTP URL.")
            p.add_argument("-p", "--path", default=None, help="Local JSON payload file path.")
            p.add_argument("-n", "--name", default=None, help="Override toolkit name.")
            p.add_argument("-w", "--overwrite", action="store_true", help="Overwrite existing toolkit.")
            p.set_defaults(_handler=lambda a: ref.do_add(name=a.name, json_str=a.json_str, url=a.url, path=a.path, overwrite=a.overwrite))

        _add("add", "Add a toolkit from JSON string, URL, or file.", _setup_add)

        return mcp_group

    # =====================================================================
    # Unified dispatcher
    # =====================================================================

    def register(self, parent, group_name: str = "mcp", backend: Literal["click", "typer", "argparse"] = "typer"):
        """\
        Register MCP subcommands on a CLI framework determined by *backend*.

        Args:
            parent: The parent CLI object (Click group, Typer app, or
                    argparse.ArgumentParser).
            group_name: Name of the mcp subgroup.
            backend: Which CLI framework to target
                     (``"click"``, ``"typer"``, or ``"argparse"``).
        """
        if backend == "click":
            return self.register_click(parent, group_name)
        elif backend == "typer":
            return self.register_typer(parent, group_name)
        elif backend == "argparse":
            return self.register_argparse(parent, group_name)
        else:
            raise ValueError(f"Unsupported backend: {backend!r}")
