__all__ = [
    "config_show",
    "config_set",
    "config_unset",
    "toolspec_factory_builtins_config_show",
    "toolspec_factory_builtins_config_set",
    "toolspec_factory_builtins_config_unset",
]

from typing import Optional

from ..base import ToolSpec

_DEFAULT_CONFIG_MANAGER = "CM_AHVN"


def _resolve_config_manager(package: str, config_manager: str):
    import importlib

    mod = importlib.import_module(package)
    return getattr(mod, config_manager)


def config_show(
    package,
    config_manager: Optional[str] = None,
    key: Optional[str] = None,
) -> str:
    """\
    Show configuration values.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").
        key (str, optional): Dot-path key to look up (e.g. 'llm.default_preset'). If omitted, shows all config.

    Returns:
        str: The configuration value(s) as a YAML-formatted string.
    """
    import yaml

    cm = None
    config_key = key

    if (not isinstance(package, str)) and hasattr(package, "get"):
        cm = package
        if config_key is None and isinstance(config_manager, str):
            config_key = config_manager
    else:
        package_name = str(package)
        cm_name = config_manager or _DEFAULT_CONFIG_MANAGER
        if config_key is None and isinstance(config_manager, str):
            try:
                cm = _resolve_config_manager(package_name, cm_name)
            except Exception:
                config_key = config_manager
                cm = _resolve_config_manager(package_name, _DEFAULT_CONFIG_MANAGER)
        else:
            cm = _resolve_config_manager(package_name, cm_name)

    data = cm.get(config_key)
    if isinstance(data, dict):
        return yaml.dump(data, default_flow_style=False, allow_unicode=True).strip()
    return str(data)


def config_set(
    package,
    config_manager: Optional[str],
    key: Optional[str],
    value: Optional[str],
    scope: Optional[str] = None,
) -> str:
    """\
    Set a configuration value.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").
        key (str): Dot-path key to set (e.g. 'llm.default_preset').
        value (str): Value to set. JSON strings are parsed automatically.
        scope (str, optional): Scope to write to. If omitted, writes to the package's base scope.

    Returns:
        str: Confirmation message.
    """
    from ...utils.basic.type_utils import autotype

    cm = None
    set_key = key
    set_value = value
    set_scope = scope

    if (not isinstance(package, str)) and hasattr(package, "set"):
        cm = package
        set_key = config_manager
        set_value = key
        if set_scope is None:
            set_scope = value
    else:
        package_name = str(package)
        cm_name = config_manager or _DEFAULT_CONFIG_MANAGER
        try:
            cm = _resolve_config_manager(package_name, cm_name)
        except Exception:
            cm = _resolve_config_manager(package_name, _DEFAULT_CONFIG_MANAGER)
            set_key = config_manager
            set_value = key
            if set_scope is None:
                set_scope = value

    typed_value = autotype(set_value)
    cm.set(set_key, typed_value, scope=set_scope or cm.base_scope)
    return f"Set '{set_key}' = {typed_value!r}"


def config_unset(
    package,
    config_manager: Optional[str],
    key: Optional[str],
    scope: Optional[str] = None,
) -> str:
    """\
    Remove a configuration key.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").
        key (str): Dot-path key to remove (e.g. 'llm.default_preset').
        scope (str, optional): Scope to modify. If omitted, modifies the package's base scope.

    Returns:
        str: Confirmation message.
    """
    cm = None
    unset_key = key
    unset_scope = scope

    if (not isinstance(package, str)) and hasattr(package, "unset"):
        cm = package
        unset_key = config_manager
        if unset_scope is None:
            unset_scope = key
    else:
        package_name = str(package)
        cm_name = config_manager or _DEFAULT_CONFIG_MANAGER
        try:
            cm = _resolve_config_manager(package_name, cm_name)
        except Exception:
            cm = _resolve_config_manager(package_name, _DEFAULT_CONFIG_MANAGER)
            unset_key = config_manager
            if unset_scope is None:
                unset_scope = key

    cm.unset(unset_key, scope=unset_scope or cm.base_scope)
    return f"Unset '{unset_key}'"


def toolspec_factory_builtins_config_show(
    package: str = "ahvn",
    config_manager: str = "CM_AHVN",
) -> ToolSpec:
    """\
    Create a ToolSpec for showing configuration values.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").

    Returns:
        ToolSpec: A ToolSpec with package and ConfigManager path bound.
    """

    def _config_show(
        package: str,
        config_manager: str,
        key: Optional[str] = None,
    ) -> str:
        """\
        Show configuration values.

        Args:
            package (str): Python package to import ConfigManager from.
            config_manager (str): ConfigManager attribute name.
            key (str, optional): Dot-path key to look up (e.g. 'llm.default_preset'). If omitted, shows all config.

        Returns:
            str: The configuration value(s) as YAML.
        """
        return config_show(package, config_manager, key)

    tool_spec = ToolSpec.from_func(
        func=_config_show,
        parse_docstring=True,
        description="Show configuration values. Provide a dot-path key to query a specific value, or omit for all config.",
        name="config_show",
    )
    tool_spec.bind(param="package", state_key=None, default=package)
    tool_spec.bind(param="config_manager", state_key=None, default=config_manager)
    return tool_spec


def toolspec_factory_builtins_config_set(
    package: str = "ahvn",
    config_manager: str = "CM_AHVN",
    scope: Optional[str] = None,
) -> ToolSpec:
    """\
    Create a ToolSpec for setting configuration values.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").
        scope (str, optional): Fixed scope for this tool. If provided, all
            writes go to this scope; the parameter is hidden from agents.

    Returns:
        ToolSpec: A ToolSpec with package and ConfigManager path bound.
    """

    def _config_set(
        package: str,
        config_manager: str,
        key: str,
        value: str,
        scope: Optional[str] = None,
    ) -> str:
        """\
        Set a configuration value.

        Args:
            package (str): Python package to import ConfigManager from.
            config_manager (str): ConfigManager attribute name.
            key (str): Dot-path key to set (e.g. 'llm.default_preset').
            value (str): Value to set. JSON strings are parsed automatically.
            scope (str, optional): Scope to write to.

        Returns:
            str: Confirmation message.
        """
        return config_set(package, config_manager, key, value, scope=scope)

    tool_spec = ToolSpec.from_func(
        func=_config_set,
        parse_docstring=True,
        description="Set a configuration value by dot-path key.",
        name="config_set",
    )
    tool_spec.bind(param="package", state_key=None, default=package)
    tool_spec.bind(param="config_manager", state_key=None, default=config_manager)
    tool_spec.bind(param="scope", state_key=None, default=scope)
    return tool_spec


def toolspec_factory_builtins_config_unset(
    package: str = "ahvn",
    config_manager: str = "CM_AHVN",
    scope: Optional[str] = None,
) -> ToolSpec:
    """\
    Create a ToolSpec for removing configuration keys.

    Args:
        package (str): Python package to import ConfigManager from (e.g. "ahvn").
        config_manager (str): ConfigManager attribute name (e.g. "CM_AHVN").
        scope (str, optional): Fixed scope for this tool. If provided, all
            modifications go to this scope; the parameter is hidden from agents.

    Returns:
        ToolSpec: A ToolSpec with package and ConfigManager path bound.
    """

    def _config_unset(
        package: str,
        config_manager: str,
        key: str,
        scope: Optional[str] = None,
    ) -> str:
        """\
        Remove a configuration key.

        Args:
            package (str): Python package to import ConfigManager from.
            config_manager (str): ConfigManager attribute name.
            key (str): Dot-path key to remove (e.g. 'llm.default_preset').
            scope (str, optional): Scope to modify.

        Returns:
            str: Confirmation message.
        """
        return config_unset(package, config_manager, key, scope=scope)

    tool_spec = ToolSpec.from_func(
        func=_config_unset,
        parse_docstring=True,
        description="Remove a configuration key by dot-path.",
        name="config_unset",
    )
    tool_spec.bind(param="package", state_key=None, default=package)
    tool_spec.bind(param="config_manager", state_key=None, default=config_manager)
    tool_spec.bind(param="scope", state_key=None, default=scope)
    return tool_spec
