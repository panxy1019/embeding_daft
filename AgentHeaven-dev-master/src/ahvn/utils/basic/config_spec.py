"""\
Formal configuration specification classes (Pydantic) and the ConfigEngine ABC.

This module defines the canonical configuration objects for Database and
VectorDatabase subsystems, as well as the ``ConfigEngine`` abstract base class
that drives the resolve → validate → materialize lifecycle.

LLM-specific specs live in ``ahvn.utils.llm.spec``.

Environment / command interpolation is handled via OmegaConf custom resolvers
so that ``${oc.env:VAR}`` and ``${cmd:whoami}`` work transparently inside any
string value.
"""

__all__ = [
    "ConfigSpec",
    "DatabaseSpec",
    "VectorDatabaseSpec",
    "ConfigEngine",
    "resolve_interpolations",
]

import os
from typing import Dict, Any, Optional, List, TypeVar, Generic
from abc import ABC, abstractmethod
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from .log_utils import get_logger
from .cmd_utils import cmd

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# OmegaConf custom resolvers for environment variable and command interpolation
# ---------------------------------------------------------------------------


def _setup_omegaconf_resolvers():
    """\
    Register custom OmegaConf resolvers once:

    - ``${env:VAR_NAME}``            – reads os.environ[VAR_NAME]
    - ``${env:VAR_NAME,default}``    – reads os.environ.get(VAR_NAME, default)
    - ``${cmd:shell_command}``       – runs a shell command, returns stripped stdout
    """
    from omegaconf import OmegaConf

    if not OmegaConf.has_resolver("env"):

        def _env_resolver(var: str, default: str = ""):
            return os.environ.get(var, default)

        OmegaConf.register_new_resolver("env", _env_resolver)

    if not OmegaConf.has_resolver("cmd"):

        def _cmd_resolver(command: str):
            result = cmd(command, include="stdout")
            return result.strip() if result else ""

        OmegaConf.register_new_resolver("cmd", _cmd_resolver)


def resolve_interpolations(d: Dict[str, Any]) -> Dict[str, Any]:
    """\
    Resolve all interpolation patterns in a flat dictionary:

    1. OmegaConf-native:  ``${env:VAR}`` / ``${cmd:whoami}``
    2. Legacy angle-bracket: ``<VAR>``  ➜ converted to ``${env:VAR}``
    3. Legacy dollar-brace:  ``${command}`` (without ``:``) ➜ converted to ``${cmd:command}``

    After conversion the dict is loaded into an OmegaConf DictConfig and fully
    resolved, so nested references work too.

    Args:
        d: A dictionary whose string values may contain interpolation patterns.

    Returns:
        A new dictionary with all interpolations resolved.
    """
    from omegaconf import OmegaConf, DictConfig

    _setup_omegaconf_resolvers()

    converted: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            # Legacy <ENV_VAR> → ${env:ENV_VAR}
            if v.startswith("<") and v.endswith(">") and ":" not in v:
                v = f"${{env:{v[1:-1]}}}"
            # Legacy ${command} (no colon inside braces) → ${cmd:command}
            elif v.startswith("${") and v.endswith("}") and ":" not in v[2:-1]:
                v = f"${{cmd:{v[2:-1]}}}"
        converted[k] = v

    # Use OmegaConf for resolution – struct flag off so unknown keys pass through
    try:
        cfg: DictConfig = OmegaConf.create(converted)
        OmegaConf.resolve(cfg)
        resolved = OmegaConf.to_container(cfg, resolve=True, throw_on_missing=False)
    except Exception:
        # Graceful fallback: return the converted dict as-is
        logger.debug("OmegaConf resolution failed, returning unresolved dict")
        resolved = converted

    return resolved


class ConfigSpec(BaseModel):
    """\
    Base class for all configuration specifications.

    All Specs are **frozen** (immutable after creation) and accept extra fields
    so that forward-compatible keys are preserved transparently.
    """

    preset: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        validate_assignment=True,
    )


class DatabaseSpec(ConfigSpec):
    """\
    Canonical database connection configuration.
    """

    dialect: str
    driver: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        validate_assignment=True,
    )


class VectorDatabaseSpec(ConfigSpec):
    """\
    Canonical vector-database configuration.
    """

    provider: str
    collection: str
    params: Dict[str, Any] = Field(default_factory=dict)
    args: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        validate_assignment=True,
    )


# ---------------------------------------------------------------------------
# ConfigEngine ABC
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=ConfigSpec)


class ConfigEngine(Generic[T], ABC):
    """\
    Abstract configuration engine that drives the resolve → validate → materialize lifecycle.

    Subclasses implement the three abstract methods for their specific resource type
    (LLM, Database, VectorDatabase, …).
    """

    # ------------------------------------------------------------------
    # Protected helpers shared by all engines
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_none(d: Dict[str, Any]) -> Dict[str, Any]:
        """Remove keys whose value is ``None``."""
        return {k: v for k, v in d.items() if v is not None}

    @staticmethod
    def _resolve_interpolations(d: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve ``<ENV>``, ``${cmd}``, and OmegaConf interpolations."""
        return resolve_interpolations(d)

    @staticmethod
    def standardize(d: Dict[str, Any]) -> Dict[str, Any]:
        """\
        Standardize keys and values in the config dict as needed.

        This is a no-op by default but can be overridden by subclasses to implement
        any necessary normalization (e.g. alias resolution, type coercion, …).
        """
        d = ConfigEngine._resolve_interpolations(d)
        d = ConfigEngine._strip_none(d)
        return d

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def resolve(self, config: Dict[str, Any] | T, override: Optional[Dict[str, Any]] = None) -> T:
        """\
        Resolve a configuration dictionary or object into a canonical ``ConfigSpec``.

        This process must be **idempotent**: ``resolve(resolve(x)) == resolve(x)``
        for any valid input *x*.

        Args:
            config: User configuration dict or already-resolved spec.
            override: If provided, used **instead** of the global
                ``CM_AHVN.get(...)`` config block.  This allows callers
                to supply a complete configuration without triggering
                ``ConfigManager`` initialisation (useful for boot-time
                contexts or testing).
        """
        ...

    @abstractmethod
    def validate(self, config: T) -> bool:
        """\
        Return ``True`` when *config* can be materialised, otherwise raise.
        """
        ...

    @abstractmethod
    def materialize(self, config: T, mode: str = "default") -> Dict[str, Any]:
        """\
        Turn a resolved spec into an argument dictionary for the underlying system
        (e.g. LiteLLM kwargs, SQLAlchemy ``create_engine`` kwargs, …).
        """
        ...
