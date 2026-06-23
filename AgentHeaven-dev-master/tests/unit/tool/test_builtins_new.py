"""
Tests for LLM and Config toolkit builtins, view command, multi-serve, and Skills export.

This module tests:
1. ConfigToolkitFactory — create, show/set/unset tools
2. LLMToolkitFactory — create, ask tool schema (no actual LLM calls)
3. McpCLI.do_show — toolkit viewing
4. Multi-serve MCP JSON merging
5. Skills export for all toolkit types
"""

import pytest
import os
import json

from ahvn.tool import ToolSpec
from ahvn.tool.toolkit import (
    Toolkit,
    ToolkitFactory,
    register_factory,
    get_factory,
    list_factories,
    _FACTORIES,
)
from ahvn.tool.manager import ToolkitManager

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_factory_registry():
    """Preserve factory registry around each test."""
    saved = dict(_FACTORIES)
    yield
    _FACTORIES.clear()
    _FACTORIES.update(saved)


@pytest.fixture
def manager(tmp_path):
    """Create a manager with a temp persistence DB."""
    from ahvn.tool.store import ToolkitStore

    store = ToolkitStore(
        provider="sqlite",
        database=str(tmp_path / "toolkits.db"),
    )
    m = ToolkitManager(store=store)
    yield m


def _create_manager_toolkit(manager, factory_name: str, name: str, **args):
    factory = get_factory(factory_name)
    toolkit = factory.create(name, **args)
    manager.add(
        toolkit,
        source={
            "factory": factory_name,
            "args": dict(args),
        },
    )
    return toolkit


# ── ConfigToolkitFactory Tests ───────────────────────────────────────────


class TestConfigToolkitFactory:
    def test_factory_registered(self):
        factories = list_factories()
        assert "config" in factories

    def test_create_toolkit(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        assert tk.name == "test-config"
        assert sorted(tk.list_tools()) == ["config_set", "config_show", "config_unset"]

    def test_create_without_scope(self):
        factory = get_factory("config")
        tk = factory.create("test-config-basic")
        assert "package `ahvn`" in tk.description
        assert "dot-path" in tk.description

    def test_config_show_tool(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        # config_show with a key should return something (llm.default_preset)
        result = tk.run("config_show", key="llm.default_preset")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_config_show_all(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        result = tk.run("config_show")
        assert isinstance(result, str)
        assert len(result) > 10  # Should have substantial config content

    def test_config_show_params(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        tool = tk.get_tool("config_show")
        params = tool.params
        assert "key" in params
        assert "scope" not in params

    def test_config_set_params(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        tool = tk.get_tool("config_set")
        params = tool.params
        assert "key" in params
        assert "value" in params
        assert "scope" not in params

    def test_config_unset_params(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        tool = tk.get_tool("config_unset")
        params = tool.params
        assert "key" in params
        assert "scope" not in params

    def test_args_schema(self):
        factory = get_factory("config")
        schema = factory.args_schema()
        assert "package" in schema["properties"]
        assert "config_manager" in schema["properties"]
        assert "scope" not in schema["properties"]

    def test_create_with_package_and_cm(self):
        factory = get_factory("config")
        tk = factory.create("test-config-cm", package="ahvn", config_manager="CM_AHVN")
        assert "package `ahvn`" in tk.description
        assert "dot-path" in tk.description

    def test_create_with_invalid_cm_raises(self):
        factory = get_factory("config")
        with pytest.raises(AttributeError):
            factory.create("test-bad-cm", package="ahvn", config_manager="NONEXISTENT")

    def test_persistence(self, manager):
        _create_manager_toolkit(manager, "config", "persist-config")
        payload = manager.store.get("persist-config")
        assert payload is not None
        assert payload["manifest"]["source"]["factory"] == "config"

    def test_persistence_round_trip_runs_config_show(self, manager):
        _create_manager_toolkit(manager, "config", "persist-config-roundtrip")
        manager2 = ToolkitManager(store=manager.store)
        result = manager2.run("persist-config-roundtrip.config_show", key="llm.default_preset")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_to_fastmcp(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        from fastmcp import FastMCP

        server = tk.to_fastmcp()
        assert isinstance(server, FastMCP)

    def test_to_jsonschema_list(self):
        factory = get_factory("config")
        tk = factory.create("test-config")
        schemas = tk.to_jsonschema_list()
        assert len(schemas) == 3
        names = {s["function"]["name"] for s in schemas}
        assert "config_show" in names
        assert "config_set" in names
        assert "config_unset" in names


# ── LLMToolkitFactory Tests ──────────────────────────────────────────────


class TestLLMToolkitFactory:
    def test_factory_registered(self):
        factories = list_factories()
        assert "llm" in factories

    def test_create_toolkit(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm")
        assert tk.name == "test-llm"
        assert "ask" in tk.list_tools()

    def test_reject_legacy_presets_arg(self):
        """`presets` is intentionally unsupported in the cleaned API."""
        factory = get_factory("llm")
        with pytest.raises(TypeError):
            factory.create("test-llm-presets", presets=["sys", "fast"])

    def test_create_with_ask_presets(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm-ask", ask_presets=["sys", "chat"])
        tool = tk.get_tool("ask")
        assert tool.params["preset"]["enum"] == ["sys", "chat"]

    def test_create_with_default_preset(self):
        factory = get_factory("llm")
        factory.create("test-llm-default", default_preset="sys")

    def test_ask_tool_params(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm")
        tool = tk.get_tool("ask")
        params = tool.params
        assert "query" in params
        assert "preset" in params

    def test_ask_tool_query_required(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm")
        tool = tk.get_tool("ask")
        required = tool.input_schema.get("required", [])
        assert "query" in required

    def test_preset_enum_constraint(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm-enum", ask_presets=["sys", "chat", "reason"])
        tool = tk.get_tool("ask")
        assert tool.params["preset"]["enum"] == ["sys", "chat", "reason"]

    def test_no_preset_constraint_when_none(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm-free")
        tool = tk.get_tool("ask")
        # No enum when presets is None
        assert "enum" not in tool.params.get("preset", {})

    def test_args_schema(self):
        factory = get_factory("llm")
        schema = factory.args_schema()
        assert "default_preset" in schema["properties"]
        assert "ask_presets" in schema["properties"]

    def test_persistence(self, manager):
        _create_manager_toolkit(manager, "llm", "persist-llm", ask_presets=["sys"])
        payload = manager.store.get("persist-llm")
        assert payload is not None
        assert payload["manifest"]["source"]["args"]["ask_presets"] == ["sys"]

    def test_to_fastmcp(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm-mcp")
        from fastmcp import FastMCP

        server = tk.to_fastmcp()
        assert isinstance(server, FastMCP)

    def test_to_jsonschema_list(self):
        factory = get_factory("llm")
        tk = factory.create("test-llm-json")
        schemas = tk.to_jsonschema_list()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "ask"


# ── Multi-serve MCP JSON Tests ──────────────────────────────────────────


class TestMultiServeMcpJson:
    def test_single_mcp_config(self):
        factory = get_factory("config")
        tk = factory.create("c1")
        result = tk.to_mcp_config()
        assert "mcpServers" in result
        assert "c1" in result["mcpServers"]
        # Default is now http transport
        assert "url" in result["mcpServers"]["c1"]
        assert result["mcpServers"]["c1"]["transport"] == "http"

    def test_single_mcp_json_is_string(self):
        factory = get_factory("config")
        tk = factory.create("c1")
        result = tk.to_mcp_json()
        assert isinstance(result, str)
        assert '"mcpServers"' in result

    def test_single_mcp_config_stdio(self):
        factory = get_factory("config")
        tk = factory.create("c1")
        result = tk.to_mcp_config(transport="stdio")
        assert "mcpServers" in result
        assert "c1" in result["mcpServers"]
        entry = result["mcpServers"]["c1"]
        # Should use ahvn CLI or sys.executable fallback
        assert "command" in entry
        assert "args" in entry

    def test_merged_mcp_config(self):
        """Simulate merging multiple MCP configs as do_serve does."""
        factory_config = get_factory("config")
        factory_llm = get_factory("llm")
        tk1 = factory_config.create("c1")
        tk2 = factory_llm.create("l1")

        merged = {"mcpServers": {}}
        for tk in [tk1, tk2]:
            mcp = tk.to_mcp_config()
            merged["mcpServers"].update(mcp["mcpServers"])

        assert "c1" in merged["mcpServers"]
        assert "l1" in merged["mcpServers"]
        assert len(merged["mcpServers"]) == 2

    def test_http_merged(self):
        """HTTP transport assigns different ports."""
        factory_config = get_factory("config")
        factory_llm = get_factory("llm")
        tk1 = factory_config.create("c1")
        tk2 = factory_llm.create("l1")

        merged = {"mcpServers": {}}
        base_port = 8000
        for i, tk in enumerate([tk1, tk2]):
            mcp = tk.to_mcp_config("http", "0.0.0.0", base_port + i)
            merged["mcpServers"].update(mcp["mcpServers"])

        assert merged["mcpServers"]["c1"]["url"] == "http://localhost:8000/c1/mcp"
        assert merged["mcpServers"]["l1"]["url"] == "http://localhost:8001/l1/mcp"


class TestCreateCommand:
    def test_do_create_uses_registry_and_persists_source(self, manager):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        cli.do_create("config", "cli-config", args=["package=ahvn", "config_manager=CM_AHVN"])

        info = manager.info("cli-config")
        assert info["source_factory"] == "config"
        assert info["source_args"]["package"] == "ahvn"
        assert info["source_args"]["config_manager"] == "CM_AHVN"

        result = manager.run("cli-config.config_show", key="llm.default_preset")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_do_create_missing_factory_reports_error(self, manager, capsys):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        cli.do_create("missing-factory", "bad-toolkit")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "no toolkitfactory registered with name" in output.lower()
        assert "missing-factory" in output

    def test_do_create_duplicate_name_reports_error(self, manager, capsys):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        # First create succeeds
        cli.do_create("config", "dup-test", args=["package=ahvn", "config_manager=CM_AHVN"])
        capsys.readouterr()  # clear output

        # Second create with same name should error
        cli.do_create("config", "dup-test", args=["package=ahvn", "config_manager=CM_AHVN"])
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "already exists" in output.lower()


class TestServeCommand:
    def test_do_serve_defaults_to_http(self, manager, monkeypatch):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager

        captured = {}

        def fake_serve_many(names, transport, host, port):
            captured["transport"] = transport
            captured["host"] = host
            captured["port"] = port
            return [
                {
                    "name": "c1",
                    "transport": "http",
                    "pid": 12345,
                    "url": f"http://{host}:{port}",
                    "mcp_config": {
                        "mcpServers": {
                            "c1": {
                                "url": f"http://{host}:{port}/c1/mcp",
                                "transport": "http",
                            }
                        }
                    },
                }
            ]

        monkeypatch.setattr(manager, "serve_many", fake_serve_many)
        monkeypatch.setattr(manager, "wait_forever", lambda: None)

        cli.do_serve(["c1"])

        assert captured["transport"] == "http"
        assert captured["host"] == "127.0.0.1"
        assert captured["port"] == 7001

    def test_do_serve_stdio_flag_switches_stdio(self, manager, monkeypatch):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager

        captured = {}

        def fake_serve_many(names, transport, host, port):
            captured["transport"] = transport
            captured["host"] = host
            captured["port"] = port
            return [
                {
                    "name": "c1",
                    "transport": "stdio",
                    "pid": 23456,
                    "mcp_config": {
                        "mcpServers": {
                            "c1": {
                                "command": "python",
                                "args": ["-c", "..."],
                            }
                        }
                    },
                }
            ]

        monkeypatch.setattr(manager, "serve_many", fake_serve_many)
        monkeypatch.setattr(manager, "wait_forever", lambda: None)

        cli.do_serve(["c1"], stdio=True)

        assert captured["transport"] == "stdio"
        assert captured["host"] == "127.0.0.1"
        assert captured["port"] == 7001


class TestResetCommand:
    def test_do_reset_calls_manager(self, manager, monkeypatch):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager

        captured = {}

        def fake_reset(name):
            captured["name"] = name

        monkeypatch.setattr(manager, "reset", fake_reset)
        cli.do_reset("c1")
        assert captured["name"] == "c1"

    def test_do_reset_handles_manager_errors(self, manager, monkeypatch, capsys):
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager

        def fake_reset(_name):
            raise RuntimeError("Cannot reset while serving")

        monkeypatch.setattr(manager, "reset", fake_reset)
        cli.do_reset("c1")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "cannot reset while serving" in output.lower()


# ── Skills Export Tests ──────────────────────────────────────────────────


class TestSkillsExport:
    def _validate_skill_dir(self, path, toolkit_name, expected_tools):
        """Validate a Skills export directory structure."""
        assert os.path.isdir(path)
        assert os.path.isfile(os.path.join(path, "SKILL.md"))

        # Check SKILL.md contains tool names and frontmatter
        with open(os.path.join(path, "SKILL.md")) as f:
            skill_md = f.read()
        for tool_name in expected_tools:
            assert tool_name in skill_md
        assert "---" in skill_md  # frontmatter marker
        assert toolkit_name in skill_md

    def test_export_config_toolkit(self, tmp_path):
        factory = get_factory("config")
        tk = factory.create("export-config")
        out = str(tmp_path / "config-skill")
        skill_dir = tk.export(out)
        # export() returns the named subfolder: out/export-config/
        assert skill_dir == os.path.join(os.path.abspath(out), "export-config")
        self._validate_skill_dir(skill_dir, "export-config", ["config_show", "config_set", "config_unset"])

    def test_export_llm_toolkit(self, tmp_path):
        factory = get_factory("llm")
        tk = factory.create("export-llm", ask_presets=["sys", "fast"])
        out = str(tmp_path / "llm-skill")
        skill_dir = tk.export(out)
        self._validate_skill_dir(skill_dir, "export-llm", ["ask"])

        # Verify preset enum is mentioned in the exported SKILL.md
        with open(os.path.join(skill_dir, "SKILL.md")) as f:
            skill_md = f.read()
        assert "sys" in skill_md
        assert "fast" in skill_md

    def test_export_config_toolkit_via_manager(self, manager, tmp_path):
        _create_manager_toolkit(manager, "config", "mgr-config")
        out = str(tmp_path / "mgr-config-skill")
        skill_dir = manager.export("mgr-config", out)
        self._validate_skill_dir(skill_dir, "mgr-config", ["config_show", "config_set", "config_unset"])

    def test_export_llm_toolkit_via_manager(self, manager, tmp_path):
        _create_manager_toolkit(manager, "llm", "mgr-llm", ask_presets=["sys"])
        out = str(tmp_path / "mgr-llm-skill")
        skill_dir = manager.export("mgr-llm", out)
        self._validate_skill_dir(skill_dir, "mgr-llm", ["ask"])

    def test_skill_ukft_compatibility_config(self, tmp_path):
        """Verify exported Skills are loadable by SkillUKFT."""
        factory = get_factory("config")
        tk = factory.create("compat-config")
        out = str(tmp_path / "skills")
        skill_dir = tk.export(out)

        from ahvn.ukf.templates.basic.skill import SkillUKFT

        skill = SkillUKFT.from_path(skill_dir)
        assert skill.name == "compat-config"
        assert "config_show" in str(skill.content_resources.get("tools", []))

    def test_skill_ukft_compatibility_llm(self, tmp_path):
        """Verify exported Skills are loadable by SkillUKFT."""
        factory = get_factory("llm")
        tk = factory.create("compat-llm", ask_presets=["sys"])
        out = str(tmp_path / "skills")
        skill_dir = tk.export(out)

        from ahvn.ukf.templates.basic.skill import SkillUKFT

        skill = SkillUKFT.from_path(skill_dir)
        assert skill.name == "compat-llm"
        assert "ask" in str(skill.content_resources.get("tools", []))


# ── View Command Tests ───────────────────────────────────────────────────


class TestShowCommand:
    def test_do_show_config(self, manager, capsys):
        """Test do_show displays toolkit metadata and MCP config."""
        _create_manager_toolkit(manager, "config", "view-config")

        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        cli.do_show("view-config")

        captured = capsys.readouterr()
        output = captured.out
        assert "view-config" in output
        assert "config_show" in output
        assert "config_set" in output
        assert "config_unset" in output
        # MCP JSON config should also appear
        assert "mcpServers" in output

    def test_do_show_llm(self, manager, capsys):
        """Test do_show displays LLM toolkit metadata and MCP config."""
        _create_manager_toolkit(manager, "llm", "view-llm", ask_presets=["sys", "fast"])

        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        cli.do_show("view-llm")

        captured = capsys.readouterr()
        output = captured.out
        assert "view-llm" in output
        assert "ask" in output
        # MCP JSON config should also appear
        assert "mcpServers" in output

    def test_do_show_missing(self, manager, capsys):
        """Test do_show for a nonexistent toolkit."""
        from ahvn.cli.mcp_cli import McpCLI

        cli = McpCLI()
        cli._manager = manager
        cli.do_show("nonexistent")

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "not found" in output.lower() or "error" in output.lower() or "nonexistent" in output.lower()


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
# 6. Create Subcommand Registration Tests
# ══════════════════════════════════════════════════════════════════════════


class TestCreateSubcommands:
    """Tests for factory CLI registration via register_create_cli."""

    def test_all_factories_have_register_create_cli(self):
        """Every registered factory has register_create_cli classmethod."""
        for name in list_factories():
            factory = get_factory(name)
            assert hasattr(factory, "register_create_cli")
            assert callable(factory.register_create_cli)

    def test_factory_names_in_list(self):
        """db, llm, config are all registered factories."""
        names = list(list_factories())
        assert "db" in names
        assert "llm" in names
        assert "config" in names
