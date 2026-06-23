"""\
Tests for ``ahvn.utils.basic.cli_utils.ConfigCLI`` and ``CLIOutput``.

Covers all three backends (click, typer, argparse), all config sub-commands,
scope resolution, alias support, and output formatting.
"""

import os
import sys
import pytest
import tempfile

# ---------------------------------------------------------------------------
# Isolated ConfigManager fixture (temp SQLite DB per test)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_cm(tmp_path, monkeypatch):
    """Create a throwaway ConfigManager backed by a temp SQLite DB."""
    # Redirect home to tmp_path so ConfigManager creates its DB there
    fake_home = str(tmp_path)
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setenv("USERPROFILE", fake_home)  # Windows

    # Pre-create the package config directory
    pkg_dir = tmp_path / ".testpkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    from ahvn.utils.basic.config_utils import ConfigManager

    ConfigManager._drop_singleton("testpkg")
    cm = ConfigManager(package="testpkg", distribution="testpkg", setup=False)
    # Manually prime with a small default (skip resource loading)
    cm.storage.set(
        cm.package,
        cm.base_scope,
        {"core": {"debug": False, "lang": "en"}, "llm": {"model": "gpt-4"}},
    )
    yield cm
    ConfigManager._drop_singleton("testpkg")


# ---------------------------------------------------------------------------
# CLIOutput tests
# ---------------------------------------------------------------------------


class TestCLIOutput:
    def test_echo(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.echo("hello")
        captured = capsys.readouterr()
        assert "hello" in captured.out

    def test_success(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.success("done")
        captured = capsys.readouterr()
        assert "done" in captured.out
        assert "✓" in captured.out

    def test_error(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.error("fail")
        captured = capsys.readouterr()
        assert "fail" in captured.err
        assert "✗" in captured.err

    def test_warning(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.warning("careful")
        captured = capsys.readouterr()
        assert "careful" in captured.err

    def test_info(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.info("note")
        captured = capsys.readouterr()
        assert "note" in captured.out

    def test_yaml(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.yaml({"x": 1, "y": [2, 3]})
        captured = capsys.readouterr()
        assert "x" in captured.out

    def test_table(self, capsys):
        from ahvn.utils.basic.cli_utils import CLIOutput

        out = CLIOutput()
        out.table("My Table", ["A", "B"], [["1", "2"], ["3", "4"]])
        captured = capsys.readouterr()
        assert "My Table" in captured.out


# ---------------------------------------------------------------------------
# ConfigCLI core operations tests
# ---------------------------------------------------------------------------


class TestConfigCLIOps:
    def test_do_show_all(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_show()
        captured = capsys.readouterr()
        assert "debug" in captured.out
        assert "gpt-4" in captured.out

    def test_do_show_key(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_show(key="core.debug")
        captured = capsys.readouterr()
        assert "false" in captured.out.lower()

    def test_do_show_scoped(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_show(scope=".")  # explicit base scope
        captured = capsys.readouterr()
        assert "debug" in captured.out

    def test_do_set_and_show(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_set("core.debug", "true")
        captured = capsys.readouterr()
        assert "Set" in captured.out

        cli.do_show(key="core.debug")
        captured = capsys.readouterr()
        assert "true" in captured.out.lower()

    def test_do_set_json(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_set("core.tags", '["a","b"]', as_json=True)
        captured = capsys.readouterr()
        assert "Set" in captured.out

        cli.do_show(key="core.tags")
        captured = capsys.readouterr()
        assert "a" in captured.out

    def test_do_set_json_invalid(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_set("core.x", "{bad json", as_json=True)
        captured = capsys.readouterr()
        assert "Invalid JSON" in captured.err

    def test_do_unset(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_unset("core.lang")
        captured = capsys.readouterr()
        assert "Unset" in captured.out

        cli.do_show(key="core.lang")
        captured = capsys.readouterr()
        assert "No config" in captured.err or "null" in captured.out.lower() or "None" in captured.out

    def test_do_scope(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_scope()
        captured = capsys.readouterr()
        assert "testpkg" in captured.out
        assert "Scope" in captured.out

    def test_do_history(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        # Create another version
        tmp_cm.set("core.debug", True)
        cli.do_history()
        captured = capsys.readouterr()
        assert "History" in captured.out
        assert "Version" in captured.out

    def test_do_compact(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        # Create history larger than retention by bypassing auto-compact in set().
        for i in range(25):
            tmp_cm.storage.set(
                tmp_cm.package,
                tmp_cm.base_scope,
                {"core": {"debug": bool(i % 2)}},
                keep_last_n=1000,
            )

        cli.do_compact(skip_confirm=True)
        captured = capsys.readouterr()
        assert "Compacted" in captured.out

    def test_do_compact_nothing(self, tmp_cm, capsys):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        cli.do_compact(skip_confirm=True)
        captured = capsys.readouterr()
        assert "Nothing to compact" in captured.out

    def test_do_reset(self, tmp_cm, capsys, monkeypatch):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        # Override load_default to return known data
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True, "lang": "zh"}})
        cli.do_reset(skip_confirm=True)
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_do_copy_from_default(self, tmp_cm, capsys, monkeypatch):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True, "lang": "zh"}, "llm": {"model": "o1"}})
        cli.do_copy(key="llm.model", from_default=True)
        captured = capsys.readouterr()
        assert "Copied" in captured.out

    def test_do_copy_key_missing(self, tmp_cm, capsys, monkeypatch):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True}})
        cli.do_copy(key="nonexistent.key", from_default=True)
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_do_diff_no_changes(self, tmp_cm, capsys):
        """Comparing a scope to itself (same version) reports no differences."""
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        ver = tmp_cm.storage.version(tmp_cm.package, tmp_cm.base_scope)
        cli.do_diff(version_a=ver, version_b=ver)
        captured = capsys.readouterr()
        assert "no differences" in captured.out.lower() or "no differences" in captured.err.lower()

    def test_do_diff_two_versions(self, tmp_cm, capsys):
        """Creating two versions and diffing them produces a non-empty diff."""
        from ahvn.cli.config_cli import ConfigCLI

        # v1 already exists from the fixture; create v2 with a changed value
        tmp_cm.storage.set(
            tmp_cm.package,
            tmp_cm.base_scope,
            {"core": {"debug": True, "lang": "fr"}, "llm": {"model": "gpt-4"}},
        )
        ver_b = tmp_cm.storage.version(tmp_cm.package, tmp_cm.base_scope)  # 2
        ver_a = ver_b - 1  # 1

        cli = ConfigCLI(tmp_cm)
        cli.do_diff(version_a=ver_a, version_b=ver_b)
        captured = capsys.readouterr()
        # Diff output should contain a change marker
        output = captured.out + captured.err
        assert any(c in output for c in ("+", "-", "debug", "lang"))


# ---------------------------------------------------------------------------
# Scope resolution tests
# ---------------------------------------------------------------------------


class TestScopeResolution:
    def test_none_returns_none(self, tmp_cm):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        assert cli.resolve_scope(None) is None
        assert cli.resolve_scope("") is None

    def test_dot_returns_base(self, tmp_cm):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        assert cli.resolve_scope(".") == "testpkg"

    def test_suffix_gets_prefixed(self, tmp_cm):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        assert cli.resolve_scope("x.y") == "testpkg.x.y"

    def test_already_prefixed_passthrough(self, tmp_cm):
        from ahvn.cli.config_cli import ConfigCLI

        cli = ConfigCLI(tmp_cm)
        assert cli.resolve_scope("testpkg.x.y") == "testpkg.x.y"


# ---------------------------------------------------------------------------
# Click backend tests
# ---------------------------------------------------------------------------


class TestClickBackend:
    @pytest.fixture()
    def click_runner(self, tmp_cm):
        import click
        from click.testing import CliRunner
        from ahvn.cli.config_cli import ConfigCLI

        @click.group()
        def app():
            pass

        config_cli = ConfigCLI(tmp_cm)
        config_cli.register_click(app)

        return CliRunner(), app

    def test_show(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "debug" in result.output

    def test_show_key(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "show", "core.debug"])
        assert result.exit_code == 0

    def test_show_scoped(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "show", "--scope", "."])
        assert result.exit_code == 0

    def test_set(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "set", "core.debug", "true"])
        assert result.exit_code == 0
        assert "Set" in result.output

    def test_set_json(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "set", "core.tags", '["a"]', "--json"])
        assert result.exit_code == 0

    def test_unset(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "unset", "core.lang"])
        assert result.exit_code == 0
        assert "Unset" in result.output

    def test_scope(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "scope"])
        assert result.exit_code == 0
        assert "testpkg" in result.output

    def test_history(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "history"])
        assert result.exit_code == 0

    def test_compact(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "compact", "--yes"])
        assert result.exit_code == 0

    def test_reset(self, click_runner, monkeypatch, tmp_cm):
        runner, app = click_runner
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True}})
        result = runner.invoke(app, ["config", "reset", "--yes"])
        assert result.exit_code == 0
        assert "Reset" in result.output

    def test_copy_alias(self, click_runner):
        """AliasedGroup should resolve 'cp' → 'copy'."""
        runner, app = click_runner
        result = runner.invoke(app, ["config", "cp", "--yes"])
        # copy from base scope to itself — should succeed
        assert result.exit_code == 0

    def test_history_alias(self, click_runner):
        """AliasedGroup should resolve 'hist' → 'history'."""
        runner, app = click_runner
        result = runner.invoke(app, ["config", "hist"])
        assert result.exit_code == 0

    def test_unset_alias(self, click_runner):
        """AliasedGroup should resolve 'rm' → 'unset'."""
        runner, app = click_runner
        result = runner.invoke(app, ["config", "rm", "core.lang"])
        assert result.exit_code == 0

    def test_help_displays_pipe_aliases(self, click_runner):
        runner, app = click_runner
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "show|list|ls" in result.output
        assert "copy|cp" in result.output

    def test_diff_no_changes(self, click_runner):
        """diff with no args on a single-version scope shows the single-version message."""
        runner, app = click_runner
        result = runner.invoke(app, ["config", "diff"])
        assert result.exit_code == 0
        assert "only one" in result.output.lower()

    def test_diff_two_versions(self, click_runner, tmp_cm):
        """diff with explicit versions shows changed lines."""
        from ahvn.cli.config_cli import ConfigCLI  # noqa: F401

        tmp_cm.storage.set(
            tmp_cm.package,
            tmp_cm.base_scope,
            {"core": {"debug": True, "lang": "fr"}, "llm": {"model": "gpt-4"}},
        )
        runner, app = click_runner
        result = runner.invoke(app, ["config", "diff", "--version-a", "1", "--version-b", "2"])
        assert result.exit_code == 0
        assert any(c in result.output for c in ("+", "-", "debug", "lang"))


# ---------------------------------------------------------------------------
# Typer backend tests
# ---------------------------------------------------------------------------


class TestTyperBackend:
    @pytest.fixture()
    def typer_runner(self, tmp_cm):
        import typer
        from typer.testing import CliRunner
        from ahvn.cli.config_cli import ConfigCLI

        app = typer.Typer()

        @app.callback()
        def main():
            pass

        config_cli = ConfigCLI(tmp_cm)
        config_cli.register_typer(app)

        return CliRunner(), app

    def test_show(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "debug" in result.output

    def test_show_key(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "show", "core.debug"])
        assert result.exit_code == 0

    def test_set(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "set", "core.debug", "true"])
        assert result.exit_code == 0
        assert "Set" in result.output

    def test_set_json(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "set", "core.tags", '["a"]', "--json"])
        assert result.exit_code == 0

    def test_unset(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "unset", "core.lang"])
        assert result.exit_code == 0

    def test_scope(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "scope"])
        assert result.exit_code == 0
        assert "testpkg" in result.output

    def test_history(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "history"])
        assert result.exit_code == 0

    def test_compact(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "compact", "--yes"])
        assert result.exit_code == 0

    def test_reset(self, typer_runner, monkeypatch, tmp_cm):
        runner, app = typer_runner
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True}})
        result = runner.invoke(app, ["config", "reset", "--yes"])
        assert result.exit_code == 0

    def test_copy(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "copy", "--yes"])
        assert result.exit_code == 0

    def test_alias_ls(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "ls"])
        assert result.exit_code == 0
        assert "debug" in result.output

    def test_alias_help_displays_pipe_aliases(self, typer_runner):
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "show|list|ls" in result.output
        assert "history|hist" in result.output

    def test_diff_no_changes(self, typer_runner):
        """diff with no args on a single-version scope shows the single-version message."""
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "diff"])
        assert result.exit_code == 0
        assert "only one" in result.output.lower()

    def test_diff_two_versions(self, typer_runner, tmp_cm):
        """diff with explicit versions shows changed lines."""
        tmp_cm.storage.set(
            tmp_cm.package,
            tmp_cm.base_scope,
            {"core": {"debug": True, "lang": "fr"}, "llm": {"model": "gpt-4"}},
        )
        runner, app = typer_runner
        result = runner.invoke(app, ["config", "diff", "--version-a", "1", "--version-b", "2"])
        assert result.exit_code == 0
        assert any(c in result.output for c in ("+", "-", "debug", "lang"))


# ---------------------------------------------------------------------------
# Argparse backend tests
# ---------------------------------------------------------------------------


class TestArgparseBackend:
    @pytest.fixture()
    def argparse_app(self, tmp_cm):
        import argparse
        from ahvn.cli.config_cli import ConfigCLI

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        config_cli = ConfigCLI(tmp_cm)
        config_parser = config_cli.register_argparse(sub)
        return parser, config_parser

    def _run(self, parser, args_list):
        args = parser.parse_args(args_list)
        fn = getattr(args, "_func", None)
        if fn:
            fn(args)

    def test_show(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "show"])
        captured = capsys.readouterr()
        assert "debug" in captured.out

    def test_show_key(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "show", "core.debug"])
        captured = capsys.readouterr()
        # Should show value
        assert "false" in captured.out.lower()

    def test_show_alias_ls(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "ls"])
        captured = capsys.readouterr()
        assert "debug" in captured.out

    def test_set(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "set", "core.debug", "true"])
        captured = capsys.readouterr()
        assert "Set" in captured.out

    def test_unset(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "unset", "core.lang"])
        captured = capsys.readouterr()
        assert "Unset" in captured.out

    def test_unset_alias_rm(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "rm", "core.lang"])
        captured = capsys.readouterr()
        assert "Unset" in captured.out

    def test_scope(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "scope"])
        captured = capsys.readouterr()
        assert "testpkg" in captured.out

    def test_history(self, argparse_app, capsys):
        parser, _ = argparse_app
        self._run(parser, ["config", "history"])
        captured = capsys.readouterr()
        # Should print history table or info
        assert "Version" in captured.out

    def test_compact(self, argparse_app, capsys, tmp_cm):
        # Create history larger than retention by bypassing auto-compact in set().
        for i in range(25):
            tmp_cm.storage.set(
                tmp_cm.package,
                tmp_cm.base_scope,
                {"core": {"debug": bool(i % 2)}},
                keep_last_n=1000,
            )
        parser, _ = argparse_app
        self._run(parser, ["config", "compact", "--yes"])
        captured = capsys.readouterr()
        assert "Compacted" in captured.out

    def test_reset(self, argparse_app, capsys, tmp_cm, monkeypatch):
        parser, _ = argparse_app
        monkeypatch.setattr(tmp_cm, "load_default", lambda: {"core": {"debug": True}})
        self._run(parser, ["config", "reset", "--yes"])
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_diff_no_changes(self, argparse_app, capsys):
        """diff with no args on a single-version scope shows the single-version message."""
        parser, _ = argparse_app
        self._run(parser, ["config", "diff"])
        captured = capsys.readouterr()
        assert "only one" in (captured.out + captured.err).lower()

    def test_diff_two_versions(self, argparse_app, capsys, tmp_cm):
        """diff with explicit versions shows changed lines."""
        tmp_cm.storage.set(
            tmp_cm.package,
            tmp_cm.base_scope,
            {"core": {"debug": True, "lang": "fr"}, "llm": {"model": "gpt-4"}},
        )
        parser, _ = argparse_app
        self._run(parser, ["config", "diff", "--version-a", "1", "--version-b", "2"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert any(c in output for c in ("+", "-", "debug", "lang"))


# ---------------------------------------------------------------------------
# Availability flags tests
# ---------------------------------------------------------------------------


class TestAvailabilityFlags:
    def test_flags_are_bools(self):
        from ahvn.utils.basic.cli_utils import (
            CLI_AVAILABLE_CLICK,
            CLI_AVAILABLE_TYPER,
            CLI_AVAILABLE_ARGPARSE,
            CLI_AVAILABLE_ARGCOMPLETE,
            CLI_AVAILABLE_PROMPT_TOOLKIT,
            CLI_AVAILABLE_RICH,
        )

        for flag in [
            CLI_AVAILABLE_CLICK,
            CLI_AVAILABLE_TYPER,
            CLI_AVAILABLE_ARGPARSE,
            CLI_AVAILABLE_ARGCOMPLETE,
            CLI_AVAILABLE_PROMPT_TOOLKIT,
            CLI_AVAILABLE_RICH,
        ]:
            assert isinstance(flag, bool)

    def test_click_is_available(self):
        from ahvn.utils.basic.cli_utils import CLI_AVAILABLE_CLICK

        assert CLI_AVAILABLE_CLICK is True

    def test_rich_is_available(self):
        from ahvn.utils.basic.cli_utils import CLI_AVAILABLE_RICH

        assert CLI_AVAILABLE_RICH is True
