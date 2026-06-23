"""\
Utility functions for KLBase configuration.

Pure functions that do not depend on KLBase instance state.
"""

__all__ = [
    "build_condition",
    "build_encoder",
    "infer_engine_type",
]

from typing import Any, Callable, Dict, Optional, Tuple, Union


def build_condition(condition_cfg: Optional[Dict[str, Any]]) -> Optional[Callable]:
    """\
    Build condition function from config.

    Args:
        condition_cfg: Dict with optional:
            - "type_include"/"type_exclude": Lists of UKF types to include/exclude
            - "tags": Dict mapping tag slots to values or operator specifications

    Returns:
        A callable that takes a KL and returns bool, or None if no condition.

    Examples:
        >>> cond = build_condition({"type_include": ["table", "column"]})
        >>> cond(kl)  # True if kl.type in ["table", "column"]

        >>> cond = build_condition({"tags": {"ANCHOR": False}})
        >>> cond(kl)  # True if kl.has_tag(slot="ANCHOR", value=False)
    """
    if condition_cfg is None:
        return None

    type_include = condition_cfg.get("type_include")
    type_exclude = condition_cfg.get("type_exclude")
    tags_include = condition_cfg.get("tags_include")
    tags_exclude = condition_cfg.get("tags_exclude")

    conditions = []

    # Type-based conditions
    if (type_include is not None) and (type_exclude is not None):
        conditions.append(lambda kl: (kl.type in type_include) and (kl.type not in type_exclude))
    elif type_include is not None:
        conditions.append(lambda kl: kl.type in type_include)
    elif type_exclude is not None:
        conditions.append(lambda kl: kl.type not in type_exclude)

    # Tag-based conditions
    if tags_include is not None:
        for key, val in tags_include.items():
            conditions.append(lambda kl: kl.has_tag(slot=key, value=val))
    if tags_exclude is not None:
        for key, val in tags_exclude.items():
            conditions.append(lambda kl: not kl.has_tag(slot=key, value=val))

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]

    # All conditions must be satisfied
    def combined_condition(kl, _conds=conditions):
        return all(cond(kl) for cond in _conds)

    return combined_condition


def build_encoder(encoder_cfg: Union[str, tuple, list, None]) -> Optional[Union[Callable, Tuple[Callable, ...]]]:
    """\
    Build encoder from config - eval lambda strings.

    Args:
        encoder_cfg: A lambda string, list/tuple of lambda strings, or None.

    Returns:
        Evaluated callable(s) or None.

    Examples:
        >>> enc = build_encoder("lambda kl: kl.name")
        >>> enc(kl)  # Returns kl.name
    """
    if encoder_cfg is None:
        return None
    if isinstance(encoder_cfg, str):
        return eval(encoder_cfg)
    if isinstance(encoder_cfg, (list, tuple)):
        return tuple(eval(e) if isinstance(e, str) else e for e in encoder_cfg)
    return encoder_cfg


def infer_engine_type(engine_cfg: Dict[str, Any]) -> str:
    """\
    Infer engine type from config keys.

    Args:
        engine_cfg: Engine configuration dict.

    Returns:
        Engine type string: "daac", "vector", or "facet".

    Logic:
        - If "path" in config -> "daac"
        - If "embedder" in config -> "vector"
        - Otherwise -> "facet"
    """
    if "path" in engine_cfg:
        return "daac"
    if "embedder" in engine_cfg:
        return "vector"
    return "facet"
