"""\
General-purpose CLI utilities for building config-aware CLIs.

BaseCLI provides a multi-backend CLI template (Click / Typer / argparse).

## How to add a new command group to ``ahvn``

Follow these 4 steps:

1. **Subclass** ``BaseCLI`` and implement one ``do_*`` method per logical action.
   Keep every ``do_*`` method *backend-agnostic*: no click/typer/argparse imports.

2. **Add** a ``register_click``, ``register_typer``, and ``register_argparse``
   method that wires the ``do_*`` methods to the chosen framework.

3. **Wire** it into the root CLI in ``ahvn.py``::

       from ahvn.utils.basic.my_cli import MyCLI
       MyCLI(CM_AHVN).register_typer(app, group_name="my-group")

4. **Test** every ``do_*`` method independently (no framework needed); test the
   ``register_*`` methods with a dummy parent.

---

Complete example — a minimal ``GreeterCLI`` using all three backends::

    from ahvn.utils.basic.cli_utils import BaseCLI

    class GreeterCLI(BaseCLI):
        def do_hello(self, name: str = "world"):
            self.out.success(f"Hello, {name}!")

        def register_click(self, parent, group_name="greet"):
            import click
            ref = self
            @parent.group(group_name)
            def greet_group():
                pass
            @greet_group.command("hello")
            @click.argument("name", default="world", required=False)
            def hello(name):
                ref.do_hello(name)
            return greet_group

        def register_typer(self, parent, group_name="greet"):
            import typer
            from typing import Annotated, Optional
            app = typer.Typer(help="Greeter commands.")
            parent.add_typer(app, name=group_name)
            ref = self
            @app.command("hello")
            def hello(
                name: Annotated[Optional[str], typer.Argument()] = "world",
            ):
                ref.do_hello(name)
            return app

        def register_argparse(self, parent, group_name="greet"):
            import argparse
            p = parent.add_parser(group_name, help="Greeter commands.")
            sub = p.add_subparsers(dest="greet_cmd")
            ref = self
            hp = sub.add_parser("hello")
            hp.add_argument("name", nargs="?", default="world")
            hp.set_defaults(_func=lambda a: ref.do_hello(a.name))
            return p

    # Wire in ahvn.py:
    #   GreeterCLI(CM_AHVN).register_typer(app, group_name="greet")
"""

__all__ = [
    "CLI_AVAILABLE_CLICK",
    "CLI_AVAILABLE_TYPER",
    "CLI_AVAILABLE_ARGPARSE",
    "CLI_AVAILABLE_ARGCOMPLETE",
    "CLI_AVAILABLE_PROMPT_TOOLKIT",
    "CLI_AVAILABLE_RICH",
    "CLIAliasRegistry",
    "CLI_ALIAS_REGISTRY",
    "register_cli_aliases",
    "get_cli_aliases",
    "get_cli_alias_map",
    "get_cli_display_name",
    "apply_cli_aliases",
    "add_aliased_parser",
    "CLIOutput",
    "AliasedGroup",
    "AliasedTyper",
    "BaseCLI",
]

from typing import Any, Dict, List, Optional, Literal, TYPE_CHECKING, Mapping, Sequence, Tuple, Union

from ahvn.utils.basic.misc_utils import unique

if TYPE_CHECKING:
    from ahvn.utils.basic.config_utils import ConfigManager

try:
    import click

    CLI_AVAILABLE_CLICK = True
except ImportError:
    CLI_AVAILABLE_CLICK = False

try:
    import typer

    CLI_AVAILABLE_TYPER = True
except ImportError:
    CLI_AVAILABLE_TYPER = False

try:
    import argparse

    CLI_AVAILABLE_ARGPARSE = True
except ImportError:
    CLI_AVAILABLE_ARGPARSE = False

try:
    import argcomplete

    CLI_AVAILABLE_ARGCOMPLETE = True
except ImportError:
    CLI_AVAILABLE_ARGCOMPLETE = False

try:
    import prompt_toolkit

    CLI_AVAILABLE_PROMPT_TOOLKIT = True
except ImportError:
    CLI_AVAILABLE_PROMPT_TOOLKIT = False

try:
    from rich.console import Console as _RichConsole

    CLI_AVAILABLE_RICH = True
except ImportError:
    CLI_AVAILABLE_RICH = False


class CLIOutput:
    """\
    Rich-first CLI output helper with graceful fallbacks to termcolor / plain text.
    """

    def __init__(self):
        if CLI_AVAILABLE_RICH:
            from rich.console import Console
            import sys
            import io

            if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
                # On Windows with non-UTF-8 locales (e.g. GBK), Rich's legacy Win32
                # renderer cannot encode Unicode symbols like ✓/✗/⚠/ℹ.
                # Wrapping stdout/stderr in explicit UTF-8 buffers and disabling the
                # legacy renderer fixes the UnicodeEncodeError on any Windows locale.
                #
                # Guard: only do the wrapping when stdout is the *real* file descriptor
                # (fileno() == 1).  In test environments pytest's capsys replaces
                # sys.stdout with an EncodedFile whose .buffer is a StringIO; that
                # object raises io.UnsupportedOperation for fileno().  If we wrapped
                # it here the TextIOWrapper destructor would close the StringIO on GC,
                # causing "I/O operation on closed file" in capsys teardown.
                try:
                    is_real_stdout = sys.stdout.fileno() == 1
                except (AttributeError, io.UnsupportedOperation):
                    is_real_stdout = False

                if is_real_stdout:
                    stdout_utf8 = io.TextIOWrapper(
                        sys.stdout.buffer,
                        encoding="utf-8",
                        errors="replace",
                        line_buffering=True,
                    )
                    stderr_buf = sys.stderr.buffer if hasattr(sys.stderr, "buffer") else sys.stderr
                    stderr_utf8 = (
                        io.TextIOWrapper(
                            stderr_buf,
                            encoding="utf-8",
                            errors="replace",
                            line_buffering=True,
                        )
                        if hasattr(sys.stderr, "buffer")
                        else sys.stderr
                    )
                    self._out = Console(file=stdout_utf8, legacy_windows=False)
                    self._err = Console(file=stderr_utf8, legacy_windows=False)
                else:
                    self._out = Console()
                    self._err = Console(stderr=True)
            else:
                self._out = Console()
                self._err = Console(stderr=True)
        else:
            self._out = None
            self._err = None

    # -- primitives --------------------------------------------------------

    def echo(self, msg: str, err: bool = False):
        """Print a message to stdout (or stderr if *err*)."""
        target = self._err if err else self._out
        if target:
            target.print(msg, highlight=False)
        else:
            import sys

            print(msg, file=sys.stderr if err else sys.stdout)

    def success(self, msg: str):
        if self._out:
            self._out.print(f"[bold green]✓[/bold green] {msg}")
        else:
            self._print_colored(f"✓ {msg}", "color_success")

    def error(self, msg: str):
        if self._err:
            self._err.print(f"[bold red]✗[/bold red] {msg}")
        else:
            self._print_colored(f"✗ {msg}", "color_error", err=True)

    def warning(self, msg: str):
        if self._err:
            self._err.print(f"[bold yellow]⚠[/bold yellow] {msg}")
        else:
            self._print_colored(f"⚠ {msg}", "color_warning", err=True)

    def info(self, msg: str):
        if self._out:
            self._out.print(f"[bold blue]ℹ[/bold blue] {msg}")
        else:
            self._print_colored(f"ℹ {msg}", "color_info")

    # -- structured output -------------------------------------------------

    def yaml(self, data: Any):
        """Pretty-print a dict/value as YAML."""
        from ahvn.utils.basic.serialize_utils import dumps_yaml

        text = dumps_yaml(data)
        if self._out:
            from rich.syntax import Syntax

            self._out.print(Syntax(text, "yaml", theme="monokai", line_numbers=False))
        else:
            print(text)

    def table(self, title: str, columns: List[str], rows: List[List[Any]]):
        """Print a table with *title*, *columns* headers, and *rows*."""
        if self._out:
            from rich.table import Table

            t = Table(title=title, show_header=True, header_style="bold cyan")
            for col in columns:
                t.add_column(col)
            for row in rows:
                t.add_row(*[str(c) for c in row])
            self._out.print(t)
        else:
            # Simple fallback
            print(f"\n  {title}")
            sep = "-" * (sum(max(len(str(c)), 8) for c in columns) + 3 * len(columns))
            print(f"  {sep}")
            print("  " + " | ".join(str(c).ljust(8) for c in columns))
            print(f"  {sep}")
            for row in rows:
                print("  " + " | ".join(str(c).ljust(8) for c in row))
            print()

    def diff(self, diff_text: str):
        """Pretty-print a unified diff string with colour-coded added/removed lines."""
        if self._out:
            from rich.syntax import Syntax

            self._out.print(Syntax(diff_text, "diff", theme="monokai"))
        else:
            for line in diff_text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    print(f"\033[32m{line}\033[0m")
                elif line.startswith("-") and not line.startswith("---"):
                    print(f"\033[31m{line}\033[0m")
                elif line.startswith("@@"):
                    print(f"\033[36m{line}\033[0m")
                else:
                    print(line)

    # -- private helpers ---------------------------------------------------

    @staticmethod
    def _print_colored(msg: str, color_fn_name: str, err: bool = False):
        import sys

        try:
            from ahvn.utils.basic import color_utils

            fn = getattr(color_utils, color_fn_name, str)
            print(fn(msg), file=sys.stderr if err else sys.stdout)
        except ImportError:
            print(msg, file=sys.stderr if err else sys.stdout)


# ── shared alias-resolution mixin ────────────────────────────────────────────


# ── global alias registry ─────────────────────────────────────────────────────

AliasScope = Tuple[str, ...]
AliasScopeLike = Optional[Union[str, Sequence[str]]]


def _normalize_alias_scope(scope: AliasScopeLike) -> AliasScope:
    """Normalize a scope key into a tuple path."""
    if scope is None:
        return ()
    if isinstance(scope, str):
        raw = scope.replace("/", ".").strip()
        if not raw:
            return ()
        return tuple(part for part in (item.strip() for item in raw.split(".")) if part)
    return tuple(str(part).strip() for part in scope if str(part).strip())


class CLIAliasRegistry:
    """Global canonical-command -> aliases registry keyed by command scope."""

    def __init__(self, initial: Optional[Mapping[AliasScopeLike, Mapping[str, Sequence[str]]]] = None):
        self._scopes: Dict[AliasScope, Dict[str, List[str]]] = {}
        if initial:
            for scope, mapping in initial.items():
                self.register_many(scope, mapping)

    def register(self, scope: AliasScopeLike, command_name: str, aliases: Sequence[str]) -> None:
        """Register aliases for one canonical command in *scope*."""
        scope_key = _normalize_alias_scope(scope)
        canonical = str(command_name).strip()
        if not canonical:
            raise ValueError("command_name must be non-empty")

        scoped = self._scopes.setdefault(scope_key, {})
        scoped.setdefault(canonical, [])
        reverse = self.get_alias_map(scope_key)

        for raw_alias in aliases:
            alias = str(raw_alias).strip()
            if not alias or alias == canonical:
                continue
            if alias in scoped and alias != canonical:
                raise ValueError(f"Alias '{alias}' conflicts with canonical command '{alias}' in scope {scope_key!r}.")
            exists = reverse.get(alias)
            if exists and exists != canonical:
                raise ValueError(f"Alias '{alias}' already points to '{exists}' in scope {scope_key!r}.")
            if alias not in scoped[canonical]:
                scoped[canonical].append(alias)

    def register_many(self, scope: AliasScopeLike, mapping: Mapping[str, Sequence[str]]) -> None:
        """Bulk register aliases for a scope from ``{canonical: [aliases...]}``."""
        for command_name, aliases in mapping.items():
            self.register(scope, command_name, aliases)

    def get_scope(self, scope: AliasScopeLike) -> Dict[str, List[str]]:
        """Return ``{canonical: aliases}`` for *scope*."""
        scope_key = _normalize_alias_scope(scope)
        scoped = self._scopes.get(scope_key, {})
        return {cmd: list(aliases) for cmd, aliases in scoped.items()}

    def get_aliases(self, scope: AliasScopeLike, command_name: str) -> List[str]:
        """Return aliases for one canonical command in *scope*."""
        scope_key = _normalize_alias_scope(scope)
        canonical = str(command_name).strip()
        return list(self._scopes.get(scope_key, {}).get(canonical, []))

    def get_alias_map(self, scope: AliasScopeLike) -> Dict[str, str]:
        """Return ``{alias: canonical}`` for *scope*."""
        scope_key = _normalize_alias_scope(scope)
        scoped = self._scopes.get(scope_key, {})
        result: Dict[str, str] = {}
        for canonical, aliases in scoped.items():
            for alias in aliases:
                result[alias] = canonical
        return result

    def display_name(self, scope: AliasScopeLike, command_name: str) -> str:
        """Return ``canonical|alias1|alias2`` display text for a command."""
        canonical = str(command_name).strip()
        aliases = self.get_aliases(scope, canonical)
        return "|".join(unique([canonical, *aliases])) if aliases else canonical


CLI_ALIAS_REGISTRY = CLIAliasRegistry(
    {
        (): {
            "config": ["cfg"],
            "capsule": ["caps"],
        },
        ("config",): {
            "show": ["list", "ls"],
            "unset": ["rm", "del"],
            "copy": ["cp"],
            "history": ["hist"],
        },
        ("mcp",): {
            "list": ["ls"],
            "rename": ["rn"],
            "remove": ["rm", "del"],
            "clear": ["clr"],
        },
        ("capsule",): {
            "list": ["ls"],
            "import": ["set", "add"],
            "clear": ["clr"],
            "remove": ["rm", "del"],
        },
        ("tr",): {
            "list": ["ls"],
            "set": ["add"],
            "unset": ["remove", "rm", "del"],
            "clear": ["clr"],
        },
    }
)


def register_cli_aliases(scope: AliasScopeLike, mapping: Mapping[str, Sequence[str]]) -> None:
    """Register aliases into the shared global registry."""
    CLI_ALIAS_REGISTRY.register_many(scope, mapping)


def get_cli_aliases(scope: AliasScopeLike) -> Dict[str, List[str]]:
    """Get ``{canonical: aliases}`` for *scope* from the global registry."""
    return CLI_ALIAS_REGISTRY.get_scope(scope)


def get_cli_alias_map(scope: AliasScopeLike) -> Dict[str, str]:
    """Get ``{alias: canonical}`` for *scope* from the global registry."""
    return CLI_ALIAS_REGISTRY.get_alias_map(scope)


def get_cli_display_name(scope: AliasScopeLike, command_name: str) -> str:
    """Get ``canonical|alias1|...`` display text for one command in *scope*."""
    return CLI_ALIAS_REGISTRY.display_name(scope, command_name)


def apply_cli_aliases(group: Any, scope: AliasScopeLike) -> Any:
    """Apply scoped aliases to a Click/Typer group that implements ``add_alias``."""
    if group is None or not hasattr(group, "add_alias"):
        return group
    for alias, canonical in get_cli_alias_map(scope).items():
        group.add_alias(alias, canonical)
    return group


def add_aliased_parser(parent: Any, scope: AliasScopeLike, command_name: str, **kwargs: Any):
    """Add an argparse parser using aliases from the shared registry."""
    aliases = CLI_ALIAS_REGISTRY.get_aliases(scope, command_name)
    extra_aliases = kwargs.pop("aliases", None) or []
    merged = list(dict.fromkeys([*aliases, *list(extra_aliases)]))
    if merged:
        kwargs["aliases"] = merged
    return parent.add_parser(command_name, **kwargs)


class _AliasedGroupMixin:
    """\
    Mixin that adds alias resolution to any Click-style ``Group`` subclass.

    Aliases are resolved without duplicate command registration and help output
    shows each canonical command as ``canonical|alias1|alias2`` when aliases
    exist.

    Works with both ``click.Group`` (via ``AliasedGroup``) and
    ``typer.core.TyperGroup`` (via ``AliasedTyper``).
    """

    _aliases: Dict[str, str]  # alias → canonical name

    def add_alias(self, alias: str, command_name: str) -> None:
        """Register *alias* as an alternative name for *command_name*."""
        self._aliases[alias] = command_name

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        return super().get_command(ctx, self._aliases.get(cmd_name, cmd_name))  # type: ignore[misc]

    def _aliases_for(self, command_name: str) -> List[str]:
        return [alias for alias, canonical in self._aliases.items() if canonical == command_name]

    def _display_command_name(self, command_name: str) -> str:
        if "|" in command_name:
            return command_name
        aliases = self._aliases_for(command_name)
        return "|".join(unique([command_name, *aliases])) if aliases else command_name

    def format_commands(self, ctx: Any, formatter: Any) -> None:
        rows = []
        for command_name in self.list_commands(ctx):
            cmd = super().get_command(ctx, command_name)  # type: ignore[misc]
            if cmd is None or getattr(cmd, "hidden", False):
                continue
            rows.append((self._display_command_name(command_name), cmd.get_short_help_str()))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


# ── AliasedGroup (Click) ──────────────────────────────────────────────────────

if CLI_AVAILABLE_CLICK:

    class AliasedGroup(_AliasedGroupMixin, click.Group):  # type: ignore[misc]
        """\
        A ``click.Group`` subclass that supports command aliases.

        Aliases are resolved silently via ``get_command`` while command help
        displays canonical names in the form ``canonical|alias``.

        Usage::

            @parent.group("mcp", cls=AliasedGroup)
            def mcp_group(): pass

            @mcp_group.command("list", help="List items.")
            def list_cmd(): ...

            mcp_group.add_alias("ls", "list")
            # help now shows:  list|ls  List items.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._aliases: Dict[str, str] = {}

else:
    AliasedGroup = None  # type: ignore[assignment,misc]


# ── AliasedTyper (Typer) ──────────────────────────────────────────────────────

if CLI_AVAILABLE_TYPER:

    class AliasedTyper(typer.Typer):  # type: ignore[misc]
        """\
        A ``typer.Typer`` subclass that supports command aliases.

        Internally creates a bound ``TyperGroup`` subclass so that alias
        resolution happens at the Click layer — no duplicate command
        definitions needed.

        Usage::

            mcp_app = AliasedTyper(help="...", no_args_is_help=True)
            parent.add_typer(mcp_app, name="mcp")

            @mcp_app.command("list", help="List items.")
            def list_cmd(): ...

            mcp_app.add_alias("ls", "list")
            # help now shows:  list|ls  List items.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._typer_aliases: Dict[str, str] = {}
            self._canonical_aliases: Dict[str, List[str]] = {}
            self._canonical_targets: Dict[str, str] = {}
            aliases_ref = self._typer_aliases

            try:
                from typer.core import TyperGroup as _TyperGroup

                class _BoundGroup(_AliasedGroupMixin, _TyperGroup):  # type: ignore[misc]
                    _aliases = aliases_ref

                kwargs.setdefault("cls", _BoundGroup)
            except (ImportError, AttributeError):
                pass  # older Typer without TyperGroup

            # Click-style help formatting is required for alias display
            # (e.g. "list|ls") to remain consistent across backends.
            kwargs.setdefault("rich_markup_mode", None)

            super().__init__(*args, **kwargs)

        def add_alias(self, alias: str, command_name: str) -> None:
            """Register *alias* for *command_name*."""
            alias = alias.strip()
            canonical = command_name.strip()
            if not alias or not canonical or alias == canonical:
                return

            aliases = self._canonical_aliases.setdefault(canonical, [])
            if alias in aliases:
                return
            aliases.append(alias)

            current_target = self._canonical_targets.get(canonical, canonical)
            display_target = "|".join(unique([canonical, *aliases]))

            if display_target != current_target:
                renamed = False
                for cmd_info in self.registered_commands:
                    if cmd_info.name == current_target:
                        cmd_info.name = display_target
                        renamed = True
                        break
                if not renamed:
                    for grp_info in getattr(self, "registered_groups", []):
                        if grp_info.name == current_target:
                            grp_info.name = display_target
                            renamed = True
                            break
                if renamed:
                    for key, target in list(self._typer_aliases.items()):
                        if target == current_target:
                            self._typer_aliases[key] = display_target
                    self._canonical_targets[canonical] = display_target
                    current_target = display_target

            self._typer_aliases[canonical] = current_target
            self._typer_aliases[alias] = current_target

else:
    AliasedTyper = None  # type: ignore[assignment,misc]


# ─────────────────────────────────────────────────────────────────────────────


class BaseCLI:
    """\
    Abstract base for multi-backend CLI command groups.

    Subclass this to implement a cohesive set of CLI commands that can be
    registered on Click, Typer, or argparse with a single call.

    Pattern::

        class MyCLI(BaseCLI):
            # 1. Backend-agnostic operations
            def do_action(self, arg: str):
                self.out.success(f"Done: {arg}")

            # 2. Backend registration
            def register_click(self, parent, group_name="my"):  ...
            def register_typer(self, parent, group_name="my"):  ...
            def register_argparse(self, parent, group_name="my"): ...

        # 3. Wire into main CLI
        MyCLI(cm).register_typer(app, group_name="my")

    Attributes:
        cm:  The ``ConfigManager`` instance (access config via ``self.cm.get(...)``).
        out: A ``CLIOutput`` instance for rich-first output.
    """

    def __init__(self, cm: "ConfigManager", out: Optional["CLIOutput"] = None):
        self.cm = cm
        if out is not None:
            self.out = out
        else:
            self.out = CLIOutput()

    def register(
        self,
        parent,
        group_name: str,
        backend: Literal["click", "typer", "argparse"] = "typer",
    ):
        """\
        Register commands on *parent* for the given *backend*.

        Calls ``register_click``, ``register_typer``, or ``register_argparse``
        depending on *backend*.

        Args:
            parent:     The parent CLI object (Click group, Typer app, or argparse parser).
            group_name: The subcommand group name exposed to the user.
            backend:    ``"click"``, ``"typer"``, or ``"argparse"``.
        """
        if backend == "click":
            return self.register_click(parent, group_name)
        elif backend == "typer":
            return self.register_typer(parent, group_name)
        elif backend == "argparse":
            return self.register_argparse(parent, group_name)
        raise ValueError(f"Unsupported backend: {backend!r}")

    def register_click(self, parent, group_name: str):
        """\
        Register commands on a Click group.

        Args:
            parent:     A ``click.Group`` (or ``AliasedGroup``) instance.
            group_name: Subcommand group name.

        Returns:
            The created Click group.

        Example::

            def register_click(self, parent, group_name="greet"):
                import click
                ref = self

                @parent.group(group_name)
                def grp(): pass

                @grp.command("hello")
                @click.argument("name", default="world")
                def cmd(name): ref.do_hello(name)

                return grp
        """
        raise NotImplementedError

    def register_typer(self, parent, group_name: str):
        """\
        Register commands on a Typer app.

        Args:
            parent:     A ``typer.Typer`` instance.
            group_name: Subcommand group name.

        Returns:
            The created Typer sub-app.

        Example::

            def register_typer(self, parent, group_name="greet"):
                import typer
                from typing import Annotated, Optional
                app = typer.Typer(help="Greeter commands.")
                parent.add_typer(app, name=group_name)
                ref = self

                @app.command("hello")
                def hello(
                    name: Annotated[Optional[str], typer.Argument()] = "world",
                ):
                    ref.do_hello(name)

                return app
        """
        raise NotImplementedError

    def register_argparse(self, parent, group_name: str):
        """\
        Register commands on an argparse subparsers object.

        Args:
            parent:     An ``argparse._SubParsersAction`` (from ``parser.add_subparsers()``).
            group_name: Subparser name.

        Returns:
            The created ArgumentParser for this group.

        Example::

            def register_argparse(self, parent, group_name="greet"):
                import argparse
                p = parent.add_parser(group_name, help="Greeter commands.")
                sub = p.add_subparsers(dest="greet_cmd")
                ref = self
                hp = sub.add_parser("hello")
                hp.add_argument("name", nargs="?", default="world")
                hp.set_defaults(_func=lambda a: ref.do_hello(a.name))
                return p
        """
        raise NotImplementedError
