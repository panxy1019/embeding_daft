"""\
Capsule CLI for AgentHeaven.

Provides a modular :class:`CapsuleCLI` class with backend-agnostic ``do_*``
methods and ``register_typer`` / ``register`` dispatchers for managing
Function Capsules stored in the global capsule DB.

Commands:
    list (ls)    List all stored capsules.
    show         Show capsule metadata.
    import (set, add)  Import a .fcap file into the store.
    run          Restore and execute a capsule.
    serve        Serve capsule(s) as MCP server.
    export       Export a capsule to .fcap file.
    remove (rm, del)  Delete a capsule from the store.
    stale        List capsules whose source file no longer exists.
    clear (clr)  Remove all capsules.
"""

__all__ = ["CapsuleCLI"]

from typing import List, Literal, Optional

from .tool_cli_utils import parse_kv_args


class CapsuleCLI:
    """\
    Modular Capsule CLI following the same pattern as :class:`McpCLI`.

    Core operations live in ``do_*`` methods; ``register_typer`` and
    ``register`` attach them to the Typer framework.
    """

    def __init__(self):
        from ..utils.basic.cli_utils import CLIOutput

        self.out = CLIOutput()
        self._store = None

    @property
    def capsule_store(self):
        """\
        Lazy-loaded CapsuleStore singleton.
        """
        if self._store is None:
            from ..utils.capsule import get_capsule_store

            self._store = get_capsule_store()
        return self._store

    # =====================================================================
    # Business logic (do_* methods)
    # =====================================================================

    def do_list(self, tag: Optional[str] = None):
        """\
        List all stored capsules (summary table).
        """
        items = self.capsule_store.list(tag=tag)
        if not items:
            self.out.info("No capsules stored.")
            return

        rows = []
        for item in items:
            tags = item.get("tags") or []
            cid = str(item.get("id", ""))
            rows.append(
                [
                    cid[:12],
                    item.get("name", ""),
                    item.get("qualname", ""),
                    ", ".join(tags) if tags else "",
                    str(item.get("updated_at", ""))[:19],
                ]
            )
        self.out.table(
            "Capsules",
            ["ID (short)", "Name", "Qualname", "Tags", "Updated"],
            rows,
        )

    def do_show(self, capsule_id: str):
        """\
        Show capsule metadata (name, id, checksum, dates).
        """
        try:
            cap = self._resolve_capsule(capsule_id)
            manifest = cap.get("manifest", {}) if isinstance(cap.get("manifest"), dict) else {}
            rows = []
            rows.append(["Name", manifest.get("name", "")])
            rows.append(["ID", str(cap.get("capsule_id", ""))])
            rows.append(["Qualname", manifest.get("qualname", "")])
            rows.append(["Checksum", str(cap.get("checksum", ""))])
            tags = cap.get("tags") or manifest.get("tags") or []
            rows.append(["Tags", ", ".join(tags) if tags else ""])
            rows.append(["Created", str(cap.get("created_at", ""))[:19]])
            rows.append(["Updated", str(cap.get("updated_at", ""))[:19]])
            self.out.table(f"Capsule: {manifest.get('name', capsule_id)}", ["Field", "Value"], rows)
        except Exception as e:
            self.out.error(str(e))

    def do_run(self, capsule_id: str, args: Optional[List[str]] = None):
        """\
        Restore and execute a capsule with key=value args.
        """
        parsed = parse_kv_args(args or [])

        try:
            cap = self._resolve_capsule(capsule_id)
            from ..utils.capsule import Capsule

            spec = Capsule.from_dict(cap).to_tool()
            result = spec(**parsed)
            self.out.echo(str(result))
        except Exception as e:
            self.out.error(str(e))

    def do_serve(
        self,
        capsule_ids: List[str],
        stdio: bool = False,
        host: str = "127.0.0.1",
        port: int = 7002,
    ):
        """\
        Restore capsules and serve them as an MCP server.
        """
        if not capsule_ids:
            self.out.error("No capsule IDs specified.")
            return

        try:
            from ..tool.toolkit import Toolkit

            capsules = []
            for cid in capsule_ids:
                capsules.append(self._resolve_capsule(cid))

            toolkit = Toolkit.from_capsules(
                name="capsule_server",
                description="Capsule-based MCP toolkit",
                capsules=capsules,
            )

            transport = "stdio" if stdio else "http"
            if stdio:
                self.out.info("Starting MCP server (stdio transport)...")
                toolkit.serve(transport="stdio")
            else:
                self.out.info(f"Starting MCP server at http://{host}:{port}/mcp ...")
                toolkit.serve(transport=transport, host=host, port=port)
        except Exception as e:
            self.out.error(str(e))

    def do_import(self, file_path: str, tags: Optional[List[str]] = None):
        """\
        Import a .fcap file into the store.
        """
        from ..utils.basic.file_utils import exists_path
        from ..utils.capsule import Capsule

        if not exists_path(file_path):
            self.out.error(f"File not found: {file_path}")
            return

        try:
            cap = Capsule.load(file_path).to_dict()
            cid = self.capsule_store.add(cap, tags=tags)
            self.out.success(f"Imported capsule '{cap.get('manifest', {}).get('name', '?')}' " f"(id={cid[:12]})")
        except Exception as e:
            self.out.error(str(e))

    def do_export(self, capsule_id: str, output: Optional[str] = None):
        """\
        Export a capsule to .fcap file.
        """
        from ..utils.capsule import Capsule
        from ..utils.basic.path_utils import pj

        try:
            cap = self._resolve_capsule(capsule_id)
            name = cap.get("manifest", {}).get("name", "capsule")
            output = pj(output or f"{name}.fcap", abs=True)
            Capsule.from_dict(cap).dump(output)
            self.out.success(f"Exported capsule '{name}' to {output}")
        except Exception as e:
            self.out.error(str(e))

    def do_remove(self, capsule_id: str, skip_confirm: bool = False):
        """\
        Delete a capsule from the store.
        """
        try:
            cap = self._resolve_capsule(capsule_id)
            actual_id = cap["capsule_id"]
            name = cap.get("manifest", {}).get("name", actual_id[:12])
        except Exception as e:
            self.out.error(str(e))
            return

        if not skip_confirm:
            confirm = input(f"Delete capsule '{name}' ({actual_id[:12]}...)? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return

        try:
            self.capsule_store.delete(actual_id)
            self.out.success(f"Deleted capsule '{name}' ({actual_id[:12]}...)")
        except Exception as e:
            self.out.error(str(e))

    def do_clear(self, skip_confirm: bool = False):
        """\
        Remove all capsules.
        """
        if not skip_confirm:
            confirm = input("Delete ALL capsules? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return

        try:
            count = self.capsule_store.clear()
            self.out.success(f"Cleared {count} capsule(s).")
        except Exception as e:
            self.out.error(str(e))

    def do_stale(self):
        """\
        List capsules whose source file no longer exists.
        """
        items = self.capsule_store.stale()
        if not items:
            self.out.info("No stale capsules.")
            return

        rows = []
        for item in items:
            cid = str(item.get("id", ""))
            rows.append(
                [
                    cid[:12],
                    item.get("name", ""),
                    item.get("source_file", ""),
                    str(item.get("updated_at", ""))[:19],
                ]
            )
        self.out.table(
            "Stale Capsules",
            ["ID (short)", "Name", "Source File", "Updated"],
            rows,
        )

    # ── helpers ───────────────────────────────────────────────────────

    def _resolve_capsule(self, capsule_id: str):
        """\
        Resolve a capsule by ID (exact), ID prefix, or name.
        """

        def _fetch_payload(registry_id):
            cap = self.capsule_store.get(registry_id)
            if cap is not None:
                return cap
            raise KeyError(f"Capsule payload not found for registry ID: {registry_id}")

        # Try exact match first
        cap = self.capsule_store.get(capsule_id)
        if cap is not None:
            return cap
        if capsule_id.isdigit():
            cap = self.capsule_store.get(int(capsule_id))
            if cap is not None:
                return cap

        # Try prefix search on id
        all_items = self.capsule_store.list()
        matches = [item for item in all_items if str(item["id"]).startswith(capsule_id)]
        if len(matches) == 1:
            return _fetch_payload(matches[0]["id"])
        elif len(matches) > 1:
            names = [f"{str(m['id'])[:12]} ({m['name']})" for m in matches]
            raise ValueError(f"Ambiguous capsule ID prefix '{capsule_id}'. " f"Matches: {', '.join(names)}")

        # Try name match
        name_matches = [item for item in all_items if item["name"] == capsule_id]
        if len(name_matches) == 1:
            return _fetch_payload(name_matches[0]["id"])
        elif len(name_matches) > 1:
            ids = [str(m["id"])[:12] for m in name_matches]
            raise ValueError(f"Multiple capsules named '{capsule_id}'. IDs: {', '.join(ids)}")

        # Try qualname match
        qn_matches = [item for item in all_items if item.get("qualname") == capsule_id]
        if len(qn_matches) == 1:
            return _fetch_payload(qn_matches[0]["id"])

        raise KeyError(f"Capsule not found: {capsule_id}")

    # =====================================================================
    # Typer backend
    # =====================================================================

    def register_typer(self, parent, group_name: str = "capsule"):
        """\
        Register capsule subcommands on a **Typer** app.
        """
        import typer
        from typing import Annotated
        from ..utils.basic.cli_utils import AliasedTyper, apply_cli_aliases

        capsule_app = AliasedTyper(
            help="Browse, run, and manage stored Function Capsules.",
            no_args_is_help=True,
        )
        parent.add_typer(capsule_app, name=group_name)
        ref = self

        @capsule_app.command("list", help="List all stored capsules.")
        def list_cmd(
            tag: Annotated[
                Optional[str],
                typer.Option("-t", "--tag", help="Filter by tag."),
            ] = None,
        ):
            ref.do_list(tag=tag)

        @capsule_app.command("show", help="Show full capsule with layer details.")
        def show_cmd(
            capsule_id: Annotated[str, typer.Argument(help="Capsule ID, prefix, or name.")],
        ):
            ref.do_show(capsule_id)

        @capsule_app.command("run", help="Restore and execute a capsule.")
        def run_cmd(
            capsule_id: Annotated[str, typer.Argument(help="Capsule ID, prefix, or name.")],
            args: Annotated[
                Optional[List[str]],
                typer.Argument(help="key=value args."),
            ] = None,
        ):
            ref.do_run(capsule_id, args or [])

        @capsule_app.command("serve", help="Serve capsule(s) as MCP server.")
        def serve_cmd(
            capsule_ids: Annotated[List[str], typer.Argument(help="Capsule ID(s) to serve.")],
            stdio: Annotated[
                bool,
                typer.Option("--stdio", help="Use stdio transport."),
            ] = False,
            host: Annotated[
                str,
                typer.Option("-H", "--host", help="Host for HTTP mode."),
            ] = "127.0.0.1",
            port: Annotated[
                int,
                typer.Option("-p", "--port", help="Port for HTTP mode."),
            ] = 7002,
        ):
            ref.do_serve(list(capsule_ids), stdio=stdio, host=host, port=port)

        @capsule_app.command("import", help="Import a .fcap file into the store.")
        def import_cmd(
            file_path: Annotated[str, typer.Argument(help="Path to .fcap file.")],
            tag: Annotated[
                Optional[List[str]],
                typer.Option("-t", "--tag", help="Tags to attach."),
            ] = None,
        ):
            ref.do_import(file_path, tags=tag)

        @capsule_app.command("export", help="Export a capsule to .fcap file.")
        def export_cmd(
            capsule_id: Annotated[str, typer.Argument(help="Capsule ID, prefix, or name.")],
            output: Annotated[
                Optional[str],
                typer.Option("-o", "--output", help="Output file path."),
            ] = None,
        ):
            ref.do_export(capsule_id, output=output)

        @capsule_app.command("remove", help="Delete a capsule from the store.")
        def remove_cmd(
            capsule_id: Annotated[str, typer.Argument(help="Capsule ID, prefix, or name.")],
            yes: Annotated[
                bool,
                typer.Option("-y", "--yes", help="Skip confirmation."),
            ] = False,
        ):
            ref.do_remove(capsule_id, skip_confirm=yes)

        @capsule_app.command("clear", help="Remove all capsules.")
        def clear_cmd(
            yes: Annotated[
                bool,
                typer.Option("-y", "--yes", help="Skip confirmation."),
            ] = False,
        ):
            ref.do_clear(skip_confirm=yes)

        @capsule_app.command("stale", help="List capsules with missing source files.")
        def stale_cmd():
            ref.do_stale()

        apply_cli_aliases(capsule_app, "capsule")

        return capsule_app

    # =====================================================================
    # Dispatcher
    # =====================================================================

    def register(
        self,
        parent,
        group_name: str = "capsule",
        backend: Literal["typer"] = "typer",
    ):
        """\
        Register capsule subcommands on a CLI framework.

        Args:
            parent: The parent Typer app.
            group_name: Name of the capsule subgroup.
            backend: CLI framework (currently only ``"typer"``).
        """
        if backend == "typer":
            return self.register_typer(parent, group_name)
        raise ValueError(f"Unsupported backend: {backend!r}")
