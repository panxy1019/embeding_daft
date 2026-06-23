import pytest

from ahvn.utils.basic.cli_utils import AliasedTyper, apply_cli_aliases, get_cli_aliases


def test_root_alias_registry():
    aliases = get_cli_aliases(())
    assert aliases["config"] == ["cfg"]
    assert aliases["capsule"] == ["caps"]


def test_mcp_alias_registry_remove_includes_del():
    aliases = get_cli_aliases("mcp")
    assert aliases["remove"] == ["rm", "del"]


def test_root_aliases_help_and_resolution():
    import typer
    from typer.testing import CliRunner

    app = AliasedTyper(no_args_is_help=True)
    config_app = typer.Typer(help="Config commands.")
    capsule_app = typer.Typer(help="Capsule commands.")

    @config_app.command("show")
    def show():
        pass

    @capsule_app.command("list")
    def list_cmd():
        pass

    app.add_typer(config_app, name="config")
    app.add_typer(capsule_app, name="capsule")
    apply_cli_aliases(app, ())

    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "config|cfg" in help_result.output
    assert "capsule|caps" in help_result.output

    cfg_result = runner.invoke(app, ["cfg", "--help"])
    assert cfg_result.exit_code == 0

    caps_result = runner.invoke(app, ["caps", "--help"])
    assert caps_result.exit_code == 0


@pytest.fixture()
def click_mcp_app(monkeypatch):
    import click
    from click.testing import CliRunner

    from ahvn.cli.mcp_cli import McpCLI
    from ahvn.tool import toolkit as toolkit_module

    remove_calls = []

    monkeypatch.setattr(toolkit_module, "list_factories", lambda: [])

    @click.group()
    def app():
        pass

    cli = McpCLI()
    monkeypatch.setattr(cli, "do_remove", lambda name, skip_confirm=False: remove_calls.append((name, skip_confirm)))
    cli.register_click(app)

    return CliRunner(), app, remove_calls


def test_click_mcp_help_displays_remove_aliases(click_mcp_app):
    runner, app, _ = click_mcp_app
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "remove|rm|del" in result.output


def test_click_mcp_remove_aliases_route_to_same_handler(click_mcp_app):
    runner, app, remove_calls = click_mcp_app

    for subcommand in ("remove", "rm", "del"):
        result = runner.invoke(app, ["mcp", subcommand, "demo", "--yes"])
        assert result.exit_code == 0

    assert remove_calls == [("demo", True), ("demo", True), ("demo", True)]


def test_real_ahvn_help_includes_requested_aliases(monkeypatch, tmp_path):
    import importlib
    from typer.testing import CliRunner

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    import ahvn.cli.ahvn as ahvn_module

    ahvn_module = importlib.reload(ahvn_module)
    runner = CliRunner()

    root_help = runner.invoke(ahvn_module.app, ["--help"])
    assert root_help.exit_code == 0
    assert "config|cfg" in root_help.output
    assert "capsule|caps" in root_help.output

    cfg_help = runner.invoke(ahvn_module.app, ["cfg", "--help"])
    assert cfg_help.exit_code == 0

    caps_help = runner.invoke(ahvn_module.app, ["caps", "--help"])
    assert caps_help.exit_code == 0

    tr_help = runner.invoke(ahvn_module.app, ["tr", "--help"])
    assert tr_help.exit_code == 0
    assert "list|ls" in tr_help.output

    mcp_help = runner.invoke(ahvn_module.app, ["mcp", "--help"])
    assert mcp_help.exit_code == 0
    assert "remove|rm|del" in mcp_help.output

    capsule_help = runner.invoke(ahvn_module.app, ["capsule", "--help"])
    assert capsule_help.exit_code == 0
    assert "remove|rm|del" in capsule_help.output
