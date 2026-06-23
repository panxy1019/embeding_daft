__all__ = [
    "ToolUKFT",
    "ToolType",
    "docstring_composer",
]

from copy import deepcopy
import asyncio
from typing import Any, Callable, ClassVar, Dict, Optional, Union

from fastmcp.tools import Tool as FastMCPTool
from mcp.types import Tool as MCPTool

from ...base import BaseUKF
from ...registry import register_ukft
from ....tool.base import ToolSpec
from ....utils.basic.func_utils import synthesize_docstring


def docstring_composer(kl, **kwargs):
    """Compose tool documentation from stored schemas."""
    resources = kl.content_resources
    return synthesize_docstring(
        description=resources.get("description", ""),
        input_schema=resources.get("input_schema", {}),
        output_schema=resources.get("output_schema", {}),
    )


@register_ukft
class ToolUKFT(BaseUKF):
    """UKF wrapper for capsule-backed tool payloads."""

    type_default: ClassVar[str] = "tool"

    @classmethod
    def _normalize_transport(cls, transport: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not isinstance(transport, dict):
            return {}
        normalized = deepcopy(transport)
        if ("transport" not in normalized) and ("type" in normalized):
            normalized["transport"] = normalized.pop("type")
        if ("script" not in normalized) and ("script_path" in normalized):
            normalized["script"] = normalized["script_path"]
        if ("script_path" not in normalized) and ("script" in normalized):
            normalized["script_path"] = normalized["script"]
        return normalized

    @property
    def capsule(self) -> Optional[Dict[str, Any]]:
        capsule = self.get("capsule")
        return capsule if isinstance(capsule, dict) else None

    @property
    def transport(self) -> Dict[str, Any]:
        transport = self.get("transport")
        if not isinstance(transport, dict):
            transport = {}
        return self._normalize_transport(transport)

    def _create_client_from_transport(self, transport: Optional[Dict[str, Any]] = None):
        from fastmcp import Client
        from fastmcp.client.transports import PythonStdioTransport, StreamableHttpTransport

        config = self._normalize_transport(transport or self.transport)
        mode = config.get("transport", "inmemory")

        if mode == "http":
            url = config.get("url")
            if not url:
                raise ValueError("HTTP transport requires 'url'.")
            return Client(StreamableHttpTransport(url=url))

        if mode == "stdio":
            script_path = config.get("script_path") or config.get("script")
            if not script_path:
                raise ValueError("STDIO transport requires 'script_path' (or 'script').")
            return Client(PythonStdioTransport(script_path=script_path, env=config.get("env")))

        if mode == "inmemory":
            raise NotImplementedError("In-memory transport requires a runtime server object. " "Please pass an active client explicitly: to_atool(client=...).")

        raise ValueError(f"Unknown transport: {mode}. Supported transports are 'http' and 'stdio'.")

    async def _tool_from_client(self, client) -> ToolSpec:
        resources = self.content_resources
        tool_name = resources.get("tool_name", self.name)
        tool_spec = await ToolSpec.from_client(client, tool_name)

        if resources.get("input_schema"):
            tool_spec.tool.parameters = resources["input_schema"]
        if resources.get("output_schema"):
            tool_spec.tool.output_schema = resources["output_schema"]
        if resources.get("description"):
            tool_spec.tool.description = resources["description"]
        if resources.get("examples"):
            tool_spec.examples = resources["examples"]
        return tool_spec

    def available(self, client=None) -> bool:
        if client is not None:
            return hasattr(client, "list_tools") and hasattr(client, "call_tool")
        mode = self.transport.get("transport", "inmemory")
        return mode in {"http", "stdio"}

    @classmethod
    def from_tool(
        cls,
        tool_spec: ToolSpec,
        name: Optional[str] = None,
        transport: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "ToolUKFT":
        serialized_examples = None
        if tool_spec.examples:
            serialized_examples = [exp.to_dict() if hasattr(exp, "to_dict") else dict(exp) for exp in tool_spec.examples]

        tool_name = tool_spec.binded.name
        transport_config = cls._normalize_transport(transport or {"transport": "inmemory"})
        transport_config.setdefault("tool_name", tool_name)

        capsule = tool_spec.to_capsule(
            layers=["runner", "source", "cloudpickle", "snapshot"],
            transport=transport_config,
        )

        content_resources = {
            "tool_name": tool_name,
            "description": tool_spec.binded.description,
            "input_schema": tool_spec.input_schema,
            "output_schema": tool_spec.output_schema,
            "examples": serialized_examples,
            "transport": transport_config,
            "capsule": capsule,
        }

        return cls(
            name=name or tool_name,
            content_resources=content_resources,
            content_composers={
                "default": docstring_composer,
                "docstring": docstring_composer,
            },
            **updates,
        )

    async def to_atool(self, client=None) -> ToolSpec:
        from ....utils.capsule import Capsule

        if client is not None:
            return await self._tool_from_client(client)

        if self.capsule:
            try:
                return Capsule.to_tool(self.capsule, layers=["runner", "source", "cloudpickle", "snapshot"])
            except Exception:
                pass

        generated_client = self._create_client_from_transport(self.transport)
        async with generated_client:
            return await self._tool_from_client(generated_client)

    def to_tool(self, client=None) -> ToolSpec:
        from ....utils.capsule import Capsule

        try:
            asyncio.get_running_loop()
            raise RuntimeError("to_tool() cannot be called from within an async context. Use 'await to_atool(...)' instead.")
        except RuntimeError as exc:
            if "cannot be called" in str(exc):
                raise

        if client is None and self.capsule:
            try:
                return Capsule.to_tool(self.capsule, layers=["runner", "source", "cloudpickle", "snapshot"])
            except Exception:
                pass

        if client is not None:

            async def _restore_with_client():
                async with client:
                    return await self.to_atool(client)

            tool_spec = asyncio.run(_restore_with_client())
            original_acall = tool_spec.acall

            async def sync_aware_acall(**kwargs):
                async with client:
                    return await original_acall(**kwargs)

            def sync_aware_call(**kwargs):
                return asyncio.run(sync_aware_acall(**kwargs))

            tool_spec.acall = sync_aware_acall
            tool_spec.call = sync_aware_call
            tool_spec.__call__ = sync_aware_call
            return tool_spec

        return asyncio.run(self.to_atool())

    @classmethod
    async def from_client(
        cls,
        client,
        tool_name: str,
        name: Optional[str] = None,
        transport: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "ToolUKFT":
        tool_spec = await ToolSpec.from_client(client, tool_name)
        transport_config = cls._normalize_transport(transport or {"transport": "inmemory", "tool_name": tool_name})
        transport_config.setdefault("tool_name", tool_name)
        return cls.from_tool(tool_spec, name=name, transport=transport_config, **updates)


ToolType = Union[Callable, MCPTool, FastMCPTool, ToolSpec, ToolUKFT]
