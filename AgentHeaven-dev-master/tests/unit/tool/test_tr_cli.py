import datetime

import pytest

from ahvn.cli.tr_cli import TrCLI
from ahvn.utils.basic.cli_utils import get_cli_aliases


def test_tr_alias_registry():
    aliases = get_cli_aliases("tr")
    assert aliases["list"] == ["ls"]
    assert aliases["unset"] == ["remove", "rm", "del"]


@pytest.fixture()
def click_tr_app(monkeypatch):
    import click
    from click.testing import CliRunner

    remove_calls = []
    list_calls = []

    @click.group()
    def app():
        pass

    cli = TrCLI()
    monkeypatch.setattr(cli, "do_remove", lambda namespace, key, lang: remove_calls.append((namespace, key, lang)))
    monkeypatch.setattr(cli, "do_list", lambda namespace=None, lang=None: list_calls.append((namespace, lang)))
    cli.register_click(app)

    return CliRunner(), app, remove_calls, list_calls


def test_click_help_displays_remove_aliases(click_tr_app):
    runner, app, _, _ = click_tr_app
    result = runner.invoke(app, ["tr", "--help"])
    assert result.exit_code == 0
    assert "list|ls" in result.output
    assert "unset|remove|rm|del" in result.output


def test_click_remove_and_aliases_route_to_same_handler(click_tr_app):
    runner, app, remove_calls, _ = click_tr_app

    for subcommand in ("unset", "remove", "rm", "del"):
        result = runner.invoke(app, ["tr", subcommand, "-n", "ns", "-l", "en", "k"])
        assert result.exit_code == 0

    assert remove_calls == [("ns", "k", "en")] * 4


def test_click_list_and_ls_route_to_same_handler(click_tr_app):
    runner, app, _, list_calls = click_tr_app

    for subcommand in ("list", "ls"):
        result = runner.invoke(app, ["tr", subcommand])
        assert result.exit_code == 0

    assert list_calls == [(None, None), (None, None)]


@pytest.fixture()
def typer_tr_app(monkeypatch):
    import typer
    from typer.testing import CliRunner

    remove_calls = []
    list_calls = []
    app = typer.Typer()

    @app.callback()
    def main():
        pass

    cli = TrCLI()
    monkeypatch.setattr(cli, "do_remove", lambda namespace, key, lang: remove_calls.append((namespace, key, lang)))
    monkeypatch.setattr(cli, "do_list", lambda namespace=None, lang=None: list_calls.append((namespace, lang)))
    cli.register_typer(app)

    return CliRunner(), app, remove_calls, list_calls


def test_typer_help_displays_remove_aliases(typer_tr_app):
    runner, app, _, _ = typer_tr_app
    result = runner.invoke(app, ["tr", "--help"])
    assert result.exit_code == 0
    assert "list|ls" in result.output
    assert "unset|remove|rm|del" in result.output


def test_typer_remove_and_aliases_route_to_same_handler(typer_tr_app):
    runner, app, remove_calls, _ = typer_tr_app

    for subcommand in ("unset", "remove", "rm", "del"):
        result = runner.invoke(app, ["tr", subcommand, "k", "-n", "ns", "-l", "en"])
        assert result.exit_code == 0

    assert remove_calls == [("ns", "k", "en")] * 4


def test_typer_list_and_ls_route_to_same_handler(typer_tr_app):
    runner, app, _, list_calls = typer_tr_app

    for subcommand in ("list", "ls"):
        result = runner.invoke(app, ["tr", subcommand])
        assert result.exit_code == 0

    assert list_calls == [(None, None), (None, None)]


def test_do_show_metadata_handles_non_json_native_types(monkeypatch):
    cli = TrCLI()
    table_calls = []

    class _StubStore:
        def info(self, namespace):
            assert namespace == "default_prompt"
            return {
                "id": namespace,
                "created_at": datetime.datetime(2026, 3, 28, 1, 2, 3),
                "updated_at": datetime.datetime(2026, 3, 28, 2, 3, 4),
            }

    monkeypatch.setattr(cli, "_get_store", lambda: _StubStore())
    monkeypatch.setattr(cli.out, "table", lambda title, columns, rows: table_calls.append((title, columns, rows)))

    cli.do_show("default_prompt")

    assert len(table_calls) == 1
    title, columns, rows = table_calls[0]
    assert title == "Translation Namespace: default_prompt"
    assert columns == ["Field", "Value"]
    assert ["ID", "default_prompt"] in rows
    assert ["Main Lang", "en"] in rows


def test_do_render_uses_args_and_lang(monkeypatch):
    cli = TrCLI()
    emitted = []
    render_calls = []

    class _StubTR:
        def render(self, namespace, *, args=None, lang=None, version=None, as_text=True):
            render_calls.append(
                {
                    "namespace": namespace,
                    "args": args,
                    "lang": lang,
                    "version": version,
                    "as_text": as_text,
                }
            )
            return "渲染结果"

    monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.TR_AHVN", _StubTR())
    monkeypatch.setattr(cli, "_echo", lambda msg, err=False: emitted.append((msg, err)))

    cli.do_render(
        "default_prompt",
        lang="zh",
        version="0",
        args=["name=Alice", "count=2", "flags=true"],
    )

    assert render_calls == [
        {
            "namespace": "default_prompt",
            "args": {"name": "Alice", "count": 2, "flags": True},
            "lang": "zh",
            "version": "0",
            "as_text": True,
        }
    ]
    assert emitted == [("渲染结果", False)]
