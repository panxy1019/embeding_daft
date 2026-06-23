"""Tests for Toolkit, ToolkitFactory, and ToolkitManager."""

import pytest
import os
import json

from ahvn.tool import ToolSpec
from ahvn.tool.toolkit import (
    Toolkit,
    ToolkitRuntime,
    ToolkitFactory,
    register_factory,
    get_factory,
    list_factories,
    _FACTORIES,
)
from ahvn.tool.manager import ToolkitManager

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_add_toolspec():
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return ToolSpec.from_func(add)


def _make_multiply_toolspec():
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    return ToolSpec.from_func(multiply)


@pytest.fixture(autouse=True)
def _clean_factory_registry():
    """Clear the factory registry before/after each test."""
    saved = dict(_FACTORIES)
    _FACTORIES.clear()
    yield
    _FACTORIES.clear()
    _FACTORIES.update(saved)


@pytest.fixture
def math_toolkit():
    return Toolkit(
        name="math",
        description="Math tools",
        tools={"add": _make_add_toolspec(), "multiply": _make_multiply_toolspec()},
    )


@pytest.fixture
def math_factory():
    @register_factory
    class MathFactory(ToolkitFactory):
        name = "math"
        description = "Create math toolkits"

        @classmethod
        def args_schema(cls):
            return {
                "type": "object",
                "properties": {"offset": {"type": "integer", "description": "Offset to add"}},
                "required": [],
            }

        @classmethod
        def create(cls, toolkit_name, **args):
            offset = args.get("offset", 0)

            def add(a: int, b: int) -> int:
                return a + b + offset

            ts = ToolSpec.from_func(add)
            return Toolkit(
                name=toolkit_name,
                description=cls.description,
                tools={"add": ts},
            )

    return MathFactory


def _create_manager_toolkit(manager, factory_name: str, name: str, overwrite: bool = True, **args):
    factory = get_factory(factory_name)
    toolkit = factory.create(name, **args)
    manager.add(
        toolkit,
        overwrite=overwrite,
        source={
            "factory": factory_name,
            "args": dict(args),
        },
    )
    return toolkit


# ── Toolkit Tests ─────────────────────────────────────────────────────────


class TestToolkit:
    def test_init_defaults(self):
        tk = Toolkit(name="empty")
        assert tk.name == "empty"
        assert tk.description == ""
        assert tk.tools == {}
        assert tk.runtime_type == "session"

    def test_list_tools(self, math_toolkit):
        tools = math_toolkit.list_tools()
        assert sorted(tools) == ["add", "multiply"]

    def test_get_tool(self, math_toolkit):
        tool = math_toolkit.get_tool("add")
        assert isinstance(tool, ToolSpec)

    def test_get_tool_missing(self, math_toolkit):
        with pytest.raises(KeyError, match="not found"):
            math_toolkit.get_tool("nonexistent")

    def test_add_tool(self):
        tk = Toolkit(name="test")
        ts = _make_add_toolspec()
        result = tk.add_tool(ts, name="my_add")
        assert result is tk  # chaining
        assert "my_add" in tk

    def test_add_tool_default_name(self):
        tk = Toolkit(name="test")
        ts = _make_add_toolspec()
        tk.add_tool(ts)
        assert ts.name in tk

    def test_remove_tool(self, math_toolkit):
        result = math_toolkit.remove_tool("add")
        assert result is math_toolkit
        assert "add" not in math_toolkit

    def test_remove_tool_missing(self, math_toolkit):
        with pytest.raises(KeyError, match="not found"):
            math_toolkit.remove_tool("nonexistent")

    def test_run(self, math_toolkit):
        result = math_toolkit.run("add", a=3, b=5)
        assert result == 8

    def test_run_multiply(self, math_toolkit):
        result = math_toolkit.run("multiply", a=4, b=7)
        assert result == 28

    def test_create_runtime(self, math_toolkit):
        runtime = math_toolkit.create_runtime(session_id="session-1")
        assert isinstance(runtime, ToolkitRuntime)
        assert runtime.session_id == "session-1"
        assert sorted(runtime.list_tools()) == ["add", "multiply"]
        assert runtime.run("add", a=2, b=3) == 5
        runtime.close()

    def test_invalid_runtime_type_raises(self):
        with pytest.raises(ValueError, match="Invalid runtime_type"):
            Toolkit(name="bad-runtime", runtime_type="unknown")

    def test_session_runtime_isolated_state_and_reset(self):
        toolkit = Toolkit(
            name="session-stateful",
            runtime_type="session",
            tools={"add": _make_add_toolspec()},
        )
        runtime_a = toolkit.create_runtime(session_id="a")
        runtime_b = toolkit.create_runtime(session_id="b")

        tool_a = runtime_a.get_tool("add")
        tool_b = runtime_b.get_tool("add")
        tool_a.state["counter"] = 3
        assert tool_a.state["counter"] == 3
        assert "counter" not in tool_b.state

        runtime_a.reset()
        assert "counter" not in runtime_a.get_tool("add").state

        runtime_a.close()
        runtime_b.close()

    def test_persistent_runtime_shares_state_across_instances(self):
        toolkit = Toolkit(
            name="persistent-stateful",
            runtime_type="persistent",
            tools={"add": _make_add_toolspec()},
        )
        runtime_a = toolkit.create_runtime(session_id="a")
        runtime_a.get_tool("add").state["counter"] = 1

        runtime_b = toolkit.create_runtime(session_id="b")
        assert runtime_b.get_tool("add").state["counter"] == 1
        runtime_b.get_tool("add").state["counter"] += 1
        assert runtime_a.get_tool("add").state["counter"] == 2

        runtime_a.close()
        runtime_b.close()

    def test_stateless_runtime_reset_is_noop(self):
        toolkit = Toolkit(
            name="stateless-toolkit",
            runtime_type="stateless",
            tools={"add": _make_add_toolspec()},
        )
        runtime = toolkit.create_runtime()
        assert runtime.run("add", a=1, b=2) == 3
        runtime.reset()
        assert runtime.run("add", a=3, b=4) == 7
        runtime.close()

    def test_tool_enabled_filters_runtime_access(self):
        toolkit = Toolkit(
            name="runtime-filter",
            tools={"add": _make_add_toolspec(), "multiply": _make_multiply_toolspec()},
            tool_enabled={"add": True, "multiply": False},
        )
        runtime = toolkit.create_runtime()
        assert runtime.list_tools() == ["add"]
        with pytest.raises(KeyError, match="not available"):
            runtime.get_tool("multiply")
        with pytest.raises(KeyError, match="not available"):
            runtime.run("multiply", a=2, b=3)
        runtime.close()

    def test_to_fastmcp_uses_runtime_filtered_tools(self):
        toolkit = Toolkit(
            name="fastmcp-filter",
            tools={"add": _make_add_toolspec(), "multiply": _make_multiply_toolspec()},
            tool_enabled={"add": True, "multiply": False},
        )
        server = toolkit.to_fastmcp()
        runtime = getattr(server, "_toolkit_runtime")
        assert isinstance(runtime, ToolkitRuntime)
        assert runtime.list_tools() == ["add"]
        runtime.close()

    def test_to_jsonschema_list(self, math_toolkit):
        schemas = math_toolkit.to_jsonschema_list()
        assert len(schemas) == 2
        names = {s["function"]["name"] for s in schemas}
        assert "add" in names
        assert "multiply" in names

    def test_to_tool_list(self, math_toolkit):
        specs = math_toolkit.to_tool_list()
        assert len(specs) == 2
        assert all(isinstance(s, ToolSpec) for s in specs)

    def test_to_capsules_from_capsules_round_trip(self, math_toolkit):
        capsules = math_toolkit.to_capsules()
        assert len(capsules) == 2
        restored = Toolkit.from_capsules(name="math-restored", capsules=capsules)
        assert sorted(restored.list_tools()) == ["add", "multiply"]
        assert restored.run("add", a=3, b=5) == 8

    def test_to_capsules_from_capsules_round_trip_database_toolkit(self, tmp_path):
        from ahvn.tool.db.toolkit import DatabaseToolkitFactory

        db_path = str(tmp_path / "capsule_db.sqlite")
        toolkit = DatabaseToolkitFactory.create("db-tools", provider="sqlite", database=db_path)
        assert "value" in toolkit.run("exec_sql", query="SELECT 1 AS value")
        capsules = toolkit.to_capsules()
        assert len(capsules) == 1

        restored = Toolkit.from_capsules(name="db-tools-restored", capsules=capsules)
        output = restored.run("exec_sql", query="SELECT 1 AS value")
        assert "value" in output
        assert "1" in output

    def test_to_fastmcp(self, math_toolkit):
        from fastmcp import FastMCP

        server = math_toolkit.to_fastmcp()
        assert isinstance(server, FastMCP)

    def test_info(self, math_toolkit):
        info = math_toolkit.info()
        assert info["name"] == "math"
        assert info["description"] == "Math tools"
        assert sorted(info["tools"]) == ["add", "multiply"]
        assert info["runtime_type"] == "session"
        assert info["tool_enabled"] == {"add": True, "multiply": True}

    def test_repr(self, math_toolkit):
        r = repr(math_toolkit)
        assert "math" in r

    def test_len(self, math_toolkit):
        assert len(math_toolkit) == 2

    def test_contains(self, math_toolkit):
        assert "add" in math_toolkit
        assert "nonexistent" not in math_toolkit

    def test_getitem(self, math_toolkit):
        tool = math_toolkit["add"]
        assert isinstance(tool, ToolSpec)


# ── ToolkitFactory Tests ─────────────────────────────────────────────────


class TestToolkitFactory:
    def test_register_factory(self, math_factory):
        assert "math" in _FACTORIES
        assert _FACTORIES["math"] is math_factory

    def test_register_factory_no_name(self):
        with pytest.raises(ValueError, match="must define a 'name'"):

            @register_factory
            class BadFactory(ToolkitFactory):
                pass

    def test_get_factory(self, math_factory):
        f = get_factory("math")
        assert f is math_factory

    def test_get_factory_missing(self):
        with pytest.raises(KeyError, match="No ToolkitFactory"):
            get_factory("nonexistent")

    def test_list_factories(self, math_factory):
        result = list_factories()
        assert "math" in result
        assert result["math"] == "Create math toolkits"

    def test_create(self, math_factory):
        tk = math_factory.create("my-math", offset=10)
        assert tk.name == "my-math"
        assert tk.run("add", a=3, b=5) == 18  # 3 + 5 + 10

    def test_args_schema(self, math_factory):
        schema = math_factory.args_schema()
        assert "properties" in schema
        assert "offset" in schema["properties"]

    def test_base_factory_create_raises(self):
        with pytest.raises(NotImplementedError):
            ToolkitFactory.create("test")


# ── ToolkitManager Tests ─────────────────────────────────────────────────


class TestToolkitManager:
    @pytest.fixture
    def manager(self, math_factory, tmp_path):
        """Create a manager with a temp persistence DB."""
        from ahvn.tool.store import ToolkitStore

        store = ToolkitStore(
            provider="sqlite",
            database=str(tmp_path / "toolkits.db"),
        )
        m = ToolkitManager(store=store)
        yield m

    def test_create_api_removed(self, manager):
        assert not hasattr(manager, "create")

    def test_create(self, manager):
        tk = _create_manager_toolkit(manager, "math", "my-math")
        assert tk.name == "my-math"
        assert "my-math" in [t["name"] for t in manager.list()]

    def test_create_duplicate(self, manager):
        _create_manager_toolkit(manager, "math", "my-math")
        with pytest.raises(KeyError, match="already exists"):
            _create_manager_toolkit(manager, "math", "my-math", overwrite=False)

    def test_get(self, manager):
        _create_manager_toolkit(manager, "math", "my-math")
        tk = manager.get("my-math")
        assert isinstance(tk, Toolkit)
        assert tk.name == "my-math"

    def test_get_missing(self, manager):
        with pytest.raises(KeyError, match="not found"):
            manager.get("nonexistent")

    def test_list(self, manager):
        _create_manager_toolkit(manager, "math", "a")
        _create_manager_toolkit(manager, "math", "b")
        result = manager.list()
        names = [t["name"] for t in result]
        assert "a" in names
        assert "b" in names

    def test_rename(self, manager):
        _create_manager_toolkit(manager, "math", "old-name")
        tk = manager.rename("old-name", "new-name")
        assert tk.name == "new-name"
        with pytest.raises(KeyError):
            manager.get("old-name")

    def test_rename_to_existing(self, manager):
        _create_manager_toolkit(manager, "math", "a")
        _create_manager_toolkit(manager, "math", "b")
        with pytest.raises(KeyError, match="already exists"):
            manager.rename("a", "b")

    def test_remove(self, manager):
        _create_manager_toolkit(manager, "math", "my-math")
        manager.remove("my-math")
        with pytest.raises(KeyError):
            manager.get("my-math")

    def test_remove_missing(self, manager):
        with pytest.raises(KeyError, match="not found"):
            manager.remove("nonexistent")

    def test_add_toolkit_from_capsules(self, manager):
        toolkit = Toolkit.from_capsules(
            name="imported-toolkit",
            capsules=[],
        )
        manager.add(toolkit)
        assert manager.info("imported-toolkit")["id"] == "imported-toolkit"

    def test_run(self, manager):
        _create_manager_toolkit(manager, "math", "my-math", offset=5)
        result = manager.run("my-math.add", a=1, b=2)
        assert result == 8  # 1 + 2 + 5

    def test_create_runtime(self, manager):
        manager.add(
            Toolkit(
                name="runtime-test",
                description="runtime test toolkit",
                tools={"add": _make_add_toolspec()},
            )
        )
        runtime = manager.create_runtime("runtime-test", session_id="session-1")
        assert isinstance(runtime, ToolkitRuntime)
        assert runtime.session_id == "session-1"
        assert runtime.run("add", a=2, b=4) == 6
        runtime.close()

    def test_create_runtime_persistent_shared_state(self, manager):
        manager.add(
            Toolkit(
                name="persistent-runtime-test",
                description="persistent runtime test toolkit",
                runtime_type="persistent",
                tools={"add": _make_add_toolspec()},
            )
        )
        runtime_a = manager.create_runtime("persistent-runtime-test", session_id="a")
        runtime_a.get_tool("add").state["counter"] = 1

        runtime_b = manager.create_runtime("persistent-runtime-test", session_id="b")
        assert runtime_b.get_tool("add").state["counter"] == 1

        runtime_a.close()
        runtime_b.close()

    def test_reset_persistent_runtime(self, manager):
        manager.add(
            Toolkit(
                name="persistent-reset-test",
                description="persistent reset toolkit",
                runtime_type="persistent",
                tools={"add": _make_add_toolspec()},
            )
        )
        runtime_before = manager.create_runtime("persistent-reset-test", session_id="before")
        runtime_before.get_tool("add").state["counter"] = 3

        manager.reset("persistent-reset-test")

        runtime_after = manager.create_runtime("persistent-reset-test", session_id="after")
        assert "counter" not in runtime_after.get_tool("add").state

        runtime_before.close()
        runtime_after.close()

    def test_reset_rejected_while_serving(self, manager):
        manager.add(
            Toolkit(
                name="serving-reset-test",
                description="serving reset toolkit",
                tools={"add": _make_add_toolspec()},
            )
        )
        manager._processes["serving-reset-test"] = object()
        with pytest.raises(RuntimeError, match="Stop it first"):
            manager.reset("serving-reset-test")
        manager._processes.pop("serving-reset-test", None)

    def test_run_invalid_format(self, manager):
        with pytest.raises(ValueError, match="Expected"):
            manager.run("no-dot-here")

    def test_persistence(self, manager, math_factory, tmp_path):
        _create_manager_toolkit(manager, "math", "persist-test", offset=42)

        # Create a new manager that loads from the same DB
        mgr2 = ToolkitManager(store=manager.store)
        result = mgr2.run("persist-test.add", a=1, b=2)
        assert result == 45  # 1 + 2 + 42

    def test_save_custom_toolkit_persistence(self, manager):
        custom = Toolkit(
            name="custom-tools",
            description="custom toolkit",
            tools={"add": _make_add_toolspec()},
        )
        manager.add(custom)

        mgr2 = ToolkitManager(store=manager.store)
        result = mgr2.run("custom-tools.add", a=4, b=6)
        assert result == 10

    def test_store_round_trip_runtime_metadata(self, manager):
        manager.add(
            Toolkit(
                name="runtime-meta",
                description="runtime metadata toolkit",
                runtime_type="persistent",
                tool_enabled={"add": True, "multiply": False},
                tools={"add": _make_add_toolspec(), "multiply": _make_multiply_toolspec()},
            )
        )
        payload = manager.store.get("runtime-meta")
        assert payload is not None
        assert payload["manifest"]["runtime_type"] == "persistent"
        assert payload["manifest"]["tool_enabled"]["add"] is True
        assert payload["manifest"]["tool_enabled"]["multiply"] is False

        restored = manager.store.load("runtime-meta")
        assert restored.runtime_type == "persistent"
        assert restored.tool_enabled["add"] is True
        assert restored.tool_enabled["multiply"] is False
        runtime = restored.create_runtime()
        assert runtime.list_tools() == ["add"]
        runtime.close()

    def test_persistence_preserves_imported_mcp_command_entry(self, manager):
        cfg = {
            "mcpServers": {
                "ext": {
                    "command": "python",
                    "args": ["-m", "external_mcp_server"],
                }
            }
        }
        manager.add(Toolkit.from_mcp_config(cfg), overwrite=True)
        mgr2 = ToolkitManager(store=manager.store)
        out = mgr2.get("ext").to_mcp_config()
        assert out["mcpServers"]["ext"] == cfg["mcpServers"]["ext"]

    def test_persistence_preserves_imported_mcp_url_entry_with_headers(self, manager):
        cfg = {
            "mcpServers": {
                "secure": {
                    "url": "https://secure.example.com/mcp",
                    "transport": "http",
                    "headers": {"Authorization": "Bearer token"},
                    "timeout": 30,
                }
            }
        }
        manager.add(Toolkit.from_mcp_config(cfg), overwrite=True)
        mgr2 = ToolkitManager(store=manager.store)
        out = mgr2.get("secure").to_mcp_config()
        assert out["mcpServers"]["secure"] == cfg["mcpServers"]["secure"]

    def test_persistence_preserves_from_url_entry_and_keeps_override_behavior(self, manager):
        manager.add(Toolkit.from_url("https://remote.example.com/my/mcp", name="remote-copy"), overwrite=True)
        mgr2 = ToolkitManager(store=manager.store)
        restored = mgr2.get("remote-copy")
        default_cfg = restored.to_mcp_config()
        override_cfg = restored.to_mcp_config(host="127.0.0.1", port=9100)

        assert default_cfg["mcpServers"]["remote-copy"]["url"] == "https://remote.example.com/my/mcp"
        assert override_cfg["mcpServers"]["remote-copy"]["url"] == "http://127.0.0.1:9100/remote-copy/mcp"

    def test_ps_empty(self, manager):
        assert manager.ps() == []

    def test_repr(self, manager):
        _create_manager_toolkit(manager, "math", "my-math")
        r = repr(manager)
        assert "my-math" in r

    def test_save_and_load_capsule_bundle(self, manager):
        _create_manager_toolkit(manager, "math", "bundle-math")
        capsule_ids = manager.save_as_capsules("bundle-math")
        assert len(capsule_ids) == 1

        restored = manager.load_from_capsules("bundle-copy", capsule_ids)
        assert restored.name == "bundle-copy"
        assert restored.run("add", a=4, b=6) == 10

    def test_get_warns_for_lossy_toolspec_state(self, manager):
        import threading

        def echo(x: int) -> int:
            return x

        tool_spec = ToolSpec.from_func(echo)
        tool_spec.state["bad_state"] = threading.Lock()
        manager.add(
            Toolkit(
                name="lossy-toolkit",
                description="Lossy state test",
                tools={"echo": tool_spec},
            )
        )

        manager2 = ToolkitManager(store=manager.store)
        with pytest.warns(UserWarning, match="lossy ToolSpec state"):
            restored = manager2.get("lossy-toolkit")
        assert restored.run("echo", x=5) == 5


def test_toolkit_store_stale_ignores_memory_database_marker(tmp_path):
    from ahvn.tool.store import ToolkitStore

    store = ToolkitStore(provider="sqlite", database=str(tmp_path / "toolkits.db"))
    store.save(
        {
            "toolkit_name": "memory-db-toolkit",
            "manifest": {
                "name": "memory-db-toolkit",
                "source": {
                    "factory": "db",
                    "args": {"database": ":memory:"},
                },
            },
            "capsules": [],
        }
    )

    assert store.stale() == []


def test_toolkit_store_tx_yields_database_handle(tmp_path):
    from ahvn.tool.store import ToolkitStore

    store = ToolkitStore(provider="sqlite", database=str(tmp_path / "toolkits.db"))
    with store.tx(write=True) as db:
        assert db is not None
    with store.tx(write=False) as db:
        assert db is not None


def test_get_toolkit_store_is_singleton(monkeypatch):
    import ahvn.tool.store as tool_store_mod

    class FakeToolkitStore:
        init_count = 0

        def __init__(self):
            FakeToolkitStore.init_count += 1

    monkeypatch.setattr(tool_store_mod, "ToolkitStore", FakeToolkitStore)
    monkeypatch.setattr(tool_store_mod, "_store_instance", None)

    store_a = tool_store_mod.get_toolkit_store()
    store_b = tool_store_mod.get_toolkit_store()
    assert store_a is store_b
    assert FakeToolkitStore.init_count == 1


# ── to_mcp_config URL fallback tests ─────────────────────────────────────


class TestMcpConfigUrlFallback:
    """Verify host/port=None defaults and remote-URL priority."""

    def test_default_uses_localhost_7001(self, math_toolkit):
        cfg = math_toolkit.to_mcp_config()
        url = cfg["mcpServers"]["math"]["url"]
        assert url == "http://127.0.0.1:7001/math/mcp"

    def test_remote_url_used_when_no_explicit_host_port(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        cfg = tk.to_mcp_config()
        assert cfg["mcpServers"]["remote"]["url"] == "https://remote.example.com/my/mcp"

    def test_explicit_host_overrides_remote_url(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        cfg = tk.to_mcp_config(host="10.0.0.1")
        assert cfg["mcpServers"]["remote"]["url"] == "http://10.0.0.1:7001/remote/mcp"

    def test_explicit_port_overrides_remote_url(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        cfg = tk.to_mcp_config(port=9000)
        assert cfg["mcpServers"]["remote"]["url"] == "http://127.0.0.1:9000/remote/mcp"

    def test_explicit_host_and_port_override_remote_url(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        cfg = tk.to_mcp_config(host="0.0.0.0", port=8080)
        assert cfg["mcpServers"]["remote"]["url"] == "http://localhost:8080/remote/mcp"

    def test_to_mcp_json_follows_same_fallback(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        json_str = tk.to_mcp_json()
        assert "https://remote.example.com/my/mcp" in json_str

    def test_to_mcp_json_explicit_port_overrides(self):
        tk = Toolkit.from_url("https://remote.example.com/my/mcp", name="remote")
        json_str = tk.to_mcp_json(port=9000)
        assert "127.0.0.1:9000" in json_str
        assert "remote.example.com" not in json_str

    def test_from_mcp_config_roundtrip_preserves_url(self):
        cfg = {"mcpServers": {"srv": {"url": "https://srv.example.com/mcp", "transport": "http"}}}
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config()
        assert out["mcpServers"]["srv"]["url"] == "https://srv.example.com/mcp"

    def test_from_mcp_config_roundtrip_preserves_command_entry(self):
        cfg = {
            "mcpServers": {
                "ext": {
                    "command": "python",
                    "args": ["-m", "external_mcp_server"],
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config()
        assert out["mcpServers"]["ext"]["command"] == "python"
        assert out["mcpServers"]["ext"]["args"] == ["-m", "external_mcp_server"]

    def test_from_mcp_config_name_override_applies_to_imported_entry(self):
        cfg = {
            "mcpServers": {
                "source": {
                    "command": "python",
                    "args": ["-c", "from ahvn.tool import TK_AHVN; TK_AHVN.get('a').serve(transport='stdio')"],
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg, name="renamed")
        out = tk.to_mcp_config()
        assert tk.name == "renamed"
        assert "renamed" in out["mcpServers"]
        assert "source" not in out["mcpServers"]
        # Entry payload is preserved without source-specific rewriting.
        assert "TK_AHVN.get('a')" in out["mcpServers"]["renamed"]["args"][1]

    def test_from_mcp_config_roundtrip_preserves_full_url_entry(self):
        cfg = {
            "mcpServers": {
                "secure": {
                    "url": "https://secure.example.com/mcp",
                    "transport": "http",
                    "headers": {"Authorization": "Bearer token"},
                    "timeout": 30,
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config()
        assert out["mcpServers"]["secure"] == cfg["mcpServers"]["secure"]

    def test_from_mcp_config_command_entry_ignores_http_host_port_rewrite(self):
        cfg = {
            "mcpServers": {
                "ext": {
                    "command": "python",
                    "args": ["-m", "external_mcp_server"],
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config(host="127.0.0.1", port=9999)
        assert out["mcpServers"]["ext"] == cfg["mcpServers"]["ext"]

    def test_from_mcp_config_rich_url_entry_ignores_http_host_port_rewrite(self):
        cfg = {
            "mcpServers": {
                "secure": {
                    "url": "https://secure.example.com/mcp",
                    "transport": "http",
                    "headers": {"Authorization": "Bearer token"},
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config(host="127.0.0.1", port=9999)
        assert out["mcpServers"]["secure"] == cfg["mcpServers"]["secure"]

    def test_from_mcp_config_name_override_is_rejected_when_source_is_ambiguous(self):
        cfg = {
            "mcpServers": {
                "a": {"url": "https://a.example.com/mcp", "transport": "http"},
                "b": {"url": "https://b.example.com/mcp", "transport": "http"},
            }
        }
        with pytest.raises(ValueError, match="Ambiguous name override"):
            Toolkit.from_mcp_config(cfg, name="renamed")

    def test_from_mcp_config_non_http_url_entry_keeps_transport_on_host_override(self):
        cfg = {
            "mcpServers": {
                "sse-srv": {
                    "url": "https://sse.example.com/mcp",
                    "transport": "sse",
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config(host="127.0.0.1", port=9999)
        assert out["mcpServers"]["sse-srv"] == cfg["mcpServers"]["sse-srv"]

    def test_from_mcp_config_non_http_url_entry_keeps_transport_on_stdio_request(self):
        cfg = {
            "mcpServers": {
                "sse-srv": {
                    "url": "https://sse.example.com/mcp",
                    "transport": "sse",
                }
            }
        }
        tk = Toolkit.from_mcp_config(cfg)
        out = tk.to_mcp_config(transport="stdio")
        assert out["mcpServers"]["sse-srv"] == cfg["mcpServers"]["sse-srv"]


# ── to_prompt tests ────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_no_tools(self):
        tk = Toolkit(name="empty", description="An empty toolkit")
        md = tk.to_prompt()
        assert "# The `empty` Toolkit" in md
        assert "An empty toolkit" in md
        assert "*(no tools)*" in md

    def test_tools_with_params(self, math_toolkit):
        md = math_toolkit.to_prompt()
        assert "# The `math` Toolkit" in md
        assert "## Tools (2)" in md
        assert "### add" in md
        assert "### multiply" in md
        # Parameters formatted with bullet_dict param mode
        assert "- `a`: integer" in md
        assert "- `b`: integer" in md
        # Usage section with placeholders
        assert "**Usage:**" in md
        assert "```bash" in md
        assert "ahvn mcp run math.add" in md
        assert "<a>" in md
        assert "```python" in md
        assert "add(a=<a>, b=<b>)" in md

    def test_instructions_rendered(self):
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        ts = ToolSpec.from_func(greet)
        tk = Toolkit(
            name="greeter",
            description="Greeting tools",
            tools={"greet": ts},
            instructions={"greet": ["Always be polite", "Use the user's preferred name"]},
        )
        md = tk.to_prompt()
        assert "**Instructions:**" in md
        assert "- Always be polite" in md
        assert "- Use the user's preferred name" in md

    def test_no_params_tool(self):
        def noop() -> str:
            """Do nothing."""
            return "done"

        ts = ToolSpec.from_func(noop)
        tk = Toolkit(name="simple", tools={"noop": ts})
        md = tk.to_prompt()
        assert "*(no parameters)*" in md
        assert "**Usage:**" in md
        assert "```bash" in md
        assert "ahvn mcp run simple.noop" in md
        assert "```python" in md
        assert "noop()" in md
