from __future__ import annotations

__all__ = [
    "LLMToolkitFactory",
]

import re
from typing import Dict, Any, Optional, List, ClassVar

from ..toolkit import Toolkit, ToolkitFactory, register_factory
from .ask import toolspec_factory_builtins_ask


def _resolve_preset_patterns(patterns: List[str], available: List[str]) -> List[str]:
    """\
    Resolve a list of preset patterns (possibly containing shell-style globs
    like ``chat*``) against the available preset names from config.

    Literal values (no glob characters) are kept as-is.
    Patterns containing ``*`` or ``?`` are expanded against *available*.
    Duplicates are removed while preserving order.
    """
    result: List[str] = []
    seen: set = set()
    for pat in patterns:
        # If no glob characters, treat as a literal preset name
        if "*" not in pat and "?" not in pat:
            if pat not in seen:
                result.append(pat)
                seen.add(pat)
            continue
        # Convert shell glob to regex: * → .*, ? → .
        regex = pat.replace("*", ".*").replace("?", ".")
        try:
            rx = re.compile(f"^{regex}$")
        except re.error:
            if pat not in seen:
                result.append(pat)
                seen.add(pat)
            continue
        for name in available:
            if rx.match(name) and name not in seen:
                result.append(name)
                seen.add(name)
    return result


def _pick_default(presets: List[str], preferred: str) -> str:
    """\
    Pick a default preset: use *preferred* if it appears in *presets*,
    otherwise fall back to the first entry.
    """
    if preferred in presets:
        return preferred
    return presets[0] if presets else preferred


def _get_preset_descs(preset_names: List[str]) -> Dict[str, str]:
    """\
    Fetch the ``desc`` field for each preset from the LLM config.

    Returns a mapping ``{preset_name: description}``; presets without a
    ``desc`` key are omitted.
    """
    try:
        from ...utils.basic.config_utils import CM_AHVN

        presets_cfg = CM_AHVN.get("llm.presets", {})
    except Exception:
        return {}
    result: Dict[str, str] = {}
    for name in preset_names:
        info = presets_cfg.get(name, {})
        if isinstance(info, dict):
            desc = info.get("desc")
            if desc:
                result[name] = desc
    return result


@register_factory
class LLMToolkitFactory(ToolkitFactory):
    """\
    Factory for creating LLM toolkits.

    Creates a Toolkit containing the LLM ask (oracle) tool
    bound to a specific LLM preset configuration.  The tool can be
    constrained to a subset of presets.

    Example:
        >>> from ahvn.tool import get_factory
        >>> factory = get_factory("llm")
        >>> toolkit = factory.create(
        ...     "my-llm",
        ...     default_preset="sys",
        ...     ask_presets=["sys", "chat"],
        ... )
        >>> toolkit.run("ask", query="What is 2+2?")
    """

    name: ClassVar[str] = "llm"
    description: ClassVar[str] = (
        "LLM toolkit with the ask (oracle) tool. "
        "Creates an ask tool backed by LiteLLM presets defined in your ahvn config. "
        "You can constrain which presets are available and set a default. "
        "Preset names support glob/regex patterns for flexible matching."
    )

    @classmethod
    def args_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "default_preset": {
                    "type": "string",
                    "description": "Default preset for the LLM instance (e.g. 'sys', 'chat', 'fast').",
                },
                "ask_presets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Presets allowed for the ask (oracle) tool. If empty, all presets are allowed.",
                },
            },
            "required": [],
        }

    @classmethod
    def create(
        cls,
        toolkit_name: str,
        default_preset: Optional[str] = None,
        ask_presets: Optional[List[str]] = None,
    ) -> Toolkit:
        """\
        Create an LLM toolkit with the ask (oracle) tool.

        Args:
            toolkit_name (str): Unique name for this toolkit.
            default_preset (str, optional): Default LLM preset for the ask tool.
                Defaults to ``"chat"`` if available in ask_presets, else the first ask preset.
            ask_presets (List[str], optional): Presets for the ask (oracle) tool.
                Supports regex/glob patterns (e.g. ``"chat*"``).
                If None, all presets are allowed.

        Returns:
            Toolkit: A toolkit with LLM tools.
        """
        from ...utils.llm import LLM
        from ...utils.basic.config_utils import CM_AHVN

        # Normalize to lists (single values come as strings from _parse_kv_args)
        if isinstance(ask_presets, str):
            ask_presets = [ask_presets]

        # Resolve glob/regex patterns against available presets in config
        available_presets = list(CM_AHVN.get("llm.presets", {}).keys())
        if ask_presets:
            ask_presets = _resolve_preset_patterns(ask_presets, available_presets)

        # Smart defaults for the LLM preset used at construction time
        ask_default = default_preset
        if not ask_default and ask_presets:
            ask_default = _pick_default(ask_presets, "chat")

        llm = LLM(preset=ask_default, cache=False)

        tools: Dict[str, Any] = {}

        # Always create the ask tool
        ask_tool = toolspec_factory_builtins_ask(
            llm_instance=llm,
            allowed_presets=ask_presets,
        )
        tools["ask"] = ask_tool

        # Build description with preset descriptions from config
        descs = _get_preset_descs(ask_presets or [])

        short_description = (
            "The `llm` Toolkit provides a single `ask` interface to one-shot chat with an LLM. "
            "It allows selecting presets for different use cases, balancing intelligence and response time. "
            "Use it for one-shot subtasks to get quick answers without the overhead of context management."
        )

        desc_lines = [short_description]
        if ask_presets:
            desc_lines.append(f"\nSupported presets (default: {ask_default or 'auto'}):")
            for p in ask_presets:
                d = descs.get(p, "")
                desc_lines.append(f"    - {p}: {d}" if d else f"    - {p}")
        desc = "\n".join(desc_lines)

        # Persistence args
        persist_args: Dict[str, Any] = {}
        if default_preset:
            persist_args["default_preset"] = default_preset
        if ask_presets:
            persist_args["ask_presets"] = ask_presets

        return Toolkit(
            name=toolkit_name,
            short_description=short_description,
            description=desc,
            tools=tools,
        )

    @classmethod
    def _register_create_typer(cls, create_app, cli_ref):
        import typer

        @create_app.command(
            "llm",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create llm my-llm\n"
                "  ahvn mcp create llm my-llm --default-preset sys -a sys -a chat -a fast\n"
                '  ahvn mcp run my-llm.ask query="What is 2+2?"'
            ),
        )
        def cmd(
            name: str = typer.Argument(..., help="Unique name for the toolkit."),
            default_preset: Optional[str] = typer.Option(None, "--default-preset", "-d", help="Default LLM preset (e.g. 'sys', 'chat', 'fast')."),
            ask_presets: Optional[List[str]] = typer.Option(None, "--ask-presets", "-a", help="Presets for ask (oracle) tool (repeatable)."),
        ):
            args = []
            if default_preset:
                args.append(f"default_preset={default_preset}")
            for p in ask_presets or []:
                args.append(f"ask_presets={p}")
            cli_ref.do_create("llm", name, args)

    @classmethod
    def _register_create_click(cls, create_group, cli_ref):
        import click

        @create_group.command(
            "llm",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create llm my-llm\n"
                "  ahvn mcp create llm my-llm --default-preset sys -a sys -a chat -a fast\n"
                '  ahvn mcp run my-llm.ask query="What is 2+2?"'
            ),
        )
        @click.argument("name")
        @click.option("-d", "--default-preset", default=None, help="Default LLM preset.")
        @click.option("-a", "--ask-presets", multiple=True, help="Presets for ask tool (repeatable).")
        def cmd(name, default_preset, ask_presets):
            args = []
            if default_preset:
                args.append(f"default_preset={default_preset}")
            for p in ask_presets:
                args.append(f"ask_presets={p}")
            cli_ref.do_create("llm", name, args)
