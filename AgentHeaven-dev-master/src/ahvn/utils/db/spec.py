"""\
Database configuration specification and engine.

Mirrors the LLM pattern: ``DatabaseConfigSpec`` holds the canonical configuration,
``DatabaseConfigEngine`` drives the resolve → validate → materialize lifecycle.
"""

__all__ = [
    "DatabaseConfigSpec",
    "DatabaseConfigEngine",
    "DATABASE_CONFIG_ENGINE",
    "POOL_CLASS_MAP",
]

from ..basic.log_utils import get_logger

logger = get_logger(__name__)
from ..basic.config_spec import ConfigSpec, ConfigEngine
from ..basic.debug_utils import raise_mismatch

from pydantic import ConfigDict, Field
from typing import List, Dict, Any, Optional, Literal, Union
from copy import deepcopy
from urllib.parse import quote_plus
from sqlalchemy.pool import StaticPool, NullPool, QueuePool, SingletonThreadPool, AssertionPool

# String → pool class mapping for config-driven pool selection.
POOL_CLASS_MAP: Dict[str, type] = {
    "static": StaticPool,
    "null": NullPool,
    "queue": QueuePool,
    "singleton": SingletonThreadPool,
    "assertion": AssertionPool,
}


class DatabaseConfigSpec(ConfigSpec):
    """\
    Canonical database connection configuration.

    Frozen and idempotent: ``resolve(resolve(x)) == resolve(x)``.
    """

    provider: str
    dialect: str
    driver: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    db_schema: Optional[str] = Field(None, alias="schema")  # Aliased to avoid shadowing Pydantic's schema() classmethod
    superuser: Optional[Dict[str, Any]] = None  # Superuser connection params for bootstrapping
    pool: Dict[str, Any] = Field(default_factory=dict)  # Pool config (e.g. pool_size, pool_recycle)
    params: Dict[str, Any] = Field(default_factory=dict)  # Connection params as "?" in the URL
    pragmas: List[str] = Field(default_factory=list)  # Dialect-specific pragmas (e.g. for SQLite)
    args: Dict[str, Any] = Field(default_factory=dict)  # extra args on each execution

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        validate_assignment=True,
        populate_by_name=True,
    )

    def to_dict(self) -> Dict[str, Any]:
        """Export as a flat dict that can be re-input to ``resolve()``."""
        d: Dict[str, Any] = {
            "provider": self.provider,
            "dialect": self.dialect,
        }
        if self.driver is not None:
            d["driver"] = self.driver
        if self.host is not None:
            d["host"] = self.host
        if self.port is not None:
            d["port"] = self.port
        if self.username is not None:
            d["username"] = self.username
        if self.password is not None:
            d["password"] = self.password
        if self.database is not None:
            d["database"] = self.database
        if self.db_schema is not None:
            d["schema"] = self.db_schema
        if self.superuser:
            d["superuser"] = dict(self.superuser)
        if self.params:
            d["params"] = dict(self.params)
        if self.pragmas:
            d["pragmas"] = list(self.pragmas)
        if self.pool:
            d["pool"] = dict(self.pool)
        if self.args:
            d.update(self.args)
        return d


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def _build_url(spec_or_dict) -> str:
    """\
    Build a SQLAlchemy connection URL.

    Accepts a ``DatabaseConfigSpec`` or a plain dict with the same keys
    (``dialect``, ``driver``, ``username``, …).

    Format: ``<dialect>[+<driver>]://[<user>[:<pass>]@][<host>[:<port>]]/<database>[?<params>]``
    """
    if isinstance(spec_or_dict, dict):
        get = spec_or_dict.get
    else:
        get = lambda k, d=None: getattr(spec_or_dict, k, d)  # noqa: E731

    parts = [get("dialect", "")]
    driver = get("driver", None)
    if driver:
        parts.extend(["+", driver])
    parts.append("://")
    username = get("username", None)
    if username:
        parts.append(quote_plus(str(username)))
        password = get("password", None)
        if password:
            parts.extend([":", quote_plus(str(password))])
        parts.append("@")
    host = get("host", None)
    if host:
        parts.append(str(host))
        port = get("port", None)
        if port:
            parts.extend([":", str(port)])
    params = get("params", None)
    parts.append("/")
    database = get("database", None)
    dialect = str(get("dialect", "") or "").lower()
    if dialect == "oracle" and isinstance(params, dict) and params.get("service_name"):
        database = None
    if database:
        parts.append(str(database))
    if params:
        params_str = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
        parts.extend(["?", params_str])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------


def _resolve_templates(value, variables: Dict[str, str]):
    """\
    Resolve ``{database}`` and ``{username}`` template placeholders in
    strings, dicts, and lists recursively.
    """
    if isinstance(value, str):
        for k, v in variables.items():
            if v is not None:
                value = value.replace(f"{{{k}}}", str(v))
        return value
    if isinstance(value, dict):
        return {k: _resolve_templates(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(v, variables) for v in value]
    return value


# ---------------------------------------------------------------------------
# Pool strategy
# ---------------------------------------------------------------------------


def _pool_kwargs(spec: DatabaseConfigSpec) -> Dict[str, Any]:
    """\
    Compute SQLAlchemy pool keyword arguments from the spec's pool config.

    All pool parameters must come from the ``pool`` config section.
    No dialect-specific defaults are applied in code.

    Requires ``pool.pool_class`` to be set (mapped via ``POOL_CLASS_MAP``).

    Raises:
        ValueError: If ``pool_class`` is missing or unknown.
    """
    kwargs: Dict[str, Any] = {}
    pool_cfg = dict(spec.pool)  # shallow copy so we can pop
    pool_cfg.pop("engine_cache_key", None)

    # --- Pool class (required) ---
    pool_class_name = pool_cfg.pop("pool_class", None)
    if not pool_class_name:
        raise ValueError("pool.pool_class is required in database config. " f"Choose from: {', '.join(sorted(POOL_CLASS_MAP))}")
    pool_class_name = pool_class_name.lower()
    if pool_class_name not in POOL_CLASS_MAP:
        raise ValueError(f"Unknown pool_class '{pool_class_name}'. " f"Choose from: {', '.join(sorted(POOL_CLASS_MAP))}")
    kwargs["poolclass"] = POOL_CLASS_MAP[pool_class_name]

    # --- connect_args (optional, forwarded to DBAPI connect) ---
    connect_args = pool_cfg.pop("connect_args", None)
    if connect_args:
        kwargs["connect_args"] = dict(connect_args)

    # --- Forward pool tuning params ---
    if pool_cfg.get("pool_pre_ping"):
        kwargs["pool_pre_ping"] = True
    for key in ("pool_size", "max_overflow", "pool_timeout"):
        if key in pool_cfg:
            kwargs[key] = pool_cfg[key]
    if pool_cfg.get("pool_recycle", -1) > 0:
        kwargs["pool_recycle"] = pool_cfg["pool_recycle"]

    return kwargs


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DatabaseConfigEngine(ConfigEngine[DatabaseConfigSpec]):
    """\
    Configuration engine for database resources.

    Lifecycle:
    - ``resolve``:      deterministic; builds canonical ``DatabaseConfigSpec``. Idempotent.
    - ``validate``:     asserts minimal requirements (dialect present).
    - ``materialize``:  produces output in one of several modes.
    """

    def resolve(self, config: Dict[str, Any] | DatabaseConfigSpec, override: Optional[Dict[str, Any]] = None) -> DatabaseConfigSpec:
        """\
        Resolve a configuration dictionary (or existing spec) into a canonical
        ``DatabaseConfigSpec``.

        Priority (low → high): global defaults → provider config → user kwargs.

        Template variables ``{database}`` and ``{username}`` in ``host``, ``schema``,
        ``params``, and ``superuser`` are resolved after merging.

        Args:
            config: User configuration dict or existing spec.
            override: If provided, used as the ``db`` configuration block instead
                of reading from ``CM_AHVN.get("db", ...)``. This breaks the
                ``CM_AHVN`` dependency, enabling use in boot-time contexts where
                ``ConfigManager`` is not yet initialized (e.g. ``ConfigStorage``).
        """
        if isinstance(config, DatabaseConfigSpec):
            return config

        cfg = dict(config)

        if override is not None:
            db_cfg = override
        else:
            from ..basic.config_utils import CM_AHVN

            db_cfg = CM_AHVN.get("db", dict())
        providers_cfg = db_cfg.get("providers", dict())
        default_provider = db_cfg.get("default_provider", "sqlite")
        default_args = db_cfg.get("default_args", dict())

        # Pop user pool before main merge to enable deep-merge later
        user_pool = cfg.pop("pool", None)

        # --- Resolve provider ---
        provider = cfg.pop("provider", None) or default_provider
        if provider not in providers_cfg:
            raise_mismatch(providers_cfg, got=provider, name="database provider", mode="raise")
        provider_cfg = deepcopy(providers_cfg.get(provider, dict()))

        # --- Merge: defaults → provider → user kwargs ---
        merged: Dict[str, Any] = {}
        merged.update(deepcopy(default_args))
        merged.update(provider_cfg)
        merged.update(cfg)

        # --- Standardize (env/cmd interpolation + strip None) ---
        merged = self.standardize(merged)

        # --- Extract known fields ---
        dialect = merged.pop("dialect", None)
        if not dialect:
            raise ValueError("Database dialect is required.")
        driver = merged.pop("driver", None)
        host = merged.pop("host", None)
        port = merged.pop("port", None)
        if port is not None:
            port = int(port)
        username = merged.pop("username", None)
        password = merged.pop("password", None)
        database = merged.pop("database", None)
        schema = merged.pop("schema", None)
        superuser = merged.pop("superuser", None)
        params = merged.pop("params", {})
        pragmas = merged.pop("pragmas", [])
        pool = merged.pop("pool", {})
        # Deep-merge user pool overrides on top of provider pool
        if user_pool:
            pool.update(user_pool)
        preset = merged.pop("preset", None)
        # Everything else goes into args
        args = merged

        # --- Resolve {database} / {username} templates ---
        tpl_vars = {"database": database, "username": username}
        host = _resolve_templates(host, tpl_vars)
        schema = _resolve_templates(schema, tpl_vars)
        params = _resolve_templates(params, tpl_vars)
        superuser = _resolve_templates(superuser, tpl_vars)

        # --- Replace Database Files ---
        if (database is not None) and database.startswith("file:"):
            from ..basic.config_utils import CM_AHVN

            database = CM_AHVN.pj(database[5:], "", abs=True)

        return DatabaseConfigSpec(
            preset=preset,
            provider=provider,
            dialect=dialect,
            driver=driver,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            schema=schema,
            superuser=superuser,
            params=params,
            pragmas=pragmas,
            pool=pool,
            args=args,
        )

    def validate(self, config: DatabaseConfigSpec) -> bool:
        """\
        Check that the spec has enough information to create a SQLAlchemy engine.
        """
        if not config.dialect:
            raise ValueError("DatabaseConfigSpec.dialect is required.")
        if not config.provider:
            raise ValueError("DatabaseConfigSpec.provider is required.")
        return True

    def materialize(
        self,
        config: DatabaseConfigSpec,
        mode: Literal["default", "spec", "engine", "superuser", "pragmas", "key", "url"] = "default",
    ) -> Union[Dict[str, Any], List[str], str]:
        """\
        Materialize the resolved spec.

        Modes:
        - ``"spec"``:       Re-inputtable dict (idempotent round-trip).
        - ``"engine"`` / ``"default"``: ``{"url": ..., **pool_kwargs, **args}``
                            ready for ``sqlalchemy.create_engine(url, **rest)``.
        - ``"superuser"``:  ``{"url": ..., **pool_kwargs}`` using superuser
                            connection params for bootstrapping / maintenance.
                            Falls back to regular connection if no superuser configured.
        - ``"pragmas"``:    ``List[str]`` of pragma SQL statements to execute
                            on each new connection.
        - ``"key"``:        Hashable string cache key for the engine registry.
        - ``"url"``:        Just the connection URL string.
        """
        if mode == "spec":
            return config.to_dict()

        if mode == "pragmas":
            return list(config.pragmas)

        if mode == "url":
            return _build_url(config)

        if mode == "key":
            # Deterministic cache key: URL + sorted pool config
            url = _build_url(config)
            pool_parts = sorted((k, str(v)) for k, v in config.pool.items())
            return f"{url}|{'&'.join(f'{k}={v}' for k, v in pool_parts)}"

        if mode == "superuser":
            su = config.superuser
            if not su:
                # Fallback to regular engine kwargs
                return self.materialize(config, mode="engine")
            # Build a temporary spec-like dict for URL construction.
            # Falls back to main config values for fields not in the superuser dict.
            su_params = su.get("params") or {}
            if not isinstance(su_params, dict):
                su_params = dict(su_params)
            merged_params = dict(config.params)
            merged_params.update(su_params)
            su_dict = {
                "dialect": su.get("dialect", config.dialect),
                "driver": su.get("driver", config.driver),
                "host": su.get("host", config.host),
                "port": su.get("port", config.port),
                "username": su.get("username", config.username),
                "password": su.get("password", config.password),
                "database": su.get("database", config.database),
                "params": merged_params,
            }
            url = _build_url(su_dict)
            return {"url": url, "isolation_level": "AUTOCOMMIT"}

        # mode == "engine" / "default"
        url = _build_url(config)
        pool_kw = _pool_kwargs(config)
        engine_args = deepcopy(config.args)
        # Runtime DB utilities (e.g. SQL healing knobs) should not be passed to SQLAlchemy engine ctor.
        engine_args.pop("sql_healing", None)
        engine_kw: Dict[str, Any] = {"url": url, **pool_kw, **engine_args}
        return engine_kw


DATABASE_CONFIG_ENGINE = DatabaseConfigEngine()
