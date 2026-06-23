from __future__ import annotations

__all__ = [
    "ToolSpec",
]

from ..utils.basic.func_utils import (
    parse_docstring as parse_docstring_to_spec,
    desc2short,
    func2meta,
    norm_schema,
    code2func,
    synthesize_docstring,
    synthesize_def,
    synthesize_sig,
    funcwrap,
)
from ..utils.basic.config_utils import dsetdef, dget

from typing import Union, Optional, Callable, Iterable, Dict, List, Any, TYPE_CHECKING

from copy import deepcopy
import asyncio
import functools
import inspect

if TYPE_CHECKING:
    from ..ukf.templates.basic.experience import ExperienceType
    from mcp.types import Tool as MCPTool
    from fastmcp.tools import Tool as FastMCPTool

import json as _json


def _extract_text_result(result) -> Any:
    """Extract a return value from a ToolResult that has no structured_content."""
    try:
        text = result.content[0].text
    except (AttributeError, IndexError, TypeError):
        return None
    try:
        return _json.loads(text)
    except (ValueError, TypeError):
        return text


# TODO: PTC Support
class ToolSpec(object):
    """\
    A specification wrapper for tools that can be used with LLMs.

    Create empty specs directly, or use `from_func` to infer schema from callables.

    Example:
        >>> spec = ToolSpec(name="add", description="Add two numbers")
        >>> spec = ToolSpec.from_func(lambda a, b: a + b, name="add", description="Add two numbers")
    """

    def __init__(
        self,
        name: str = "tool",
        short_description: str = "",
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable["ExperienceType"]] = None,
    ):
        from fastmcp.tools import Tool as FastMCPTool

        async def _placeholder():
            return None

        self.tool: FastMCPTool = FastMCPTool.from_function(
            fn=_placeholder,
            name=name or "tool",
            description=description or "",
        )
        self._short_description: str = short_description or desc2short(description)
        self._description: str = description or ""
        self._examples: Optional[Iterable["ExperienceType"]] = examples
        self.state: Dict[str, Any] = dict()
        self.binds: Dict[str, str] = dict()
        if input_schema is not None:
            self.tool.parameters = deepcopy(norm_schema(input_schema))
        if output_schema is not None:
            self.tool.output_schema = deepcopy(output_schema)
        self._clear_cache()

    def _clear_cache(self):
        self._binded: FastMCPTool = None
        self._params: Dict[str, Any] = None
        self._sig: str = None
        self._code: str = None
        self._docstring: str = None

    @property
    def name(self):
        return self.tool.name

    @name.setter
    def name(self, value: str):
        self.tool.name = value
        self._clear_cache()

    @property
    def short_description(self) -> str:
        return self._short_description or desc2short(self.description)

    @short_description.setter
    def short_description(self, value: str):
        self._short_description = value or ""
        self._docstring = None
        self._code = None

    @property
    def description(self) -> str:
        return self.binded.description or self._description or ""

    @description.setter
    def description(self, value: str):
        self.tool.description = value or ""
        self._description = value or ""
        self._clear_cache()

    @property
    def binded(self):
        if not self.binds:
            return self.tool
        if self._binded is not None:
            return self._binded
        from fastmcp.tools import Tool as FastMCPTool
        from fastmcp.tools.tool_transform import ArgTransform

        self._binded = FastMCPTool.from_tool(
            tool=self.tool, transform_args={k: ArgTransform(hide=True, default=dget(self.state, v)) for k, v in self.binds.items()}
        )
        return self._binded

    @property
    def input_schema(self):
        if self._params is not None:
            return self._params
        self._params = norm_schema(deepcopy(self.binded.parameters))
        return self._params

    @input_schema.setter
    def input_schema(self, value: Optional[Dict[str, Any]]):
        schema = norm_schema(value)
        self.tool.parameters = deepcopy(schema)
        self._clear_cache()

    @property
    def params(self):
        return self.input_schema.get("properties", {})

    @property
    def output_schema(self):
        return self.binded.output_schema

    @output_schema.setter
    def output_schema(self, value: Optional[Dict[str, Any]]):
        self.tool.output_schema = deepcopy(value)
        self._clear_cache()

    @property
    def examples(self):
        return self._examples

    @examples.setter
    def examples(self, value):
        self._examples = value

    async def aexec(self, **kwargs):
        """\
        Execute the tool asynchronously with the provided keyword arguments, returning the full structured content.

        Args:
            **kwargs: The keyword arguments to pass to the tool.

        Returns:
            ToolResult. The full structured content.
        """
        return await self.binded.run(arguments=kwargs)

    def exec(self, **kwargs):
        """\
        Execute the tool synchronously with the provided keyword arguments, returning the full structured content.

        Args:
            **kwargs: The keyword arguments to pass to the tool

        Returns:
            ToolResult. The full structured content.
        """
        coro = self.binded.run(arguments=kwargs)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                try:
                    import nest_asyncio

                    nest_asyncio.apply(loop)
                    return loop.run_until_complete(coro)
                except ImportError:
                    raise RuntimeError(
                        "Cannot call async tool synchronously from a running event loop. "
                        "Please use 'await tool.aexec(...)' instead, or install 'nest_asyncio'."
                    )
        except RuntimeError as e:
            if "no running event loop" not in str(e).lower() and "cannot be called" not in str(e).lower():
                raise
        return asyncio.run(coro)

    async def acall(self, **kwargs):
        """\
        Execute the tool asynchronously with the provided keyword arguments, returning the main output value.

        Args:
            **kwargs: The keyword arguments to pass to the tool

        Returns:
            Any. The main output value.
        """
        result = await self.aexec(**kwargs)
        output_schema = self.binded.output_schema
        if output_schema and len(output_schema.get("properties", {})) == 1:
            return result.structured_content[next(iter(output_schema.get("properties", {})))]
        if result.structured_content is not None:
            return result.structured_content
        return _extract_text_result(result)

    def call(self, **kwargs):
        """\
        Execute the tool synchronously with the provided keyword arguments, returning the main output value.

        Args:
            **kwargs: The keyword arguments to pass to the tool

        Returns:
            Any. The main output value.
        """
        result = self.exec(**kwargs)
        output_schema = self.binded.output_schema
        if output_schema and len(output_schema.get("properties", {})) == 1:
            return result.structured_content[next(iter(output_schema.get("properties", {})))]
        if result.structured_content is not None:
            return result.structured_content
        return _extract_text_result(result)

    def __call__(self, **kwargs):
        return self.call(**kwargs)

    def available(self) -> bool:
        """\
        Check if this ToolSpec has an active MCP client connection.

        This is useful for ToolSpecs created via from_client() to verify
        the connection is still active before attempting remote calls.

        Returns:
            bool: True if connected to an MCP server, False otherwise.
                    For local tools (from_func), always returns True.

        Example:
            >>> spec = await ToolSpec.from_client(client, "add")
            >>> if spec.available():
            ...     result = await spec.acall(a=3, b=7)
        """
        # Check if this is a remote tool with client
        client = self.state.get("_mcp_client")
        if client is None:
            # Local tool, always "connected"
            return True

        # Check if client has an active session
        try:
            return client.session is not None
        except (AttributeError, RuntimeError):
            return False

    @classmethod
    def from_func(
        cls,
        func: Callable,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable[ExperienceType]] = None,
        parse_docstring: bool = True,
    ) -> "ToolSpec":
        """\
        Create a ToolSpec from a Python function.

        Args:
            func (Callable): The Python function or callable class instance to convert into a tool.
                If a class instance with a __call__ method is provided, that method will be used.
            name (Optional[str], optional): Tool name override. Defaults to None.
            short_description (Optional[str], optional): One-line tool summary. Defaults to None.
            description (Optional[str], optional): Detailed tool description. Defaults to None.
            input_schema (Optional[Dict[str, Any]], optional): Input schema override. Defaults to None.
            output_schema (Optional[Dict[str, Any]], optional): Output schema override. Defaults to None.
            examples (Optional[Iterable[ExperienceType]], optional): Example usages of the tool. Defaults to None.
            parse_docstring (bool, optional): Whether to parse the function's docstring for description. Defaults to True.

        Returns:
            ToolSpec: An instance of ToolSpec wrapping the provided function.
        """
        from fastmcp.tools import Tool as FastMCPTool

        docstring_spec = None

        if (not inspect.isroutine(func)) and hasattr(func, "__call__"):
            actual_func = func.__call__
        else:
            actual_func = func
        resolved_name = name or getattr(actual_func, "__name__", None) or getattr(type(func), "__name__", "tool")
        tool_spec = cls(
            name=resolved_name,
            short_description=short_description or "",
            description=description or "",
            examples=examples,
        )
        func_spec: Dict[str, Any] = {"name": resolved_name}

        if description and (not func_spec.get("description")):
            func_spec["description"] = description
        if output_schema is not None and (not func_spec.get("output_schema")):
            func_spec["output_schema"] = deepcopy(output_schema)

        if parse_docstring:
            docstring_spec = parse_docstring_to_spec(actual_func)

        if docstring_spec:
            doc_description = docstring_spec.get("description")
            if (not func_spec.get("description")) and doc_description:
                func_spec["description"] = doc_description
            returns = docstring_spec.get("returns")
            if (not func_spec.get("output_schema")) and returns:
                func_spec["output_schema"] = returns

        resolved_output_schema = deepcopy(func_spec.get("output_schema"))

        def _wrap_output(result: Any) -> Any:
            if resolved_output_schema is None or isinstance(result, dict):
                return result
            properties = resolved_output_schema.get("properties", {}) if isinstance(resolved_output_schema, dict) else {}
            if len(properties) == 1:
                return {next(iter(properties)): result}
            if "result" in properties:
                return {"result": result}
            return result

        if resolved_output_schema:
            if inspect.iscoroutinefunction(actual_func):

                @functools.wraps(actual_func)
                async def wrapper(*fargs, **fkwargs):
                    return _wrap_output(await actual_func(*fargs, **fkwargs))

            else:

                @functools.wraps(actual_func)
                def wrapper(*fargs, **fkwargs):
                    return _wrap_output(actual_func(*fargs, **fkwargs))

            tool_spec.tool = FastMCPTool.from_function(fn=wrapper, **func_spec)
        else:
            tool_spec.tool = FastMCPTool.from_function(fn=actual_func, **func_spec)

        if docstring_spec:
            parsed = docstring_spec.get("args", {}).get("properties", {})
            if parsed:
                schema = deepcopy(tool_spec.tool.parameters or {})
                if not schema:
                    schema = {"type": "object", "properties": {}}
                properties = schema.setdefault("properties", {})
                for param_name, param_schema in list(properties.items()):
                    if param_name in parsed:
                        properties[param_name] = parsed[param_name] | param_schema
                tool_spec.input_schema = schema

        if input_schema is not None:
            tool_spec.input_schema = input_schema
        if output_schema is not None:
            tool_spec.output_schema = output_schema
        inferred = func2meta(actual_func, include_docstring=parse_docstring)
        if short_description is None:
            short_description = inferred.get("short_description", "")
        if not description:
            description = tool_spec.tool.description or inferred.get("description") or ""
        tool_spec._description = description or ""
        tool_spec._short_description = short_description or desc2short(tool_spec._description)
        return tool_spec

    @classmethod
    def from_mcp(
        cls,
        tool: Union[MCPTool, FastMCPTool],
        examples: Optional[Iterable[ExperienceType]] = None,
    ) -> "ToolSpec":
        """\
        Create a ToolSpec from an MCP Tool.

        Args:
            tool (Union[MCPTool, FastMCPTool]): The MCP Tool to convert into a ToolSpec.
            examples (Optional[Iterable[ExperienceType]], optional): Example usages of the tool. Defaults to None.
        Returns:
            ToolSpec: An instance of ToolSpec wrapping the provided MCP tool.
        """
        from fastmcp.tools import Tool as FastMCPTool

        tool_spec = cls()
        tool_spec.tool = FastMCPTool.from_tool(tool=tool)
        tool_spec.examples = examples
        tool_spec._description = tool_spec.tool.description or ""
        tool_spec._short_description = desc2short(tool_spec._description)
        return tool_spec

    @classmethod
    async def from_client(
        cls,
        client,
        tool_name: str,
        examples: Optional[Iterable[ExperienceType]] = None,
    ) -> "ToolSpec":
        """\
        Create a ToolSpec from a FastMCP Client by retrieving a tool from an MCP server.

        This method connects to an MCP server via the provided client, retrieves the
        specified tool's definition, and creates a ToolSpec that can call the remote tool.

        Args:
            client: FastMCP Client instance (must be within an async context manager).
            tool_name (str): The name of the tool to retrieve from the server.
            examples (Optional[Iterable[ExperienceType]], optional): Example usages of the tool. Defaults to None.
        Returns:
            ToolSpec: An instance of ToolSpec wrapping the remote tool.

        Raises:
            ValueError: If the specified tool is not found on the server.
            RuntimeError: If the client is not connected.

        Example:
            >>> from fastmcp import FastMCP, Client
            >>> server = FastMCP("test")
            >>> @server.tool()
            >>> def add(a: int, b: int = 5) -> int:
            ...     return a + b
            >>> client = Client(server)
            >>> async with client:
            ...     spec = await ToolSpec.from_client(client, "add")
            ...     result = spec.call(a=3, b=7)
            ...     print(result)  # 10
        """
        from fastmcp.tools import Tool as FastMCPTool

        # List all available tools from the server
        tools = await client.list_tools()

        # Find the requested tool
        mcp_tool = None
        for tool in tools:
            if tool.name == tool_name:
                mcp_tool = tool
                break

        if mcp_tool is None:
            available = [t.name for t in tools]
            raise ValueError(f"Tool '{tool_name}' not found on server. Available tools: {available}")

        # Extract schema information
        input_schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else {}
        output_schema = mcp_tool.outputSchema if hasattr(mcp_tool, "outputSchema") else {}
        description = mcp_tool.description or ""

        # Create a simple placeholder function
        async def placeholder():
            pass

        # Create ToolSpec with placeholder, then override schemas
        tool_spec = cls()
        tool_spec.tool = FastMCPTool.from_function(
            fn=placeholder,
            name=mcp_tool.name,
            description=description,
        )

        # Override parameters and output_schema with actual MCP schemas
        if input_schema:
            tool_spec.input_schema = input_schema
        if output_schema:
            tool_spec.output_schema = output_schema

        # Store client reference for execution
        tool_spec.state["_mcp_client"] = client
        tool_spec.state["_mcp_tool_name"] = tool_name

        # Override the call methods to use the client directly
        async def client_acall(**kwargs):
            result = await client.call_tool(tool_name, kwargs)
            # Extract the actual value from the result
            if hasattr(result, "structured_content") and result.structured_content:
                structured = result.structured_content
                # If single-key dict with 'result', unwrap it
                if isinstance(structured, dict) and len(structured) == 1 and "result" in structured:
                    return structured["result"]
                return structured
            if result.content and len(result.content) > 0:
                text = result.content[0].text
                try:
                    if "." in text:
                        return float(text)
                    return int(text)
                except (ValueError, AttributeError):
                    return text
            return None

        tool_spec.acall = client_acall

        def client_call(**kwargs):
            # Try to get the current running loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, safe to use asyncio.run()
                return asyncio.run(client_acall(**kwargs))
            # We are in a running loop. Try nest_asyncio, otherwise raise.
            try:
                import nest_asyncio

                nest_asyncio.apply(loop)
                return loop.run_until_complete(client_acall(**kwargs))
            except ImportError:
                raise RuntimeError(
                    "Cannot call async tool synchronously from a running event loop. "
                    "Please use 'await tool.acall(...)' instead, or install 'nest_asyncio' "
                    "and apply it to the loop."
                )

        tool_spec.call = client_call
        tool_spec.__call__ = client_call

        tool_spec.examples = examples
        tool_spec._description = description or ""
        tool_spec._short_description = desc2short(tool_spec._description)
        return tool_spec

    @classmethod
    def from_code(
        cls,
        code: str,
        func_name: Optional[str] = None,
        env: Optional[Dict] = None,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable[ExperienceType]] = None,
        parse_docstring: bool = True,
    ) -> "ToolSpec":
        """\
        Create a ToolSpec from a code snippet.

        Args:
            code (str): The code snippet containing the function definition.
            func_name (Optional[str], optional): The name of the function to extract from the code. Defaults to None.
                If None, and only one callable is found, that function will be used.
                Notice that `func_name` is NOT the same as `name`, which will be used as the function name in the tool spec.
                `func_name` only helps to identify which function to use from the code snippet, it does NOT affect the tool spec.
            env (Optional[Dict], optional): The environment in which to execute the code. Defaults to None.
            name (Optional[str], optional): Tool name override. Defaults to None.
            short_description (Optional[str], optional): One-line tool summary. Defaults to None.
            description (Optional[str], optional): Detailed tool description. Defaults to None.
            input_schema (Optional[Dict[str, Any]], optional): Input schema override. Defaults to None.
            output_schema (Optional[Dict[str, Any]], optional): Output schema override. Defaults to None.
            examples (Optional[Iterable[ExperienceType]], optional): Example usages of the tool. Defaults to None.
            parse_docstring (bool, optional): Whether to parse docstring metadata. Defaults to True.

        Returns:
            ToolSpec: An instance of ToolSpec wrapping the function defined in the provided code.
        """
        return cls.from_func(
            func=code2func(code=code, func_name=func_name, env=env),
            name=name,
            short_description=short_description,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            examples=examples,
            parse_docstring=parse_docstring,
        )

    def bind(self, param: str, state_key: Optional[str] = None, default: Optional[Any] = None) -> "ToolSpec":
        """\
        Bind a parameter to a state key (and a value if the key is not present).
        The benefit of using a `state` instead of a direct value is that the state can be
        updated externally, and the tool will always use the latest value from the state.

        Args:
            param (str): The parameter name to bind.
            state_key (Optional[str]): The dot-separated state key path to bind the parameter to.
                It supports nested keys using dot notation (e.g., "user.age").
                If None, the parameter name will be used as the state key. Defaults to None.
            default: The default value if the state key is not present. Defaults to None.

        Returns:
            ToolSpec: The ToolSpec instance (for chaining).
        """
        dsetdef(self.state, state_key or param, default)
        self.binds[param] = state_key or param
        self._clear_cache()  # Invalidate the cached binded tool
        return self

    def unbind(self, param: str) -> "ToolSpec":
        """\
        Unbind a parameter from its state key.

        Args:
            param (str): The parameter name to unbind.

        Returns:
            ToolSpec: The ToolSpec instance (for chaining).
        """
        self.binds.pop(param, None)
        self._clear_cache()  # Invalidate the cached binded tool
        return self

    def clone(self) -> "ToolSpec":
        """Create a shallow clone of this ToolSpec.

        The clone shares the same underlying callable (exec/aexec/call/acall) and tool
        definition with the original. State is shallow-copied so clones share mutable
        resources (e.g. clients, connections) by default. Use ``copy.deepcopy(ts.state)``
        on the returned clone if full state isolation is needed.

        Returns:
            ToolSpec: A new ToolSpec that shares callables and state with the original.
        """
        new_tool = ToolSpec()
        new_tool.tool = self.tool
        # Copy call interfaces correctly
        new_tool.exec = self.exec
        new_tool.aexec = self.aexec
        new_tool.call = self.call
        new_tool.acall = self.acall
        new_tool._short_description = self._short_description
        new_tool._description = self._description
        # Preserve examples and binds using deep copy to avoid shared mutation
        new_tool.examples = deepcopy(self.examples) if self.examples is not None else None
        new_tool.binds = deepcopy(self.binds)
        # State is propagated (shallow copy) to preserve shared resources like clients
        new_tool.state = self.state.copy()
        # Invalidate any cached binded tool so the clone computes its own cache
        new_tool._clear_cache()
        return new_tool

    def to_fastmcp(self) -> FastMCPTool:
        return self.binded.copy()

    def to_mcp(self) -> MCPTool:
        return self.binded.copy().to_mcp_tool()

    def to_jsonschema(self, **kwargs):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
                # Note: strict mode disabled due to compatibility issues with Optional parameters
                # "strict": True,
            }
            | kwargs,
        }

    @property
    def docstring(self):
        """\
        Generate and return a synthesized docstring from the tool specification.

        Returns:
            str: The synthesized docstring in Google style format.
        """
        if self._docstring is not None:
            return self._docstring
        self._docstring = synthesize_docstring(
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            # examples=self.examples,
        )
        return self._docstring

    @property
    def code(self):
        """\
        Generate a complete Python function definition with synthesized docstring.

        Returns:
            str: The complete function code including signature, docstring, and placeholder body.
        """
        if self._code is not None:
            return self._code
        self._code = synthesize_def(
            name=self.name,
            docstring=self.docstring,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            code="pass",
        )
        return self._code

    def to_func(self):
        """\
        Return a function that behaves like `ToolSpec.__call__` but has the same signature as the string produced by `ToolSpec.code`.

        Returns:
            Callable: The generated function.
        """
        try:
            func = code2func(code=self.code, func_name=self.name)
            return funcwrap(exec_func=self.__call__, sig_func=func)
        except Exception as e:
            raise RuntimeError(f"Failed to convert ToolSpec to function.\nCode:\n{self.code}\nError: {e}") from e

    def to_sig(self, **kwargs) -> Optional[Iterable[ExperienceType]]:
        """\
        Generate a tool function call signature with provided keyword arguments (and default values for missing arguments).

        Args:
            **kwargs: The keyword arguments to include in the function call signature.

        Returns:
            str: The function call signature as a string.
        """
        return synthesize_sig(
            name=self.name,
            input_schema=self.input_schema,
            arguments=kwargs,
        )

    def to_prompt(self, lang: Optional[str] = None):
        from ..utils.prompt import PM_AHVN, PromptSpec, setup_system_prompts

        prompt_spec = PM_AHVN.get("toolspec_prompt")
        if not isinstance(prompt_spec, PromptSpec):
            setup_system_prompts(force=False)
            prompt_spec = PM_AHVN.get("toolspec_prompt")
        if not isinstance(prompt_spec, PromptSpec):
            raise RuntimeError("Failed to load 'toolspec_prompt' from PM_AHVN.")
        docstring = self.docstring
        rendered = prompt_spec(
            sig=self.to_sig(),
            docstring=docstring.strip() if isinstance(docstring, str) else docstring,
            lang=lang,
        )
        return rendered

    def to_ukf(self, transport: Optional[Dict[str, Any]] = None, **updates):
        from ..ukf.templates.basic.tool import ToolUKFT

        return ToolUKFT.from_tool(self, transport=transport, **updates)

    def to_capsule(self, **kwargs) -> Dict[str, Any]:
        """\
        Create a Function Capsule from this ToolSpec.

        The capsule is a JSON-serializable dict storing multiple recovery
        strategies (source, cloudpickle, snapshot, runner) for portable
        persistence of the tool.

        Args:
            **kwargs: Forwarded to ``Capsule.from_func()`` (e.g. ``layers``,
                ``snapshot_modules``, ``transport``, ``dependencies``, ``identifier``).

        Returns:
            dict: The capsule dict.
        """
        from ..utils.capsule import Capsule

        return Capsule.from_func(self, **kwargs).to_dict()

    @classmethod
    def from_capsule(cls, capsule: Dict[str, Any], **kwargs) -> "ToolSpec":
        """\
        Restore a ToolSpec from a Function Capsule dict.

        Args:
            capsule (dict): The capsule dict (as returned by ``Capsule.from_func`` or
                ``to_capsule``).
            **kwargs: Forwarded to ``Capsule.to_tool()`` (e.g. ``layers``).

        Returns:
            ToolSpec: A fully executable tool specification.
        """
        from ..utils.capsule import Capsule

        return Capsule.from_dict(capsule).to_tool(**kwargs)
