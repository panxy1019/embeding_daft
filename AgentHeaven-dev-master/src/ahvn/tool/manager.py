from __future__ import annotations

__all__ = [
    "ToolkitManager",
    "get_toolkit_manager",
    "TK_AHVN",
]

import json
import os
import subprocess
import threading
import time
from typing import Dict, List, Any, Optional

from .toolkit import Toolkit, ToolkitRuntime
from .store import ToolkitStore, get_toolkit_store
from ..utils.basic.log_utils import get_logger
from ..utils.basic.serialize_utils import load_json, save_json

logger = get_logger(__name__)


class ToolkitManager:
    """\
    Service layer for managing Toolkit instances.

    Provides CRUD operations, persistence, MCP serving, and direct tool execution.
    Toolkit configs are persisted to a SQLite database (single source of truth).
    Constructed toolkit objects are cached in-memory with thread-safe locking.

    Example:
        >>> from ahvn.tool import get_factory
        >>> manager = ToolkitManager()
        >>> factory = get_factory("db")
        >>> toolkit = factory.create("my-database", provider="sqlite", database="test.db")
        >>> manager.add(toolkit, source={"factory": "db", "args": {"provider": "sqlite", "database": "test.db"}})
        >>> manager.run("my-database.exec_sql", query="SELECT 1")
        >>> manager.serve("my-database", transport="stdio")
        >>> manager.stop("my-database")
    """

    def __init__(self, store: Optional[ToolkitStore] = None):
        self._store = store or get_toolkit_store()
        self._cache: Dict[str, Toolkit] = {}
        self._lock = threading.Lock()
        self._processes: Dict[str, subprocess.Popen] = {}

    @property
    def store(self) -> ToolkitStore:
        return self._store

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add(
        self,
        toolkit: Toolkit,
        overwrite: bool = True,
        source: Optional[Dict[str, Any]] = None,
        **capsule_kwargs,
    ) -> Toolkit:
        """Add a toolkit to the manager for reuse across sessions.

        Args:
            toolkit: Toolkit instance to persist and cache.
            overwrite: Whether to replace existing toolkit rows by name.
            source: Optional source metadata (for example, factory and args).
            **capsule_kwargs: Forwarded to ``toolkit.to_capsules(...)`` during persistence.
        """
        if (not overwrite) and self._store.exists(toolkit.name):
            raise KeyError(f"Toolkit '{toolkit.name}' already exists.")
        self._store.save_toolkit(toolkit, source=source, **capsule_kwargs)
        with self._lock:
            self._cache[toolkit.name] = toolkit
        return toolkit

    def create_runtime(self, toolkit_name: str, session_id: Optional[str] = None) -> ToolkitRuntime:
        """Create a runtime for the toolkit."""
        toolkit = self.get(toolkit_name)
        return toolkit.create_runtime(session_id=session_id)

    def get(self, name: str) -> Toolkit:
        """\
        Get a toolkit by name.

        Returns a cached instance if available, otherwise reconstructs from the DB.

        Args:
            name (str): Toolkit name.

        Returns:
            Toolkit: The requested toolkit.

        Raises:
            KeyError: If toolkit not found.
        """
        with self._lock:
            if name in self._cache:
                return self._cache[name]

        if not self._store.exists(name):
            available = self._store.list_names()
            raise KeyError(f"Toolkit '{name}' not found. Available: {available}")

        toolkit = self._store.load(name)
        with self._lock:
            self._cache.setdefault(name, toolkit)
            return self._cache[name]

    def list(self) -> List[Dict[str, Any]]:
        """\
        List all toolkit summaries.

        Returns:
            List[Dict[str, Any]]: List of toolkit info dicts.
        """
        records = self._store.list()
        result = []
        for record in records:
            name = record["name"]
            with self._lock:
                cached = self._cache.get(name)
            if cached is not None:
                info = cached.info()
            else:
                info = {
                    "name": name,
                    "description": record.get("description", ""),
                    "short_description": record.get("short_description", ""),
                    "tools": record.get("tools", []),
                    "instructions": {},
                    "runtime_type": record.get("runtime_type", "session"),
                    "tool_enabled": record.get("tool_enabled", {}),
                }
            info["checksum"] = record.get("checksum")
            info["serving"] = name in self._processes
            result.append(info)
        return result

    def info(self, name: str) -> Dict[str, Any]:
        """Return a persisted registry-style info row for a toolkit."""
        info = self._store.info(name)
        if info is None:
            raise KeyError(f"Toolkit '{name}' not found.")
        info = dict(info)
        info["serving"] = name in self._processes
        return info

    def rename(self, old_name: str, new_name: str) -> Toolkit:
        """\
        Rename a toolkit.

        Args:
            old_name (str): Current name.
            new_name (str): New name.

        Returns:
            Toolkit: The renamed toolkit.

        Raises:
            KeyError: If old_name not found or new_name already taken.
        """
        if not self._store.exists(old_name):
            raise KeyError(f"Toolkit '{old_name}' not found.")
        if self._store.exists(new_name):
            raise KeyError(f"Toolkit '{new_name}' already exists.")
        if old_name in self._processes:
            raise RuntimeError(f"Cannot rename '{old_name}' while it is serving. Stop it first.")

        self._store.rename(old_name, new_name)
        with self._lock:
            toolkit = self._cache.pop(old_name, None)
            if toolkit is not None:
                toolkit.name = new_name
                self._cache[new_name] = toolkit

        return self.get(new_name)

    def remove(self, name: str) -> None:
        """\
        Remove a toolkit.

        Args:
            name (str): Toolkit name to remove.

        Raises:
            KeyError: If toolkit not found.
        """
        if not self._store.exists(name):
            raise KeyError(f"Toolkit '{name}' not found.")
        if name in self._processes:
            self.stop(name)

        self._store.delete(name)
        with self._lock:
            self._cache.pop(name, None)

    def stale(self) -> List[Dict[str, Any]]:
        """List persisted toolkits whose recorded source paths are stale."""
        return self._store.stale()

    # ── Direct Execution ──────────────────────────────────────────────────

    def export(self, name: str, output_path: str) -> str:
        """\
        Export a toolkit as a Skills package directory.

        Args:
            name (str): Toolkit name.
            output_path (str): Directory path for the export.

        Returns:
            str: Absolute path of the created Skills directory.
        """
        toolkit = self.get(name)
        return toolkit.export(output_path)

    def save_as_capsules(self, name: str, tags: Optional[List[str]] = None, **capsule_kwargs) -> List[str]:
        """Persist toolkit tools as individual capsules in the global capsule store."""
        from ..utils.capsule import get_capsule_store

        toolkit = self.get(name)
        store = get_capsule_store()
        base_tags = list(tags or [])
        if f"toolkit:{name}" not in base_tags:
            base_tags.append(f"toolkit:{name}")

        capsule_ids: List[str] = []
        for capsule in toolkit.to_capsules(**capsule_kwargs):
            tool_name = capsule.get("manifest", {}).get("tool_name")
            tool_tags = list(base_tags)
            if tool_name:
                tool_tag = f"tool:{tool_name}"
                if tool_tag not in tool_tags:
                    tool_tags.append(tool_tag)
            capsule_ids.append(store.add(capsule, tags=tool_tags))
        return capsule_ids

    def load_from_capsules(
        self,
        toolkit_name: str,
        capsule_ids: List[str],
        short_description: str = "",
        description: str = "",
        instructions: Optional[Dict[str, List[str]]] = None,
    ) -> Toolkit:
        """Load a toolkit from capsule ids stored in the global capsule store."""
        from ..utils.capsule import get_capsule_store

        store = get_capsule_store()
        capsules: List[Dict[str, Any]] = []
        for capsule_id in capsule_ids:
            capsule = store.get(capsule_id)
            if capsule is None:
                raise KeyError(f"Capsule not found: {capsule_id}")
            capsules.append(capsule)

        toolkit = Toolkit.from_capsules(
            name=toolkit_name,
            capsules=capsules,
            short_description=short_description,
            description=description,
            instructions=instructions,
        )
        self._store.save_toolkit(
            toolkit,
            source={
                "factory": "capsules",
                "args": {"capsule_ids": list(capsule_ids)},
            },
        )
        with self._lock:
            self._cache[toolkit_name] = toolkit
        return toolkit

    def run(self, qualified_name: str, **kwargs) -> Any:
        """\
        Execute a tool by qualified name (toolkit_name.tool_name).

        Args:
            qualified_name (str): Dot-separated "toolkit_name.tool_name".
            **kwargs: Arguments to pass to the tool.

        Returns:
            Any: The tool's return value.
        """
        parts = qualified_name.split(".", 1)
        if len(parts) != 2:
            raise ValueError(f"Expected 'toolkit_name.tool_name', got '{qualified_name}'.")
        toolkit_name, tool_name = parts
        runtime = self.create_runtime(toolkit_name)
        try:
            return runtime.run(tool_name, **kwargs)
        finally:
            runtime.close()

    def reset(self, name: str) -> None:
        """Reset toolkit runtime state (for persistent/shared toolkits)."""
        if name in self._processes:
            raise RuntimeError(f"Cannot reset '{name}' while it is serving. Stop it first.")
        toolkit = self.get(name)
        runtime = toolkit.create_runtime()
        try:
            if toolkit.runtime_type == "persistent":
                toolkit._reset_persistent_tool_states(names=toolkit.list_tools())
            runtime.reset()
        finally:
            runtime.close()

    # ── MCP Serving ───────────────────────────────────────────────────────

    def serve(
        self,
        name: str,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 7001,
    ) -> Dict[str, Any]:
        """\
        Prepare or start serving a toolkit as an MCP server.

        For HTTP transport, delegates to :meth:`serve_many` which
        launches a background subprocess and mounts at ``/<name>/mcp``.
        For stdio, no subprocess is started — the MCP client will launch
        the process itself using the returned ``mcp_config``.

        Args:
            name (str): Toolkit name.
            transport (str): Transport type ("stdio", "http"). Defaults to "stdio".
            host (str): Host to bind to (for http). Defaults to "127.0.0.1".
            port (int): Port to bind to (for http). Defaults to 7001.

        Returns:
            Dict[str, Any]: Connection info for clients (includes ``mcp_config``).

        Raises:
            KeyError: If toolkit not found.
            RuntimeError: If toolkit is already serving (HTTP only).
        """
        if transport not in ("stdio", "http"):
            raise ValueError(f"Unsupported transport: {transport!r}. Supported transports are 'stdio' and 'http'.")

        if transport == "http":
            return self.serve_many([name], transport="http", host=host, port=port)[0]

        # ── stdio ─────────────────────────────────────────────────────
        # Stdio MCP servers are launched by the client, not by us.
        # We only build and return the JSON config for copy-paste.
        if not self._store.exists(name):
            raise KeyError(f"Toolkit '{name}' not found.")

        logger.info("stdio transport: MCP client will launch the server process. " "Copy the JSON config below into your MCP client settings.")

        from .toolkit import _stdio_mcp_entry

        mcp_config = {
            "mcpServers": {
                name: _stdio_mcp_entry(name),
            }
        }

        return {
            "name": name,
            "transport": "stdio",
            "mcp_config": mcp_config,
        }

    def serve_many(
        self,
        names,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 7001,
    ) -> List[Dict[str, Any]]:
        """\
        Prepare or start multiple toolkit MCP servers.

        - ``stdio``: returns JSON configs for each toolkit (no subprocess).
          The MCP client launches the server processes itself.
        - ``http``: all toolkits are served under one host/port using
          per-toolkit MCP paths: ``/<toolkit_name>/mcp``.
        """
        if transport not in ("stdio", "http"):
            raise ValueError(f"Unsupported transport: {transport!r}. Supported transports are 'stdio' and 'http'.")

        if isinstance(names, str):
            names = [names]

        if not names:
            return []

        if transport == "http":
            for name in names:
                if name in self._processes:
                    raise RuntimeError(f"Toolkit '{name}' is already serving. Stop it first.")

            valid_names: List[str] = []
            for name in names:
                if not self._store.exists(name):
                    raise KeyError(f"Toolkit '{name}' not found.")
                valid_names.append(name)

            import sys

            serve_script = _build_multi_http_serve_script(valid_names, host, port)
            proc = subprocess.Popen(
                [sys.executable, "-c", serve_script],
            )

            infos: List[Dict[str, Any]] = []
            for name in names:
                self._processes[name] = proc
                infos.append(
                    {
                        "name": name,
                        "transport": "http",
                        "pid": proc.pid,
                        "url": f"http://{host}:{port}/{name}/mcp",
                        "mcp_config": {
                            "mcpServers": {
                                name: {
                                    "url": f"http://{host}:{port}/{name}/mcp",
                                    "transport": "http",
                                }
                            }
                        },
                    }
                )
            return infos

        # ── stdio: just build configs, no subprocess ─────────────────
        infos: List[Dict[str, Any]] = []
        for name in names:
            info = self.serve(name, transport="stdio")
            infos.append(info)
        return infos

    def stop_many(self, names) -> None:
        """Stop a batch of serving toolkits (best effort)."""
        if isinstance(names, str):
            names = [names]

        procs = {}
        for name in names:
            proc = self._processes.pop(name, None)
            if proc is not None:
                procs[id(proc)] = proc

        for proc in procs.values():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        # cleanup remaining names pointing to terminated procs
        remaining = {}
        for name, proc in self._processes.items():
            if id(proc) not in procs:
                remaining[name] = proc
        self._processes = remaining

    def stop_all(self) -> None:
        """Stop all serving toolkits tracked by this manager instance."""
        self.stop_many(list(self._processes.keys()))

    def wait_forever(self, poll_interval: float = 0.5) -> None:
        """\
        Keep the supervisor process alive until interrupted.

        On ``KeyboardInterrupt``, all child server subprocesses are terminated.
        """
        try:
            while True:
                self.ps()
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            self.stop_all()

    def stop(self, name: str) -> None:
        """\
        Stop a serving toolkit (in-process tracking only).

        Args:
            name (str): Toolkit name.

        Raises:
            KeyError: If toolkit is not serving.
        """
        if name not in self._processes:
            raise KeyError(f"Toolkit '{name}' is not serving.")

        proc = self._processes.pop(name)
        self._processes = {key: value for key, value in self._processes.items() if value is not proc}
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def clear(self) -> int:
        """\
        Remove all toolkits.

        Stops any running MCP server subprocesses before clearing persisted
        toolkits and in-process cache entries.

        Returns:
            int: Number of toolkits removed.
        """
        self.stop_all()
        count = self._store.clear()
        with self._lock:
            self._cache.clear()
        return count

    def ps(self) -> List[Dict[str, Any]]:
        """\
        List currently serving toolkits (in-process tracking only).

        Returns:
            List[Dict[str, Any]]: List of serving status dicts with name, pid, alive status.
        """
        result = []
        dead = []
        for name, proc in self._processes.items():
            alive = proc.poll() is None
            result.append({"name": name, "pid": proc.pid, "alive": alive})
            if not alive:
                dead.append(name)
        for name in dead:
            self._processes.pop(name, None)
        return result

    def __repr__(self) -> str:
        names = self._store.list_names()
        return f"ToolkitManager(toolkits={names})"


def _build_multi_http_serve_script(
    names: List[str],
    host: str,
    port: int,
) -> str:
    """Build a script that serves multiple toolkit MCP apps on one host/port.

    Each toolkit is mounted at ``/<toolkit_name>/mcp``.
    Sub-app lifespans are combined via ``AsyncExitStack`` so that
    FastMCP's session managers are properly initialized.
    """
    names_json = json.dumps(names)
    return f"""\
import json
from contextlib import asynccontextmanager, AsyncExitStack
from ahvn.tool.manager import ToolkitManager
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

names = json.loads('''{names_json}''')
manager = ToolkitManager()
runtimes = []
sub_apps = []
routes = []
for name in names:
    runtime = manager.create_runtime(name)
    runtimes.append(runtime)
    sub_app = runtime.to_fastmcp().http_app(path="/mcp")
    sub_apps.append(sub_app)
    routes.append(Mount("/" + name, app=sub_app))

@asynccontextmanager
async def combined_lifespan(app):
    async with AsyncExitStack() as stack:
        for sa in sub_apps:
            if hasattr(sa, "lifespan") and sa.lifespan:
                await stack.enter_async_context(sa.lifespan(app))
        try:
            yield
        finally:
            for runtime in runtimes:
                runtime.close()

app = Starlette(routes=routes, lifespan=combined_lifespan)
uvicorn.run(app, host="{host}", port={port})
"""


# -- singleton -------------------------------------------------------- #

_manager_instance: Optional[ToolkitManager] = None
_manager_lock = threading.Lock()


def get_toolkit_manager() -> ToolkitManager:
    """Return the process-wide ``ToolkitManager`` singleton."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = ToolkitManager()
    return _manager_instance


class _LazyTK:
    """Module-level proxy so ``TK_AHVN`` can be imported at the top of a
    module without triggering DB initialization on import."""

    def __getattr__(self, name):
        return getattr(get_toolkit_manager(), name)

    def __repr__(self):
        return repr(get_toolkit_manager())


TK_AHVN: ToolkitManager = _LazyTK()  # type: ignore[assignment]
