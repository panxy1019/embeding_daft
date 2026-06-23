from __future__ import annotations

__all__ = [
    "Toolkit",
    "ToolkitRuntime",
    "ToolkitFactory",
    "ServeHandle",
    "register_factory",
    "get_factory",
    "list_factories",
]

import os
import threading
from typing import (
    Dict,
    List,
    Any,
    Optional,
    Literal,
    Union,
    ClassVar,
    Type,
    TYPE_CHECKING,
)
from copy import deepcopy

from .base import ToolSpec
from ..utils.basic.log_utils import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def _stdio_mcp_entry(name: str) -> Dict[str, Any]:
    """Build a single MCP server entry dict for stdio transport.

    Runs :file:`ahvn/_mcp_stdio.py` directly as a script so that the
    conda DLL PATH is fixed *before* the ``ahvn`` package is imported.
    Direct-file execution (``python file.py``) does **not** trigger the
    parent package's ``__init__.py``, unlike ``python -m ahvn._mcp_stdio``.
    """
    import sys

    bootstrap = os.path.join(os.path.dirname(__file__), os.pardir, "_mcp_stdio.py")
    bootstrap = os.path.normpath(bootstrap)

    return {
        "command": sys.executable,
        "args": [bootstrap, name],
    }


# Global factory registry
_FACTORIES: Dict[str, Type["ToolkitFactory"]] = {}
_RUNTIME_TYPES = ("session", "persistent", "stateless")


def register_factory(cls: Type["ToolkitFactory"]) -> Type["ToolkitFactory"]:
    """\
    Class decorator to register a ToolkitFactory subclass in the global factory registry.

    Example:
        >>> @register_factory
        ... class DatabaseToolkitFactory(ToolkitFactory):
        ...     name = "db"
        ...     description = "Database toolkit"
    """
    if not hasattr(cls, "name") or not cls.name:
        raise ValueError(f"ToolkitFactory subclass {cls.__name__} must define a 'name' class variable.")
    _FACTORIES[cls.name] = cls
    return cls


def get_factory(name: str) -> Type["ToolkitFactory"]:
    """\
    Retrieve a registered ToolkitFactory by name.

    Args:
        name (str): The factory name (e.g., "db", "config", "llm").

    Returns:
        Type[ToolkitFactory]: The factory class.

    Raises:
        KeyError: If no factory is registered with the given name.
    """
    if name not in _FACTORIES:
        available = list(_FACTORIES.keys())
        raise KeyError(f"No ToolkitFactory registered with name '{name}'. Available: {available}")
    return _FACTORIES[name]


def list_factories() -> Dict[str, str]:
    """\
    List all registered toolkit factories.

    Returns:
        Dict[str, str]: Mapping of factory name to description.
    """
    return {name: cls.description for name, cls in _FACTORIES.items()}


def _copy_state_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        return deepcopy(value)
    except Exception:
        return dict(value)


class ToolkitRuntime:
    """Runtime wrapper for executing Toolkit tools with runtime semantics."""

    def __init__(self, toolkit: "Toolkit", session_id: Optional[str] = None):
        self.toolkit = toolkit
        self.session_id = session_id
        self.runtime_type = toolkit.runtime_type
        self._closed = False
        self.tools: Dict[str, ToolSpec] = {}

        for tool_name in toolkit.list_tools():
            if not toolkit.is_tool_enabled(tool_name):
                continue
            self.tools[tool_name] = self._clone_toolspec(toolkit.get_tool(tool_name))

        # Contract: runtime reset must always happen at creation.
        self.reset()

    @staticmethod
    def _clone_toolspec(tool_spec: ToolSpec) -> ToolSpec:
        from fastmcp.tools import Tool as FastMCPTool

        cloned = ToolSpec()
        cloned.tool = FastMCPTool.from_tool(tool_spec.tool)
        cloned._short_description = getattr(tool_spec, "_short_description", "")
        cloned._description = getattr(tool_spec, "_description", "")
        cloned.examples = deepcopy(tool_spec.examples) if tool_spec.examples is not None else None
        cloned.binds = deepcopy(getattr(tool_spec, "binds", {}))
        cloned.state = _copy_state_dict(getattr(tool_spec, "state", {}))

        # Preserve custom execution hooks when the ToolSpec overrides them.
        for attr in ("exec", "aexec", "call", "acall", "__call__"):
            if attr in tool_spec.__dict__:
                setattr(cloned, attr, getattr(tool_spec, attr))

        cloned._clear_cache()
        return cloned

    @staticmethod
    def _set_tool_state(tool_spec: ToolSpec, state: Dict[str, Any]) -> None:
        tool_spec.state = state
        try:
            tool_spec._clear_cache()
        except Exception:
            pass

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"ToolkitRuntime for '{self.toolkit.name}' is closed.")

    def list_tools(self) -> List[str]:
        self._assert_open()
        return list(self.tools.keys())

    def get_tool(self, name: str) -> ToolSpec:
        self._assert_open()
        if name not in self.tools:
            available = self.list_tools()
            raise KeyError(f"Tool '{name}' is not available in runtime '{self.toolkit.name}'. Available: {available}")
        return self.tools[name]

    def run(self, tool_name: str, **kwargs) -> Any:
        tool = self.get_tool(tool_name)
        return tool(**kwargs)

    def reset(self) -> None:
        self._assert_open()
        if self.runtime_type == "stateless":
            return

        if self.runtime_type == "persistent":
            for tool_name, tool_spec in self.tools.items():
                shared_state = self.toolkit._get_persistent_tool_state(tool_name)
                self._set_tool_state(tool_spec, shared_state)
            return

        # session runtime
        for tool_name, tool_spec in self.tools.items():
            initial_state = self.toolkit._initial_tool_state(tool_name)
            self._set_tool_state(tool_spec, initial_state)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

    def __enter__(self) -> "ToolkitRuntime":
        self._assert_open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "ToolkitRuntime":
        self._assert_open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def to_fastmcp(self, **server_kwargs) -> "FastMCP":
        from fastmcp import FastMCP

        self._assert_open()
        server_kwargs.setdefault("instructions", self.toolkit.description or None)
        server = FastMCP(self.toolkit.name, **server_kwargs)
        for tool_spec in self.tools.values():
            server.add_tool(tool_spec.to_fastmcp())
        return server


class ServeHandle:
    """\
    Handle for a running toolkit MCP server (background mode).

    Returned by :meth:`Toolkit.serve` when ``wait=False``.
    Provides ``mcp_config`` for client configuration, ``stop()``
    for graceful shutdown, ``wait()`` for blocking, and
    context-manager support::

        with toolkit.serve(wait=False) as handle:
            print(handle.mcp_config)
        # server is stopped on exit

    Attributes:
        name (str): Toolkit name.
        mcp_config (Dict[str, Any]): MCP client config dict.
        url (str): Endpoint URL.
    """

    def __init__(
        self,
        name: str,
        mcp_config: Dict[str, Any],
        url: str,
        runtime: "ToolkitRuntime",
        _server: Any,
        _thread: threading.Thread,
    ):
        self.name = name
        self.mcp_config = mcp_config
        self.url = url
        self._runtime = runtime
        self._server = _server
        self._thread = _thread
        self._stopped = False

    @property
    def mcp_json(self) -> str:
        """MCP client config as a formatted JSON string."""
        from ..utils.basic.serialize_utils import dumps_json

        return dumps_json(self.mcp_config, indent=2)

    @property
    def is_alive(self) -> bool:
        """Whether the server thread is still running."""
        return self._thread.is_alive() and not self._stopped

    def stop(self) -> None:
        """Stop the server gracefully."""
        if self._stopped:
            return
        self._stopped = True
        self._server.should_exit = True
        self._thread.join(timeout=10)
        self._runtime.close()

    def wait(self) -> None:
        """Block until the server stops (via ``stop()`` or ``KeyboardInterrupt``)."""
        try:
            self._thread.join()
        except KeyboardInterrupt:
            self.stop()
        finally:
            if not self._stopped:
                self._runtime.close()
                self._stopped = True

    def __enter__(self) -> "ServeHandle":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def __repr__(self) -> str:
        status = "alive" if self.is_alive else "stopped"
        return f"ServeHandle(name={self.name!r}, url={self.url!r}, {status})"


class Toolkit:
    """\
    A named collection of ToolSpecs.

    Toolkit is protocol-agnostic — it groups multiple ToolSpecs together under a name
    and provides conversion methods for different serving protocols (MCP, jsonschema, etc.).

    Attributes:
        name (str): User-assigned unique name (e.g., "my-math").
        description (str): Human-readable description of this toolkit.
        short_description (str): One-line summary (auto-derived from description if omitted).
        tools (Dict[str, ToolSpec]): Named tools in this toolkit.

    Example:
        >>> toolkit = Toolkit(
        ...     name="my-math",
        ...     description="Math tools",
        ...     tools={"add": add_toolspec},
        ... )
        >>> toolkit.list_tools()
        ['add']
        >>> result = toolkit.run("add", a=1, b=2)
    """

    def __init__(
        self,
        name: Optional[str] = None,
        short_description: str = "",
        description: str = "",
        tools: Optional[Dict[str, ToolSpec]] = None,
        instructions: Optional[Dict[str, List[str]]] = None,
        runtime_type: Literal["session", "persistent", "stateless"] = "session",
        tool_enabled: Optional[Dict[str, bool]] = None,
    ):
        self.name = name or type(self).__name__
        self.description = description
        self.short_description = short_description or self._derive_short_description(description)
        self.tools = tools or {}
        self.instructions: Dict[str, List[str]] = instructions or {}
        if runtime_type not in _RUNTIME_TYPES:
            raise ValueError(f"Invalid runtime_type '{runtime_type}'. Supported values: {_RUNTIME_TYPES}")
        self.runtime_type: Literal["session", "persistent", "stateless"] = runtime_type
        enabled_raw = tool_enabled if isinstance(tool_enabled, dict) else {}
        self.tool_enabled: Dict[str, bool] = {}
        for tool_name in self.tools:
            self.tool_enabled[tool_name] = bool(enabled_raw.get(tool_name, True))
        self._persistent_tool_states: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _derive_short_description(description: str) -> str:
        """\
        Extract a one-line summary from a longer description.
        Uses the first sentence (up to the first period or newline).
        """
        if not description:
            return ""
        line = description.split("\n")[0].strip()
        # Take first sentence
        dot = line.find(".")
        if dot > 0:
            return line[: dot + 1]
        return line

    # ── Remote tool discovery ────────────────────────────────────

    async def discover_remote_tools(self) -> "Toolkit":
        """\
        Connect to the remote MCP server and populate :attr:`tools` with
        metadata ToolSpecs for every tool advertised by the server.

        Only meaningful when the toolkit was created via :meth:`from_url` or
        :meth:`from_mcp_config` with an HTTP URL.  Already-present tool
        entries are **not** overwritten.

        Returns:
            Toolkit: *self* (for chaining).
        """
        url = getattr(self, "_remote_url", None)
        if not url:
            return self

        from fastmcp import Client
        from fastmcp.tools import Tool as FastMCPTool
        from ..utils.basic.func_utils import desc2short

        client = Client(url)
        async with client:
            mcp_tools = await client.list_tools()
            for mt in mcp_tools:
                if mt.name in self.tools:
                    continue  # don't overwrite existing entries

                description = mt.description or ""
                input_schema = mt.inputSchema if hasattr(mt, "inputSchema") else {}

                # Build a lightweight placeholder function so FastMCPTool can
                # be constructed (required by ToolSpec internals).  The tool
                # is not meant to be called directly — MCP protocol handles
                # invocation via the remote server.
                _tool_name = mt.name  # capture for closure

                async def _placeholder(_tool_name=_tool_name):  # noqa: E501
                    raise RuntimeError(f"Tool '{_tool_name}' is a remote MCP tool at {url}. " "Use MCP protocol to call it (e.g. via MCP client config).")

                tool_spec = ToolSpec()
                tool_spec.tool = FastMCPTool.from_function(
                    fn=_placeholder,
                    name=mt.name,
                    description=description,
                )
                if input_schema:
                    tool_spec.input_schema = input_schema
                tool_spec._description = description
                tool_spec._short_description = desc2short(description)

                self.tools[mt.name] = tool_spec
                self.tool_enabled.setdefault(mt.name, True)

        return self

    def _ensure_tools_discovered(self) -> None:
        """\
        Lazily discover remote tools when the toolkit has a remote URL but
        no tools have been loaded yet.  This is a **sync** helper — it
        creates an event loop if necessary or reuses the existing one via
        ``nest_asyncio``.

        Called transparently by :meth:`list_tools`, :meth:`get_tool`,
        :meth:`export`, etc. so that URL-based toolkits behave just like
        locally-defined ones once the server is reachable.
        """
        if self.tools:
            return
        url = getattr(self, "_remote_url", None)
        if not url:
            return

        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is None:
                asyncio.run(self.discover_remote_tools())
            else:
                try:
                    import nest_asyncio

                    nest_asyncio.apply(loop)
                    loop.run_until_complete(self.discover_remote_tools())
                except ImportError:
                    logger.warning(
                        "Cannot discover remote tools from within a running event loop. "
                        "Install 'nest_asyncio' or call 'await toolkit.discover_remote_tools()' explicitly."
                    )
        except Exception as exc:
            logger.debug("Remote tool discovery failed for '%s' (%s): %s", self.name, url, exc)

    # ── Tool access ───────────────────────────────────────────────

    def list_tools(self) -> List[str]:
        """\
        List the names of all tools in this toolkit.

        Returns:
            List[str]: Tool names.
        """
        self._ensure_tools_discovered()
        return list(self.tools.keys())

    def get_tool(self, name: str) -> ToolSpec:
        """\
        Get a tool by name.

        Args:
            name (str): The tool name.

        Returns:
            ToolSpec: The requested tool.

        Raises:
            KeyError: If no tool with the given name exists.
        """
        self._ensure_tools_discovered()
        if name not in self.tools:
            available = self.list_tools()
            raise KeyError(f"Tool '{name}' not found in toolkit '{self.name}'. Available: {available}")
        return self.tools[name]

    def add_tool(self, tool: ToolSpec, name: Optional[str] = None) -> "Toolkit":
        """\
        Add a tool to this toolkit.

        Args:
            tool (ToolSpec): The tool to add.
            name (str, optional): Override name. Defaults to tool.name.

        Returns:
            Toolkit: Self (for chaining).
        """
        key = name or tool.name
        self.tools[key] = tool
        self.tool_enabled.setdefault(key, True)
        self._persistent_tool_states.pop(key, None)
        return self

    def remove_tool(self, name: str) -> "Toolkit":
        """\
        Remove a tool from this toolkit.

        Args:
            name (str): The tool name to remove.

        Returns:
            Toolkit: Self (for chaining).

        Raises:
            KeyError: If no tool with the given name exists.
        """
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' not found in toolkit '{self.name}'.")
        del self.tools[name]
        self.tool_enabled.pop(name, None)
        self._persistent_tool_states.pop(name, None)
        return self

    def run(self, tool_name: str, **kwargs) -> Any:
        """\
        Execute a tool by name with the given arguments.

        Args:
            tool_name (str): Name of the tool to run.
            **kwargs: Arguments to pass to the tool.

        Returns:
            Any: The tool's return value.
        """
        runtime = self.create_runtime()
        try:
            return runtime.run(tool_name, **kwargs)
        finally:
            runtime.close()

    def is_tool_enabled(self, tool_name: str) -> bool:
        return bool(self.tool_enabled.get(tool_name, True))

    def create_runtime(self, session_id: Optional[str] = None) -> "ToolkitRuntime":
        return ToolkitRuntime(toolkit=self, session_id=session_id)

    def _initial_tool_state(self, tool_name: str) -> Dict[str, Any]:
        if tool_name not in self.tools:
            return {}
        return _copy_state_dict(getattr(self.tools[tool_name], "state", {}))

    def _get_persistent_tool_state(self, tool_name: str) -> Dict[str, Any]:
        if tool_name not in self._persistent_tool_states:
            self._persistent_tool_states[tool_name] = self._initial_tool_state(tool_name)
        return self._persistent_tool_states[tool_name]

    def _reset_persistent_tool_states(self, names: Optional[List[str]] = None) -> None:
        target_names = names if isinstance(names, list) else self.list_tools()
        for tool_name in target_names:
            if tool_name not in self.tools:
                continue
            fresh_state = self._initial_tool_state(tool_name)
            existing = self._persistent_tool_states.get(tool_name)
            if isinstance(existing, dict):
                existing.clear()
                existing.update(fresh_state)
            else:
                self._persistent_tool_states[tool_name] = fresh_state

    def to_fastmcp(self, **server_kwargs) -> "FastMCP":
        """\
        Convert this toolkit into a FastMCP server.

        Each ToolSpec is converted to a FastMCPTool and added to the server.
        The toolkit's description is passed as ``instructions`` to the
        FastMCP server so MCP clients can display it.

        Args:
            **server_kwargs: Additional kwargs passed to FastMCP constructor.

        Returns:
            FastMCP: A configured FastMCP server instance ready to run.
        """
        runtime = self.create_runtime()
        server = runtime.to_fastmcp(**server_kwargs)
        # Keep runtime alive while server is alive.
        setattr(server, "_toolkit_runtime", runtime)
        return server

    def to_mcp_config(
        self,
        transport: str = "http",
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> Dict[str, Any]:
        """\
        Build a standard MCP client config dict for copy-paste into
        Claude Desktop / Cursor / VS Code / etc.

        Uses ``sys.executable`` so the config works regardless of whether
        the ``ahvn`` console-script is on the system PATH.

        When *host* and *port* are both ``None`` the method first checks for
        a previously saved remote URL (set by :meth:`from_url` /
        :meth:`from_mcp_config`).  If one exists it is emitted directly.
        Otherwise the default ``127.0.0.1:7001`` is used.  Passing *host*
        or *port* explicitly always takes priority over any saved URL.

        Args:
            transport (str): Transport type ("stdio", "http").
            host (Optional[str]): Host to bind to (for http).  ``None`` means
                use the saved remote URL or fall back to ``"127.0.0.1"``.
            port (Optional[int]): Port to bind to (for http).  ``None`` means
                use the saved remote URL or fall back to ``7001``.

        Returns:
            Dict[str, Any]: MCP client config dict with ``mcpServers`` key.
        """
        preserved_entry = getattr(self, "_mcp_entry", None)
        if isinstance(preserved_entry, dict):
            # Preserve imported MCP entries by default so
            # from_mcp_config(...) -> to_mcp_config() round-trips.
            has_url = isinstance(preserved_entry.get("url"), str) and bool(preserved_entry.get("url").strip())
            has_command = isinstance(preserved_entry.get("command"), str) and bool(preserved_entry.get("command").strip())
            entry_transport = preserved_entry.get("transport")
            if not isinstance(entry_transport, str):
                entry_transport = ""
            entry_transport = entry_transport.strip().lower()
            is_simple_url_entry = has_url and set(preserved_entry.keys()).issubset({"url", "transport"})

            preserve = False
            if transport == "http":
                if has_command:
                    preserve = True
                elif host is None and port is None:
                    preserve = True
                elif has_url and entry_transport and entry_transport != "http":
                    # Keep non-http URL entries (for example SSE/other transports)
                    # verbatim even when host/port are provided.
                    preserve = True
                elif has_url and (not is_simple_url_entry):
                    # Do not rewrite rich URL entries (headers/auth/options)
                    # into synthetic local URLs.
                    preserve = True
            elif transport == "stdio" and (has_command or (not has_url) or (has_url and entry_transport and entry_transport != "http")):
                preserve = True

            if preserve:
                return {
                    "mcpServers": {
                        self.name: deepcopy(preserved_entry),
                    }
                }

        if transport == "stdio":
            return {
                "mcpServers": {
                    self.name: _stdio_mcp_entry(self.name),
                }
            }
        if transport == "http":
            remote_url = getattr(self, "_remote_url", None)
            # Use saved remote URL only when host/port are not explicitly given
            if remote_url and host is None and port is None:
                return {
                    "mcpServers": {
                        self.name: {
                            "url": remote_url,
                            "transport": "http",
                        }
                    }
                }
            _host = host if host is not None else "127.0.0.1"
            _port = port if port is not None else 7001
            url_host = "localhost" if _host == "0.0.0.0" else _host
            return {
                "mcpServers": {
                    self.name: {
                        "url": f"http://{url_host}:{_port}/{self.name}/mcp",
                        "transport": "http",
                    }
                }
            }
        raise ValueError(f"Unsupported transport: {transport!r}. Supported transports are 'stdio' and 'http'.")

    def to_mcp_json(
        self,
        transport: str = "http",
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> str:
        """\
        JSON string variant of :meth:`to_mcp_config`.

        See :meth:`to_mcp_config` for the *host* / *port* fallback logic.

        Args:
            transport (str): Transport type ("stdio", "http").
            host (Optional[str]): Host to bind to (for http).  ``None`` means
                use the saved remote URL or fall back to ``"127.0.0.1"``.
            port (Optional[int]): Port to bind to (for http).  ``None`` means
                use the saved remote URL or fall back to ``7001``.

        Returns:
            str: MCP client config as a JSON-formatted string.
        """
        from ..utils.basic.serialize_utils import dumps_json

        config = self.to_mcp_config(transport=transport, host=host, port=port)
        return dumps_json(config, indent=2)

    def serve(
        self,
        transport: str = "http",
        host: str = "127.0.0.1",
        port: int = 7001,
        wait: bool = True,
        **server_kwargs,
    ) -> Union[Dict[str, Any], "ServeHandle"]:
        """\
        Start this toolkit as an MCP server.

        For **http** transport, starts an HTTP server and prints MCP client
        config matching ``ahvn mcp serve``.

        For **stdio** transport, runs a blocking stdin/stdout loop.  This is
        the internal entry point used by MCP clients that spawn the server
        process themselves (see :meth:`to_mcp_json` for the config to paste).
        Users typically do **not** call ``serve(transport="stdio")`` directly;
        use ``to_mcp_json(transport="stdio")`` to get the client config instead.

        Args:
            transport (str): ``"stdio"`` or ``"http"``. Defaults to ``"http"``.
            host (str): Host to bind to (for http). Defaults to ``"127.0.0.1"``.
            port (int): Port to bind to (for http). Defaults to ``7001``.
            wait (bool): If ``True`` (default), block in the foreground.
                If ``False``, start in a background thread and return a
                :class:`ServeHandle` for lifecycle control.
                Ignored for ``stdio`` transport (always blocking).
            **server_kwargs: Additional kwargs passed to FastMCP constructor.

        Returns:
            ``wait=True``: ``Dict[str, Any]`` — MCP client config
            (returned after the server exits).

            ``wait=False``: :class:`ServeHandle` — background server handle
            with ``.mcp_config``, ``.url``, ``.json``, ``.stop()``,
            ``.wait()``, and context-manager support.
        """
        if transport not in ("stdio", "http"):
            raise ValueError(f"Unsupported transport: {transport!r}. Supported transports are 'stdio' and 'http'.")

        # ── stdio: subprocess-internal, no user-facing output ─────────
        if transport == "stdio":
            runtime = self.create_runtime()
            server = runtime.to_fastmcp(**server_kwargs)
            try:
                # show_banner=False avoids a blocking PyPI version-check
                # and noisy stderr output that can confuse MCP client bridges.
                server.run(transport="stdio", show_banner=False)
            except (KeyboardInterrupt, BaseExceptionGroup):
                # Normal shutdown: stdin closed (pipe ended) or Ctrl+C.
                pass
            finally:
                runtime.close()
            return self.to_mcp_config(transport="stdio")

        # ── HTTP ──────────────────────────────────────────────────────
        mcp_config = self.to_mcp_config(transport=transport, host=host, port=port)

        json_text = self.to_mcp_json(transport=transport, host=host, port=port)
        logger.info("MCP client config (copy to your MCP client settings):\n%s", json_text)

        runtime = self.create_runtime()
        server = runtime.to_fastmcp(**server_kwargs)

        # ── HTTP ──────────────────────────────────────────────────────
        url = f"http://{host}:{port}/{self.name}/mcp"
        run_kwargs: Dict[str, Any] = {
            "transport": "http",
            "host": host,
            "port": port,
            "path": f"/{self.name}/mcp",
        }

        if wait:
            logger.info("Serving '%s' via http (pid=%d)", self.name, os.getpid())
            logger.info("Endpoint: %s", url)
            logger.info("Press Ctrl+C to stop.")
            try:
                server.run(**run_kwargs)
            finally:
                runtime.close()
            return mcp_config

        # ── wait=False: background thread via uvicorn ─────────────────
        import uvicorn

        asgi_app = server.http_app(path=f"/{self.name}/mcp")
        uvi_config = uvicorn.Config(asgi_app, host=host, port=port, log_level="warning")
        uvi_server = uvicorn.Server(uvi_config)

        thread = threading.Thread(
            target=uvi_server.run,
            daemon=True,
            name=f"mcp-{self.name}",
        )
        thread.start()

        logger.info("Serving '%s' via http (background)", self.name)
        logger.info("Endpoint: %s", url)

        return ServeHandle(
            name=self.name,
            mcp_config=mcp_config,
            url=url,
            runtime=runtime,
            _server=uvi_server,
            _thread=thread,
        )

    def to_jsonschema_list(self) -> List[Dict]:
        """\
        Convert all tools to OpenAI-style JSON schema function call format.

        Returns:
            List[Dict]: List of JSON schema function definitions.
        """
        return [tool.to_jsonschema() for tool in self.tools.values()]

    def to_tool_list(self) -> List[ToolSpec]:
        """\
        Return a flat list of all ToolSpecs.

        Returns:
            List[ToolSpec]: All tools in this toolkit.
        """
        return list(self.tools.values())

    def to_capsules(self, **kwargs) -> List[Dict[str, Any]]:
        """Serialize all tools in this toolkit as capsule payloads."""
        self._ensure_tools_discovered()
        capsules: List[Dict[str, Any]] = []
        for tool_name, tool_spec in self.tools.items():
            capsule = tool_spec.to_capsule(**kwargs)
            manifest = capsule.setdefault("manifest", {})
            manifest.setdefault("tool_name", tool_name)
            capsules.append(capsule)
        return capsules

    @classmethod
    def from_capsules(
        cls,
        name: str,
        capsules: List[Dict[str, Any]],
        short_description: str = "",
        description: str = "",
        instructions: Optional[Dict[str, List[str]]] = None,
        runtime_type: Literal["session", "persistent", "stateless"] = "session",
        tool_enabled: Optional[Dict[str, bool]] = None,
    ) -> "Toolkit":
        """Restore a toolkit from a list of capsule payloads."""
        tools: Dict[str, ToolSpec] = {}
        for capsule in capsules:
            tool_spec = ToolSpec.from_capsule(capsule)
            manifest = capsule.get("manifest", {}) if isinstance(capsule, dict) else {}
            tool_name = manifest.get("tool_name", tool_spec.name)
            if isinstance(tool_name, str) and tool_name:
                try:
                    tool_spec.tool.name = tool_name
                    tool_spec._clear_cache()
                except Exception:
                    pass
            tools[tool_name] = tool_spec
        return cls(
            name=name,
            short_description=short_description,
            description=description,
            tools=tools,
            instructions=instructions,
            runtime_type=runtime_type,
            tool_enabled=tool_enabled,
        )

    @classmethod
    def from_mcp_config(
        cls,
        config: Dict[str, Any],
        name: Optional[str] = None,
    ) -> "Toolkit":
        """\
        Reconstruct a toolkit from an MCP client config dict.

        The *config* is the same format produced by :meth:`to_mcp_config`:

        .. code-block:: json

            {
                "mcpServers": {
                    "my_toolkit": {
                        "command": "python",
                        "args": ["-c", "..."]
                    }
                }
            }

        Imported entries are kept generic:
        - The selected server entry is preserved verbatim for MCP config
          round-tripping, regardless of entry shape.
        - ``name`` only controls the local toolkit id / emitted key under
          ``mcpServers``; the preserved entry payload is not rewritten.

        Args:
            config: MCP client config dict (must contain ``mcpServers``).
            name: Override the toolkit name (default: first key in ``mcpServers``).

        Returns:
            Toolkit: A toolkit reconstructed from the config.
        """
        servers = config.get("mcpServers", {})
        if (not isinstance(servers, dict)) or (not servers):
            raise ValueError("Config must contain a non-empty 'mcpServers' mapping.")

        requested_name = name.strip() if isinstance(name, str) else None
        if requested_name == "":
            requested_name = None
        default_source = next(iter(servers))
        if requested_name and requested_name in servers:
            source_name = requested_name
        elif requested_name and len(servers) == 1:
            source_name = default_source
        elif requested_name and len(servers) > 1:
            available = sorted(servers.keys())
            raise ValueError(
                f"Ambiguous name override '{requested_name}' for config with multiple servers {available}. " "Use an existing key to select a source entry."
            )
        else:
            source_name = default_source
        server_name = requested_name or source_name
        entry = servers.get(source_name)
        if not isinstance(entry, dict):
            raise ValueError("MCP server entry must be a dictionary.")

        # Preserve imported entry verbatim (url/command/other) so conversion stays generic.
        toolkit = cls(
            name=server_name,
            short_description="Imported from MCP config",
            description=f"Toolkit imported from MCP client config entry '{source_name}'.",
        )
        toolkit._mcp_entry = deepcopy(entry)
        url = entry.get("url")
        if isinstance(url, str) and url.strip():
            toolkit._remote_url = url.strip()
        return toolkit

    @classmethod
    def from_url(
        cls,
        url: str,
        name: Optional[str] = None,
    ) -> "Toolkit":
        """\
        Create a toolkit from a remote MCP server URL.

        For Streamable HTTP servers the URL typically looks like
        ``http://host:port/mcp`` or ``http://host:port/<name>/mcp``.

        This constructs a toolkit that can be re-registered locally. The
        MCP config will point to the remote URL so that MCP clients can
        connect to it directly.

        Args:
            url: HTTP(S) URL of the remote MCP server endpoint.
            name: Override the toolkit name (default: derived from URL path).

        Returns:
            Toolkit: A toolkit whose :meth:`to_mcp_config` points to *url*.
        """
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://, got: {url!r}")

        # Derive a name from the URL path if not provided
        if name is None:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            # e.g. /my_toolkit/mcp -> my_toolkit
            parts = [p for p in parsed.path.strip("/").split("/") if p and p != "mcp"]
            name = parts[-1] if parts else parsed.netloc.replace(":", "_").replace(".", "_")

        toolkit = cls(
            name=name,
            short_description=f"Remote MCP server at {url}",
            description=f"Toolkit proxying remote MCP server at {url}.",
        )
        # Store the URL so to_mcp_config can emit it
        toolkit._remote_url = url
        toolkit._mcp_entry = {
            "url": url,
            "transport": "http",
        }
        return toolkit

    def info(self) -> Dict[str, Any]:
        """\
        Return a metadata summary of this toolkit.

        Returns:
            Dict[str, Any]: Summary including name, description, short_description, tools, and instructions.
        """
        return {
            "name": self.name,
            "short_description": self.short_description,
            "description": self.description,
            "tools": self.list_tools(),
            "instructions": self.instructions,
            "runtime_type": self.runtime_type,
            "tool_enabled": deepcopy(self.tool_enabled),
        }

    def to_prompt(self) -> str:
        """\
        Render a human-readable markdown description of this toolkit,
        including tool signatures and parameter details.

        Used by both ``do_show`` (CLI) and ``export`` (SKILL.md body).

        Returns:
            str: Markdown-formatted toolkit description.
        """
        from ..utils.basic.str_utils import bullet_list, md_block, md_section, bullet_dict

        tool_names = self.list_tools()

        if not tool_names:
            content = f"{self.description}\n\n*(no tools)*" if self.description else "*(no tools)*"
            return md_section(title=f"The `{self.name}` Toolkit", content=content)

        tool_sections = []
        for t_name in tool_names:
            tool = self.tools[t_name]
            desc = tool.tool.description if hasattr(tool, "tool") and tool.tool else ""
            params = tool.params
            required = set(tool.input_schema.get("required", []))
            parts = [desc] if desc else []

            # Parameters
            if params:
                schema = {k: {"mode": "param", "kwargs": {"required": k in required}} for k in params}
                parts.append(f"**Parameters:**\n{bullet_dict(params, schema=schema)}")
            else:
                parts.append("*(no parameters)*")

            # Usage
            cli_kv = " ".join(f'"{k}=<{k}>"' for k in params)
            cli_cmd = f"ahvn mcp run {self.name}.{t_name} {cli_kv}".rstrip()
            py_kv = ", ".join(f"{k}=<{k}>" for k in params)
            py_import = f"from ahvn.tool import TK_AHVN\n{t_name} = TK_AHVN.get('{self.name}').get_tool('{t_name}')"
            py_cmd = f"{py_import}\nprint({t_name}({py_kv}))"
            parts.append(f"**Usage:**\n{md_block(cli_cmd, lang='bash')}\n{md_block(py_cmd, lang='python')}")

            # Per-tool instructions
            tool_instructions = self.instructions.get(t_name, [])
            if tool_instructions:
                parts.append(f"**Instructions:**\n{bullet_list(tool_instructions)}")

            tool_sections.append({"title": t_name, "content": "\n\n".join(parts)})

        return md_section(
            title=f"The `{self.name}` Toolkit",
            content=self.description or None,
            sections=[{"title": f"Tools ({len(tool_names)})", "sections": tool_sections}],
        ).strip()

    def __repr__(self) -> str:
        tools_str = ", ".join(self.list_tools())
        return f"Toolkit(name='{self.name}', tools=[{tools_str}])"

    def __len__(self) -> int:
        self._ensure_tools_discovered()
        return len(self.tools)

    def __contains__(self, tool_name: str) -> bool:
        self._ensure_tools_discovered()
        return tool_name in self.tools

    def __getitem__(self, tool_name: str) -> ToolSpec:
        return self.get_tool(tool_name)

    def export(self, output_path: str) -> str:
        """\
        Export this toolkit as a Skills package directory.

        Creates a subfolder ``<output_path>/<toolkit_name>/`` containing a
        single ``SKILL.md`` with YAML frontmatter and a rendered markdown body.

        Args:
            output_path (str): Parent directory to write the Skills package into.
                A subfolder named after the toolkit is always created inside.

        Returns:
            str: Absolute path of the created Skills directory
                (i.e. ``<output_path>/<toolkit_name>/``).
        """
        import os
        import yaml
        from collections import OrderedDict
        from ..utils.basic.file_utils import touch_dir
        from ..utils.basic.serialize_utils import save_txt

        # Always create a named subfolder so `export("./skills/")` →
        # `./skills/<name>/SKILL.md` instead of `./skills/SKILL.md`.
        skill_dir = os.path.join(output_path, self.name)
        touch_dir(skill_dir)

        tool_names = self.list_tools()

        # ── SKILL.md ─────────────────────────────────────────────────
        # Use short_description for frontmatter description (concise, no preset list etc.)
        fm_desc = self.short_description or self.description or f"Toolkit '{self.name}'"

        # Register OrderedDict representer to preserve key order
        yaml.add_representer(
            OrderedDict,
            lambda dumper, data: dumper.represent_mapping("tag:yaml.org,2002:map", data.items()),
        )

        # Custom str representer: plain style for single-line strings (no wrapping),
        # literal block style for multiline strings.
        def _str_representer(dumper, data):
            if "\n" in data:
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=None)

        yaml.add_representer(str, _str_representer)

        # Build frontmatter with controlled key order: name, description, tools
        frontmatter = OrderedDict(
            [
                ("name", self.name),
                ("description", fm_desc),
                ("tools", tool_names),
            ]
        )

        body = self.to_prompt()
        fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, width=2147483647).strip()
        skill_md = "---\n" + fm_str + "\n---\n" + body
        save_txt(skill_md, os.path.join(skill_dir, "SKILL.md"))

        return os.path.abspath(skill_dir)


class ToolkitFactory:
    """\
    Base class for toolkit factories.

    A ToolkitFactory defines how to create a Toolkit from a set of arguments.
    Subclasses must define `name`, `description` class variables and implement
    the `create()` classmethod.

    Use the `@register_factory` decorator to register a factory in the global registry.

    Example (full override):
        >>> @register_factory
        ... class DatabaseToolkitFactory(ToolkitFactory):
        ...     name = "db"
        ...     description = "Create database toolkits with SQL execution and schema inspection."
        ...
        ...     @classmethod
        ...     def create(cls, toolkit_name: str, **args) -> Toolkit:
        ...         db = Database(**args)
        ...         tool = toolspec_factory_builtins_execute_sql(db)
        ...         return Toolkit(name=toolkit_name, tools={"exec_sql": tool})

    Example (simple override via tools):
        >>> @register_factory
        ... class MathToolkitFactory(ToolkitFactory):
        ...     name = "math"
        ...     description = "Basic math operations."
        ...
        ...     @classmethod
        ...     def tools(cls, **args) -> Dict[str, ToolSpec]:
        ...         return {"add": ToolSpec.from_func(add)}
    """

    name: ClassVar[str] = ""
    short_description: ClassVar[str] = ""
    description: ClassVar[str] = ""

    @classmethod
    def args_schema(cls) -> Dict[str, Any]:
        """\
        Return a JSON schema describing the arguments this factory accepts.

        Returns:
            Dict[str, Any]: JSON schema for factory arguments.
        """
        return {"type": "object", "properties": {}}

    @classmethod
    def tools(cls, **args) -> Dict[str, ToolSpec]:
        """\
        Return a dict of tool name → ToolSpec for this factory.

        Override this instead of ``create()`` for simple factories that
        don't need custom Toolkit construction logic. The default
        ``create()`` calls this method and wraps the result in a Toolkit.

        Args:
            **args: Factory-specific arguments.

        Returns:
            Dict[str, ToolSpec]: Mapping of tool names to ToolSpec instances.
        """
        raise NotImplementedError(f"{cls.__name__} must implement tools() or create().")

    @classmethod
    def create(cls, toolkit_name: str, **args) -> Toolkit:
        """\
        Create a Toolkit with the given name and factory-specific arguments.

        The default implementation calls ``cls.tools(**args)`` and wraps the
        result in a Toolkit. Override this for full control over Toolkit construction.

        Args:
            toolkit_name (str): The unique name for this toolkit instance.
            **args: Factory-specific arguments.

        Returns:
            Toolkit: A new Toolkit instance.
        """
        return Toolkit(
            name=toolkit_name,
            short_description=cls.short_description,
            description=cls.description,
            tools=cls.tools(**args),
        )

    @classmethod
    def register_create_cli(cls, create_group, cli_ref, backend: str = "typer"):
        """\
        Register a typed create subcommand for this factory on the CLI.

        Subclasses override this to provide factory-specific typed options.
        The default implementation creates a generic command that accepts
        ``key=value`` positional args.

        Args:
            create_group: The create subgroup (Typer app or Click group).
            cli_ref: Reference to the McpCLI instance.
            backend: CLI framework ("typer" or "click").
        """
        if backend == "typer":
            cls._register_create_typer(create_group, cli_ref)
        elif backend == "click":
            cls._register_create_click(create_group, cli_ref)

    @classmethod
    def _register_create_typer(cls, create_app, cli_ref):
        """\
        Default Typer create command using generic key=value args.
        """
        import typer

        factory_name = cls.name

        @create_app.command(factory_name, help=cls.description)
        def cmd(
            name: str = typer.Argument(..., help="Unique name for the toolkit."),
            args: Optional[List[str]] = typer.Argument(None, help="key=value args."),
        ):
            cli_ref.do_create(factory_name, name, args)

    @classmethod
    def _register_create_click(cls, create_group, cli_ref):
        """\
        Default Click create command using generic key=value args.
        """
        import click

        factory_name = cls.name

        @create_group.command(factory_name, help=cls.description)
        @click.argument("name")
        @click.argument("args", nargs=-1)
        def cmd(name, args):
            cli_ref.do_create(factory_name, name, list(args))
