import pytest

from ahvn.cli.capsule_cli import CapsuleCLI
from ahvn.utils.basic.cli_utils import get_cli_aliases


class _DummyStore:
    def __init__(self):
        self.deleted = []

    def delete(self, capsule_id):
        self.deleted.append(capsule_id)


def test_do_remove_deletes_resolved_capsule_id(monkeypatch):
    cli = CapsuleCLI()
    cli._store = _DummyStore()

    expected_id = "1234567890abcdef1234567890abcdef"
    monkeypatch.setattr(
        cli,
        "_resolve_capsule",
        lambda cid: {
            "capsule_id": expected_id,
            "manifest": {"name": "demo-capsule"},
        },
    )

    cli.do_remove("123456", skip_confirm=True)

    assert cli._store.deleted == [expected_id]


def test_do_serve_uses_http_transport_by_default(monkeypatch):
    cli = CapsuleCLI()

    monkeypatch.setattr(
        cli,
        "_resolve_capsule",
        lambda cid: {
            "capsule_id": "1234567890abcdef1234567890abcdef",
            "manifest": {"name": f"cap-{cid}"},
        },
    )

    captured = {}

    class _FakeToolkit:
        def serve(self, transport="stdio", host="127.0.0.1", port=7002):
            captured["transport"] = transport
            captured["host"] = host
            captured["port"] = port

    from ahvn.tool.toolkit import Toolkit

    monkeypatch.setattr(Toolkit, "from_capsules", classmethod(lambda cls, **kwargs: _FakeToolkit()))

    cli.do_serve(["abc123"], stdio=False, host="127.0.0.1", port=7002)

    assert captured["transport"] == "http"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 7002


def test_capsule_alias_registry_remove_order():
    aliases = get_cli_aliases("capsule")
    assert aliases["remove"] == ["rm", "del"]


def test_resolve_capsule_by_name_handles_integer_registry_id():
    class _DummyStore:
        def get(self, capsule_id):
            if capsule_id == 42:
                return {
                    "capsule_id": "0000000000000000000000000000000000000042",
                    "manifest": {"name": "add"},
                }
            return None

        def list(self):
            return [{"id": 42, "name": "add", "qualname": "pkg.add"}]

    cli = CapsuleCLI()
    cli._store = _DummyStore()

    resolved = cli._resolve_capsule("add")
    assert resolved["manifest"]["name"] == "add"
    assert resolved["capsule_id"].endswith("42")


@pytest.fixture()
def typer_capsule_app(monkeypatch):
    import typer
    from typer.testing import CliRunner

    remove_calls = []

    app = typer.Typer()

    @app.callback()
    def main():
        pass

    cli = CapsuleCLI()
    monkeypatch.setattr(cli, "do_remove", lambda capsule_id, skip_confirm=False: remove_calls.append((capsule_id, skip_confirm)))
    monkeypatch.setattr(cli, "do_list", lambda tag=None: None)
    cli.register_typer(app)

    return CliRunner(), app, remove_calls


def test_typer_help_displays_capsule_remove_aliases(typer_capsule_app):
    runner, app, _ = typer_capsule_app
    result = runner.invoke(app, ["capsule", "--help"])
    assert result.exit_code == 0
    assert "remove|rm|del" in result.output


def test_typer_capsule_remove_aliases_route_to_same_handler(typer_capsule_app):
    runner, app, remove_calls = typer_capsule_app

    for subcommand in ("remove", "rm", "del"):
        result = runner.invoke(app, ["capsule", subcommand, "abc123", "--yes"])
        assert result.exit_code == 0

    assert remove_calls == [("abc123", True), ("abc123", True), ("abc123", True)]
