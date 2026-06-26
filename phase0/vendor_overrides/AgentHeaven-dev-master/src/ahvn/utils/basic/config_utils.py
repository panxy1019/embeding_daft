__all__ = [
    "dmerge",
    "dget",
    "dset",
    "dunset",
    "dsetdef",
    "dflat",
    "dunflat",
    "ConfigManager",
    "CM_AHVN",
    "VersionConflictError",
    "encrypt_display",
]

from .log_utils import get_logger, encrypt_display

logger = get_logger(__name__)
from .misc_utils import unique
from .path_utils import pj

from typing import Any, Union, Dict, List, Optional, Generator, Iterable, Callable, Tuple

__rnd_sep = "#@#@#"


def _split_key_path(key_path: str) -> List[str]:
    """\
    Split a key path string into a list of keys, handling escaped dots.
    """
    return [key.replace(__rnd_sep, ".") for key in key_path.replace("\\.", __rnd_sep).split(".") if key]


def dmerge(iterable: Iterable[Dict[str, Any]], start: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """\
    Merge multiple dictionaries into a single dictionary, with later dictionaries overwriting earlier ones. Nested dictionaries are merged recursively while all other non-dictionary values are overwritten.

    Warning:
        The merging of dictionaries is not order-preserving. The order of keys in the resulting dictionary may not match the order of keys in the input dictionaries.

    Args:
        iterable (Iterable[Dict[str, Any]]): An iterable of dictionaries to merge.
        start (Optional[Dict[str, Any]]): An optional starting dictionary to merge into.

    Returns:
        Dict[str, Any]: The merged dictionary.

    Examples:
        >>> d1 = {'a': 1, 'b': {'c': 2, 'f': 5}}
        >>> d2 = {'b': {'d': 3, 'f': 0}, 'e': 4}
        >>> dmerge([d1, d2])
        {'a': 1, 'b': {'c': 2, 'f': 0, 'd': 3}, 'e': 4}
        >>> dmerge([d2, d1])
        {'b': {'d': 3, 'f': 5, 'c': 2}, 'e': 4, 'a': 1}
        >>> dmerge([d1, d2], start={'a': 0, 'g': 6})
        {'a': 1, 'g': 6, 'b': {'c': 2, 'f': 0, 'd': 3}, 'e': 4}
    """
    if start is None:
        start = dict()
    else:
        start = deepcopy(start)
    for d in iterable:
        if not d:
            continue
        if "_OVERWRITE_" in d and d["_OVERWRITE_"]:
            start = deepcopy({k: v for k, v in d.items() if k != "_OVERWRITE_"})
            continue
        for k, v in d.items():
            if (k in start) and isinstance(v, dict):
                start[k] = dmerge([v], start=start[k])
            else:
                start[k] = v
    return start


def dget(d: Dict[str, Any], key_path: Optional[str] = None, default: Optional[Any] = None) -> Any:
    """\
    Get a value from a dictionary using a dot-separated key path. If the key path does not exist, return the default value.

    Args:
        d (Dict[str, Any]): The dictionary to search.
        key_path (Optional[str]): The dot-separated key path to the value.
        default (Optional[Any]): The default value to return if the key path does not exist.

    Returns:
        Any: The value at the specified key path or the default value if not found.

    Examples:
        >>> dget({'a': {'b': {'c': 42}}}, 'a.b.c')
        42
        >>> dget({'a': {'b': {'c': 42}}}, 'a.b.d', default='not found')
        'not found'
        >>> dget({'a': {'b': {'c': [1, 2, 3]}}}, 'a.b.c[1]')
        2
    """
    if key_path is None:
        return d
    keys = _split_key_path(key_path)
    for key in keys:
        if d is None:
            return default
        if key.endswith("]"):
            k, idx = key[:-1].rsplit("[", 1)
            idx = int(idx)
            if (k not in d) or (not isinstance(d[k], list)) or (idx >= len(d[k])) or (idx < -len(d[k])):
                return default
            d = d[k][idx]
        elif key not in d:
            return default
        else:
            d = d[key]
    return d


def dset(d: Dict[str, Any], key_path: str, value: Optional[Any] = None) -> bool:
    """\
    Set a value in a dictionary using a dot-separated key path. If the key path does not exist, it will be created.

    Args:
        d (Dict[str, Any]): The dictionary to modify.
        key_path (str): The dot-separated key path to the value.
        value (Optional[Any]): The value to set at the specified key path.

    Returns:
        bool: True if the value was set successfully, False if the key path is invalid.

    Examples:
        >>> d = {}
        >>> dset(d, 'a.b.c', 42)
        True
        >>> d
        {'a': {'b': {'c': 42}}}
    """
    if key_path is None:
        if not isinstance(value, dict):
            return False
        d.update(value)
        return True
    keys = _split_key_path(key_path)
    for key in keys[:-1]:
        if key.endswith("]"):
            k, idx = key[:-1].rsplit("[", 1)
            if k not in d:
                d[k] = list()
            if not isinstance(d[k], list):
                return False
            if str(idx.strip()) == "-":  # special syntax for appending to list
                d[k].append(dict())
                d = d[k][-1]
                continue
            idx = int(idx)
            if (idx >= len(d[k])) or (idx < -len(d[k])):
                return False
            d = d[k][idx]
        elif key not in d:
            d[key] = dict()
            d = d[key]
        else:
            d = d[key]
    last_key = keys[-1]
    if last_key.endswith("]"):
        k, idx = last_key[:-1].rsplit("[", 1)
        if k not in d:
            d[k] = list()
        if not isinstance(d[k], list):
            return False
        if str(idx.strip()) == "-":  # special syntax for appending to list
            d[k].append(value)
            return True
        idx = int(idx)
        if (not isinstance(d[k], list)) or (idx < -len(d[k])):
            return False
        if idx >= len(d[k]):
            d[k].extend([None] * (idx - len(d[k]) + 1))
        d[k][idx] = value
    else:
        d[last_key] = value
    return True


def dunset(d: Dict[str, Any], key_path: str) -> bool:
    """\
    Unset a value in a dictionary using a dot-separated key path. If the key path does not exist, it will be ignored.

    Args:
        d (Dict[str, Any]): The dictionary to modify.
        key_path (str): The dot-separated key path to the value to unset.

    Returns:
        bool: True if the value was unset successfully, False if the key path is invalid.

    Examples:
        >>> d = {'a': {'b': {'c': 42}}}
        >>> dunset(d, 'a.b.c')
        True
        >>> d
        {'a': {'b': {}}}
    """
    if key_path is None:
        d.clear()
        return True
    keys = _split_key_path(key_path)
    for key in keys[:-1]:
        if key.endswith("]"):
            k, idx = key[:-1].rsplit("[", 1)
            idx = int(idx)
            if (k not in d) or (not isinstance(d[k], list)) or (idx >= len(d[k])) or (idx < -len(d[k])):
                return False
            d = d[k][idx]
        elif key not in d:
            return False
        else:
            d = d[key]
    last_key = keys[-1]
    if last_key.endswith("]"):
        k, idx = last_key[:-1].rsplit("[", 1)
        idx = int(idx)
        if (k not in d) or (not isinstance(d[k], list)) or (idx >= len(d[k])) or (idx < -len(d[k])):
            return False
        if idx < 0:
            idx += len(d[k])
        del d[k][idx]
    else:
        if last_key in d:
            del d[last_key]
        else:
            return False
    return True


def dsetdef(d: Dict[str, Any], key_path: str, default: Optional[Any] = None) -> bool:
    """\
    Set a default value in a dictionary using a dot-separated key path if the key path does not exist.

    Notice that if key_path exists but its value is None, the default value will also be set.

    Args:
        d (Dict[str, Any]): The dictionary to modify.
        key_path (str): The dot-separated key path to the value.
        default (Optional[Any]): The default value to set at the specified key path if it does not exist.

    Returns:
        bool: True if the default value was set successfully, False if the key path is invalid or already exists.

    Examples:
        >>> d = {}
        >>> dsetdef(d, 'a.b.c', 42)
        True
        >>> d
        {'a': {'b': {'c': 42}}}
        >>> dsetdef(d, 'a.b.c', 100)
        False
        >>> d
        {'a': {'b': {'c': 42}}}
    """
    if dget(d, key_path, default=None) is not None:
        return False
    return dset(d, key_path, default)


def dflat(d: Dict[str, Any], prefix: str = "", enum: bool = False) -> Generator[str, None, None]:
    """\
    Flatten a nested dictionary into a flat dictionary with dot-separated keys.

    Args:
        d (Dict[str, Any]): The dictionary to flatten.
        prefix (str): The prefix to prepend to the keys. Defaults to an empty string.
        enum (bool): If True, apart from leaf nodes, also include intermediate nodes in the flattened output. Defaults to False.

    Yields:
        Generator[str, None, None]: A generator yielding key-value pairs in the flattened format.

    Examples:
        >>> dict(dflat({'a': {'b': {'c': 42, 'd': [1, 2, 3]}}, 'e': 5}))
        {'a.b.c': 42, 'a.b.d[0]': 1, 'a.b.d[1]': 2, 'a.b.d[2]': 3, 'e': 5}
        >>> dict(dflat({'a': {'b': {'c': 42, 'd': [1, 2, 3]}}, 'e': 5}, enum=True))
        {'a': {'b': {'c': 42, 'd': [1, 2, 3]}}, 'a.b': {'c': 42, 'd': [1, 2, 3]}, 'a.b.c': 42, 'a.b.d': [1, 2, 3], 'a.b.d[0]': 1, 'a.b.d[1]': 2, 'a.b.d[2]': 3, 'e': 5}
    """

    def _dlist(d: Dict[str, Any], prefix: str = "", enum: bool = False) -> Generator[str, None, None]:
        for k, v in d.items():
            ck = k.replace(".", "\\.")
            if enum:
                yield ((".".join([prefix, ck]) if prefix else ck), v)
            if isinstance(v, dict):
                yield from _dlist(v, prefix=".".join([prefix, ck]) if prefix else ck, enum=enum)
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    yield from _dlist({f"{ck}[{i}]": item}, prefix=prefix, enum=enum)
            elif not enum:
                yield ((".".join([prefix, ck]) if prefix else ck), v)

    yield from _dlist(d, prefix=prefix, enum=enum)


def dunflat(d: Dict[str, Any]) -> Dict[str, Any]:
    """\
    Unflatten a flat dictionary with dot-separated keys into a nested dictionary.

    Args:
        d (Dict[str, Any]): The flat dictionary to unflatten.

    Returns:
        Dict[str, Any]: The nested dictionary.

    Examples:
        >>> d = {'a.b.c': 42}
        >>> dunflat(d)
        {'a': {'b': {'c': 42}}}
    """
    merged = dict()
    for k, v in d.items():
        dset(merged, k, v)
    return merged


# Copy functions from file_utils.py to avoid circular imports
import os
import shutil


def _touch_dir(path: str, clear: bool = False) -> str:
    path = os.path.abspath(path)
    if clear and os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)
    return path


def _exists_file(path: str) -> bool:
    path = os.path.abspath(path)
    return os.path.exists(path) and os.path.isfile(path)


# Copy functions from serialize_utils.py to avoid circular imports
def _load_yaml(path: str) -> Dict[str, Any]:
    import yaml

    path = os.path.abspath(path)
    if not _exists_file(path):
        return dict()
    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
        return yaml.safe_load(fp)


# Refactor Config
import datetime
import threading
from collections import OrderedDict
from copy import deepcopy
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import inspect
import types

_SCOPE_STACKS: ContextVar[Dict[str, List[str]]] = ContextVar("_SCOPE_STACKS", default=dict())

from sqlalchemy import Table, Column, Integer, String, DateTime, JSON, MetaData, Index
from sqlalchemy import select, or_, func

metadata = MetaData()

configs_table = Table(
    "configs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("package", String(191), nullable=False),
    Column("scope", String(191), nullable=False),
    Column("version", Integer, nullable=False),
    Column("package_version", String(191), nullable=True),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
    Index("ix_pkg_scope", "package", "scope"),
    Index("ix_pkg_scope_ver", "package", "scope", "version", unique=True),
)

compatibility_table = Table(
    "compatibility",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("package", String(191), nullable=False),
    Column("package_version", String(191), nullable=False),
    Column("version_order", Integer, nullable=False),
    Column("compatible_order", Integer, nullable=True),
    Index("ix_compat_pkg_ver", "package", "package_version", unique=True),
    Index("ix_compat_pkg_order", "package", "version_order"),
)


class VersionConflictError(Exception):
    """Raised when an optimistic-lock version conflict is detected."""

    pass


class ConfigSnapshot(dict):
    def __init__(self, data, id, package, scope, version, created_at, package_version=None):
        super().__init__(data)
        self.id = id
        self.package = package
        self.scope = scope
        self.version = version
        self.created_at = created_at
        self.package_version = package_version


class ConfigStorage:
    # Resolved lazily via importlib.resources (cached by package name)
    _BUNDLED_ENTRY_CONFIG: Dict[str, Optional[str]] = dict()
    _BUNDLED_DEFAULT_CONFIG: Dict[str, Optional[str]] = dict()
    _LAYER_CACHE_MAX = 4096
    _MERGED_CACHE_MAX = 2048
    _MISSING = object()
    _DEFAULT_KEEP_LAST_N = 20
    _DEFAULT_KEEP_LAST_N_PATH = "core.config.versioning.keep_last_n"
    _KEEP_LAST_N_CACHE: Dict[str, int] = dict()

    @staticmethod
    def _normalize_package(package: str) -> str:
        normalized = package.strip().lower()
        if not normalized:
            raise ValueError("package must be a non-empty string")
        return normalized

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if not normalized:
            raise ValueError("scope must be a non-empty string")
        return normalized

    @staticmethod
    def _lru_get(cache: "OrderedDict[Any, Any]", key: Any) -> Any:
        if key not in cache:
            return None
        value = cache[key]
        cache.move_to_end(key)
        return value

    @staticmethod
    def _lru_put(cache: "OrderedDict[Any, Any]", key: Any, value: Any, max_size: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)

    @classmethod
    def _resolve_resource(cls, package: str, *parts: str) -> Optional[str]:
        """Resolve a package resource path via importlib, with __file__ fallback."""
        try:
            import importlib.resources as pkg_resources

            path = str(pkg_resources.files(package).joinpath("resources", *parts))
            if _exists_file(path):
                return path
        except Exception:
            return None

    @classmethod
    def _bundled_entry_config_path(cls, package: str) -> Optional[str]:
        """Resolve the path to the bundled entry_config.yaml."""
        package = cls._normalize_package(package)
        if package not in cls._BUNDLED_ENTRY_CONFIG:
            path = cls._resolve_resource(package, "configs", "entry_config.yaml")
            if path is None and package != "ahvn":
                path = cls._resolve_resource("ahvn", "configs", "entry_config.yaml")
            cls._BUNDLED_ENTRY_CONFIG[package] = path
        return cls._BUNDLED_ENTRY_CONFIG.get(package)

    @classmethod
    def _bundled_default_config_path(cls, package: str = "ahvn") -> Optional[str]:
        """Resolve the path to the ahvn's bundled default_config.yaml. Notice that this config should always use the ahvn's one since it contains shared defaults for all packages unless you are changing the configuration system itself."""
        package = cls._normalize_package(package)
        if package not in cls._BUNDLED_DEFAULT_CONFIG:
            path = cls._resolve_resource(package, "configs", "default_config.yaml")
            if path is None and package != "ahvn":
                path = cls._resolve_resource("ahvn", "configs", "default_config.yaml")
            cls._BUNDLED_DEFAULT_CONFIG[package] = path
        return cls._BUNDLED_DEFAULT_CONFIG.get(package)

    @classmethod
    def _user_entry_path(cls, package: str) -> str:
        package = cls._normalize_package(package)
        return os.path.join(os.path.expanduser("~"), f".{package}", "entry.yaml")

    def __init__(self, package: str, database: Optional[str] = None, provider: Optional[str] = None, **kwargs):
        """\
        Initialize the configuration storage backed by a ``Database`` instance.

        Args:
            package: Package name used to resolve bundled config files and
                        user entry path (``~/.{package}/entry.yaml``).
            database: Optional path override for the backing SQLite file.
                        When omitted the path is read from ``entry_config.yaml``.
                        Useful for tests that need an isolated temporary database.
        """
        super().__init__()
        self.package = self._normalize_package(package)
        from ahvn.utils.db.base import Database

        # --- Build db override config (base defaults -> entry overrides) ---
        base_db_cfg = self._load_default_db_config(package=self.package)
        entry_cfg = self._load_entry_config(package=self.package)
        if provider is None:
            provider = dget(entry_cfg, "config.provider", None)
        if database is None:
            database = dget(entry_cfg, "config.database", None)
            if (database is not None) and database.startswith("file:"):
                database = pj(database[5:], "", abs=True)
        entry_kwargs = {k: v for k, v in dget(entry_cfg, "config", dict()).items() if k not in ["provider", "database"]}

        # --- Create Database with _override to bypass CM_AHVN ---
        self.db = Database(
            provider=provider,
            database=database,
            **(entry_kwargs | kwargs),
            _override=base_db_cfg,
        )
        logger.info(f"Initializing ConfigStorage with provider '{provider}' and database '{database}'")

        self._write_lock = threading.Lock()
        self._lock = threading.RLock()
        self._layer_cache: "OrderedDict[Tuple[str, str, Optional[str]], Tuple[int, Dict[str, Any]]]" = OrderedDict()
        self._merged_cache: "OrderedDict[Tuple[str, Tuple[str, ...], Optional[str], Tuple[int, ...]], Dict[str, Any]]" = OrderedDict()
        self._compat_cache: Dict[Tuple[str, str], Optional[Tuple[str, ...]]] = dict()

        # --- Create tables ---
        self.db.create_tabs([configs_table, compatibility_table])

    @classmethod
    def _load_default_db_config(cls, package: str) -> Dict[str, Any]:
        """Load the ``db:`` section from the bundled ``default_config.yaml``."""
        path = cls._bundled_default_config_path(package="ahvn")
        if path:
            full = _load_yaml(path)
            return full.get("db", {})
        return {}

    @classmethod
    def _load_entry_config(cls, package: str) -> Dict[str, Any]:
        """\
        Load the entry config YAML with fallback chain.

        If the user's ``~/.{package}/entry.yaml`` exists but is broken (parse
        error), a warning is logged and the bundled default is used instead.

        1. Explicit ``entry_config_path``
        2. ``~/.{package}/entry.yaml``
        3. Bundled ``entry_config.yaml``
        """
        user_entry_path = cls._user_entry_path(package=package)
        if _exists_file(user_entry_path):
            try:
                cfg = _load_yaml(user_entry_path)
                if isinstance(cfg, dict):
                    return cfg
                raise ValueError("entry.yaml parsed to non-dict")
            except Exception as exc:
                logger.warning(f"Broken entry config at {user_entry_path}: {exc}. " "Falling back to bundled default.")
        bundled = cls._bundled_entry_config_path(package=package)
        if bundled:
            return _load_yaml(bundled)
        return {}

    @classmethod
    def ensure_user_entry_config(cls, package: str) -> None:
        """\
        Copy the bundled ``entry_config.yaml`` to ``~/.{package}/entry.yaml``
        if the user-level file does not already exist.
        """
        user_entry_path = cls._user_entry_path(package=package)
        if _exists_file(user_entry_path):
            return
        bundled = cls._bundled_entry_config_path(package=package)
        if not bundled:
            return
        _touch_dir(os.path.dirname(user_entry_path))
        try:
            import shutil as _shutil

            _shutil.copy2(bundled, user_entry_path)
        except Exception as exc:
            logger.warning(f"Failed to copy entry config to {user_entry_path}: {exc}")

    @classmethod
    def _default_keep_last_n(cls, package: str) -> int:
        package = cls._normalize_package(package)
        cached = cls._KEEP_LAST_N_CACHE.get(package)
        if cached is not None:
            return cached
        value = cls._DEFAULT_KEEP_LAST_N
        path = cls._bundled_default_config_path(package=package)
        if path:
            cfg = _load_yaml(path)
            raw = dget(cfg, cls._DEFAULT_KEEP_LAST_N_PATH, cls._DEFAULT_KEEP_LAST_N)
            try:
                value = int(raw)
            except Exception:
                value = cls._DEFAULT_KEEP_LAST_N
        if value < 1:
            value = 1
        cls._KEEP_LAST_N_CACHE[package] = value
        return value

    @classmethod
    def _resolve_keep_last_n(cls, package: str, keep_last_n: Optional[int] = None) -> int:
        if keep_last_n is None:
            return cls._default_keep_last_n(package)
        try:
            value = int(keep_last_n)
        except Exception as exc:
            raise ValueError(f"keep_last_n must be an integer, got {keep_last_n!r}") from exc
        if value < 1:
            raise ValueError(f"keep_last_n must be >= 1, got {value}")
        return value

    def clear(self):
        with self._write_lock:
            configs_table.drop(self.db.engine, checkfirst=True)
            compatibility_table.drop(self.db.engine, checkfirst=True)
            self.db.create_tabs([configs_table, compatibility_table])
            with self._lock:
                self._layer_cache.clear()
                self._merged_cache.clear()
                self._compat_cache.clear()

    def _clear_package_cache(self, package: str) -> None:
        with self._lock:
            for key in list(self._layer_cache.keys()):
                if key[0] == package:
                    self._layer_cache.pop(key, None)
            for key in list(self._merged_cache.keys()):
                if key[0] == package:
                    self._merged_cache.pop(key, None)
            for key in list(self._compat_cache.keys()):
                if key[0] == package:
                    self._compat_cache.pop(key, None)

    def _clear_scope_cache(self, package: str, scope: str) -> None:
        with self._lock:
            for key in list(self._layer_cache.keys()):
                if key[0] == package and key[1] == scope:
                    self._layer_cache.pop(key, None)

    def _cache_layer_update(self, package: str, scope: str, package_version: Optional[str], version: int, data: Dict[str, Any]) -> None:
        layer_key = (package, scope, package_version)
        with self._lock:
            for key in list(self._layer_cache.keys()):
                if key[0] == package and key[1] == scope and key != layer_key:
                    self._layer_cache.pop(key, None)
            self._lru_put(
                self._layer_cache,
                layer_key,
                (version, deepcopy(data) if data else dict()),
                self._LAYER_CACHE_MAX,
            )

    def _compatibles_uncached(self, package: str, package_version: str) -> Optional[List[str]]:
        # Look up the current version's row
        stmt = select(compatibility_table).where(
            compatibility_table.c.package == package,
            compatibility_table.c.package_version == package_version,
        )
        result = self.db.orm_execute(stmt, readonly=True)
        if len(result) == 0:
            return None  # version not registered -> no filtering
        current = result[0]

        # Look up the min-compatible version's order
        stmt = select(compatibility_table.c.version_order).where(
            compatibility_table.c.package == package,
            compatibility_table.c.version_order == current["compatible_order"],
        )
        result = self.db.orm_execute(stmt, readonly=True)
        if len(result) == 0:
            return [package_version]
        min_order = result[0, 0]

        # All versions with order in [min_order, current.order]
        stmt = select(compatibility_table.c.package_version).where(
            compatibility_table.c.package == package,
            compatibility_table.c.version_order >= min_order,
            compatibility_table.c.version_order <= current["version_order"],
        )
        result = self.db.orm_execute(stmt, readonly=True)
        return [row["package_version"] for row in result]

    def compatibles(self, package: str, package_version: str) -> Optional[List[str]]:
        package = self._normalize_package(package)
        cache_key = (package, package_version)
        with self._lock:
            cached = self._compat_cache.get(cache_key, self._MISSING)
        if cached is not self._MISSING:
            return None if cached is None else list(cached)

        resolved = self._compatibles_uncached(package, package_version)
        with self._lock:
            self._compat_cache[cache_key] = None if resolved is None else tuple(resolved)
        return resolved

    def _compat_filter(self, package: str, package_version: Optional[str]):
        """Return a WHERE clause fragment for compatible-version filtering, or None."""
        if not package_version:
            return None
        compat = self.compatibles(package, package_version)
        if compat is None:
            return None
        return or_(
            configs_table.c.package_version.in_(compat),
            configs_table.c.package_version.is_(None),
        )

    def version_order(self, package: str, package_version: str) -> Optional[int]:
        package = self._normalize_package(package)
        stmt = select(compatibility_table.c.version_order).where(
            compatibility_table.c.package == package,
            compatibility_table.c.package_version == package_version,
        )
        result = self.db.orm_execute(stmt, readonly=True)
        return result[0, 0] if len(result) > 0 else None

    def register(
        self,
        package: str,
        versions: Dict[str, str],
    ) -> int:
        package = self._normalize_package(package)
        if not versions:
            return 0
        batch_orders: Dict[str, int] = {pv: idx for idx, pv in enumerate(versions.keys(), start=1)}
        with self._write_lock:
            with self.db:
                self.db.orm_execute(compatibility_table.delete().where(compatibility_table.c.package == package))
                for idx, (package_version, compatible_version) in enumerate(versions.items(), start=1):
                    compatible_order = batch_orders.get(compatible_version) or self.version_order(package, compatible_version)
                    self.db.orm_execute(
                        compatibility_table.insert().values(
                            package=package,
                            package_version=package_version,
                            version_order=idx,
                            compatible_order=compatible_order,
                        ),
                        readonly=False,
                    )
            self._clear_package_cache(package)
        return len(versions)

    def _query_latest_layer(self, package: str, scope: str, package_version: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        stmt = select(configs_table.c.version, configs_table.c.data).where(
            configs_table.c.package == package,
            configs_table.c.scope == scope,
        )
        filt = self._compat_filter(package, package_version)
        if filt is not None:
            stmt = stmt.where(filt)
        stmt = stmt.order_by(configs_table.c.version.desc()).limit(1)
        result = self.db.orm_execute(stmt, readonly=True)
        if len(result) == 0:
            return 0, dict()
        row = result[0]
        return int(row["version"] or 0), deepcopy(row["data"] if row["data"] else dict())

    def _latest_layer(self, package: str, scope: str, package_version: Optional[str], copy_data: bool = True) -> Tuple[int, Dict[str, Any]]:
        layer_key = (package, scope, package_version)
        with self._lock:
            cached = self._lru_get(self._layer_cache, layer_key)
        if cached is not None:
            version, data = cached
            return version, (deepcopy(data) if copy_data else data)

        queried_version, queried_data = self._query_latest_layer(package, scope, package_version=package_version)
        with self._lock:
            cached = self._lru_get(self._layer_cache, layer_key)
            if cached is None:
                self._lru_put(
                    self._layer_cache,
                    layer_key,
                    (queried_version, deepcopy(queried_data)),
                    self._LAYER_CACHE_MAX,
                )
                cached = (queried_version, queried_data)
        version, data = cached
        return version, (deepcopy(data) if copy_data else data)

    def latest(self, package: str, scope: str, package_version: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        return self._latest_layer(package, scope, package_version=package_version, copy_data=True)

    def version(self, package: str, scope: str, package_version: Optional[str] = None) -> Optional[int]:
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        version, _ = self._latest_layer(package, scope, package_version=package_version, copy_data=False)
        return version

    def scopes(self, package: str) -> List[str]:
        package = self._normalize_package(package)
        stmt = select(configs_table.c.scope).where(configs_table.c.package == package).distinct()
        result = self.db.orm_execute(stmt, readonly=True)
        return [row["scope"] for row in result]

    def versions(self, package: str, scope: str, package_version: Optional[str] = None) -> List[int]:
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        stmt = (
            select(configs_table.c.version)
            .where(
                configs_table.c.package == package,
                configs_table.c.scope == scope,
            )
            .order_by(configs_table.c.version.desc())
        )
        filt = self._compat_filter(package, package_version)
        if filt is not None:
            stmt = stmt.where(filt)
        result = self.db.orm_execute(stmt, readonly=True)
        return [row["version"] for row in result]

    def get(
        self,
        package: str,
        scope: str,
        version: Optional[int] = -1,
        snapshot: bool = False,
        package_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        version = -1 if version is None else int(version)

        if version == -1 and not snapshot:
            _, data = self._latest_layer(package, scope, package_version=package_version, copy_data=True)
            return data

        if version >= 0 and not snapshot:
            latest_version, latest_data = self._latest_layer(package, scope, package_version=package_version, copy_data=True)
            if version == latest_version:
                return latest_data

        filt = self._compat_filter(package, package_version)
        if version < 0:
            stmt = select(configs_table).where(
                configs_table.c.package == package,
                configs_table.c.scope == scope,
            )
            if filt is not None:
                stmt = stmt.where(filt)
            stmt = stmt.order_by(configs_table.c.version.desc()).offset(abs(version) - 1).limit(1)
        else:
            stmt = select(configs_table).where(
                configs_table.c.package == package,
                configs_table.c.scope == scope,
                configs_table.c.version == version,
            )
            if filt is not None:
                stmt = stmt.where(filt)
        result = self.db.orm_execute(stmt, readonly=True)
        if len(result) == 0:
            return dict() if not snapshot else None
        row = result[0]
        data = deepcopy(row["data"] if row["data"] else dict())
        return (
            data
            if not snapshot
            else ConfigSnapshot(
                data=data,
                id=row["id"],
                package=row["package"],
                scope=row["scope"],
                version=row["version"],
                package_version=row["package_version"],
                created_at=row["created_at"],
            )
        )

    def load_merged(self, package: str, scopes: Iterable[str], package_version: Optional[str] = None) -> Dict[str, Any]:
        package = self._normalize_package(package)
        scope_chain = tuple(self._normalize_scope(scope) for scope in scopes if scope and scope.strip())
        if not scope_chain:
            return dict()

        versions: List[int] = []
        layers: List[Dict[str, Any]] = []
        for scope in scope_chain:
            version, layer = self._latest_layer(package, scope, package_version=package_version, copy_data=True)
            versions.append(version)
            layers.append(layer)

        merged_key = (package, scope_chain, package_version, tuple(versions))
        with self._lock:
            cached = self._lru_get(self._merged_cache, merged_key)
        if cached is not None:
            return deepcopy(cached)

        merged = dmerge(layers)
        with self._lock:
            cached = self._lru_get(self._merged_cache, merged_key)
            if cached is None:
                self._lru_put(
                    self._merged_cache,
                    merged_key,
                    deepcopy(merged),
                    self._MERGED_CACHE_MAX,
                )
            else:
                return deepcopy(cached)
        return merged

    def _latest_version_unlocked(self, package: str, scope: str) -> int:
        stmt = select(func.max(configs_table.c.version)).where(
            configs_table.c.package == package,
            configs_table.c.scope == scope,
        )
        result = self.db.orm_execute(stmt, readonly=True)
        value = result[0, 0] if len(result) > 0 else None
        return int(value or 0)

    def _trim_scope_versions_unlocked(self, package: str, scope: str, keep_last_n: int) -> int:
        if keep_last_n <= 0:
            return 0
        cutoff_stmt = (
            select(configs_table.c.version)
            .where(
                configs_table.c.package == package,
                configs_table.c.scope == scope,
            )
            .order_by(configs_table.c.version.desc())
            .offset(keep_last_n - 1)
            .limit(1)
        )
        cutoff_result = self.db.orm_execute(cutoff_stmt, readonly=True)
        cutoff_version = cutoff_result[0, 0] if len(cutoff_result) > 0 else None
        if cutoff_version is None:
            return 0
        result = self.db.orm_execute(
            configs_table.delete().where(
                configs_table.c.package == package,
                configs_table.c.scope == scope,
                configs_table.c.version < int(cutoff_version),
            ),
            readonly=False,
        )
        return max(int(result.row_count), 0)

    def set(
        self,
        package: str,
        scope: str,
        data: Dict[str, Any],
        package_version: Optional[str] = None,
        expected_version: Optional[int] = None,
        keep_last_n: Optional[int] = None,
    ) -> int:
        """Append a new version of config data for (*package*, *scope*) with optimistic locking."""
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        keep_last_n = self._resolve_keep_last_n(package, keep_last_n)

        with self._write_lock:
            new_version: Optional[int] = None
            try:
                with self.db:
                    # For write correctness, always resolve the current version from DB.
                    current_version = self._latest_version_unlocked(package, scope)
                    if expected_version is not None and current_version != expected_version:
                        raise VersionConflictError(f"Expected version {expected_version} for {package}/{scope}, but current is {current_version}")

                    new_version = current_version + 1
                    self.db.orm_execute(
                        configs_table.insert().values(
                            package=package,
                            scope=scope,
                            version=new_version,
                            package_version=package_version,
                            data=data,
                            created_at=datetime.datetime.now(datetime.timezone.utc),
                        ),
                        readonly=False,
                    )
                    self._trim_scope_versions_unlocked(package, scope, keep_last_n=keep_last_n)
            except VersionConflictError:
                self._clear_scope_cache(package, scope)
                raise
            except Exception as exc:
                self._clear_scope_cache(package, scope)
                suffix = f" version {new_version}" if new_version is not None else ""
                raise VersionConflictError(f"Version conflict on insert for {package}/{scope}{suffix}") from exc

            self._cache_layer_update(package, scope, package_version, new_version, data)
            return new_version

    def compact(
        self,
        package: str,
        scope: str,
        package_version: Optional[str] = None,
        keep_last_n: Optional[int] = None,
        reset: bool = False,
    ):
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        keep_last_n = self._resolve_keep_last_n(package, keep_last_n)
        with self._write_lock:
            if reset:
                latest = self.get(package, scope, version=-1, snapshot=True, package_version=package_version)
                if latest is None:
                    return 0
                with self.db:
                    self.db.orm_execute(
                        configs_table.delete().where(
                            configs_table.c.package == package,
                            configs_table.c.scope == scope,
                        ),
                        readonly=False,
                    )
                    self.db.orm_execute(
                        configs_table.insert().values(
                            package=package,
                            scope=scope,
                            version=1,
                            package_version=latest.package_version,
                            data=dict(latest),
                            created_at=latest.created_at,
                        ),
                        readonly=False,
                    )
                self._clear_scope_cache(package, scope)
                self._cache_layer_update(package, scope, latest.package_version, 1, dict(latest))
                return 1

            with self.db:
                removed = self._trim_scope_versions_unlocked(package, scope, keep_last_n=keep_last_n)
            if removed > 0:
                self._clear_scope_cache(package, scope)
                latest = self.get(package, scope, version=-1, snapshot=True, package_version=package_version)
                if latest is not None:
                    self._cache_layer_update(package, scope, latest.package_version, latest.version, dict(latest))
            return removed

    def remove(self, package: str, scope: str):
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        with self._write_lock:
            self.db.orm_execute(
                configs_table.delete().where(
                    configs_table.c.package == package,
                    configs_table.c.scope == scope,
                ),
                autocommit=True,
                readonly=False,
            )
            self._clear_scope_cache(package, scope)

    def remove_version(self, package: str, scope: str, version: int):
        package = self._normalize_package(package)
        scope = self._normalize_scope(scope)
        with self._write_lock:
            self.db.orm_execute(
                configs_table.delete().where(
                    configs_table.c.package == package,
                    configs_table.c.scope == scope,
                    configs_table.c.version == version,
                ),
                autocommit=True,
                readonly=False,
            )
            self._clear_scope_cache(package, scope)


class ConfigManager:
    _SINGLETONS: Dict[Tuple[type, str], "ConfigManager"] = dict()
    _SINGLETON_LOCK = threading.RLock()
    compatibility_table: Dict[str, List[str]] = dict()

    @classmethod
    def _singleton_key(cls, package: str) -> Tuple[type, str]:
        normalized = package.strip().lower()
        if not normalized:
            raise ValueError("package must be a non-empty string")
        return cls, normalized

    @classmethod
    def _drop_singleton(cls, package: str) -> None:
        try:
            key = cls._singleton_key(package)
        except Exception:
            return
        with cls._SINGLETON_LOCK:
            cls._SINGLETONS.pop(key, None)

    def __new__(cls, package: str, distribution: Optional[str] = None, scope: Optional[str] = None, setup: bool = True):
        if not isinstance(package, str):
            return super().__new__(cls)
        normalized = package.strip().lower()
        if not normalized:
            return super().__new__(cls)

        key = (cls, normalized)
        with cls._SINGLETON_LOCK:
            inst = cls._SINGLETONS.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._SINGLETONS[key] = inst
        return inst

    def __init__(self, package: str, distribution: Optional[str] = None, scope: Optional[str] = None, setup: bool = True):
        normalized_package = package.strip().lower()
        normalized_distribution = distribution.strip().lower() if distribution else normalized_package
        normalized_scope = (scope or normalized_package).strip().lower()
        singleton_sig = (normalized_package, normalized_distribution, normalized_scope)
        if "." in normalized_scope:
            raise ValueError(f"Base scope cannot contain dots: {normalized_scope}")

        with self.__class__._SINGLETON_LOCK:
            initialized_sig = getattr(self, "_singleton_sig", None)
            if initialized_sig is not None:
                if initialized_sig != singleton_sig:
                    raise ValueError(
                        "ConfigManager singleton already initialized with a different distribution/scope for package "
                        f"'{normalized_package}': {initialized_sig[1:]}, requested {singleton_sig[1:]}"
                    )
                if setup:
                    self.setup(reset=False)
                return

            try:
                super().__init__()
                self.package = normalized_package
                self.distribution = normalized_distribution
                self.base_scope = normalized_scope
                self.root = pj("~", f".{self.package}", abs=True)
                _touch_dir(self.root)
                # Ensure the user-level entry.yaml exists (copy bundled if missing)
                ConfigStorage.ensure_user_entry_config(package=self.package)
                self.storage = ConfigStorage(package=self.package)
                self._max_retries: int = 16  # max optimistic-lock retries for set/unset/setdef
                if setup:
                    self.setup(reset=False)
                self._singleton_sig = singleton_sig
            except Exception:
                self.__class__._SINGLETONS.pop((self.__class__, normalized_package), None)
                raise

    def scoped(self, name: Union[str, Callable[[], Optional[str]], None] = None, **kwargs):
        """Create a scope context that can be used as a decorator or context manager.

        **Push mode** (``name`` provided) - pushes a scope name onto the stack::

            @CM.scoped("rubik")
            def my_func(): ...

        **Snapshot mode** (no ``name``) - captures the current scope state at
        creation time and restores it on each entry.  Use this to propagate
        scope into lazily-evaluated generators or callbacks::

            @CM.scoped("rubik")
            def my_endpoint():
                @CM.scoped()          # inherits 'rubik' scope
                def generate():
                    yield ...         # scope is active here
                return StreamingResponse(generate())
        """
        is_snapshot = name is None
        snapshot: Optional[Dict[str, list]] = {k: v[:] for k, v in _SCOPE_STACKS.get().items()} if is_snapshot else None

        # name can be a string, or a callable that returns a string (evaluated dynamically on each context entry)
        def _resolve_scope_name():
            n = name(**kwargs) if callable(name) else name
            return n.lower().strip() if n else None

        package = self.package

        class Scoped:
            def __init__(self):
                self._token_stack = []

            # ---------------------
            # Context Manager API
            # ---------------------
            def __enter__(self):
                if is_snapshot and snapshot is not None:
                    token = _SCOPE_STACKS.set({k: v[:] for k, v in snapshot.items()})
                    self._token_stack.append(token)
                    return self
                scope_name = _resolve_scope_name()
                if not scope_name:
                    self._token_stack.append(None)
                    return self
                current = _SCOPE_STACKS.get()
                new_state = {k: v[:] for k, v in current.items()}
                new_state.setdefault(package, []).append(scope_name)
                token = _SCOPE_STACKS.set(new_state)
                self._token_stack.append(token)
                return self

            def __exit__(self, exc_type, exc, tb):
                if self._token_stack:
                    token = self._token_stack.pop()
                    if token is not None:
                        _SCOPE_STACKS.reset(token)

            # ---------------------
            # Decorator API
            # ---------------------
            def __call__(self, obj):
                if inspect.isclass(obj):
                    return self._wrap_class(obj)
                if callable(obj):
                    return self._wrap_func(obj)
                return obj

            # ---------------------
            # Function Wrapper
            # ---------------------
            def _wrap_func(self, func: Callable):
                if inspect.isasyncgenfunction(func):

                    @wraps(func)
                    async def async_gen_wrapper(*args, **kwargs):
                        agen = func(*args, **kwargs)
                        try:
                            sent = None
                            while True:
                                with Scoped():
                                    try:
                                        item = await agen.asend(sent)
                                    except StopAsyncIteration:
                                        return
                                sent = yield item
                        finally:
                            with Scoped():
                                await agen.aclose()

                    return async_gen_wrapper
                elif inspect.iscoroutinefunction(func):

                    @wraps(func)
                    async def async_wrapper(*args, **kwargs):
                        with Scoped():
                            return await func(*args, **kwargs)

                    return async_wrapper
                elif inspect.isgeneratorfunction(func):

                    @wraps(func)
                    def gen_wrapper(*args, **kwargs):
                        gen = func(*args, **kwargs)
                        try:
                            sent = None
                            while True:
                                with Scoped():
                                    try:
                                        item = gen.send(sent)
                                    except StopIteration:
                                        return
                                sent = yield item
                        finally:
                            with Scoped():
                                gen.close()

                    return gen_wrapper
                else:

                    @wraps(func)
                    def sync_wrapper(*args, **kwargs):
                        with Scoped():
                            return func(*args, **kwargs)

                    return sync_wrapper

            # ---------------------
            # Class Wrapper
            # ---------------------
            def _wrap_class(self, cls):

                class ScopedClass(cls):
                    def __init__(self_inner, *args, **kwargs):
                        with Scoped():
                            super(ScopedClass, self_inner).__init__(*args, **kwargs)

                    def __getattribute__(self_inner, name):
                        attr = super(ScopedClass, self_inner).__getattribute__(name)
                        # Only wrap dunder-free bound methods (instance methods & classmethods).
                        # This avoids wrapping callable instance attributes (e.g. objects
                        # with __call__), properties returning callables, lambdas, etc.
                        if name.startswith("__") or not isinstance(attr, types.MethodType):
                            return attr
                        # Handle generator, async generator, coroutine, and sync methods
                        if inspect.isasyncgenfunction(attr):

                            async def wrapped(*args, **kwargs):
                                agen = attr(*args, **kwargs)
                                try:
                                    sent = None
                                    while True:
                                        with Scoped():
                                            try:
                                                item = await agen.asend(sent)
                                            except StopAsyncIteration:
                                                return
                                        sent = yield item
                                finally:
                                    with Scoped():
                                        await agen.aclose()

                            return wrapped
                        if inspect.iscoroutinefunction(attr):

                            async def wrapped(*args, **kwargs):
                                with Scoped():
                                    return await attr(*args, **kwargs)

                            return wrapped
                        if inspect.isgeneratorfunction(attr):

                            def wrapped(*args, **kwargs):
                                gen = attr(*args, **kwargs)
                                try:
                                    sent = None
                                    while True:
                                        with Scoped():
                                            try:
                                                item = gen.send(sent)
                                            except StopIteration:
                                                return
                                        sent = yield item
                                finally:
                                    with Scoped():
                                        gen.close()

                            return wrapped

                        def wrapped(*args, **kwargs):
                            with Scoped():
                                return attr(*args, **kwargs)

                        return wrapped

                ScopedClass.__name__ = cls.__name__
                ScopedClass.__qualname__ = cls.__qualname__
                ScopedClass.__module__ = cls.__module__

                return ScopedClass

        return Scoped()

    @property
    def scope_chain(self) -> List[str]:
        ctx = _SCOPE_STACKS.get()
        suffixes = ctx.get(self.package, [])
        suffixes = unique(self.base_scope.split(".") + list(reversed(suffixes)))
        cur, chain = None, list()
        for s in suffixes:
            cur = f"{s}" if cur is None else f"{cur}.{s}"
            chain.append(cur)
        return chain

    @property
    def scope(self) -> str:
        return self.scope_chain[-1]

    @property
    def version_chain(self) -> List[int]:
        return [self.storage.version(self.package, scope, package_version=self.package_version) for scope in self.scope_chain]

    @property
    def version(self) -> int:
        return self.version_chain[-1]

    @property
    def package_version(self) -> Optional[str]:
        try:
            import importlib

            mod = importlib.import_module(f"{self.package}")
            ver = getattr(mod, "__version__", None)
            if ver is not None:
                return str(ver)
        except Exception:
            pass

        try:
            import importlib.metadata

            return str(importlib.metadata.version(self.distribution))
        except Exception:
            return None

    def resource(self, *args: List[str]) -> str:
        """\
        Get the path to a resource file in the package `resources` directory.

        Args:
            *args (List[str]): The path components to the resource file.

        Returns:
            str: The absolute path to the resource file.
        """
        import importlib.resources

        try:
            with importlib.resources.as_file(importlib.resources.files(self.package).joinpath("resources", *args)) as path:
                return pj(str(path.resolve()), abs=True)
        except Exception:
            return None

    def load_default(self) -> Dict[str, Any]:
        try:
            return _load_yaml(self.resource("configs", "default_config.yaml"))
        except Exception:
            return dict()

    def init(self, scope: Optional[str] = None, reset: bool = False, data: Optional[Dict[str, Any]] = None) -> bool:
        """\
        Initialize the specified or current scope.

        Args:
            scope (Optional[str]): The scope to initialize. If None, the current scope is used.
            reset (bool): If True, reset the configuration to the default values.
                If False, only initialize if there's no current configuration for the scope.
            data (Optional[Dict[str, Any]]): The configuration data to initialize with.
                If None, the default configuration will be loaded and used for initialization (path "&/configs/default_config.yaml").

        Returns:
            bool: True if the scope was initialized, False if it was not initialized because it already exists and reset is False (or set operation failed).
        """
        scope = self.scope if scope is None else scope.strip().lower()
        if reset or (self.storage.version(self.package, scope) == 0):
            return self.set(key_path=None, value=data if data is not None else self.load_default(), scope=scope)
        return False

    def setup(self, reset: bool = False) -> bool:
        """\
        Setup the configuration manager.

        Args:
            reset (bool): If True, clear all configuration data and reset the base scope to the default values.
                If False, only initialize the base scope if there's no current configuration for it.

        Returns:
            bool: True if the base scope was initialized, False if it was not initialized because it already exists and reset is False (or set operation failed).
        """
        if reset:
            self.storage.clear()
            return self.init(scope=self.base_scope, reset=True)
        else:
            return self.init(scope=self.base_scope, reset=False)

    def load(self) -> Dict[str, Any]:
        """\
        Load the merged configuration for the current scope chain.

        Returns:
            Dict[str, Any]: The merged configuration for the current scope chain.
        """
        return self.storage.load_merged(
            package=self.package,
            scopes=self.scope_chain,
            package_version=self.package_version,
        )

    def get(self, key_path: Optional[str] = None, default: Optional[Any] = None, **_: Any) -> Any:
        config = self.load()
        return dget(config, key_path, default=default)

    def layer(self, scope: Optional[str] = None, version: Optional[int] = None) -> Dict[str, Any]:
        """\
        Get the configuration layer for the specified or current scope and version.
        If version is None, get the latest version in the scope.

        Args:
            scope (Optional[str]): The scope to get the layer for. If None, the current scope is used.
            version (Optional[int]): The version to get the layer for. If None, the latest version in the scope is used.

        Returns:
            Dict[str, Any]: The configuration layer for the specified scope and version.
        """
        pv = self.package_version
        scope = scope.strip().lower() if scope else self.scope
        if version is None:
            _, layer = self.storage.latest(self.package, scope, package_version=pv)
            return layer
        return deepcopy(self.storage.get(self.package, scope, version, package_version=pv))

    def _mutate_layer(self, scope: str, op_name: str, mutator: Callable[[Dict[str, Any]], bool]) -> bool:
        for _attempt in range(self._max_retries):
            pv = self.package_version
            visible_ver, layer = self.storage.latest(self.package, scope, package_version=pv)
            expected_ver = visible_ver if pv is None else self.storage.version(self.package, scope, package_version=None)
            success = mutator(layer)
            try:
                self.storage.set(
                    self.package,
                    scope,
                    layer,
                    package_version=pv,
                    expected_version=expected_ver,
                )
                return success
            except VersionConflictError:
                logger.debug(f"Optimistic-lock retry {_attempt + 1}/{self._max_retries} for ConfigManager.{op_name} on scope '{scope}'")
                continue
        raise VersionConflictError(f"ConfigManager.{op_name} failed after {self._max_retries} retries")

    def set(self, key_path: str, value: Optional[Any] = None, scope: Optional[str] = None, **_: Any) -> bool:
        scope = scope.strip().lower() if scope else self.scope
        return self._mutate_layer(scope, op_name=f"set('{key_path}')", mutator=lambda layer: dset(layer, key_path, value))

    def unset(self, key_path: str, scope: Optional[str] = None) -> bool:
        scope = scope.strip().lower() if scope else self.scope
        return self._mutate_layer(scope, op_name=f"unset('{key_path}')", mutator=lambda layer: dunset(layer, key_path))

    def setdef(self, key_path: str, default: Optional[Any] = None, scope: Optional[str] = None) -> bool:
        scope = scope.strip().lower() if scope else self.scope
        return self._mutate_layer(
            scope,
            op_name=f"setdef('{key_path}')",
            mutator=lambda layer: dsetdef(layer, key_path, default=default),
        )

    def compact(self, scope: Optional[str] = None, keep_last_n: Optional[int] = None, reset: bool = False) -> int:
        """\
        Compact version history for the specified or current scope.

        By default this keeps only the latest ``keep_last_n`` versions and preserves
        their original version numbers. If ``keep_last_n`` is ``None``, the value is
        resolved from default config key ``core.config.versioning.keep_last_n`` with
        fallback ``20``.

        Set ``reset=True`` to use legacy reset behavior: remove all history and keep
        only a single latest snapshot as version ``1``.

        Args:
            scope (Optional[str]): The scope to compact. If None, the current scope is used.
            keep_last_n (Optional[int]): Number of latest versions to keep.
            reset (bool): If True, reset version numbering back to 1 (legacy behavior).

        Returns:
            int: Number of removed versions (or ``1`` for ``reset=True`` on non-empty scope).
        """
        scope = scope.strip().lower() if scope else self.scope
        return self.storage.compact(
            self.package,
            scope,
            package_version=self.package_version,
            keep_last_n=keep_last_n,
            reset=reset,
        )

    def remove(self, scope: Optional[str] = None, version: Optional[int] = None) -> bool:
        """\
        Remove the configuration for the specified or current scope and version.
        If no version is specified, ALL versions in the scope will be removed.

        Args:
            scope (Optional[str]): The scope to remove. If None, the current scope is used.
            version (Optional[int]): The version to remove. If None, all versions in the scope will be removed.

        Returns:
            bool: True if the configuration was removed, False if the scope/version does not exist or the operation failed.
        """
        scope = scope.strip().lower() if scope else self.scope
        if version is None:
            self.storage.remove(self.package, scope)
        else:
            self.storage.remove_version(self.package, scope, version)
        return True

    def set_cwd(self, root: str) -> str:
        self.root = pj(root, abs=True)
        _touch_dir(self.root)
        return self.root

    def pj(self, *args: List[str], abs: bool = False) -> str:
        return pj(
            *args,
            abs=abs,
            aliases={
                "%": self.root,
                "&": self.resource(),
            },
        )

    def register(self, versions: Dict[str, str]) -> int:
        """
        Register the compatibility table for this package.

        Args:
            versions: Ordered mapping from versions to their minimum compatible versions.
                The order of the versions should be from the oldest to the newest.
                For example, if version "1.1.0" is backward-compatible to "1.0.0", and "2.0.0" is a breaking change,
                then the mapping should be {"1.0.0": "1.0.0", "1.1.0": "1.0.0", "2.0.0": "2.0.0"}.

        Returns:
            Number of versions registered.

        Example:
            CM_AHVN.register({
                "1.0.0": "1.0.0",  # 1.0.0 is only compatible with itself
                "1.1.0": "1.0.0",  # 1.1.0 is backward-compatible to 1.0.0
                "2.0.0": "2.0.0",  # breaking: 2.0.0 only compatible with itself
            })
        """
        return self.storage.register(self.package, versions)

    def scopes(self) -> List[str]:
        """\
        Get the list of all scopes that have been used for this package.

        Returns:
            List[str]: The list of scopes.
        """
        return self.storage.scopes(self.package)

    def history(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """\
        Get the history of all versions in the specified or current scope.

        Args:
            scope (Optional[str]): The scope to get the history for. If None, the current scope is used.

        Returns:
            List[Dict[str, Any]]: The list of versions in the scope, ordered from oldest to newest.
        """
        scope = scope.strip().lower() if scope else self.scope
        return self.storage.versions(self.package, scope)

    def save(self) -> bool:
        """\
        No-op compatibility method. In the new SQLite-backed system, data is
        persisted automatically on every `set` / `unset` / `setdef` call.

        Returns:
            bool: Always returns True.
        """
        return True


class AhvnConfigManager(ConfigManager):
    def setup(self, reset: bool = False) -> bool:
        setup_ok = super().setup(reset=reset)
        if reset:
            tmp_path = pj(self.get("core.tmp_path", default="%/tmp"), abs=True)
            _touch_dir(tmp_path, clear=True)
            cache_path = pj(self.get("core.cache_path", default="%/cache"), abs=True)
            _touch_dir(cache_path, clear=True)

        # During singleton bootstrap (CM_AHVN module import), avoid importing
        # stores that depend on this module to prevent circular imports.
        if not hasattr(self, "_singleton_sig"):
            return setup_ok

        # Ensure persistent stores are initialized on setup,
        # and reset to clean state when requested.
        from ahvn.tool.store import get_toolkit_store
        from ahvn.utils.capsule.store import get_capsule_store
        from ahvn.utils.prompt.prompt_spec import get_prompt_manager, setup_system_prompts
        from ahvn.utils.prompt.prompt_store import get_prompt_store
        from ahvn.utils.prompt.translate import get_translation_store

        toolkit_store = get_toolkit_store()
        capsule_store = get_capsule_store()
        get_prompt_manager()
        prompt_store = get_prompt_store()
        translation_store = get_translation_store()
        if reset:
            toolkit_store.clear()
            capsule_store.clear()
            prompt_store.clear()
            translation_store.clear()
        setup_system_prompts(force=reset)

        return setup_ok


CM_AHVN = AhvnConfigManager(package="ahvn", distribution="agent-heaven", scope="ahvn", setup=True)
HEAVEN_CM = CM_AHVN
COMPATIBILITY_AHVN = {
    "0.9.4.dev0": "0.9.4.dev0",
    "0.9.4": "0.9.4.dev0",
}
CM_AHVN.register(versions=COMPATIBILITY_AHVN)
CM_AHVN.compatibility_table = COMPATIBILITY_AHVN


# Deprecating soon, use `CM_AHVN.pj` instead
def hpj(*args: List[str], abs: bool = False) -> str:
    return CM_AHVN.pj(*args, abs=abs)
