import click
import typer
from click.testing import CliRunner as ClickCliRunner
from typer.testing import CliRunner as TyperCliRunner

from ahvn.tool.db.toolkit import DatabaseToolkitFactory


class _CaptureCLI:
    def __init__(self):
        self.calls = []

    def do_create(self, factory_name, name, args):
        self.calls.append((factory_name, name, list(args)))


def test_db_create_typer_forwards_no_heal_sql():
    app = typer.Typer()
    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_typer(app, cli_ref)

    runner = TyperCliRunner()
    result = runner.invoke(app, ["tk1", "-P", "sqlite", "-D", "./tmp.db", "--no-heal-sql"])

    assert result.exit_code == 0, result.stdout
    assert len(cli_ref.calls) == 1
    factory_name, name, args = cli_ref.calls[0]
    assert factory_name == "db"
    assert name == "tk1"
    assert "heal_sql=False" in args
    assert "heal_sql=True" not in args


def test_db_create_typer_forwards_heal_sql_true():
    app = typer.Typer()
    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_typer(app, cli_ref)

    runner = TyperCliRunner()
    result = runner.invoke(app, ["tk2", "-P", "sqlite", "-D", "./tmp.db", "--heal-sql"])

    assert result.exit_code == 0, result.stdout
    assert len(cli_ref.calls) == 1
    _, _, args = cli_ref.calls[0]
    assert "heal_sql=True" in args


def test_db_create_typer_default_forwards_heal_sql_false():
    app = typer.Typer()
    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_typer(app, cli_ref)

    runner = TyperCliRunner()
    result = runner.invoke(app, ["tk5", "-P", "sqlite", "-D", "./tmp.db"])

    assert result.exit_code == 0, result.stdout
    assert len(cli_ref.calls) == 1
    _, _, args = cli_ref.calls[0]
    assert "heal_sql=False" in args


def test_db_create_click_forwards_no_heal_sql():
    @click.group()
    def create_group():
        pass

    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_click(create_group, cli_ref)

    runner = ClickCliRunner()
    result = runner.invoke(create_group, ["db", "tk3", "-P", "sqlite", "-D", "./tmp.db", "--no-heal-sql"])

    assert result.exit_code == 0, result.output
    assert len(cli_ref.calls) == 1
    factory_name, name, args = cli_ref.calls[0]
    assert factory_name == "db"
    assert name == "tk3"
    assert "heal_sql=False" in args
    assert "heal_sql=True" not in args


def test_db_create_click_forwards_heal_sql_true():
    @click.group()
    def create_group():
        pass

    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_click(create_group, cli_ref)

    runner = ClickCliRunner()
    result = runner.invoke(create_group, ["db", "tk4", "-P", "sqlite", "-D", "./tmp.db", "--heal-sql"])

    assert result.exit_code == 0, result.output
    assert len(cli_ref.calls) == 1
    _, _, args = cli_ref.calls[0]
    assert "heal_sql=True" in args


def test_db_create_click_default_forwards_heal_sql_false():
    @click.group()
    def create_group():
        pass

    cli_ref = _CaptureCLI()
    DatabaseToolkitFactory._register_create_click(create_group, cli_ref)

    runner = ClickCliRunner()
    result = runner.invoke(create_group, ["db", "tk6", "-P", "sqlite", "-D", "./tmp.db"])

    assert result.exit_code == 0, result.output
    assert len(cli_ref.calls) == 1
    _, _, args = cli_ref.calls[0]
    assert "heal_sql=False" in args
