"""Function Capsule core implementation."""

from __future__ import annotations

__all__ = [
    "Capsule",
    "register_layer",
    "CapsuleError",
    "CapsuleCreationError",
    "CapsuleRestorationError",
    "CAPSULE_VERSION",
    "SUPPORTED_VERSIONS",
]

import ast
import asyncio
import datetime
import functools
import hashlib
import importlib
import inspect
import os
import re
import sys
import tempfile
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING, Literal, Union

from ..basic.file_utils import delete_dir, exists_path
from ..basic.func_utils import code2func
from ..basic.hash_utils import fmt_hash, md5hash
from ..basic.log_utils import get_logger
from ..basic.path_utils import get_file_basename, get_file_dir, get_file_ext, get_file_name, pj
from ..basic.serialize_utils import (
    deserialize_path,
    dumps_json,
    load_json,
    loads_json,
    save_json,
    serialize_func,
    serialize_path,
)

if TYPE_CHECKING:
    from ...tool.base import ToolSpec

logger = get_logger(__name__)

CAPSULE_VERSION = "1.0"
SUPPORTED_VERSIONS = {"1.0"}

_LAYER_REGISTRY: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
LayerName = Union[Literal["source", "cloudpickle", "snapshot", "runner"], str]
_GLOBAL_REF_KEY = "__capsule_global_ref__"
_GLOBAL_REF_MODULE = "module"
_GLOBAL_REF_ATTR = "module_attr"
_GLOBAL_REF_REPR = "repr"


class CapsuleError(Exception):
    """Base exception for capsule operations."""


class CapsuleCreationError(CapsuleError):
    """Raised when capsule creation fails."""


class CapsuleRestorationError(CapsuleError):
    """Raised when all recovery layers have been exhausted."""


def register_layer(
    type_name: str,
    builder: Optional[Callable] = None,
    restorer: Optional[Callable] = None,
) -> None:
    """Register a custom capsule layer type."""
    if builder is None and restorer is None:
        raise ValueError("At least one of builder or restorer must be provided.")
    _LAYER_REGISTRY[type_name] = (builder, restorer)


def _verify_global_ref_roundtrip(module_name: str, qualname: str, original: Any) -> bool:
    """Check whether re-importing *module_name* and traversing *qualname* yields
    an object identical (``is``) to *original*.  Returns ``False`` on any failure."""
    try:
        target = importlib.import_module(module_name)
        for part in qualname.split("."):
            target = getattr(target, part)
        return target is original
    except Exception:
        return False


def _find_value_in_module(value: Any, module_name: str) -> Optional[Dict[str, str]]:
    """Search *module_name*'s namespace for an attribute that ``is`` *value*.

    Returns a serializable global-ref dict on success, ``None`` otherwise.
    """
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        return None
    for attr_name in dir(mod):
        try:
            if getattr(mod, attr_name) is value:
                if _verify_global_ref_roundtrip(module_name, attr_name, value):
                    return {
                        _GLOBAL_REF_KEY: _GLOBAL_REF_ATTR,
                        "module": module_name,
                        "qualname": attr_name,
                    }
        except Exception:
            continue
    return None


def _serialize_repr_global(value: Any) -> Optional[Dict[str, str]]:
    """Last-resort serialization via ``repr()``.

    Only emitted when the repr is likely to be eval-safe (round-trips
    successfully in a namespace containing common builtins and the value's
    own module).
    """
    try:
        text = repr(value)
        if not text or len(text) > 2048:
            return None
        # Reject repr strings that contain potentially dangerous tokens.
        _REPR_BLOCKLIST = ("__import__", "exec(", "eval(", "os.", "subprocess", "open(")
        if any(tok in text for tok in _REPR_BLOCKLIST):
            return None
        # Quick smoke test: try to eval it back to verify round-trip.
        ns: Dict[str, Any] = {"__builtins__": {}}
        mod_name = getattr(value, "__module__", None)
        if isinstance(mod_name, str) and mod_name:
            try:
                ns[mod_name.split(".")[0]] = importlib.import_module(mod_name.split(".")[0])
                ns.update(vars(importlib.import_module(mod_name)))
            except Exception:
                pass
        reconstructed = eval(text, ns)  # noqa: S307 – trusted internal repr
        if reconstructed == value:
            return {
                _GLOBAL_REF_KEY: _GLOBAL_REF_REPR,
                "repr": text,
                "module": mod_name or "",
            }
    except Exception:
        pass
    return None


def _serialize_importable_global(value: Any, *, func_module: Optional[str] = None) -> Optional[Dict[str, str]]:
    if inspect.ismodule(value):
        module_name = getattr(value, "__name__", None)
        if isinstance(module_name, str) and module_name:
            return {
                _GLOBAL_REF_KEY: _GLOBAL_REF_MODULE,
                "module": module_name,
            }
        return None

    module_name = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)

    # Try the straightforward module_attr reference first.
    if isinstance(module_name, str) and module_name and isinstance(qualname, str) and qualname and "<locals>" not in qualname:
        if _verify_global_ref_roundtrip(module_name, qualname, value):
            return {
                _GLOBAL_REF_KEY: _GLOBAL_REF_ATTR,
                "module": module_name,
                "qualname": qualname,
            }

    # The naïve reference does not round-trip (common for parameterised
    # type aliases like ``Union[...]``, ``Optional[...]``, dataclass fields
    # created by frameworks, etc.).  Try to locate the value by scanning
    # the function's defining module.
    if func_module:
        ref = _find_value_in_module(value, func_module)
        if ref is not None:
            return ref

    # Fall back to repr-based serialization.
    return _serialize_repr_global(value)


def _restore_serialized_global(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    ref_type = value.get(_GLOBAL_REF_KEY)
    if ref_type == _GLOBAL_REF_MODULE:
        module_name = value.get("module")
        if not isinstance(module_name, str) or not module_name:
            raise ValueError("Invalid module reference in source globals.")
        return importlib.import_module(module_name)

    if ref_type == _GLOBAL_REF_ATTR:
        module_name = value.get("module")
        qualname = value.get("qualname")
        if not isinstance(module_name, str) or not module_name:
            raise ValueError("Invalid module_attr reference (missing module).")
        if not isinstance(qualname, str) or not qualname:
            raise ValueError("Invalid module_attr reference (missing qualname).")
        target = importlib.import_module(module_name)
        for part in qualname.split("."):
            target = getattr(target, part)
        return target

    if ref_type == _GLOBAL_REF_REPR:
        text = value.get("repr")
        if not isinstance(text, str) or not text:
            raise ValueError("Invalid repr reference (missing repr text).")
        # Safety: repr-based restoration uses eval.  Capsule data is
        # considered trusted (it originates from our own serialization
        # pipeline), but we apply a minimal allowlist to limit the blast
        # radius of any corrupted/tampered repr strings.
        _REPR_BLOCKLIST = ("__import__", "exec(", "eval(", "os.", "subprocess", "open(")
        if any(tok in text for tok in _REPR_BLOCKLIST):
            raise ValueError(f"Repr text contains blocked token — refusing eval: {text[:80]!r}")
        ns: Dict[str, Any] = {"__builtins__": {}}
        mod_name = value.get("module", "")
        if isinstance(mod_name, str) and mod_name:
            try:
                root_mod = importlib.import_module(mod_name.split(".")[0])
                ns[mod_name.split(".")[0]] = root_mod
                ns.update(vars(importlib.import_module(mod_name)))
            except Exception:
                pass
        return eval(text, ns)  # noqa: S307 – trusted capsule data

    return value


def _restore_source_globals(globals_data: Dict[str, Any]) -> Dict[str, Any]:
    restored: Dict[str, Any] = {}
    for name, value in (globals_data or {}).items():
        if not isinstance(name, str) or not name:
            continue
        try:
            restored[name] = _restore_serialized_global(value)
        except Exception as exc:
            logger.debug("Failed to restore source global '%s': %s", name, exc)
    return restored


def _extract_simple_globals(func: Callable, code: str) -> Dict[str, Any]:
    try:
        tree = ast.parse(code)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    except Exception:
        return {}

    func_module = getattr(func, "__module__", None) or ""
    func_globals = getattr(func, "__globals__", {})
    result: Dict[str, Any] = {}
    for name in names:
        if name not in func_globals or name == func.__name__:
            continue
        value = func_globals[name]
        if isinstance(value, (int, float, str, bool, type(None))):
            result[name] = value
        elif isinstance(value, (list, dict)):
            try:
                from ..basic.serialize_utils import dumps_json

                dumps_json(value, indent=None)
                result[name] = value
            except (TypeError, ValueError):
                pass
        else:
            importable_ref = _serialize_importable_global(value, func_module=func_module)
            if importable_ref is not None:
                result[name] = importable_ref
    return result


def _build_source_imports(func: Callable, func_code: str) -> List[Dict[str, Any]]:
    """Extract the absolute-form import statements that *func* depends on.

    Analyses the **module-level** imports of the file where *func* is defined,
    resolves any relative imports to their absolute forms, and filters down to
    those that actually provide names used inside *func_code*.

    Returns a list of structured import descriptors::

        {"type": "import", "module": "os.path", "alias": "osp"}
        {"type": "from",   "module": "ahvn.cache", "names": [{"name": "CacheEntry", "alias": null}]}

    These are deterministic, JSON-serializable, and can be losslessly
    reconstructed into Python import statements by ``_imports_to_code``.
    """
    module_name = getattr(func, "__module__", "") or ""

    # Collect all names actually referenced inside the function source.
    try:
        func_tree = ast.parse(func_code)
        used_names: set[str] = set()
        for node in ast.walk(func_tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                # Capture top-level attribute access (e.g. ``os.path``)
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    used_names.add(root.id)
    except Exception:
        return []

    if not used_names:
        return []

    # Parse the module source to get its top-level import statements.
    try:
        source_file = inspect.getfile(func)
        with open(source_file, "r", encoding="utf-8") as fh:
            module_source = fh.read()
        module_tree = ast.parse(module_source)
    except Exception:
        return []

    imports: List[Dict[str, Any]] = []
    for node in ast.iter_child_nodes(module_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                if local_name in used_names:
                    entry: Dict[str, Any] = {"type": "import", "module": alias.name}
                    if alias.asname:
                        entry["alias"] = alias.asname
                    imports.append(entry)

        elif isinstance(node, ast.ImportFrom):
            abs_module = (
                _resolve_relative_module(
                    module_name,
                    node.module,
                    int(node.level or 0),
                )
                if node.level
                else node.module
            )
            if not abs_module:
                continue
            needed = []
            for alias in node.names:
                local_name = alias.asname or alias.name
                if local_name in used_names:
                    name_entry: Dict[str, Optional[str]] = {"name": alias.name}
                    if alias.asname:
                        name_entry["alias"] = alias.asname
                    else:
                        name_entry["alias"] = None
                    needed.append(name_entry)
            if needed:
                imports.append({"type": "from", "module": abs_module, "names": needed})

    return imports


def _imports_to_code(imports: List[Dict[str, Any]]) -> str:
    """Reconstruct Python import statements from structured descriptors."""
    lines: List[str] = []
    for entry in imports:
        if entry.get("type") == "import":
            alias = entry.get("alias")
            stmt = f"import {entry['module']}"
            if alias:
                stmt += f" as {alias}"
            lines.append(stmt)
        elif entry.get("type") == "from":
            names = entry.get("names", [])
            parts = []
            for n in names:
                if n.get("alias"):
                    parts.append(f"{n['name']} as {n['alias']}")
                else:
                    parts.append(n["name"])
            if parts:
                lines.append(f"from {entry['module']} import {', '.join(parts)}")
    return "\n".join(lines)


def _resolve_relative_module(module_name: str, imported_module: Optional[str], level: int) -> Optional[str]:
    """Resolve a relative import to an absolute module name.

    Uses ``importlib.util.resolve_name`` when possible (package-aware,
    handles ``__init__.py`` correctly).  Falls back to manual segment
    arithmetic when the defining module is not loaded in ``sys.modules``.
    """
    if level <= 0:
        return imported_module
    if not module_name or module_name in {"__main__", "__mp_main__"}:
        return None

    # Build the relative name expected by resolve_name:  "." * level + tail
    rel_name = "." * level + (imported_module or "")

    # Determine the package anchor.  For regular modules the __package__
    # attribute is the parent package; for __init__.py modules it equals
    # the module name itself.  We prefer __package__ from sys.modules
    # because it handles both cases correctly.
    package: Optional[str] = None
    mod = sys.modules.get(module_name)
    if mod is not None:
        package = getattr(mod, "__package__", None)
    if not package:
        # If the module isn't loaded, fall back to parent-package heuristic
        # (equivalent to the old segment arithmetic, correct for regular
        # modules but ambiguous for packages).
        parts = module_name.rsplit(".", 1)
        package = parts[0] if len(parts) > 1 else module_name

    try:
        import importlib.util as _ilu

        return _ilu.resolve_name(rel_name, package)
    except (ImportError, ValueError):
        pass

    # Ultimate fallback: manual segment arithmetic (pre-existing logic).
    parts = module_name.split(".")
    if level > len(parts):
        return None
    parent_parts = parts[:-level]
    if imported_module:
        parent_parts += imported_module.split(".")
    if not parent_parts:
        return None
    return ".".join(parent_parts)


def _extract_import_modules(code: str, module_name: Optional[str] = None) -> List[str]:
    try:
        tree = ast.parse(code)
    except Exception:
        return []

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = (alias.name or "").strip()
                if imported:
                    modules.add(imported)
        elif isinstance(node, ast.ImportFrom):
            imported = _resolve_relative_module(module_name or "", node.module, int(node.level or 0))
            if imported:
                modules.add(imported)
    return sorted(modules)


def _extract_modules_from_globals(globals_data: Optional[Dict[str, Any]]) -> List[str]:
    modules: set[str] = set()
    for value in (globals_data or {}).values():
        if not isinstance(value, dict):
            continue
        ref_type = value.get(_GLOBAL_REF_KEY)
        if ref_type in {_GLOBAL_REF_MODULE, _GLOBAL_REF_ATTR, _GLOBAL_REF_REPR}:
            module_name = value.get("module")
            if isinstance(module_name, str) and module_name:
                modules.add(module_name)
    return sorted(modules)


def _infer_project_root(func: Optional[Callable]) -> Optional[str]:
    if func is None:
        return None
    try:
        source_file = inspect.getfile(func)
    except (TypeError, OSError):
        return None

    source_file = pj(source_file, abs=True)
    func_module = getattr(func, "__module__", "") or ""
    if func_module and func_module not in {"__main__", "__mp_main__"}:
        root_pkg = func_module.split(".")[0]
        marker = f"{os.sep}{root_pkg}{os.sep}"
        source_lower = source_file.lower()
        marker_lower = marker.lower()
        idx = source_lower.rfind(marker_lower)
        if idx > 0:
            return source_file[:idx]
    return get_file_dir(source_file, abs=True)


def _module_origin(module_name: str) -> Optional[str]:
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception:
        return None
    if spec is None:
        return None

    origin = getattr(spec, "origin", None)
    if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
        return pj(origin, abs=True)

    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        first = next(iter(locations), None)
        if isinstance(first, str):
            return pj(first, abs=True)
    return None


def _classify_module(module_name: str, project_root: Optional[str] = None) -> str:
    root = module_name.split(".")[0]
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    if root in stdlib_names:
        return "stdlib"

    origin = _module_origin(module_name)
    if origin and project_root:
        try:
            if os.path.commonpath([pj(origin, abs=True), pj(project_root, abs=True)]) == pj(project_root, abs=True):
                return "internal"
        except ValueError:
            pass
    if origin:
        return "external"
    return "unknown"


def _build_requirements_payload(
    func: Optional[Callable],
    code: Optional[str],
    *,
    globals_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not code:
        return None

    module_name = getattr(func, "__module__", "") if func is not None else ""
    imported_modules = set(_extract_import_modules(code, module_name=module_name))
    imported_modules.update(_extract_modules_from_globals(globals_data))
    imported_modules = set(m for m in imported_modules if m and (not m.startswith(".")))
    imported_modules.discard("__future__")
    if not imported_modules:
        return None

    project_root = _infer_project_root(func)
    modules: List[Dict[str, str]] = []
    python_packages: set[str] = set()
    for module in sorted(imported_modules):
        kind = _classify_module(module, project_root=project_root)
        root = module.split(".")[0]
        if kind in {"internal", "external"}:
            python_packages.add(root)
        modules.append(
            {
                "name": module,
                "kind": kind,
            }
        )

    return {
        "modules": modules,
        "python_packages": sorted(python_packages),
    }


def _merge_dependency_payloads(
    dependencies: Optional[Dict[str, Any]],
    inferred: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = deepcopy(dependencies) if isinstance(dependencies, dict) else {}
    if not isinstance(inferred, dict):
        return merged

    existing_packages = merged.get("python_packages", [])
    pkg_set = set(str(v) for v in existing_packages if isinstance(v, str) and v)
    pkg_set.update(v for v in inferred.get("python_packages", []) if isinstance(v, str) and v)
    if pkg_set:
        merged["python_packages"] = sorted(pkg_set)

    module_map: Dict[str, Dict[str, str]] = {}
    for item in merged.get("modules", []):
        if isinstance(item, str):
            module_map[item] = {"name": item, "kind": "unknown"}
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            module_map[name] = {
                "name": name,
                "kind": str(item.get("kind", "unknown")),
            }

    for item in inferred.get("modules", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in module_map:
            if module_map[name].get("kind") == "unknown":
                module_map[name]["kind"] = str(item.get("kind", "unknown"))
            continue
        module_map[name] = {
            "name": name,
            "kind": str(item.get("kind", "unknown")),
        }

    if module_map:
        merged["modules"] = [module_map[name] for name in sorted(module_map)]
    return merged


def _extract_requirement_modules(requirements: Any) -> List[str]:
    if not isinstance(requirements, dict):
        return []

    modules: set[str] = set()
    module_entries = requirements.get("modules", [])
    if isinstance(module_entries, list):
        for item in module_entries:
            if isinstance(item, str):
                if item:
                    modules.add(item)
                continue
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name:
                    modules.add(name)
    return sorted(modules)


def _normalize_requirements_payload(requirements: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = {
        "modules": [],
        "python_packages": [],
    }
    if not isinstance(requirements, dict):
        return normalized

    module_map: Dict[str, Dict[str, str]] = {}
    module_entries = requirements.get("modules", [])
    if isinstance(module_entries, list):
        for item in module_entries:
            if isinstance(item, str):
                name = item
                kind = "unknown"
            elif isinstance(item, dict):
                name = item.get("name")
                kind = item.get("kind", "unknown")
            else:
                continue
            if not isinstance(name, str) or not name:
                continue
            if name in module_map:
                if module_map[name]["kind"] == "unknown":
                    module_map[name]["kind"] = str(kind)
                continue
            module_map[name] = {
                "name": name,
                "kind": str(kind),
            }

    pkg_entries = requirements.get("python_packages", [])
    if isinstance(pkg_entries, list):
        normalized["python_packages"] = sorted({str(v) for v in pkg_entries if isinstance(v, str) and v})
    normalized["modules"] = [module_map[name] for name in sorted(module_map)]
    return normalized


def _validate_layer_requirements(layer_type: str, layer: Dict[str, Any]) -> None:
    requirements = layer.get("requirements")
    if layer_type in {"source", "cloudpickle"} and not isinstance(requirements, dict):
        raise CapsuleRestorationError(f"Layer '{layer_type}' missing requirements metadata.")
    if not isinstance(requirements, dict):
        return

    modules = _extract_requirement_modules(requirements)
    if not modules:
        return

    missing: List[str] = []
    for module_name in modules:
        try:
            spec = importlib.util.find_spec(module_name)
        except Exception:
            spec = None
        if spec is None:
            missing.append(module_name)
    if missing:
        raise CapsuleRestorationError(f"Layer '{layer_type}' missing requirements: {', '.join(sorted(set(missing)))}")


def _build_schema(tool_spec: Optional["ToolSpec"]) -> Dict[str, Any]:
    schema: Dict[str, Any] = {}
    if tool_spec is None:
        return schema

    try:
        schema["description"] = tool_spec.binded.description or ""
    except Exception:
        schema["description"] = ""

    try:
        schema["input_schema"] = tool_spec.input_schema
    except Exception:
        schema["input_schema"] = {}

    try:
        schema["output_schema"] = tool_spec.output_schema
    except Exception:
        schema["output_schema"] = {}

    return schema


def _serialize_toolspec_state_value(value: Any) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    json_error = ""
    try:
        payload = dumps_json(value, indent=None)
        return {"encoding": "json", "payload": payload}, None
    except Exception as exc:
        json_error = str(exc)

    try:
        import cloudpickle

        payload = cloudpickle.dumps(value).hex()
        return {"encoding": "cloudpickle", "payload": payload}, None
    except Exception as exc:
        return None, f"json_error={json_error}; cloudpickle_error={exc}"


def _deserialize_toolspec_state_value(encoded: Dict[str, Any]) -> Any:
    if not isinstance(encoded, dict):
        raise ValueError(f"Invalid state payload: {type(encoded)}")
    encoding = encoded.get("encoding")
    payload = encoded.get("payload")
    if encoding == "json":
        if not isinstance(payload, str):
            raise ValueError("JSON state payload must be a string.")
        return loads_json(payload)
    if encoding == "cloudpickle":
        if not isinstance(payload, str):
            raise ValueError("Cloudpickle state payload must be a hex string.")
        import cloudpickle

        return cloudpickle.loads(bytes.fromhex(payload))
    raise ValueError(f"Unknown state payload encoding: {encoding}")


def _build_toolspec_metadata(tool_spec: Optional["ToolSpec"]) -> Optional[Dict[str, Any]]:
    if tool_spec is None:
        return None

    meta: Dict[str, Any] = {
        "lossless": True,
        "name": "",
        "description": "",
        "binds": {},
        "state": {},
        "warnings": [],
    }

    try:
        meta["name"] = tool_spec.name
    except Exception:
        meta["name"] = ""

    try:
        meta["description"] = tool_spec.binded.description or ""
    except Exception:
        meta["description"] = ""

    try:
        binds = deepcopy(tool_spec.binds)
        if isinstance(binds, dict):
            meta["binds"] = binds
        else:
            meta["binds"] = {}
    except Exception as exc:
        meta["lossless"] = False
        meta["warnings"].append(f"Failed to copy binds: {exc}")
        meta["binds"] = {}

    try:
        state_raw = getattr(tool_spec, "state", {})
        state = dict(state_raw) if isinstance(state_raw, dict) else {}
    except Exception as exc:
        meta["lossless"] = False
        meta["warnings"].append(f"Failed to copy state: {exc}")
        state = {}

    serialized_state: Dict[str, Dict[str, str]] = {}
    for key, value in state.items():
        encoded, error = _serialize_toolspec_state_value(value)
        if encoded is None:
            meta["lossless"] = False
            meta["warnings"].append(f"State key '{key}' was dropped: {error}")
            continue
        serialized_state[str(key)] = encoded
    meta["state"] = serialized_state

    if not meta["warnings"]:
        meta.pop("warnings", None)
    return meta


def _apply_toolspec_metadata(tool_spec: "ToolSpec", metadata: Optional[Dict[str, Any]], *, layer_type: str) -> "ToolSpec":
    if not isinstance(metadata, dict) or not metadata:
        return tool_spec

    warnings_list: List[str] = []

    name = metadata.get("name")
    if isinstance(name, str) and name:
        try:
            tool_spec.tool.name = name
        except Exception as exc:
            warnings_list.append(f"Failed to restore name: {exc}")

    description = metadata.get("description")
    if isinstance(description, str):
        try:
            tool_spec.tool.description = description
        except Exception as exc:
            warnings_list.append(f"Failed to restore description: {exc}")

    restored_state: Dict[str, Any] = {}
    state_data = metadata.get("state")
    if isinstance(state_data, dict):
        for key, encoded in state_data.items():
            try:
                restored_state[str(key)] = _deserialize_toolspec_state_value(encoded)
            except Exception as exc:
                warnings_list.append(f"State key '{key}' failed to restore: {exc}")

    binds = metadata.get("binds")
    if isinstance(binds, dict):
        tool_spec.binds = deepcopy(binds)
    else:
        warnings_list.append("Invalid binds payload; binds were skipped.")
        tool_spec.binds = {}

    tool_spec.state = restored_state
    try:
        tool_spec._clear_cache()
    except Exception:
        pass

    stored_warnings = metadata.get("warnings")
    if isinstance(stored_warnings, list):
        warnings_list.extend(str(msg) for msg in stored_warnings)

    lossless = bool(metadata.get("lossless", True)) and (len(warnings_list) == 0)
    tool_spec._capsule_lossless = lossless

    if not lossless:
        logger.warning(
            "ToolSpec restored with state loss on layer '%s': %s",
            layer_type,
            " | ".join(warnings_list) if warnings_list else "unknown reason",
        )

    return tool_spec


def _build_source_layer(
    func: Callable,
    blob: Optional[Dict[str, Any]] = None,
    *,
    globals_data: Optional[Dict[str, Any]] = None,
    requirements: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        if getattr(func, "__name__", "") == "<lambda>":
            return None
        if blob is None:
            blob = serialize_func(func)
        code = blob.get("code")
        if not code:
            return None

        name = blob.get("name", func.__name__)
        layer: Dict[str, Any] = {
            "type": "source",
            "code": code,
            "func_name": name,
            "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        }
        globals_data = globals_data if isinstance(globals_data, dict) else _extract_simple_globals(func, code)
        if globals_data:
            layer["globals"] = globals_data
        source_imports = _build_source_imports(func, code)
        if source_imports:
            layer["imports"] = source_imports
        requirements = requirements if isinstance(requirements, dict) else _build_requirements_payload(func, code, globals_data=globals_data)
        layer["requirements"] = _normalize_requirements_payload(requirements)
        return layer
    except Exception:
        return None


def _is_toolspec_instance(obj: Any) -> bool:
    try:
        from ...tool.base import ToolSpec

        return isinstance(obj, ToolSpec)
    except Exception:
        return False


def _build_cloudpickle_layer(
    target: Any,
    blob: Optional[Dict[str, Any]] = None,
    requirements: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        import cloudpickle

        hex_dumps = blob.get("hex_dumps") if isinstance(blob, dict) else None
        if not hex_dumps:
            hex_dumps = cloudpickle.dumps(target).hex()
        payload_kind = "toolspec" if _is_toolspec_instance(target) else "callable"
        layer = {
            "type": "cloudpickle",
            "hex_dumps": hex_dumps,
            "payload_kind": payload_kind,
            "python_version": sys.version.split()[0],
            "cloudpickle_version": cloudpickle.__version__,
        }
        layer["requirements"] = _normalize_requirements_payload(requirements)
        return layer
    except Exception:
        return None


def _build_snapshot_layer(
    func: Callable,
    snapshot_modules: Optional[List[str]],
) -> Optional[Dict[str, Any]]:
    if not snapshot_modules:
        return None

    try:
        snapshot_data: Dict[str, Optional[str]] = {}
        for mod_path in snapshot_modules:
            mod_path = pj(mod_path, abs=True)
            snapshot_data.update(serialize_path(mod_path))
        if not snapshot_data:
            return None

        mod = inspect.getmodule(func)
        entrypoint_file = None
        if mod and getattr(mod, "__file__", None):
            entrypoint_file = get_file_basename(pj(mod.__file__, abs=True))

        return {
            "type": "snapshot",
            "data": snapshot_data,
            "entrypoint_file": entrypoint_file,
            "func_name": func.__name__,
        }
    except Exception:
        return None


def _build_runner_layer(runner: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(runner, dict) or not runner:
        return None
    config = deepcopy(runner)
    if ("transport" not in config) and ("type" in config):
        config["transport"] = config.pop("type")
    if ("script" not in config) and ("script_path" in config):
        config["script"] = config["script_path"]
    if ("script_path" not in config) and ("script" in config):
        config["script_path"] = config["script"]
    layer = {"type": "runner"}
    layer.update(config)
    return layer


def _restore_source(layer: Dict[str, Any]) -> Callable:
    code = layer["code"]
    func_name = layer["func_name"]
    expected_sha = layer.get("sha256")
    if expected_sha:
        actual_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if actual_sha != expected_sha:
            # Intentional: warn-and-continue is the default policy for capsule source
            # restoration.  Strict integrity enforcement should be opt-in at the
            # CapsuleStore / application layer rather than silently aborting here.
            logger.warning(
                "Source layer SHA-256 mismatch (expected %s, got %s).",
                expected_sha[:12],
                actual_sha[:12],
            )
    env = _restore_source_globals(layer.get("globals") or {})

    # Execute captured import statements into *env* so the restored
    # function sees them in its ``__globals__``.  We must NOT prepend
    # them to the code string because ``code2func`` runs
    # ``exec(code, env, locals_dict)`` — imports would land in
    # ``locals_dict`` while the function reads ``__globals__`` (== env).
    source_imports = layer.get("imports")
    if isinstance(source_imports, list) and source_imports:
        import_block = _imports_to_code(source_imports)
        if import_block:
            try:
                exec(import_block, env)  # noqa: S102 – trusted capsule data
            except Exception as exc:
                logger.debug("Failed to execute source imports: %s", exc)

    return code2func(code, func_name=func_name, env=env)


def _restore_cloudpickle(layer: Dict[str, Any]) -> Callable:
    import cloudpickle

    hex_dumps = layer["hex_dumps"]
    stored_py = layer.get("python_version", "")
    current_py = sys.version.split()[0]
    if stored_py and stored_py != current_py:
        logger.warning(
            "Cloudpickle layer was created with Python %s but restoring on %s.",
            stored_py,
            current_py,
        )
    return cloudpickle.loads(bytes.fromhex(hex_dumps))


def _restore_snapshot(layer: Dict[str, Any]) -> Callable:
    import importlib.util

    data = layer["data"]
    func_name = layer["func_name"]
    entrypoint_file = layer.get("entrypoint_file")
    if not entrypoint_file:
        entrypoint_file = next((k for k in data if k.endswith(".py")), None)
    if not entrypoint_file:
        raise CapsuleRestorationError("Snapshot layer has no entrypoint file.")

    tmp_dir = tempfile.mkdtemp(prefix="capsule_snap_")
    try:
        deserialize_path(data, tmp_dir)
        entrypoint_file = pj(entrypoint_file, abs=False)
        module_path = get_file_name(entrypoint_file, ext=False, abs=False)
        module_name = ".".join([part for part in re.split(r"[\\/]+", module_path) if part])
        file_path = pj(tmp_dir, entrypoint_file, abs=True)

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise CapsuleRestorationError(f"Cannot create module spec from {file_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        func = getattr(module, func_name, None)
        if func is None or not callable(func):
            raise CapsuleRestorationError(f"Function '{func_name}' not found in snapshot module.")
        return func
    finally:
        delete_dir(tmp_dir)


def _restore_runner(layer: Dict[str, Any]):
    from ...tool.base import ToolSpec

    transport = layer.get("transport", "http")
    tool_name = layer.get("tool_name")
    if not tool_name:
        raise CapsuleRestorationError("Runner layer missing 'tool_name'.")

    if transport == "http":
        url = layer.get("url")
        if not url:
            raise CapsuleRestorationError("HTTP runner layer missing 'url'.")

        def create_client():
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            return Client(StreamableHttpTransport(url))

    elif transport == "stdio":
        script = layer.get("script") or layer.get("script_path")
        if not script:
            raise CapsuleRestorationError("Stdio runner layer missing 'script' or 'script_path'.")

        def create_client():
            from fastmcp import Client
            from fastmcp.client.transports import PythonStdioTransport

            return Client(PythonStdioTransport(script_path=script, env=layer.get("env")))

    else:
        raise CapsuleRestorationError(f"Unknown runner transport: {transport}. Supported transports are 'http' and 'stdio'.")

    async def _connect_and_get() -> ToolSpec:
        client = create_client()
        async with client:
            return await ToolSpec.from_client(client, tool_name)

    def _run_async(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        try:
            import nest_asyncio

            nest_asyncio.apply(loop)
            return loop.run_until_complete(coro)
        except ImportError:
            raise RuntimeError("Cannot run async capsule runner from a running event loop. " "Install 'nest_asyncio' or use an async context.")

    tool_spec = _run_async(_connect_and_get())

    async def reconnect_acall(**kwargs):
        client = create_client()
        async with client:
            result = await client.call_tool(tool_name, kwargs)
            if hasattr(result, "structured_content") and result.structured_content:
                structured = result.structured_content
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

    def reconnect_call(**kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(reconnect_acall(**kwargs))
        try:
            import nest_asyncio

            nest_asyncio.apply(loop)
            return loop.run_until_complete(reconnect_acall(**kwargs))
        except ImportError:
            raise RuntimeError(
                "Cannot call async tool synchronously from a running event loop. " "Please use 'await tool.acall(...)' instead, or install 'nest_asyncio'."
            )

    tool_spec.acall = reconnect_acall
    tool_spec.call = reconnect_call
    tool_spec.__call__ = reconnect_call
    return tool_spec


register_layer("source", builder=_build_source_layer, restorer=_restore_source)
register_layer("cloudpickle", builder=_build_cloudpickle_layer, restorer=_restore_cloudpickle)
register_layer("snapshot", builder=_build_snapshot_layer, restorer=_restore_snapshot)
register_layer("runner", builder=_build_runner_layer, restorer=_restore_runner)


def _restore_layer_result(
    layer_type: str,
    layer: Dict[str, Any],
    tool_spec_cls,
    *,
    toolspec_metadata: Optional[Dict[str, Any]] = None,
) -> "ToolSpec":
    _validate_layer_requirements(layer_type, layer)
    entry = _LAYER_REGISTRY.get(layer_type)
    if entry is None:
        raise CapsuleRestorationError(f"Unknown layer type: {layer_type}")

    _, restorer = entry
    if restorer is None:
        raise CapsuleRestorationError(f"Layer type '{layer_type}' has no restorer registered.")

    result = restorer(layer)
    if isinstance(result, tool_spec_cls):
        return result

    create_kwargs: Dict[str, Any] = {}
    if isinstance(toolspec_metadata, dict):
        name = toolspec_metadata.get("name")
        if isinstance(name, str) and name:
            create_kwargs["name"] = name
        description = toolspec_metadata.get("description")
        if isinstance(description, str) and description:
            create_kwargs["description"] = description

    tool_spec = tool_spec_cls.from_func(result, **create_kwargs)
    return _apply_toolspec_metadata(tool_spec, toolspec_metadata, layer_type=layer_type)


def _restore_layer_callable(
    layer_type: str,
    layer: Dict[str, Any],
) -> Callable:
    _validate_layer_requirements(layer_type, layer)
    entry = _LAYER_REGISTRY.get(layer_type)
    if entry is None:
        raise CapsuleRestorationError(f"Unknown layer type: {layer_type}")

    _, restorer = entry
    if restorer is None:
        raise CapsuleRestorationError(f"Layer type '{layer_type}' has no restorer registered.")

    result = restorer(layer)
    if callable(result):
        return result
    raise CapsuleRestorationError(f"Layer '{layer_type}' did not return a callable.")


def _normalize_tool_arguments(tool_spec: "ToolSpec", args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    call_kwargs = dict(kwargs)
    if not args:
        return call_kwargs

    schema = tool_spec.input_schema if isinstance(tool_spec.input_schema, dict) else {}
    properties = schema.get("properties", {}) if isinstance(schema.get("properties", {}), dict) else {}
    parameter_names = list(properties.keys())

    if not parameter_names:
        raise CapsuleRestorationError("Positional arguments are not supported for this capsule because the input schema has no parameter ordering.")
    if len(args) > len(parameter_names):
        raise CapsuleRestorationError(f"Too many positional arguments: got {len(args)} but at most {len(parameter_names)} are allowed.")

    for idx, value in enumerate(args):
        name = parameter_names[idx]
        if name in call_kwargs:
            raise CapsuleRestorationError(f"Ambiguous arguments: parameter '{name}' is provided by both positional and keyword argument.")
        call_kwargs[name] = value

    return call_kwargs


def _invoke_toolspec(tool_spec: "ToolSpec", *args, **kwargs):
    return tool_spec(**_normalize_tool_arguments(tool_spec, args, kwargs))


class Capsule:
    """Serializable function capsule."""

    DEFAULT_LAYERS: Tuple[LayerName, ...] = ("source", "cloudpickle", "snapshot", "runner")

    def __init__(self, data: Dict[str, Any]):
        self._data = deepcopy(data)

    @staticmethod
    def capsule_id(identity_key: str) -> str:
        """Deterministic capsule id from an identity key."""
        return fmt_hash(md5hash(identity_key))

    @property
    def id(self) -> Optional[str]:
        return self._data.get("capsule_id")

    @id.setter
    def id(self, value: Optional[str]) -> None:
        if value is None:
            self._data.pop("capsule_id", None)
        else:
            self._data["capsule_id"] = str(value)

    @property
    def capsule_version(self) -> Optional[str]:
        return self._data.get("capsule_version")

    @capsule_version.setter
    def capsule_version(self, value: Optional[Union[str, int]]) -> None:
        if value is None:
            self._data.pop("capsule_version", None)
        else:
            self._data["capsule_version"] = str(value)

    @property
    def version(self) -> Optional[str]:
        return self.capsule_version

    @version.setter
    def version(self, value: Optional[Union[str, int]]) -> None:
        self.capsule_version = value

    @property
    def checksum(self) -> Optional[str]:
        value = self._data.get("checksum")
        if value is None:
            return None
        return str(value)

    @checksum.setter
    def checksum(self, value: Optional[str]) -> None:
        if value is None:
            self._data.pop("checksum", None)
        else:
            self._data["checksum"] = str(value)

    @property
    def name(self) -> str:
        return str(self._data.get("manifest", {}).get("name", ""))

    @name.setter
    def name(self, value: str) -> None:
        manifest = self._data.setdefault("manifest", {})
        manifest["name"] = str(value)

    @property
    def qualname(self) -> str:
        return str(self._data.get("manifest", {}).get("qualname", ""))

    @qualname.setter
    def qualname(self, value: str) -> None:
        manifest = self._data.setdefault("manifest", {})
        manifest["qualname"] = str(value)

    @property
    def created_at(self) -> str:
        return str(self._data.get("manifest", {}).get("created_at", ""))

    @created_at.setter
    def created_at(self, value: str) -> None:
        manifest = self._data.setdefault("manifest", {})
        manifest["created_at"] = str(value)

    def to_dict(self) -> Dict[str, Any]:
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Capsule":
        return cls(data)

    @staticmethod
    def _normalize_capsule_data(capsule_or_data: Union["Capsule", Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(capsule_or_data, Capsule):
            cap = capsule_or_data.to_dict()
        else:
            cap = capsule_or_data
        if not isinstance(cap, dict):
            raise CapsuleRestorationError(f"Expected capsule dict or Capsule, got {type(capsule_or_data)}")
        return cap

    @classmethod
    def _select_layers(
        cls,
        cap: Dict[str, Any],
        *,
        layers: Optional[List[LayerName]] = None,
    ) -> List[Dict[str, Any]]:
        version = cap.get("capsule_version", "1.0")
        if version not in SUPPORTED_VERSIONS:
            raise CapsuleRestorationError(
                f"Unsupported capsule version '{version}'. " f"Supported versions: {sorted(SUPPORTED_VERSIONS)}. " "Upgrade ahvn to restore this capsule."
            )

        available_layers = cap.get("layers", [])
        if not available_layers:
            raise CapsuleRestorationError("Capsule has no layers.")

        if layers:
            layer_types = [str(v) for v in layers]
            order_map = {name: idx for idx, name in enumerate(layer_types)}
            available_layers = [layer for layer in available_layers if layer.get("type") in order_map]
            available_layers.sort(key=lambda layer: order_map.get(layer.get("type"), 10**9))
            if not available_layers:
                raise CapsuleRestorationError(f"No requested layers found in capsule: {layer_types}")

        return available_layers

    @classmethod
    def _restore_callable(
        cls,
        capsule_or_data: Union["Capsule", Dict[str, Any]],
        *,
        layers: Optional[List[LayerName]] = None,
    ) -> Callable:
        from ...tool.base import ToolSpec

        cap = cls._normalize_capsule_data(capsule_or_data)
        available_layers = cls._select_layers(cap, layers=layers)
        toolspec_metadata = cap.get("toolspec") if isinstance(cap.get("toolspec"), dict) else None
        schema = cap.get("schema") if isinstance(cap.get("schema"), dict) else None
        has_output_schema = schema is not None and schema.get("output_schema") not in (None, {})

        errors: List[str] = []

        # When there is no output_schema, prefer restoring the raw callable
        # so non-serializable return values (e.g. custom objects) are preserved.
        if not has_output_schema:
            for layer in available_layers:
                layer_type = layer.get("type")
                try:
                    restored = _restore_layer_callable(layer_type, layer)
                    if isinstance(restored, ToolSpec):
                        # Extract the raw function from the ToolSpec to avoid
                        # fastmcp serialization which would lose custom object returns.
                        raw_fn = getattr(restored.tool, "fn", None)
                        if callable(raw_fn):
                            return raw_fn
                    return restored
                except Exception as exc:
                    message = f"Layer '{layer_type}' failed: {exc}"
                    logger.debug(message)
                    errors.append(message)

        if isinstance(toolspec_metadata, dict):
            for layer in available_layers:
                layer_type = layer.get("type")
                try:
                    restored_tool = _restore_layer_result(
                        layer_type,
                        layer,
                        ToolSpec,
                        toolspec_metadata=toolspec_metadata,
                    )

                    def _toolspec_callable(*args, **kwargs):
                        return _invoke_toolspec(restored_tool, *args, **kwargs)

                    return _toolspec_callable
                except Exception as exc:
                    message = f"Layer '{layer_type}' failed: {exc}"
                    logger.debug(message)
                    errors.append(message)

        for layer in available_layers:
            layer_type = layer.get("type")
            try:
                restored = _restore_layer_callable(layer_type, layer)
                if isinstance(restored, ToolSpec):

                    def _runner_callable(*args, **kwargs):
                        return _invoke_toolspec(restored, *args, **kwargs)

                    return _runner_callable
                return restored
            except Exception as exc:
                message = f"Layer '{layer_type}' failed: {exc}"
                logger.debug(message)
                errors.append(message)

        error_detail = "\n".join(f"  - {err}" for err in errors)
        raise CapsuleRestorationError(f"All recovery layers exhausted:\n{error_detail}")

    def _summary(self) -> Dict[str, Any]:
        return {
            "capsule_id": self._data.get("capsule_id"),
            "capsule_version": self._data.get("capsule_version"),
            "name": self._data.get("manifest", {}).get("name"),
            "entrypoint": self._data.get("manifest", {}).get("entrypoint"),
            "python_version": self._data.get("manifest", {}).get("python_version"),
            "created_at": self._data.get("manifest", {}).get("created_at"),
            "description": self._data.get("schema", {}).get("description", ""),
            "layers": [layer.get("type") for layer in self._data.get("layers", [])],
            "lossless": self._data.get("toolspec", {}).get("lossless", True),
        }

    def __str__(self) -> str:
        from ..basic.serialize_utils import dumps_yaml

        return dumps_yaml(self._summary(), sort_keys=False, indent=2).strip()

    @classmethod
    def _normalize_input(cls, func_or_spec):
        from ...tool.base import ToolSpec

        func = None
        tool_spec: Optional[ToolSpec] = None

        if isinstance(func_or_spec, Capsule):
            return None, None, func_or_spec.to_dict()

        if isinstance(func_or_spec, ToolSpec):
            tool_spec = func_or_spec
            func = None
            try:
                func = tool_spec.tool.fn
            except Exception:
                func = None
            if func is None:
                try:
                    func = tool_spec.to_func()
                except Exception:
                    func = None
            return func, tool_spec, None

        if isinstance(func_or_spec, str):
            try:
                func = code2func(func_or_spec)
            except Exception as exc:
                raise CapsuleCreationError(f"Failed to extract function from code: {exc}") from exc
            try:
                tool_spec = ToolSpec.from_func(func)
            except Exception:
                tool_spec = None
            return func, tool_spec, None

        if callable(func_or_spec):
            func = func_or_spec
            try:
                tool_spec = ToolSpec.from_func(func)
            except Exception:
                tool_spec = None
            return func, tool_spec, None

        raise CapsuleCreationError(f"Unsupported input type: {type(func_or_spec)}")

    @classmethod
    def from_code(
        cls,
        code: str,
        *,
        func_name: Optional[str] = None,
        env: Optional[Dict[str, Any]] = None,
        layers: Optional[List[LayerName]] = None,
        snapshot_modules: Optional[List[str]] = None,
        transport: Optional[Dict[str, Any]] = None,
        dependencies: Optional[Dict[str, Any]] = None,
        identifier: Optional[str] = None,
    ) -> "Capsule":
        try:
            func = code2func(code=code, func_name=func_name, env=env)
        except Exception as exc:
            raise CapsuleCreationError(f"Failed to extract function from code: {exc}") from exc
        return cls.from_func(
            func,
            layers=layers,
            snapshot_modules=snapshot_modules,
            transport=transport,
            dependencies=dependencies,
            identifier=identifier,
        )

    @classmethod
    def from_func(
        cls,
        func_or_spec,
        *,
        layers: Optional[List[LayerName]] = None,
        snapshot_modules: Optional[List[str]] = None,
        transport: Optional[Dict[str, Any]] = None,
        dependencies: Optional[Dict[str, Any]] = None,
        identifier: Optional[str] = None,
    ) -> "Capsule":
        func, tool_spec, prebuilt = cls._normalize_input(func_or_spec)
        if prebuilt is not None:
            return cls(prebuilt)

        toolspec_metadata: Optional[Dict[str, Any]] = None
        if tool_spec is not None:
            try:
                toolspec_metadata = _build_toolspec_metadata(tool_spec)
                if isinstance(toolspec_metadata, dict) and (not bool(toolspec_metadata.get("lossless", True))):
                    warn_list = toolspec_metadata.get("warnings", [])
                    logger.warning(
                        "Capsule created with lossy ToolSpec state for '%s': %s",
                        toolspec_metadata.get("name") or getattr(func, "__name__", "unknown"),
                        " | ".join(str(msg) for msg in warn_list) if warn_list else "state/binds could not be fully serialized",
                    )
            except Exception as exc:
                logger.warning("Failed to serialize ToolSpec metadata: %s", exc)
                toolspec_metadata = {
                    "lossless": False,
                    "name": getattr(func, "__name__", "unknown"),
                    "binds": {},
                    "state": {},
                    "warnings": [f"Failed to serialize ToolSpec metadata: {exc}"],
                }

        requested_layers = layers
        built_layers: List[Dict[str, Any]] = []
        blob: Optional[Dict[str, Any]] = None
        globals_data: Optional[Dict[str, Any]] = None
        inferred_requirements: Optional[Dict[str, Any]] = None
        cloudpickle_targets: List[Tuple[Any, Optional[Dict[str, Any]]]] = []
        if tool_spec is not None:
            cloudpickle_targets.append((tool_spec, None))
        if func is not None:
            cloudpickle_targets.append((func, None))

        layers_order = list(requested_layers) if requested_layers is not None else list(cls.DEFAULT_LAYERS)

        if func is not None:
            try:
                blob = serialize_func(func)
            except Exception:
                blob = None
            if blob and blob.get("code"):
                globals_data = _extract_simple_globals(func, blob["code"])
                inferred_requirements = _build_requirements_payload(func, blob["code"], globals_data=globals_data)
            if cloudpickle_targets:
                cloudpickle_targets[-1] = (func, blob)

        for layer_type in layers_order:
            layer_type = str(layer_type)
            if layer_type == "source":
                if func is None:
                    continue
                source_layer = _build_source_layer(
                    func,
                    blob,
                    globals_data=globals_data,
                    requirements=inferred_requirements,
                )
                if source_layer:
                    built_layers.append(source_layer)
                continue

            if layer_type == "cloudpickle":
                if not cloudpickle_targets:
                    continue
                cloudpickle_layer = None
                for target, target_blob in cloudpickle_targets:
                    cloudpickle_layer = _build_cloudpickle_layer(target, target_blob, requirements=inferred_requirements)
                    if cloudpickle_layer:
                        break
                if cloudpickle_layer is not None:
                    built_layers.append(cloudpickle_layer)
                continue

            if layer_type == "snapshot":
                if func is None:
                    continue
                snapshot_layer = _build_snapshot_layer(func, snapshot_modules)
                if snapshot_layer:
                    built_layers.append(snapshot_layer)
                continue

            if layer_type == "runner":
                runner_layer = _build_runner_layer(transport)
                if runner_layer:
                    built_layers.append(runner_layer)
                continue

            builder, _ = _LAYER_REGISTRY.get(layer_type, (None, None))
            if builder is None:
                continue
            try:
                custom_layer = builder(func, blob)
            except TypeError:
                try:
                    custom_layer = builder(func)
                except Exception as exc:
                    logger.debug("Failed to build custom layer '%s': %s", layer_type, exc)
                    continue
            except Exception as exc:
                logger.debug("Failed to build custom layer '%s': %s", layer_type, exc)
                continue
            if custom_layer:
                built_layers.append(custom_layer)

        if not built_layers:
            raise CapsuleCreationError("No recovery layers could be built for the input.")

        func_name = func.__name__ if func else (tool_spec.name if tool_spec else "unknown")
        func_qualname = getattr(func, "__qualname__", func_name) if func else func_name
        func_module = getattr(func, "__module__", None) or ""

        try:
            func_sig = str(inspect.signature(func)) if func else ""
        except (ValueError, TypeError):
            func_sig = ""

        identity_key = identifier if identifier is not None else f"{func_module}:{func_qualname}{func_sig}"
        source_file = None
        if func is not None:
            try:
                source_file = inspect.getfile(func)
            except (TypeError, OSError):
                source_file = None

        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        manifest = {
            "name": func_name,
            "qualname": func_qualname,
            "module": func_module,
            "entrypoint": func_name,
            "python_version": sys.version.split()[0],
            "dependencies": _merge_dependency_payloads(dependencies, inferred_requirements),
            "created_at": created_at,
        }
        if source_file:
            manifest["source_file"] = source_file
        if identifier is not None:
            manifest["identifier"] = identifier

        data = {
            "capsule_version": CAPSULE_VERSION,
            "capsule_id": cls.capsule_id(identity_key),
            "checksum": fmt_hash(md5hash({"layers": built_layers, "toolspec": toolspec_metadata or {}})),
            "manifest": manifest,
            "schema": _build_schema(tool_spec),
            "layers": built_layers,
        }
        if isinstance(toolspec_metadata, dict):
            data["toolspec"] = toolspec_metadata
        return cls(data)

    @classmethod
    def from_file(cls, path: str) -> "Capsule":
        import gzip
        from ..basic.serialize_utils import loads_json

        path = pj(path, abs=True)
        if not get_file_ext(path):
            fcap_path = get_file_name(path, ext="fcap", abs=True)
            if exists_path(fcap_path):
                path = fcap_path
        if path.endswith(".fcap"):
            with gzip.open(path, "rt", encoding="utf-8") as fp:
                return cls.from_dict(loads_json(fp.read()))
        return cls.from_dict(load_json(path, strict=True))

    @classmethod
    def capsule(
        cls,
        func: Optional[Callable] = None,
        *,
        identifier: Optional[str] = None,
        **kwargs,
    ) -> Callable:
        def _decorate(fn: Callable) -> "Capsule":
            cap_obj = cls.from_func(fn, identifier=identifier, **kwargs)
            cap = cap_obj.to_dict()

            try:
                from .store import get_capsule_store

                store = get_capsule_store()
                capsule_id = cap["capsule_id"]
                existing_checksum = store.get_checksum(capsule_id)
                if existing_checksum != cap.get("checksum"):
                    store.add(cap)
                    logger.debug("Capsule '%s' stored (id=%s)", cap["manifest"]["qualname"], capsule_id[:12])
                else:
                    logger.debug("Capsule '%s' unchanged, skip store", cap["manifest"]["qualname"])
            except Exception as exc:
                logger.debug("Capsule auto-store failed: %s", exc)

            # Keep decorator output function-like for regular introspection.
            try:
                functools.update_wrapper(cap_obj, fn)
                cap_obj.__wrapped__ = fn
            except Exception:
                pass

            return cap_obj

        if func is None:
            return _decorate
        return _decorate(func)

    @classmethod
    def load(cls, path: str) -> "Capsule":
        return cls.from_file(path)

    def dump(self, path: str) -> str:
        import gzip
        from ..basic.serialize_utils import dumps_json

        path = pj(path, abs=True)
        if not get_file_ext(path):
            path = get_file_name(path, ext="fcap", abs=True)
        folder = get_file_dir(path, abs=True)
        if folder:
            from ..basic.file_utils import touch_dir

            touch_dir(folder)

        if path.endswith(".fcap"):
            with gzip.open(path, "wt", encoding="utf-8") as fp:
                fp.write(dumps_json(self.to_dict(), indent=None))
        else:
            save_json(self.to_dict(), path, indent=2)
        return path

    def save(self, path: str) -> str:
        import gzip
        from ..basic.serialize_utils import dumps_json

        path = pj(path, abs=True)
        if not get_file_ext(path):
            path = get_file_name(path, ext="fcap", abs=True)
        folder = get_file_dir(path, abs=True)
        if folder:
            from ..basic.file_utils import touch_dir

            touch_dir(folder)

        if path.endswith(".fcap"):
            with gzip.open(path, "wt", encoding="utf-8") as fp:
                fp.write(dumps_json(self.to_dict(), indent=None))
        else:
            save_json(self.to_dict(), path, indent=2)
        return path

    def to_tool(self, *, layers: Optional[List[LayerName]] = None) -> "ToolSpec":
        from ...tool.base import ToolSpec

        cap = Capsule._normalize_capsule_data(self)
        available_layers = Capsule._select_layers(cap, layers=layers)
        toolspec_metadata = cap.get("toolspec") if isinstance(cap.get("toolspec"), dict) else None

        errors: List[str] = []
        for layer in available_layers:
            layer_type = layer.get("type")
            try:
                restored = _restore_layer_result(
                    layer_type,
                    layer,
                    ToolSpec,
                    toolspec_metadata=toolspec_metadata,
                )
                if isinstance(toolspec_metadata, dict) and not hasattr(restored, "_capsule_lossless"):
                    restored._capsule_lossless = bool(toolspec_metadata.get("lossless", True))
                    if not restored._capsule_lossless:
                        logger.warning(
                            "ToolSpec restored from layer '%s' is marked lossy in capsule metadata.",
                            layer_type,
                        )
                return restored
            except Exception as exc:
                message = f"Layer '{layer_type}' failed: {exc}"
                logger.debug(message)
                errors.append(message)

        error_detail = "\n".join(f"  - {err}" for err in errors)
        raise CapsuleRestorationError(f"All recovery layers exhausted:\n{error_detail}")

    def call(self, *args, **kwargs):
        tool = self.to_tool()
        return _invoke_toolspec(tool, *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.call(*args, **kwargs)
