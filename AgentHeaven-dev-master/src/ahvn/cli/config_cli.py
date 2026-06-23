__all__ = [
    "ConfigCLI",
]

import os
import subprocess
from typing import Literal, List, Annotated, Optional, Tuple
from ..utils.basic.cli_utils import *

_msys_root_cache: Optional[str] = None


def _msys_root() -> Optional[str]:
    """Return MSYS2/Git-Bash install root (forward-slash Windows path), or None."""
    global _msys_root_cache
    if not os.environ.get("MSYSTEM") or os.environ.get("MSYS_NO_PATHCONV") == "1":
        return None
    if _msys_root_cache is not None:
        return _msys_root_cache or None
    try:
        res = subprocess.run(["cygpath", "-w", "/"], capture_output=True, text=True, timeout=2)
        root = res.stdout.strip().rstrip("\\/").replace("\\", "/")
        _msys_root_cache = root if root else ""
    except Exception:
        _msys_root_cache = ""
    return _msys_root_cache or None


def _fix_msys_args(*parts: str) -> Tuple[str, ...]:
    """Undo Git Bash POSIX to Windows path conversion on CLI arguments.

    Git Bash silently rewrites ``&/data/test`` to
    ``&C:/Program Files/Git/data/test`` before Python sees ``sys.argv``.
    This strips the injected MSYS root back out so alias prefixes
    (``&``, ``%``, ``...``) work correctly.  No-op outside Git Bash.
    """
    root = _msys_root()
    if not root:
        return parts
    root_lower = root.lower()
    result = []
    for part in parts:
        norm = part.replace("\\", "/")
        idx = norm.lower().find(root_lower)
        if idx >= 0:
            before = norm[:idx]
            if "/" not in before and ":" not in before:
                part = before + norm[idx + len(root) :]
        result.append(part)
    return tuple(result)


class ConfigCLI(BaseCLI):
    """\
    General-purpose config CLI that wraps a :class:`ConfigManager`.

    All operations are backend-agnostic (the ``do_*`` methods).
    Use ``register_click``, ``register_typer``, or ``register_argparse``
    to attach the *config* subgroup, and ``register_root_*`` to attach
    top-level package commands (``setup``, ``pj``, ``--version``).

    **Naming rationale** - the class is called ``ConfigCLI`` and not
    ``PackageCLI`` / ``HeavenCLI`` because all 12 ``do_*`` operations are
    either direct config operations or config-adjacent (``setup`` initialises
    config; ``pj`` resolves paths derived from config).  For building *other*
    command groups (chat, repo, ``...``) see :class:`ahvn.utils.basic.cli_utils.BaseCLI`.
    """

    def resolve_scope(self, scope: Optional[str] = None) -> Optional[str]:
        """\
        Resolve a CLI scope argument to a full scope name.

        * ``None`` / empty  -> ``None`` (ConfigManager uses its current scope)
        * ``"."``           -> ``cm.base_scope``  (explicit base)
        * ``"x.y"``        -> ``"{base_scope}.x.y"``
        * Already prefixed  -> pass-through
        """
        if not scope:
            return None
        scope = scope.strip().lower()
        if scope == ".":
            return self.cm.base_scope
        if scope.startswith(self.cm.base_scope):
            return scope
        return f"{self.cm.base_scope}.{scope}"

    def resolve_version(self, version: Optional[int], scope: str) -> Optional[int]:
        """\
        Resolve a (possibly negative) version index to an absolute version number.

        Negative indices work like Python lists: ``-1`` = latest, ``-2`` = second
        latest, etc.  Positive integers (absolute version IDs) are returned as-is.
        ``None`` is returned as-is.

        Args:
            version: Negative index or absolute version ID.
            scope:   The scope whose version list is used for index resolution.

        Returns:
            Absolute version number, or ``None`` if the scope has no versions.
        """
        if version is None or version >= 0:
            return version
        versions = self.cm.storage.versions(self.cm.package, scope)
        if not versions:
            return None
        try:
            return versions[version]
        except IndexError:
            return versions[0]  # clamp to oldest available

    def do_show(self, key: Optional[str] = None, scope: Optional[str] = None, version: Optional[int] = None):
        """Show merged config, a specific scope layer, or a specific historical *version*."""
        resolved = self.resolve_scope(scope)
        if version is not None:
            tgt_scope = resolved or self.cm.scope
            version = self.resolve_version(version, tgt_scope)
            config = self.cm.storage.get(self.cm.package, tgt_scope, version=version)
        elif resolved:
            config = self.cm.layer(scope=resolved)
        else:
            config = self.cm.load()

        if key:
            from ahvn.utils.basic.config_utils import dget

            config = dget(config, key, default=...)

        if config is ...:
            self.out.warning(f'No config for key "{key}".')
            return
        if config is None and not key:
            self.out.warning("No config found.")
            return
        self.out.yaml(config)

    def do_set(self, key: str, value: str, scope: Optional[str] = None, as_json: bool = False):
        """Set a config value (auto-typed or JSON-parsed)."""
        if as_json:
            from ahvn.utils.basic.serialize_utils import loads_json

            try:
                value = loads_json(value)
            except Exception as e:
                self.out.error(f"Invalid JSON: {e}")
                return
        else:
            from ahvn.utils.basic.type_utils import autotype

            value = autotype(value)

        resolved = self.resolve_scope(scope)
        success = self.cm.set(key, value, scope=resolved)
        if success:
            self.out.success(f"Set {key}")
        else:
            self.out.error(f"Failed to set {key}.")

    def do_unset(self, key: str, scope: Optional[str] = None):
        """Unset (remove) a config key."""
        resolved = self.resolve_scope(scope)
        success = self.cm.unset(key, scope=resolved)
        if success:
            self.out.success(f"Unset {key}")
        else:
            self.out.error(f"Failed to unset {key}.")

    def do_copy(
        self,
        key: Optional[str] = None,
        from_default: bool = False,
        from_scope: Optional[str] = None,
        from_version: Optional[int] = None,
        skip_confirm: bool = False,
    ):
        """\
        Copy config to the current scope from:
        - the default resource (``--default``),
        - a specific scope / version (``--from-scope`` / ``--from-version``),
        - or the base scope (default behaviour).

        If *key* is ``None``, copies everything (with optional confirm).
        """
        resolved_from = self.resolve_scope(from_scope) if from_scope else None

        if from_default:
            source = self.cm.load_default()
            source_label = "default"
        elif resolved_from:
            if from_version is not None:
                from_version = self.resolve_version(from_version, resolved_from)
                source = self.cm.storage.get(self.cm.package, resolved_from, version=from_version)
                source_label = f"scope `{resolved_from}` v{from_version}"
            else:
                source = self.cm.layer(scope=resolved_from)
                source_label = f"scope `{resolved_from}`"
        else:
            source = self.cm.layer(scope=self.cm.base_scope)
            source_label = f"scope `{self.cm.base_scope}`"

        if not key:
            if not skip_confirm:
                self.out.warning(f"This will overwrite current scope with {source_label} config.")
                confirm = input("Continue? [y/N] ").strip().lower()
                if confirm not in ("y", "yes"):
                    self.out.info("Aborted.")
                    return
            self.cm.set(key_path=None, value=source)
            self.out.success(f"Copied all from {source_label}.")
        else:
            from ahvn.utils.basic.config_utils import dget

            val = dget(source, key)
            if val is None:
                self.out.error(f"Key '{key}' not found in {source_label}.")
                return
            success = self.cm.set(key, val)
            if success:
                self.out.success(f"Copied {key} from {source_label}.")
            else:
                self.out.error(f"Failed to copy {key}.")

    def do_scope(self):
        """List all scopes and highlight the current one."""
        scopes = self.cm.scopes()
        if not scopes:
            self.out.info("No scopes configured.")
            return
        current = self.cm.scope
        rows = []
        for s in sorted(scopes):
            marker = "\u2192" if s == current else " "
            ver = self.cm.storage.version(self.cm.package, s)
            rows.append([marker, s, str(ver)])
        self.out.table(f"Scopes ({self.cm.package})", ["", "Scope", "Version"], rows)

    def do_history(self, scope: Optional[str] = None):
        """Show version history for a scope, marking the current version with an arrow."""
        resolved = self.resolve_scope(scope) or self.cm.scope
        versions = self.cm.history(scope=resolved)
        if not versions:
            self.out.info(f"No history for scope `{resolved}`.")
            return
        current_ver = self.cm.storage.version(self.cm.package, resolved)
        rows = []
        for v in versions:
            snap = self.cm.storage.get(self.cm.package, resolved, version=v, snapshot=True)
            created = str(snap.created_at)[:19] if (snap and snap.created_at) else "?"
            marker = "\u2192" if v == current_ver else " "
            rows.append([marker, str(v), created])
        self.out.table(f"History: `{resolved}`", ["", "Version", "Created"], rows)

    def do_compact(self, scope: Optional[str] = None, skip_confirm: bool = False):
        """Compact version history by trimming old versions."""
        resolved = self.resolve_scope(scope) or self.cm.scope
        versions = self.cm.storage.versions(self.cm.package, resolved)
        if len(versions) <= 1:
            self.out.info(f"Nothing to compact for `{resolved}` ({len(versions)} version).")
            return
        if not skip_confirm:
            self.out.warning(f"Compact history for `{resolved}` ({len(versions)} versions). Cannot be undone.")
            confirm = input("Continue? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return
        removed = self.cm.compact(scope=resolved)
        if removed <= 0:
            self.out.info(f"Nothing to compact for `{resolved}` (already within retention window).")
            return
        self.out.success(f"Compacted `{resolved}`: removed {removed} old version(s).")

    def do_reset(self, scope: Optional[str] = None, skip_confirm: bool = False):
        """Reset a scope to default config values."""
        resolved = self.resolve_scope(scope)
        target = resolved or self.cm.scope
        if not skip_confirm:
            self.out.warning(f"Reset `{target}` to defaults. Cannot be undone.")
            confirm = input("Continue? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                self.out.info("Aborted.")
                return
        self.cm.init(scope=resolved, reset=True)
        self.out.success(f"Reset `{target}` to defaults.")

    def do_edit(self, scope: Optional[str] = None):
        """\
        Edit config in ``$EDITOR`` via a temporary YAML file.

        Workflow:
            1. Read current config from DB for *scope*.
            2. Write to a temporary ``.yaml`` file.
            3. Open ``$EDITOR`` (blocking).  Falls back to ``nano`` on POSIX
                and ``notepad`` on Windows when ``$EDITOR`` is unset.
            4. On clean exit, read the file back, validate YAML, and write to DB.
            5. Remove the temp file.
        """
        import os
        import subprocess
        import sys
        import tempfile

        from ahvn.utils.basic.serialize_utils import dumps_yaml, loads_yaml

        resolved = self.resolve_scope(scope) or self.cm.scope
        config = self.cm.layer(scope=resolved) or {}
        yaml_text = dumps_yaml(config)

        prefix = f".ahvn-edit-{resolved.replace('.', '-')}-"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".yaml", prefix=prefix)
        try:
            with os.fdopen(tmp_fd, "w") as fh:
                fh.write(yaml_text)

            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or ("notepad" if sys.platform == "win32" else "nano")
            ret = subprocess.call([editor, tmp_path])
            if ret != 0:
                self.out.warning(f"Editor exited with code {ret}; changes discarded.")
                return

            with open(tmp_path, encoding="utf-8") as fh:
                new_data = loads_yaml(fh.read())

            if new_data is None:
                self.out.warning("File is empty; changes discarded.")
                return

            self.cm.set(key_path=None, value=new_data, scope=resolved)
            self.out.success(f"Config updated for `{resolved}`.")
        except Exception as e:
            self.out.error(f"Edit failed: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def do_open(self, scope: Optional[str] = None):
        """\
        Open config in the system default viewer via a temporary YAML file.

        The file is written then handed off to the OS.  Because the viewer
        runs asynchronously the temp file path is printed so the user knows
        where to find (and manually delete) it.
        """
        import os
        import subprocess
        import sys
        import tempfile

        from ahvn.utils.basic.serialize_utils import dumps_yaml

        resolved = self.resolve_scope(scope) or self.cm.scope
        config = self.cm.layer(scope=resolved) or {}
        yaml_text = dumps_yaml(config)

        prefix = f".ahvn-view-{resolved.replace('.', '-')}-"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".yaml", prefix=prefix)
        try:
            with os.fdopen(tmp_fd, "w") as fh:
                fh.write(yaml_text)

            if sys.platform == "win32":
                os.startfile(tmp_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", tmp_path])
            else:
                subprocess.Popen(["xdg-open", tmp_path])

            self.out.info(f"Opened `{resolved}` in system viewer (read-only).")
            self.out.info(f"Temp file (delete when done): {tmp_path}")
        except Exception as e:
            self.out.error(f"Open failed: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def do_diff(
        self,
        scope_a: Optional[str] = None,
        version_a: Optional[int] = None,
        scope_b: Optional[str] = None,
        version_b: Optional[int] = None,
    ):
        """\
        Show a unified diff between two config snapshots.

        Version numbers support Python-style negative indexing: ``-1`` = latest,
        ``-2`` = second latest, etc.  By default (no arguments) the current scope's
        latest version is compared against its previous version.  When scopes differ,
        both sides default to their respective latest versions.

        When only one version exists for the current scope and no scopes are specified,
        a special message is shown instead.  The two compared snapshots are always
        printed as a header before the diff output.

        Args:
            scope_a:   "From" scope  (default: current scope).
            version_a: "From" version (-1=latest; default: -2 same-scope, -1 cross-scope).
            scope_b:   "To" scope    (default: current scope).
            version_b: "To" version  (-1=latest; default: -1).
        """
        from ahvn.utils.basic.serialize_utils import dumps_yaml
        import difflib

        r_a = self.resolve_scope(scope_a) or self.cm.scope
        r_b = self.resolve_scope(scope_b) or self.cm.scope

        # Resolve version_b: None means "latest" (-1)
        if version_b is None:
            version_b = -1
        ver_b = self.resolve_version(version_b, r_b)
        if ver_b is None:
            self.out.warning(f"No versions found for scope `{r_b}`.")
            return

        # Resolve version_a
        if version_a is None:
            if r_a == r_b:
                # Same scope: check for the single-version edge case first
                all_vers = self.cm.storage.versions(self.cm.package, r_a)
                if len(all_vers) <= 1 and scope_a is None and scope_b is None:
                    self.out.info(f"Only one config version for scope `{r_a}`. Nothing to diff.")
                    return
                # Default "from" side: second-latest (-2)
                ver_a = self.resolve_version(-2, r_a)
                if ver_a is None:
                    ver_a = ver_b  # safety fallback
            else:
                # Different scopes: default to latest (-1) of scope_a
                ver_a = self.resolve_version(-1, r_a)
        else:
            ver_a = self.resolve_version(version_a, r_a)

        if ver_a is None:
            self.out.warning(f"No versions found for scope `{r_a}`.")
            return

        snap_a = self.cm.storage.get(self.cm.package, r_a, version=ver_a) or {}
        snap_b = self.cm.storage.get(self.cm.package, r_b, version=ver_b) or {}

        label_a = f"{r_a} v{ver_a}"
        label_b = f"{r_b} v{ver_b}"

        # Always show the two compared snapshots as a header
        self.out.info(f"Comparing: `{label_a}`  \u2192  `{label_b}`")

        lines_a = dumps_yaml(snap_a).splitlines(keepends=True)
        lines_b = dumps_yaml(snap_b).splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b))
        if not diff_lines:
            self.out.info("No differences.")
            return
        self.out.diff("".join(diff_lines))

    def do_setup(self, reset: bool = False):
        """Initialize or reset the package configuration to defaults."""
        already_exists = self.cm.storage.version(self.cm.package, self.cm.base_scope) != 0
        result = self.cm.setup(reset=reset)
        if result:
            self.out.success(f"{self.cm.package} initialized{' (reset)' if reset else ''}.")
        elif not reset and already_exists:
            self.out.info(f"{self.cm.package} already configured. Use --reset to reinitialize.")
        else:
            self.out.error(f"Failed to initialize {self.cm.package}.")

    def do_pj(self, *parts: str):
        """Join path parts using the ConfigManager path utility and print the result."""
        result = self.cm.pj(*_fix_msys_args(*parts), abs=True)
        self.out.echo(result)

    # =====================================================================
    # Backend registration
    # =====================================================================

    def register_click(self, parent, group_name: str = "config"):
        """\
        Register config subcommands on a **Click** group.

        Args:
            parent: A ``click.Group`` (or ``AliasedGroup``) instance.
            group_name: Name of the config subgroup.

        Returns:
            The created config group.
        """
        if not CLI_AVAILABLE_CLICK:
            raise RuntimeError("click is required for register_click")
        import click
        from ..utils.basic.cli_utils import AliasedGroup

        ref = self

        @parent.group(group_name, cls=AliasedGroup, help="Config operations: show, set, unset, copy, scope, history, compact, reset.")
        def config_group():
            pass

        @config_group.command("show", help="Show config values.")
        @click.argument("key", required=False, default=None)
        @click.option("--scope", "-s", default=None, help="Scope (omit for merged config).")
        @click.option("--version", "-v", "version", type=int, default=None, help="Version to show (-1=latest, -2=previous, etc.).")
        def show(key, scope, version):
            ref.do_show(key, scope, version)

        @config_group.command("set", help="Set a config value.")
        @click.argument("key")
        @click.argument("value")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        @click.option("--json", "-j", "as_json", is_flag=True, help="Parse VALUE as JSON.")
        def set_cmd(key, value, scope, as_json):
            ref.do_set(key, value, scope, as_json)

        @config_group.command("unset", help="Unset a config key.")
        @click.argument("key")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        def unset_cmd(key, scope):
            ref.do_unset(key, scope)

        @config_group.command("copy", help="Copy config from default/base/scope to current scope.")
        @click.argument("key", required=False, default=None)
        @click.option("--default", "-d", "from_default", is_flag=True, help="Copy from default config.")
        @click.option("--from-scope", "-S", "from_scope", default=None, help="Copy from a specific scope.")
        @click.option("--from-version", "-V", "from_version", type=int, default=None, help="Source version (-1=latest, -2=previous, etc.).")
        @click.option("--yes", "-y", "skip_confirm", is_flag=True, help="Skip confirmation.")
        def copy_cmd(key, from_default, from_scope, from_version, skip_confirm):
            ref.do_copy(key, from_default, from_scope, from_version, skip_confirm)

        @config_group.command("scope", help="List all scopes.")
        def scope_cmd():
            ref.do_scope()

        @config_group.command("history", help="Show version history.")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        def history_cmd(scope):
            ref.do_history(scope)

        @config_group.command("compact", help="Compact version history into one.")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        @click.option("--yes", "-y", "skip_confirm", is_flag=True, help="Skip confirmation.")
        def compact_cmd(scope, skip_confirm):
            ref.do_compact(scope, skip_confirm)

        @config_group.command("reset", help="Reset config to defaults.")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        @click.option("--yes", "-y", "skip_confirm", is_flag=True, help="Skip confirmation.")
        def reset_cmd(scope, skip_confirm):
            ref.do_reset(scope, skip_confirm)

        @config_group.command("edit", help="Edit config in $EDITOR (via temp file).")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        def edit_cmd(scope):
            ref.do_edit(scope)

        @config_group.command("open", help="Open config in system viewer.")
        @click.option("--scope", "-s", default=None, help="Target scope.")
        def open_cmd(scope):
            ref.do_open(scope)

        @config_group.command("diff", help="Diff two config snapshots (scope+version vs scope+version).")
        @click.option("--scope-a", "-s", default=None, help='"From" scope (default: current).')
        @click.option("--version-a", "-v", type=int, default=None, help='"From" version (-1=latest; default: -2 same-scope, -1 cross-scope).')
        @click.option("--scope-b", "-S", default=None, help='"To" scope (default: current).')
        @click.option("--version-b", "-V", type=int, default=None, help='"To" version (-1=latest; default: -1).')
        def diff_cmd(scope_a, version_a, scope_b, version_b):
            ref.do_diff(scope_a, version_a, scope_b, version_b)

        apply_cli_aliases(config_group, "config")

        return config_group

    def register_typer(self, parent, group_name: str = "config"):
        """\
        Register config subcommands on a **Typer** app.

        Args:
            parent: A ``typer.Typer`` instance.
            group_name: Name of the config subgroup.

        Returns:
            The created config Typer app.
        """
        if not CLI_AVAILABLE_TYPER:
            raise RuntimeError("typer is required for register_typer")
        import typer

        config_app = AliasedTyper(help="Config operations: show, set, unset, copy, scope, history, compact, reset.")
        parent.add_typer(config_app, name=group_name)
        ref = self

        @config_app.command("show", help="Show config values.")
        def show(
            key: Annotated[Optional[str], typer.Argument(help="Dot-separated key path.")] = None,
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Scope (omit for merged config).")] = None,
            version: Annotated[Optional[int], typer.Option("-v", "--version", help="Version to show (-1=latest, -2=previous, etc.).")] = None,
        ):
            ref.do_show(key, scope, version)

        @config_app.command("set", help="Set a config value.")
        def set_cmd(
            key: Annotated[str, typer.Argument(help="Dot-separated key path.")],
            value: Annotated[str, typer.Argument(help="Value to set.")],
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
            as_json: Annotated[bool, typer.Option("-j", "--json", help="Parse value as JSON.")] = False,
        ):
            ref.do_set(key, value, scope, as_json)

        @config_app.command("unset", help="Unset a config key.")
        def unset_cmd(
            key: Annotated[str, typer.Argument(help="Dot-separated key path.")],
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
        ):
            ref.do_unset(key, scope)

        @config_app.command("copy", help="Copy config from default/base/scope to current scope.")
        def copy_cmd(
            key: Annotated[Optional[str], typer.Argument(help="Key to copy (omit for all).")] = None,
            from_default: Annotated[bool, typer.Option("-d", "--default", help="Copy from default config.")] = False,
            from_scope: Annotated[Optional[str], typer.Option("-S", "--from-scope", help="Copy from a specific scope.")] = None,
            from_version: Annotated[Optional[int], typer.Option("-V", "--from-version", help="Source version (-1=latest, -2=previous, etc.).")] = None,
            skip_confirm: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation.")] = False,
        ):
            ref.do_copy(key, from_default, from_scope, from_version, skip_confirm)

        @config_app.command("scope", help="List all scopes.")
        def scope_cmd():
            ref.do_scope()

        @config_app.command("history", help="Show version history.")
        def history_cmd(
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
        ):
            ref.do_history(scope)

        @config_app.command("compact", help="Compact version history into one.")
        def compact_cmd(
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
            skip_confirm: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation.")] = False,
        ):
            ref.do_compact(scope, skip_confirm)

        @config_app.command("reset", help="Reset config to defaults.")
        def reset_cmd(
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
            skip_confirm: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation.")] = False,
        ):
            ref.do_reset(scope, skip_confirm)

        @config_app.command("edit", help="Edit config in $EDITOR (via temp file).")
        def edit_cmd(
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
        ):
            ref.do_edit(scope)

        @config_app.command("open", help="Open config in system viewer.")
        def open_cmd(
            scope: Annotated[Optional[str], typer.Option("-s", "--scope", help="Target scope.")] = None,
        ):
            ref.do_open(scope)

        @config_app.command("diff", help="Diff two config snapshots (scope+version vs scope+version).")
        def diff_cmd(
            scope_a: Annotated[Optional[str], typer.Option("-s", "--scope-a", help='"From" scope (default: current).')] = None,
            version_a: Annotated[
                Optional[int], typer.Option("-v", "--version-a", help='"From" version (-1=latest; default: -2 same-scope, -1 cross-scope).')
            ] = None,
            scope_b: Annotated[Optional[str], typer.Option("-S", "--scope-b", help='"To" scope (default: current).')] = None,
            version_b: Annotated[Optional[int], typer.Option("-V", "--version-b", help='"To" version (-1=latest; default: -1).')] = None,
        ):
            ref.do_diff(scope_a, version_a, scope_b, version_b)

        apply_cli_aliases(config_app, "config")
        return config_app

    def register_argparse(self, parent, group_name: str = "config"):
        """\
        Register config subcommands on an **argparse** subparsers object.

        Args:
            parent: An ``argparse._SubParsersAction`` (from ``parser.add_subparsers()``).
            group_name: Name of the config subparser.

        Returns:
            The created config ArgumentParser.
        """
        import argparse

        config_parser = add_aliased_parser(parent, (), group_name, help="Config operations.")
        sub = config_parser.add_subparsers(dest="config_cmd")
        ref = self

        # show
        p = add_aliased_parser(sub, "config", "show", help="Show config values.")
        p.add_argument("key", nargs="?", default=None, help="Dot-separated key path.")
        p.add_argument("-s", "--scope", default=None, help="Scope (omit for merged config).")
        p.add_argument("-v", "--version", dest="version", type=int, default=None, help="Version to show (-1=latest, -2=previous, etc.).")
        p.set_defaults(_func=lambda a: ref.do_show(a.key, a.scope, a.version))

        # set
        p = add_aliased_parser(sub, "config", "set", help="Set a config value.")
        p.add_argument("key", help="Dot-separated key path.")
        p.add_argument("value", help="Value to set.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.add_argument("-j", "--json", dest="as_json", action="store_true", help="Parse value as JSON.")
        p.set_defaults(_func=lambda a: ref.do_set(a.key, a.value, a.scope, a.as_json))

        # unset
        p = add_aliased_parser(sub, "config", "unset", help="Unset a config key.")
        p.add_argument("key", help="Dot-separated key path.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.set_defaults(_func=lambda a: ref.do_unset(a.key, a.scope))

        # copy
        p = add_aliased_parser(sub, "config", "copy", help="Copy from default/base/scope to current scope.")
        p.add_argument("key", nargs="?", default=None, help="Key to copy (omit for all).")
        p.add_argument("-d", "--default", dest="from_default", action="store_true", help="Copy from default config.")
        p.add_argument("-S", "--from-scope", dest="from_scope", default=None, help="Copy from a specific scope.")
        p.add_argument("-V", "--from-version", dest="from_version", type=int, default=None, help="Source version (-1=latest, -2=previous, etc.).")
        p.add_argument("-y", "--yes", dest="skip_confirm", action="store_true", help="Skip confirmation.")
        p.set_defaults(_func=lambda a: ref.do_copy(a.key, a.from_default, a.from_scope, a.from_version, a.skip_confirm))

        # scope
        p = add_aliased_parser(sub, "config", "scope", help="List all scopes.")
        p.set_defaults(_func=lambda a: ref.do_scope())

        # history
        p = add_aliased_parser(sub, "config", "history", help="Show version history.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.set_defaults(_func=lambda a: ref.do_history(a.scope))

        # compact
        p = add_aliased_parser(sub, "config", "compact", help="Compact version history into one.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.add_argument("-y", "--yes", dest="skip_confirm", action="store_true", help="Skip confirmation.")
        p.set_defaults(_func=lambda a: ref.do_compact(a.scope, a.skip_confirm))

        # reset
        p = add_aliased_parser(sub, "config", "reset", help="Reset config to defaults.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.add_argument("-y", "--yes", dest="skip_confirm", action="store_true", help="Skip confirmation.")
        p.set_defaults(_func=lambda a: ref.do_reset(a.scope, a.skip_confirm))

        # edit
        p = add_aliased_parser(sub, "config", "edit", help="Edit config in $EDITOR (via temp file).")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.set_defaults(_func=lambda a: ref.do_edit(a.scope))

        # open
        p = add_aliased_parser(sub, "config", "open", help="Open config in system viewer.")
        p.add_argument("-s", "--scope", default=None, help="Target scope.")
        p.set_defaults(_func=lambda a: ref.do_open(a.scope))

        # diff
        p = add_aliased_parser(sub, "config", "diff", help="Diff two config snapshots (scope+version vs scope+version).")
        p.add_argument("-s", "--scope-a", dest="scope_a", default=None, help='"From" scope (default: current).')
        p.add_argument(
            "-v", "--version-a", dest="version_a", type=int, default=None, help='"From" version (-1=latest; default: -2 same-scope, -1 cross-scope).'
        )
        p.add_argument("-S", "--scope-b", dest="scope_b", default=None, help='"To" scope (default: current).')
        p.add_argument("-V", "--version-b", dest="version_b", type=int, default=None, help='"To" version (-1=latest; default: -1).')
        p.set_defaults(_func=lambda a: ref.do_diff(a.scope_a, a.version_a, a.scope_b, a.version_b))

        # Attach argcomplete if available
        if CLI_AVAILABLE_ARGCOMPLETE:
            import argcomplete

            argcomplete.autocomplete(config_parser)

        # Dispatch helper
        def dispatch(args):
            fn = getattr(args, "_func", None)
            if fn:
                fn(args)
            else:
                config_parser.print_help()

        config_parser.set_defaults(_func=lambda a: config_parser.print_help())
        config_parser._dispatch = dispatch
        return config_parser

    def register_root_typer(self, parent, version: Optional[str] = None):
        """\
        Register top-level commands (``setup``, ``pj``) on a **Typer** app and
        optionally add a ``--version``/``-v`` eager option via ``@app.callback()``.

        Call this *before* ``register_typer`` so the callback is set first.

        Args:
            parent: A ``typer.Typer`` instance.
            version: Version string to display with ``--version``/``-v``.
                     If ``None``, uses ``cm.package_version`` (may be ``None`` too,
                     in which case the flag is omitted).
        """
        if not CLI_AVAILABLE_TYPER:
            raise RuntimeError("typer is required for register_root_typer")
        import typer

        ref = self
        ver = version if version is not None else (self.cm.package_version or "")

        if ver:

            def _version_cb(value: bool):
                if value:
                    typer.echo(f"v{ver}")
                    raise typer.Exit()

            @parent.callback()
            def _root(
                _version: Annotated[
                    Optional[bool],
                    typer.Option(
                        "-v",
                        "--version",
                        callback=_version_cb,
                        is_eager=True,
                        help="Show the version and exit.",
                    ),
                ] = None,
            ):
                pass

        else:

            @parent.callback()
            def _root():
                pass

        @parent.command("setup")
        def setup_cmd(
            reset: Annotated[bool, typer.Option("-r", "--reset", help="Reset to default values.")] = False,
        ):
            """Initialize or reset the package configuration."""
            ref.do_setup(reset)

        @parent.command("pj")
        def pj_cmd(
            parts: Annotated[List[str], typer.Argument(help="Path components to join.")],
        ):
            """Join path strings using the package path utility."""
            ref.do_pj(*parts)

        apply_cli_aliases(parent, ())

    def register_root_click(self, parent, version: Optional[str] = None):
        """\
        Register top-level commands (``setup``, ``pj``) on a **Click** group and
        optionally add a ``--version``/``-v`` option.

        Args:
            parent: A ``click.Group`` instance.
            version: Version string. If ``None``, uses ``cm.package_version``.
        """
        if not CLI_AVAILABLE_CLICK:
            raise RuntimeError("click is required for register_root_click")
        import click

        ref = self
        ver = version if version is not None else (self.cm.package_version or "")

        if ver:
            # Inject a version option into the group's params list
            def _version_cb(ctx, _param, value):
                if value:
                    click.echo(f"v{ver}")
                    ctx.exit()

            parent.params.append(
                click.Option(
                    ["-v", "--version"],
                    is_flag=True,
                    is_eager=True,
                    expose_value=False,
                    callback=_version_cb,
                    help="Show the version and exit.",
                )
            )

        @parent.command("setup")
        @click.option("--reset", "-r", is_flag=True, help="Reset to default values.")
        def setup_cmd(reset):
            """Initialize or reset the package configuration."""
            ref.do_setup(reset)

        @parent.command("pj")
        @click.argument("parts", nargs=-1, required=True)
        def pj_cmd(parts):
            """Join path strings using the package path utility."""
            ref.do_pj(*parts)

        apply_cli_aliases(parent, ())

    def register_root_argparse(self, parent, version: Optional[str] = None):
        """\
        Register top-level commands (``setup``, ``pj``) on an **argparse** parser
        and optionally add a ``--version``/``-v`` flag.

        Args:
            parent: An ``argparse.ArgumentParser`` instance.
            version: Version string. If ``None``, uses ``cm.package_version``.
        """
        import argparse

        ref = self
        ver = version if version is not None else (self.cm.package_version or "")

        if ver:
            parent.add_argument("-v", "--version", action="version", version=f"v{ver}")

        sub = parent.add_subparsers(dest="root_cmd")

        p = add_aliased_parser(sub, (), "setup", help="Initialize or reset the package configuration.")
        p.add_argument("-r", "--reset", action="store_true", help="Reset to default values.")
        p.set_defaults(_func=lambda a: ref.do_setup(a.reset))

        p = add_aliased_parser(sub, (), "pj", help="Join path strings using the package path utility.")
        p.add_argument("parts", nargs="+", help="Path components to join.")
        p.set_defaults(_func=lambda a: ref.do_pj(*a.parts))

        return parent

    def register_root(
        self,
        parent,
        version: Optional[str] = None,
        backend: Literal["click", "typer", "argparse"] = "typer",
    ):
        """\
        Register top-level commands (``setup``, ``pj``, ``--version``) on a CLI
        framework determined by *backend*.

        Args:
            parent: The parent CLI object.
            version: Version string for ``--version``/``-v``.
            backend: ``"typer"``, ``"click"``, or ``"argparse"``.
        """
        if backend == "typer":
            return self.register_root_typer(parent, version)
        elif backend == "click":
            return self.register_root_click(parent, version)
        elif backend == "argparse":
            return self.register_root_argparse(parent, version)
        else:
            raise ValueError(f"Unsupported backend: {backend}")
