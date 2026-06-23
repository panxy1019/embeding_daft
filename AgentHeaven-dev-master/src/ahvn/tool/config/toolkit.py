from __future__ import annotations

__all__ = [
    "ConfigToolkitFactory",
]

from typing import Dict, Any, Optional, List, ClassVar

from ..toolkit import Toolkit, ToolkitFactory, register_factory
from .ops import (
    toolspec_factory_builtins_config_show,
    toolspec_factory_builtins_config_set,
    toolspec_factory_builtins_config_unset,
)


def _resolve_config_manager(package: str, config_manager: str):
    """\
    Dynamically import a ConfigManager instance from a package.

    Args:
        package (str): Python package to import from (e.g. "ahvn").
        config_manager (str): Attribute name of the ConfigManager (e.g. "CM_AHVN").

    Returns:
        ConfigManager: The resolved ConfigManager instance.

    Raises:
        ImportError: If the package cannot be imported.
        AttributeError: If the attribute is not found.
    """
    import importlib

    mod = importlib.import_module(package)
    cm = getattr(mod, config_manager)
    return cm


@register_factory
class ConfigToolkitFactory(ToolkitFactory):
    """\
    Factory for creating configuration management toolkits.

    Creates a Toolkit containing config tools (show, set, unset)
    bound to a ConfigManager instance, enabling agentic configuration workflows.

    Example:
        >>> from ahvn.tool import get_factory
        >>> factory = get_factory("config")
        >>> toolkit = factory.create("my-config", package="ahvn", config_manager="CM_AHVN")
        >>> toolkit.run("config_show", key="llm.default_preset")
    """

    name: ClassVar[str] = "config"
    description: ClassVar[str] = (
        "Configuration toolkit with show, set, and unset operations. "
        "Provides agentic access to ahvn configuration using dot-path notation "
        "(e.g. 'llm.default_preset', 'db.providers.sqlite.pragmas'). Supports "
        "array indexing, auto-typing of values, and scoped config layers."
    )

    @classmethod
    def args_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Python package to import ConfigManager from (e.g. 'ahvn').",
                    "default": "ahvn",
                },
                "config_manager": {
                    "type": "string",
                    "description": "ConfigManager attribute name in the package (e.g. 'CM_AHVN').",
                    "default": "CM_AHVN",
                },
            },
            "required": [],
        }

    @classmethod
    def create(
        cls,
        toolkit_name: str,
        package: str = "ahvn",
        config_manager: str = "CM_AHVN",
        **kwargs,
    ) -> Toolkit:
        """\
        Create a configuration toolkit.

        Args:
            toolkit_name (str): Unique name for this toolkit.
            package (str): Python package to import ConfigManager from. Default: "ahvn".
            config_manager (str): ConfigManager attribute name. Default: "CM_AHVN".
            **kwargs: Reserved for future use.

        Returns:
            Toolkit: A toolkit with config tools.
        """
        cm = _resolve_config_manager(package, config_manager)

        show_tool = toolspec_factory_builtins_config_show(
            package=package,
            config_manager=config_manager,
        )
        set_tool = toolspec_factory_builtins_config_set(
            package=package,
            config_manager=config_manager,
            scope=cm.base_scope,
        )
        unset_tool = toolspec_factory_builtins_config_unset(
            package=package,
            config_manager=config_manager,
            scope=cm.base_scope,
        )

        return Toolkit(
            name=toolkit_name,
            short_description=_build_config_short_description(package),
            description=_build_config_description(package, cm),
            tools={
                "config_show": show_tool,
                "config_set": set_tool,
                "config_unset": unset_tool,
            },
            instructions={
                "config_show": [
                    "Always show relevant configs before editing.",
                    "Use a specific subconfig key (e.g. `llm.presets`, `db.providers`) for focused output.",
                ],
                "config_set": [
                    "Always show the current value of a config key before setting a new value.",
                ],
                "config_unset": [
                    "Always show the current value of a config key before unsetting it.",
                ],
            },
        )

    @classmethod
    def _register_create_typer(cls, create_app, cli_ref):
        import typer

        @create_app.command(
            "config",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create config my-config\n"
                "  ahvn mcp create config ahvn -p ahvn -cm CM_AHVN\n"
                "  ahvn mcp run my-config.config_show key=llm.default_preset\n"
                "  ahvn mcp run my-config.config_set key=llm.default_preset value=fast"
            ),
        )
        def cmd(
            name: str = typer.Argument(..., help="Unique name for the toolkit (used as MCP server name)."),
            package: str = typer.Option("ahvn", "-p", "--package", help="Python package to import ConfigManager from."),
            config_manager: str = typer.Option("CM_AHVN", "-cm", "--config-manager", help="ConfigManager attribute name in the package."),
        ):
            cli_ref.do_create(
                "config",
                name,
                [
                    f"package={package}",
                    f"config_manager={config_manager}",
                ],
            )

    @classmethod
    def _register_create_click(cls, create_group, cli_ref):
        import click

        @create_group.command(
            "config",
            help=cls.description,
            epilog=(
                "Examples:\n"
                "  ahvn mcp create config my-config\n"
                "  ahvn mcp create config ahvn -p ahvn -cm CM_AHVN\n"
                "  ahvn mcp run my-config.config_show key=llm.default_preset\n"
                "  ahvn mcp run my-config.config_set key=llm.default_preset value=fast"
            ),
        )
        @click.argument("name")
        @click.option("-p", "--package", default="ahvn", help="Python package to import ConfigManager from.")
        @click.option("-cm", "--config-manager", default="CM_AHVN", help="ConfigManager attribute name in the package.")
        def cmd(name, package, config_manager):
            cli_ref.do_create(
                "config",
                name,
                [
                    f"package={package}",
                    f"config_manager={config_manager}",
                ],
            )


def _build_config_short_description(package: str) -> str:
    """\
    Build a concise one-line description for the config toolkit, suitable for frontmatter.

    Returns:
        str: A short description summarizing the toolkit's purpose and key features.
    """
    return f"Toolkit for managing hierarchical configuration for package `{package}` supporting dot-path keys, with show/set/unset operations."


def _build_config_description(package: str, cm=None) -> str:
    """\
    Build a detailed MCP server description for a config toolkit,
    explaining the dot-path key system and special features.

    Args:
        package (str): Python package name.
        cm: ConfigManager instance (optional). If provided, base-level keys are listed.
    """
    base_keys_section = ""
    if cm is not None:
        try:
            keys = list(cm.get().keys())
            if keys:
                keys_str = ", ".join(f"`{k}`" for k in keys)
                base_keys_section = f"\n\nBase-level config keys: {keys_str}."
        except Exception:
            pass

    return f"""\
Toolkit for managing hierarchical configuration for package `{package}`.

Keys use dot-path notation to navigate nested config (e.g. "llm.default_preset", "db.providers.sqlite.pragmas").

Key features:
- Dot-path access: "a.b.c" navigates into nested dicts.
- Array indexing: "a.b[0]" reads/writes the first element of a list, "a.b[-1]" reads/writes the last element.
- Array append: "a.b[-]" appends a new element to a list.
- _OVERWRITE_: Setting "_OVERWRITE_" to true in a scope layer replaces (instead of merges) the parent scope's config entirely.
- Auto-typing: Values passed to config_set are auto-typed (numbers, booleans, null, JSON objects/arrays are parsed automatically).

When using `config_show`, it is recommended to specify a subconfig (e.g., `llm.presets`, `llm.models`, `llm.providers`, `db`, etc.) to get partial config displays.{base_keys_section}\
"""
