__all__ = [
    "ask_llm",
    "toolspec_factory_builtins_ask",
]

from typing import Optional, List

from ..base import ToolSpec
import functools


def ask_llm(
    llm_instance,
    query: str,
    preset: Optional[str] = None,
) -> str:
    """\
    Ask a question to an LLM and get a text response.

    Args:
        llm_instance: The LLM instance to use.
        query (str): The question or prompt to send to the LLM.
        preset (str, optional): Override preset for this call (must be in allowed list).

    Returns:
        str: The LLM's text response.
    """
    kwargs = {}
    if preset:
        # Create a new LLM with the requested preset
        from ...utils.llm import LLM

        llm_instance = LLM(preset=preset, cache=False)

    response = llm_instance.oracle(messages=query, include=["text"], reduce=True, **kwargs)
    return response


def toolspec_factory_builtins_ask(
    llm_instance,
    allowed_presets: Optional[List[str]] = None,
    name: str = "ask",
) -> ToolSpec:
    """\
    Create a ToolSpec for asking questions to an LLM.

    The ``preset`` parameter of the resulting tool is constrained to only
    the presets listed in *allowed_presets* (via JSON schema ``enum``).

    Args:
        llm_instance: The LLM instance to bind.
        allowed_presets (List[str], optional): Allowed preset names. If provided,
            the preset param will be an enum.
        name (str): Tool name. Defaults to "ask".

    Returns:
        ToolSpec: A ToolSpec with the LLM instance bound.
    """

    @functools.wraps(ask_llm)
    def ask_llm_wrapper(llm_instance, query: str, preset: Optional[str] = None) -> str:
        """\
        Ask a question to an LLM and get a text response.

        Args:
            llm_instance: The LLM instance to use.
            query (str): The question or prompt to send to the LLM.
            preset (str, optional): Override preset for this call.

        Returns:
            str: The LLM's text response.
        """
        if preset and allowed_presets and preset not in allowed_presets:
            return f"Error: preset '{preset}' is not allowed. Choose from: {allowed_presets}"
        return ask_llm(llm_instance, query, preset)

    tool_spec = ToolSpec.from_func(
        func=ask_llm_wrapper,
        parse_docstring=True,
        description="Ask a question to an LLM and get a text response.",
        name=name,
    )

    # Bind the llm_instance parameter
    tool_spec.bind(param="llm_instance", state_key=None, default=llm_instance)

    # Constrain preset enum in the schema if allowed_presets provided
    if allowed_presets:
        from copy import deepcopy

        schema = deepcopy(tool_spec.input_schema)
        props = schema.get("properties", {})
        if "preset" in props:
            props["preset"]["enum"] = allowed_presets
            tool_spec._params = schema

    return tool_spec
