"""\
Versioned prompt specification with class-based API.

``PromptSpec`` wraps a function ``(**kwargs, tr) -> Messages`` together with
auto-managed versioning, checksum-based change detection, and optional
translation dictionary references for automatic ``tr`` injection.

Quick start::

    from ahvn.utils.prompt import PromptSpec, PM_AHVN

    @PromptSpec.prompt
    def greet(name, *, tr: Callable = str):
        return f"{tr('Hello')}, {name}!"

    # Add translations via the .tr shortcut
    greet.tr.set("Hello", "zh", "浣犲ソ")

    # Override language per call (highest priority)
    greet("Alice", lang="zh")   # 鈫?"浣犲ソ, Alice!"

    # Or via CM_AHVN scoping (affects all prompts in scope)
    from ahvn.utils.basic.config_utils import CM_AHVN
    with CM_AHVN.scoped("zh"):
        CM_AHVN.set("prompts.lang", "zh")
        greet("Alice")           # 鈫?"浣犲ソ, Alice!"

    # Quick template-based prompts template is translated as a whole,
    # trs lists placeholders whose values are also translated.
    hi = PromptSpec.from_str(
        "Hello, {name}! Welcome to {place}",
        trs=["place"],
    )
    hi.tr.set("Hello, {name}! Welcome to {place}", "zh",
              "浣犲ソ, {name}! 娆㈣繋鏉ュ埌 {place}")
    hi(name="Alice", place="Paris", lang="zh")  # 鈫?"浣犲ソ, Alice! 娆㈣繋鏉ュ埌 Paris"

Language resolution priority (highest 鈫?lowest):

1. ``lang`` kwarg in ``__call__`` per-invocation override.
2. ``lang`` in ``PM_AHVN.get(..., lang="zh")`` freezes language.
3. ``CM_AHVN`` scoped config ``prompts.lang`` resolved at call time.
4. ``main_lang`` of the TranslationDict (default ``"en"``).
"""

__all__ = [
    "PromptSpec",
    "prompt",
    "PromptManager",
    "get_prompt_manager",
    "setup_system_prompts",
    "ensure_system_prompts",
    "get_system_prompt_spec",
    "PM_AHVN",
    "TR_AHVN",
]

import inspect
import functools
import os
import textwrap
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from ahvn.utils.basic.hash_utils import md5hash, fmt_hash
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.capsule.core import Capsule
from ahvn.utils.llm.llm_utils import Messages
from ahvn.utils.prompt.prompt_store import get_prompt_store
from ahvn.utils.prompt.translate import get_translation_store

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
#  In-memory registry
# ------------------------------------------------------------------ #

# {prompt_id: {version: PromptSpec}}
_PROMPT_REGISTRY: Dict[str, Dict[int, "PromptSpec"]] = {}
_registry_lock = threading.Lock()


def _register(spec: "PromptSpec") -> None:
    with _registry_lock:
        bucket = _PROMPT_REGISTRY.setdefault(spec.id, {})
        bucket[spec.version] = spec


def _lookup(prompt_id: str, version: Optional[int] = None) -> Optional["PromptSpec"]:
    with _registry_lock:
        bucket = _PROMPT_REGISTRY.get(prompt_id)
        if bucket is None:
            return None
        if version is not None:
            return bucket.get(version)
        return bucket[max(bucket)] if bucket else None


def _unregister(prompt_id: str, version: Optional[int] = None) -> None:
    with _registry_lock:
        if version is not None:
            bucket = _PROMPT_REGISTRY.get(prompt_id)
            if bucket:
                bucket.pop(version, None)
        else:
            _PROMPT_REGISTRY.pop(prompt_id, None)


# ------------------------------------------------------------------ #
#  Internal helpers
# ------------------------------------------------------------------ #


def _get_source(fn: Callable) -> str:
    try:
        return textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return ""


def _get_source_file(fn: Callable) -> str:
    try:
        return os.path.abspath(inspect.getfile(fn))
    except (OSError, TypeError):
        return ""


def _compute_checksum(source_code: str) -> str:
    return fmt_hash(md5hash(source_code))


def _resolve_tr(
    td_refs: List[str],
    lang: Optional[str] = None,
    elicit: str = "none",
) -> Callable[[str], str]:
    """Build a ``tr`` callable from an ordered list of TranslationDict ids."""
    if not td_refs:
        return str

    from .translate import TranslationDict

    store = get_translation_store()

    dicts: List[TranslationDict] = []
    for ns in td_refs:
        if store.exists(ns):
            dicts.append(TranslationDict(namespace=ns, store=store))

    if not dicts:
        return str

    primary = dicts[0]
    fallbacks = dicts[1:] if len(dicts) > 1 else None
    return primary.tr(lang=lang, elicit=elicit, fallbacks=fallbacks)


def _ensure_inline_translations(td: Dict[str, Dict[str, Dict[str, str]]]) -> List[str]:
    """Create / update TranslationDicts from inline ``td`` mapping.

    ``td`` format::

        {
            "namespace": {
                "source_key": {"lang": "value", ...},
                ...
            },
        }

    Returns the ordered list of namespace ids.
    """
    from .translate import TranslationDict

    store = get_translation_store()
    ns_ids: List[str] = []

    for namespace, entries in td.items():
        ns_ids.append(namespace)
        td_obj = TranslationDict(namespace=namespace, store=store)
        for source_key, lang_map in entries.items():
            for lang, value in lang_map.items():
                td_obj.set(source_key, lang, value)

    return ns_ids


def _build_template_func(
    template: str,
    field_names: List[str],
    trs_keys: List[str],
    prompt_id: str,
) -> Callable[..., Messages]:
    """Reconstruct the closure that ``from_str`` creates.

    This is used both during initial ``from_str`` and when restoring a
    template-type prompt from the store (``from_store`` / ``from_dict``).
    """

    def _template_func(tr: Optional[Callable] = None, **kwargs):
        translator = tr or str
        for key in trs_keys:
            if key in kwargs:
                kwargs[key] = translator(kwargs[key])
        return translator(template).format(**kwargs)

    _template_func.__name__ = prompt_id
    _template_func.__qualname__ = prompt_id
    _template_func._template = template
    _template_func._fields = field_names
    _template_func._trs_keys = trs_keys
    return _template_func


def _build_inline_jinja_env(
    tr: Callable[[str], str],
):
    """Create an in-memory Jinja environment without filesystem/Babel dependencies."""
    from jinja2 import StrictUndefined
    from jinja2.nativetypes import NativeEnvironment

    from ..basic.serialize_utils import dumps_json
    from ..basic.str_utils import line_numbered, md_symbol, omission_list, value_repr

    env = NativeEnvironment(
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters.update(
        {
            "zip": zip,
            "value_repr": value_repr,
            "omission_list": omission_list,
            "md_symbol": md_symbol,
            "line_numbered": line_numbered,
            "dumps_json": dumps_json,
            "tr": tr,
        }
    )
    env.tests.update(
        {
            "ellipsis": lambda x: x is ...,
            "not_ellipsis": lambda x: x is not ...,
        }
    )
    env.globals.update(
        {
            "Ellipsis": ...,
            "ellipsis": ...,
            "tr": tr,
        }
    )
    return env


def _build_jinja_func(
    template: str,
    prompt_id: str,
) -> Callable[..., Messages]:
    """Reconstruct the closure that ``from_jinja`` creates."""

    def _jinja_func(tr: Optional[Callable] = None, **kwargs):
        translator = tr or str
        env = _build_inline_jinja_env(tr=translator)
        return env.from_string(template).render(**kwargs)

    _jinja_func.__name__ = prompt_id
    _jinja_func.__qualname__ = prompt_id
    _jinja_func._template = template
    return _jinja_func


_PROMPT_CAPSULE_KEY = "__prompt_capsule__"


def _pack_store_metadata(
    user_metadata: Optional[Dict[str, Any]],
    capsule_data: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(user_metadata or {})
    payload[_PROMPT_CAPSULE_KEY] = capsule_data
    return payload


def _unpack_store_capsule(
    store_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = dict(store_metadata or {})
    capsule_data = payload.get(_PROMPT_CAPSULE_KEY)
    if not isinstance(capsule_data, dict):
        raise ValueError("Prompt row is missing capsule payload metadata.")
    return capsule_data


# ------------------------------------------------------------------ #
#  _PromptTR per-prompt translation helper (internal)
# ------------------------------------------------------------------ #


class _PromptTR:
    """Translation helper bound to a PromptSpec's td_refs.

    Provides a shortcut for managing translations without touching
    ``TR_AHVN`` directly::

        greet.tr.set("Hello", "zh", "浣犲ソ")
        greet.tr.set_many({"ja": {"Hello": "銇撱倱銇仭銇?}})
        greet.tr.bind("shared_vocab")
    """

    __slots__ = ("_spec",)

    def __init__(self, spec: "PromptSpec"):
        self._spec = spec

    @property
    def tds(self) -> List[str]:
        """Current bound td namespaces."""
        return list(self._spec.td_refs)

    def _resolve_td(self, td: Optional[str] = None) -> str:
        if td is None:
            if not self._spec.td_refs:
                raise ValueError(f"Prompt '{self._spec.id}' has no bound td namespaces")
            return self._spec.td_refs[0]
        if td not in self._spec.td_refs:
            raise ValueError(f"td '{td}' not bound to prompt '{self._spec.id}'. " f"Bound: {self._spec.td_refs}")
        return td

    def set(self, key: str, lang: str, value: str, *, td: Optional[str] = None) -> None:
        """Set a single translation in the prompt's td namespace."""
        from .translate import TranslationDict

        ns = self._resolve_td(td)
        TranslationDict(namespace=ns, store=get_translation_store()).set(key, lang, value)

    def set_many(
        self,
        translations: Dict[str, Dict[str, str]],
        *,
        td: Optional[str] = None,
    ) -> None:
        """Bulk-set translations: ``{lang: {key: value, ...}, ...}``.

        This matches ``TranslationDict.set_many`` language code is the
        outer key, source text is the inner key.
        """
        from .translate import TranslationDict

        ns = self._resolve_td(td)
        TranslationDict(namespace=ns, store=get_translation_store()).set_many(translations)

    def get(self, key: str, lang: str, *, td: Optional[str] = None) -> Optional[str]:
        """Look up a single translation entry."""
        from .translate import TranslationDict

        ns = self._resolve_td(td)
        return TranslationDict(namespace=ns, store=get_translation_store()).lookup(key, lang)

    def delete(self, key: str, lang: str, *, td: Optional[str] = None) -> None:
        """Remove a single translation entry."""
        from .translate import TranslationDict

        ns = self._resolve_td(td)
        TranslationDict(namespace=ns, store=get_translation_store()).delete(key, lang)

    def bind(self, td: str) -> None:
        """Append a td namespace to the prompt's td_refs (persisted)."""
        if td not in self._spec.td_refs:
            self._spec.td_refs.append(td)
            self._persist_td_refs()

    def unbind(self, td: str) -> None:
        """Remove a td namespace from td_refs (persisted)."""
        if td in self._spec.td_refs:
            self._spec.td_refs.remove(td)
            self._persist_td_refs()

    def _persist_td_refs(self) -> None:
        """Write current td_refs back to the PromptStore."""
        spec = self._spec
        try:
            store = get_prompt_store()
            store.save(
                prompt_id=spec.id,
                version=spec.version,
                checksum=spec.checksum,
                qualname=spec.qualname,
                source_file=spec.source_file,
                source_code=spec.source_code,
                td_refs=list(spec.td_refs),
                metadata=_pack_store_metadata(spec.metadata, spec.to_dict()),
            )
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  PromptSpec
# ------------------------------------------------------------------ #


class PromptSpec(Capsule):
    """Versioned prompt specification wrapping a callable.

    Combines metadata (id, version, checksum), a prompt function, and
    translation dictionary references into a single object.

    After decoration, the PromptSpec **is** the decorated name::

        @PromptSpec.prompt
        def greet(name, tr=None): ...

        greet("Alice")      # calls __call__ 鈫?auto-injects tr
        greet.id             # "greet"
        greet.version        # 1
    """

    def __init__(
        self,
        id: str,
        version: int,
        checksum: str,
        func: Callable[..., Messages],
        td_refs: Optional[List[str]] = None,
        qualname: str = "",
        source_file: str = "",
        source_code: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        prompt_id = str(id)
        prompt_version = int(version)
        prompt_checksum = str(checksum)
        prompt_qualname = qualname or getattr(func, "__qualname__", prompt_id)
        prompt_source_file = source_file or _get_source_file(func)
        prompt_source_code = source_code or _get_source(func)
        prompt_metadata = dict(metadata or {})

        super().__init__(
            self._build_capsule_data(
                func=func,
                prompt_id=prompt_id,
                version=prompt_version,
                checksum=prompt_checksum,
                qualname=prompt_qualname,
                source_file=prompt_source_file,
            )
        )

        self._prompt_id = prompt_id
        self._prompt_version = prompt_version
        self._prompt_checksum = prompt_checksum
        self.func = func
        self.td_refs: List[str] = td_refs or []
        self.qualname = prompt_qualname
        self.source_file = prompt_source_file
        self.source_code = prompt_source_code
        self.metadata: Dict[str, Any] = prompt_metadata
        self._sync_capsule_prompt_data()
        # Make PromptSpec look like the wrapped function for introspection.
        functools.update_wrapper(self, func)
        self.__wrapped__ = func
        self._tr = _PromptTR(self)

    @staticmethod
    def _build_capsule_data(
        *,
        func: Callable[..., Messages],
        prompt_id: str,
        version: int,
        checksum: str,
        qualname: str,
        source_file: str,
    ) -> Dict[str, Any]:
        prompt_identity = f"prompt:{prompt_id}:{version}:{qualname}"
        cap_data = Capsule.from_func(func, identifier=prompt_identity).to_dict()
        manifest = cap_data.setdefault("manifest", {})
        manifest["name"] = prompt_id
        manifest["entrypoint"] = prompt_id
        manifest["qualname"] = qualname
        if source_file:
            manifest["source_file"] = source_file
        manifest["prompt_id"] = prompt_id
        manifest["prompt_version"] = int(version)
        manifest["prompt_checksum"] = str(checksum)
        return cap_data

    @property
    def id(self) -> str:
        return self._prompt_id

    @id.setter
    def id(self, value: str) -> None:
        prompt_id = str(value)
        self._prompt_id = prompt_id
        self._sync_capsule_prompt_data()

    @property
    def version(self) -> int:
        return self._prompt_version

    @version.setter
    def version(self, value: Union[int, str]) -> None:
        prompt_version = int(value)
        self._prompt_version = prompt_version
        self._sync_capsule_prompt_data()

    @property
    def checksum(self) -> str:
        return self._prompt_checksum

    @checksum.setter
    def checksum(self, value: str) -> None:
        prompt_checksum = str(value)
        self._prompt_checksum = prompt_checksum
        self._sync_capsule_prompt_data()

    def _sync_capsule_prompt_data(self) -> None:
        manifest = self._data.setdefault("manifest", {})
        manifest["name"] = self._prompt_id
        manifest["entrypoint"] = self._prompt_id
        manifest["qualname"] = self.qualname
        if self.source_file:
            manifest["source_file"] = self.source_file
        else:
            manifest.pop("source_file", None)
        manifest["prompt_id"] = self._prompt_id
        manifest["prompt_version"] = self._prompt_version
        manifest["prompt_checksum"] = self._prompt_checksum

        self._data["checksum"] = self._prompt_checksum
        self._data["prompt_spec"] = {
            "id": self._prompt_id,
            "version": self._prompt_version,
            "checksum": self._prompt_checksum,
            "td_refs": list(self.td_refs),
            "qualname": self.qualname,
            "source_file": self.source_file,
            "source_code": self.source_code,
            "metadata": dict(self.metadata),
        }

    @property
    def tr(self) -> "_PromptTR":
        """Translation helper bound to this prompt's td_refs."""
        return self._tr

    @property
    def prompt_type(self) -> str:
        """Normalized prompt type identifier."""
        return str(self.metadata.get("type") or "func")

    # -- execution ---------------------------------------------------- #

    def __call__(self, *args, **kwargs) -> Messages:
        """Call with automatic ``tr`` injection from ``td_refs``.

        Special kwargs (popped before reaching the prompt function):

        - ``lang`` override language for this single invocation
          (highest priority; does **not** affect nested PromptSpec calls).
        - ``elicit`` missing-translation behaviour for this call:
          ``"none"`` (default), ``"human"``, or ``"llm"``.
        """
        lang = kwargs.pop("lang", None)
        elicit = kwargs.pop("elicit", "none")
        tr = _resolve_tr(self.td_refs, lang=lang, elicit=elicit)
        kwargs.setdefault("tr", tr)
        return self.func(*args, **kwargs)

    # -- serialization ------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to capsule payload."""
        self._sync_capsule_prompt_data()
        return super().to_dict()

    def to_func(
        self,
        *,
        name: Optional[str] = None,
        bind: Optional[Dict[str, Any]] = None,
    ) -> Callable[..., Messages]:
        """Export this PromptSpec as a plain callable function."""
        bound_kwargs = dict(bind or {})

        @functools.wraps(self.func)
        def _prompt_func(*args, **kwargs):
            return self(*args, **(bound_kwargs | kwargs))

        fn_name = name or self.id
        _prompt_func.__name__ = fn_name
        _prompt_func.__qualname__ = fn_name
        _prompt_func.__prompt_spec__ = self
        return _prompt_func

    # -- factory classmethods ----------------------------------------- #

    @classmethod
    def from_dict(cls, data: Dict[str, Any], func: Optional[Callable] = None) -> "PromptSpec":
        """Restore PromptSpec from a capsule payload."""
        cap_data = Capsule.from_dict(data).to_dict()
        prompt_payload = cap_data.get("prompt_spec") or {}
        if not isinstance(prompt_payload, dict):
            raise ValueError("Invalid prompt capsule payload: missing 'prompt_spec'.")

        manifest = cap_data.get("manifest") if isinstance(cap_data.get("manifest"), dict) else {}
        if func is None:
            func = Capsule._restore_callable(cap_data)

        prompt_id = str(prompt_payload.get("id") or manifest.get("prompt_id") or manifest.get("name") or "")
        if not prompt_id:
            raise ValueError("Invalid prompt capsule payload: missing prompt id.")

        prompt_version_raw = prompt_payload.get("version")
        if prompt_version_raw is None:
            prompt_version_raw = manifest.get("prompt_version")
        prompt_version = int(prompt_version_raw) if prompt_version_raw is not None else 1
        prompt_checksum = str(prompt_payload.get("checksum") or manifest.get("prompt_checksum") or cap_data.get("checksum") or "")

        return cls(
            id=prompt_id,
            version=prompt_version,
            checksum=prompt_checksum,
            func=func,
            td_refs=prompt_payload.get("td_refs") or [],
            qualname=str(prompt_payload.get("qualname") or manifest.get("qualname") or ""),
            source_file=str(prompt_payload.get("source_file") or manifest.get("source_file") or ""),
            source_code=str(prompt_payload.get("source_code") or ""),
            metadata=prompt_payload.get("metadata") or {},
        )

    @classmethod
    def from_func(
        cls,
        func: Callable,
        *,
        id: Optional[str] = None,
        version: Optional[int] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "PromptSpec":
        """Create a PromptSpec from a function (non-decorator API).

        Performs persistence and registers in the in-memory registry.

        Versioning rules:
        - ``version=None`` (default): upsert the latest version (or create
          version 1 if the prompt_id is new).
        - ``version=N``: upsert that specific version row.

        Args:
            func: The prompt function.
            id: Prompt identifier. Defaults to ``func.__name__``.
            version: Explicit version number. ``None`` 鈫?upsert latest.
            tds: Translation references. Accepts:
                - ``str`` single namespace id.
                - ``List[str | Dict]`` multiple items; each is either a
                  namespace id or a dict ``{namespace: {key: {lang: val}}}``
                  that creates inline translations.
                - ``Dict`` single inline translations dict.
            metadata: Arbitrary user metadata.

        Returns:
            PromptSpec instance, also registered in the in-memory registry.
        """
        prompt_id = id or func.__name__
        td_refs = _normalize_tds(tds)
        # Always include the prompt's own id as a td namespace so that
        # translations can be added after prompt creation via TR_AHVN.
        if prompt_id not in td_refs:
            td_refs.insert(0, prompt_id)

        # Materialize each namespace in the TranslationStore so that
        # _resolve_tr 鈫?store.exists(ns) returns True from the start.
        try:
            tr_store = get_translation_store()
            for ns in td_refs:
                tr_store.ensure_namespace(ns, "en")
        except Exception:
            pass

        meta = metadata or {}

        source_code = _get_source(func)
        source_file = _get_source_file(func)
        checksum = _compute_checksum(source_code)
        qualname = func.__qualname__

        # Version policy: when version=None, the latest stored version is reused (or 1
        # if no version exists).  This means changed prompt code overwrites the same
        # version rather than auto-incrementing.  Version bumps are intentionally manual
        # — callers must explicitly pass a higher version number to create a new version.
        ver = version if version is not None else 1
        store = None
        try:
            store = get_prompt_store()
            if version is None:
                existing = store.get_latest_version(prompt_id)
                ver = existing if existing is not None else 1
        except Exception:
            store = None

        spec = cls(
            id=prompt_id,
            version=ver,
            checksum=checksum,
            func=func,
            td_refs=td_refs,
            qualname=qualname,
            source_file=source_file,
            source_code=source_code,
            metadata=meta,
        )
        if store is not None:
            try:
                store.save(
                    prompt_id=prompt_id,
                    version=ver,
                    checksum=checksum,
                    qualname=qualname,
                    source_file=source_file,
                    source_code=source_code,
                    td_refs=td_refs,
                    metadata=_pack_store_metadata(meta, spec.to_dict()),
                )
                logger.debug("Prompt '%s' v%d stored (checksum=%s)", prompt_id, ver, checksum[:12])
            except Exception as exc:
                logger.debug("Prompt auto-store failed for '%s': %s", prompt_id, exc)
        _register(spec)
        return spec

    @classmethod
    def from_store(
        cls,
        prompt_id: str,
        version: Optional[int] = None,
    ) -> Optional["PromptSpec"]:
        """Load a PromptSpec from the DB store."""
        try:
            store = get_prompt_store()
        except Exception:
            return None

        row = store.get(prompt_id, version)
        if row is None:
            return None

        try:
            capsule_data = _unpack_store_capsule(row.get("metadata_json"))
            prompt_payload = capsule_data.get("prompt_spec") if isinstance(capsule_data.get("prompt_spec"), dict) else {}
            manifest = capsule_data.get("manifest") if isinstance(capsule_data.get("manifest"), dict) else {}
            if prompt_payload.get("version") is None and manifest.get("prompt_version") is None:
                raise ValueError("Stored prompt capsule is missing prompt version metadata.")
            spec = cls.from_dict(capsule_data)
        except Exception as exc:
            logger.debug("Prompt load failed for '%s': %s", prompt_id, exc)
            return None
        _register(spec)
        return spec

    @classmethod
    def from_str(
        cls,
        template: str,
        *,
        id: Optional[str] = None,
        trs: Optional[List[str]] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "PromptSpec":
        """Create a PromptSpec from a format-string template.

        The *template* string itself is the translation key it is
        passed through ``tr(template)`` to produce a localised format
        string, then ``str.format(**kwargs)`` fills in the placeholders.

        *trs* lists placeholder names whose **values** should also be
        translated before formatting::

            hi = PromptSpec.from_str(
                "Hello, {name}! Welcome to {place}",
                trs=["place"],
            )
            hi.tr.set(
                "Hello, {name}! Welcome to {place}", "zh",
                "浣犲ソ, {name}! 娆㈣繋鏉ュ埌 {place}",
            )
            hi(name="Alice", place="Paris", lang="zh")
            # 鈫?"浣犲ソ, Alice! 娆㈣繋鏉ュ埌 Paris"
            # ("place" value would also be translated if a mapping exists)

        Args:
            template: Python format string, e.g. ``"Hello, {name}!"``.
            id: Prompt identifier.  Defaults to ``"template_<hash>"``.
            trs: Placeholder names whose runtime values are passed
                through ``tr()`` before formatting.
            tds: Additional translation namespaces (same as decorator).
            version: Explicit version number.
            metadata: Arbitrary user metadata.
        """
        import string

        trs_keys = list(trs or [])
        # Discover {placeholder} names from the template.
        field_names = [name for _, name, _, _ in string.Formatter().parse(template) if name is not None and name != "tr"]

        prompt_id = id or f"template_{fmt_hash(md5hash(template))[:12]}"
        _template_func = _build_template_func(template, field_names, trs_keys, prompt_id)

        # Persist template metadata for introspection/debugging.
        meta = dict(metadata or {})
        meta.update(
            {
                "type": "template",
                "template": template,
                "fields": field_names,
                "trs_keys": trs_keys,
            }
        )

        return cls.from_func(
            _template_func,
            id=prompt_id,
            version=version,
            tds=tds,
            metadata=meta,
        )

    @classmethod
    def from_jinja(
        cls,
        template: str,
        *,
        id: Optional[str] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "PromptSpec":
        """Create a PromptSpec from an in-memory Jinja template string.

        This path is filesystem-free and Babel-free. It supports a lightweight
        in-memory environment with builtin filters/tests/globals (including ``tr``),
        suitable for simple migrated templates.
        """
        prompt_id = id or f"jinja_{fmt_hash(md5hash(template))[:12]}"
        _jinja_func = _build_jinja_func(template, prompt_id)

        meta = dict(metadata or {})
        meta.update(
            {
                "type": "jinja",
                "template": template,
            }
        )
        return cls.from_func(
            _jinja_func,
            id=prompt_id,
            version=version,
            tds=tds,
            metadata=meta,
        )

    # -- decorator ---------------------------------------------------- #

    @classmethod
    def prompt(
        cls,
        func: Optional[Callable] = None,
        *,
        id: Optional[str] = None,
        version: Optional[int] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "PromptSpec":
        """Decorator that registers a prompt function.

        The decorated name **becomes** the :class:`PromptSpec` itself::

            @PromptSpec.prompt
            def greet(name, tr=None):
                return f"{tr('Hello')}, {name}!"

            greet("Alice")      # auto-injects tr
            greet.id             # "greet"
            greet.version        # 1

        Versioning rules:
        - ``version=None`` (default): upsert the latest existing version
          (or create version 1 if new).
        - ``version=N``: upsert that specific version row.

        ``tds`` accepts:
        - ``str`` single namespace id.
        - ``List[str | Dict]`` multiple items; strings are namespace
          ids, dicts create inline translations.
        - ``Dict`` single inline translations dict::

              tds={"ns": {"Hello": {"zh": "浣犲ソ"}}}

        When omitted, a namespace matching the prompt ``id`` is created
        automatically so translations can be added later via ``.tr``.

        Args:
            id: Prompt identifier. Defaults to ``fn.__name__``.
            version: Explicit version number. ``None`` 鈫?upsert latest.
            tds: Translation dict references or inline translations.
            metadata: Arbitrary user metadata stored alongside the prompt.
        """

        def _decorate(fn: Callable) -> "PromptSpec":
            return cls.from_func(fn, id=id, version=version, tds=tds, metadata=metadata)

        if func is not None:
            return _decorate(func)
        return _decorate

    def __repr__(self) -> str:
        return f"PromptSpec(id={self.id!r}, version={self.version}, " f"checksum={self.checksum[:12]!r}..., td_refs={self.td_refs})"


# ------------------------------------------------------------------ #
#  Auto-versioning helper
# ------------------------------------------------------------------ #


def _normalize_tds(tds) -> List[str]:
    """Normalize the ``tds`` parameter to a flat list of namespace ids.

    Accepts ``str``, ``Dict``, or ``List[str | Dict]``.
    """
    if tds is None:
        return []
    if isinstance(tds, str):
        return [tds]
    if isinstance(tds, dict):
        return _ensure_inline_translations(tds)
    if isinstance(tds, list):
        result: List[str] = []
        for item in tds:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.extend(_ensure_inline_translations(item))
        return result
    return []


# ------------------------------------------------------------------ #
#  Backward-compat standalone decorator
# ------------------------------------------------------------------ #


def prompt(
    func: Optional[Callable] = None,
    *,
    id: Optional[str] = None,
    version: Optional[int] = None,
    tds: Optional[Union[str, Dict, List]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Standalone decorator delegates to ``PromptSpec.prompt``."""
    return PromptSpec.prompt(func, id=id, version=version, tds=tds, metadata=metadata)


# ------------------------------------------------------------------ #
#  PromptManager
# ------------------------------------------------------------------ #


class PromptManager:
    """Global prompt retrieval and management.

    Language resolution priority (highest 鈫?lowest):

    1. Explicit ``lang`` in ``get(..., lang="zh")`` freezes language
       at retrieval time; the returned callable always uses that language.
    2. ``CM_AHVN`` scoped config ``prompts.lang`` resolved dynamically
       at every call.
    3. ``main_lang`` of the TranslationDict (default ``"en"``).
    """

    def get(
        self,
        prompt_id: str,
        version: Optional[Union[int, str]] = None,
        lang: Optional[str] = None,
    ) -> Optional[Callable[..., Messages]]:
        """Return a callable that produces Messages.

        When *lang* is given, the returned callable's translation is
        **frozen** to that language (priority 1).  When *lang* is
        ``None``, translation resolves at each call from the current
        ``CM_AHVN`` scope (priority 2) or falls back to ``main_lang``
        (priority 3).
        """
        resolved_ver = None
        if version is not None and version != "latest":
            resolved_ver = int(version)

        spec = _lookup(prompt_id, resolved_ver)

        if spec is None:
            spec = PromptSpec.from_store(prompt_id, resolved_ver)

        if spec is None:
            return None

        if lang is not None:
            return self._wrap(spec, lang)
        return spec

    def versions(self, prompt_id: str) -> List[int]:
        """Return the list of stored version numbers for *prompt_id*."""
        return get_prompt_store().list_versions(prompt_id)

    def latest_version(self, prompt_id: str) -> Optional[int]:
        """Return the latest version number, or ``None`` if not found."""
        return get_prompt_store().get_latest_version(prompt_id)

    def list(self) -> List[Dict[str, Any]]:
        return get_prompt_store().list()

    def info(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        return get_prompt_store().info(prompt_id)

    def remove(self, prompt_id: str, version: Optional[int] = None) -> None:
        get_prompt_store().remove(prompt_id, version)
        _unregister(prompt_id, version)

    def stale(self) -> List[Dict[str, Any]]:
        return get_prompt_store().stale()

    @staticmethod
    def _wrap(spec: PromptSpec, lang: str) -> Callable[..., Messages]:
        """Wrap a PromptSpec with a **frozen** language.

        The returned callable always resolves ``tr`` to *lang*,
        ignoring ``CM_AHVN`` scoped settings.  ``elicit`` can still be
        passed per-call.
        """

        @functools.wraps(spec.func)
        def _call_fixed(*args, **kwargs):
            kwargs.pop("lang", None)
            elicit = kwargs.pop("elicit", "none")
            tr_fixed = _resolve_tr(spec.td_refs, lang, elicit=elicit)
            kwargs.setdefault("tr", tr_fixed)
            return spec.func(*args, **kwargs)

        _call_fixed.__prompt_spec__ = spec
        return _call_fixed


# -- singleton -------------------------------------------------------- #

_manager_instance: Optional[PromptManager] = None
_manager_lock = threading.Lock()


def get_prompt_manager() -> PromptManager:
    """Return the process-wide ``PromptManager`` singleton."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = PromptManager()
    return _manager_instance


class _LazyPM:
    """Module-level proxy so ``PM_AHVN`` can be imported at the top of a
    module without triggering DB initialization on import."""

    def __getattr__(self, name):
        return getattr(get_prompt_manager(), name)

    def __repr__(self):
        return repr(get_prompt_manager())


PM_AHVN: PromptManager = _LazyPM()  # type: ignore[assignment]


# ------------------------------------------------------------------ #
#  System prompt bootstrap (version 0 defaults)
# ------------------------------------------------------------------ #

_system_prompt_lock = threading.Lock()
_SYSTEM_PROMPT_VERSION = 0


def _translation_prompt(
    source_lang: str,
    target_lang: str,
    content: str,
    *,
    tr: Optional[Callable] = None,
) -> str:
    translator = tr or str
    return "\n".join(
        [
            translator("Translate the following text from {source_lang} to {target_lang}.").format(
                source_lang=source_lang,
                target_lang=target_lang,
            ),
            "",
            translator("Rules:"),
            translator("- Output ONLY the translated text, nothing else."),
            translator(r"- Preserve all {curly-brace} placeholders exactly as they appear (e.g. {name}, {place})."),
            translator("- Keep Markdown formatting, code blocks, and URLs unchanged."),
            translator("- Do not add explanations, notes, or commentary."),
            "",
            translator("Text to translate:"),
            content,
        ]
    )


_FAST_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "Task Descriptions": "任务描述",
    "Instructions": "指令",
    "Examples": "示例",
    "New Instance": "新实例",
    "Inputs": "输入",
    "Hints": "提示",
    "Output": "输出",
    "Expected": "期望",
    "Notes": "备注",
}
_DEFAULT_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "Skills": "技能",
    "Skills are a series of documents that provide specialized knowledge or capabilities to help complete the task.": "技能是一组文档，提供专门知识或能力以帮助完成任务。",
    "Check if any of the available skills below can help complete the task more effectively.": "请检查下方可用技能是否能更高效地完成任务。",
    'To view a skill, call tool `Skill(name: str, path: Optional[str] = "SKILL.md")`, where `name` is the skill name and `path` is the path to the specific resource within the skill, which defaults to `SKILL.md` if not provided.': '查看技能时，请调用工具 `Skill(name: str, path: Optional[str] = "SKILL.md")`，其中 `name` 是技能名称，`path` 是技能内资源路径；若不提供则默认 `SKILL.md`。',
    "The resources structure within each skill will be provided upon calling the skill. Do not invoke a skill that is already loaded or not listed below.": "调用技能后会提供该技能的资源结构。不要调用已加载或未在下方列出的技能。",
    "Here are some skills potentially useful for completing the task:": "以下是可能有助于完成任务的技能：",
}
_TRANSLATION_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "Translate the following text from {source_lang} to {target_lang}.": "将以下文本从 {source_lang} 翻译为 {target_lang}。",
    "Rules:": "规则：",
    "- Output ONLY the translated text, nothing else.": "- 只输出翻译后的文本，不要输出其他内容。",
    r"- Preserve all {curly-brace} placeholders exactly as they appear (e.g. {name}, {place}).": r"- 保留所有 {curly-brace} 占位符，保持与原文完全一致（例如 {name}、{place}）。",
    "- Keep Markdown formatting, code blocks, and URLs unchanged.": "- 保持 Markdown 格式、代码块和 URL 不变。",
    "- Do not add explanations, notes, or commentary.": "- 不要添加解释、备注或评论。",
    "Text to translate:": "待翻译文本：",
}
_AUTOCODE_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "You are a skillful Python expert. Your task is to generate a complete Python function implementation based on the provided signature and test cases.": "你是一名熟练的 Python 专家。你的任务是根据给定的函数签名和测试用例，生成完整的 Python 函数实现。",
    "Implement the following function:\n```python\n{impl_block}\n```": "实现以下函数：\n```python\n{impl_block}\n```",
    "Analyze the function signature and test cases to understand the required logic.": "分析函数签名和测试用例，理解所需逻辑。",
    "Generate a complete Python function implementation that passes all the test cases.": "生成可通过所有测试用例的完整 Python 函数实现。",
    "Preserve the exact function signature including name, parameters, type hints, and return type.": "严格保留原函数签名，包括名称、参数、类型注解和返回类型。",
    "Include necessary imports at the top level if needed.": "如有需要，在顶层添加必要的导入语句。",
    "DO NOT include the test assertions in your output - only generate the function implementation.": "输出中不要包含测试断言，只生成函数实现。",
    "Wrap the complete Python code in a single markdown 'python' code block.": "将完整 Python 代码包裹在单个 markdown 的 `python` 代码块中。",
    "Output in Simplified Chinese.": "请使用简体中文输出。",
    "Test cases that your implementation must pass:": "你的实现必须通过的测试用例：",
}
_AUTOFUNC_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "You are a skillful Python expert. Your task is to act as a function and produce output given its specification and inputs.": "你是一名熟练的 Python 专家。你的任务是根据函数规格和输入，像函数一样生成输出。",
    "## Function Specification": "## 函数规格",
    "Keep your reasoning or response as brief as possible.": "尽量简洁地给出推理或回复。",
    "The final answer must be a string that supports python `repr`.": "最终答案必须是支持 Python `repr` 的字符串。",
    "Wrap the final answer in `<output></output>` tags.": "请将最终答案包裹在 `<output></output>` 标签中。",
    "Output in Simplified Chinese.": "请使用简体中文输出。",
}
_AUTOTASK_PROMPT_ZH_TRANSLATIONS: Dict[str, str] = {
    "You are a helpful AI assistant. Your task is to complete a task given its description, examples, and new inputs. Infer the task's logic from the examples and apply it to the new inputs.": "你是一个有帮助的 AI 助手。你的任务是根据任务描述、示例和新输入完成任务。请从示例中归纳任务逻辑，并将其应用到新输入。",
    "Keep your reasoning or response as brief as possible.": "尽量简洁地给出推理或回复。",
    "The final answer must be a string that supports python `repr`.": "最终答案必须是支持 Python `repr` 的字符串。",
    "The final answer must be a markdown code block containing a valid JSON object using '```json'.": "最终答案必须是一个 markdown 代码块，并使用 ` ```json ` 包含合法 JSON 对象。",
    "The final answer must be a markdown code block using '```'.": "最终答案必须是使用 ` ``` ` 的 markdown 代码块。",
    "Wrap the final answer in `<output></output>` tags.": "请将最终答案包裹在 `<output></output>` 标签中。",
    "Output in Simplified Chinese.": "请使用简体中文输出。",
}


def _seed_system_prompt_translations(force: bool = False) -> None:
    from .translate import TranslationDict

    translation_specs = {
        "default_prompt": _FAST_PROMPT_ZH_TRANSLATIONS | _DEFAULT_PROMPT_ZH_TRANSLATIONS,
        "translation_prompt": _TRANSLATION_PROMPT_ZH_TRANSLATIONS,
        "toolspec_prompt": {},
        "experience_prompt": _FAST_PROMPT_ZH_TRANSLATIONS,
        "autocode_prompt": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOCODE_PROMPT_ZH_TRANSLATIONS,
        "autofunc_prompt": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOFUNC_PROMPT_ZH_TRANSLATIONS,
        "autotask_prompt": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOTASK_PROMPT_ZH_TRANSLATIONS,
        "autotask_prompt_base": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOTASK_PROMPT_ZH_TRANSLATIONS,
        "autotask_prompt_repr": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOTASK_PROMPT_ZH_TRANSLATIONS,
        "autotask_prompt_json": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOTASK_PROMPT_ZH_TRANSLATIONS,
        "autotask_prompt_code": _FAST_PROMPT_ZH_TRANSLATIONS | _AUTOTASK_PROMPT_ZH_TRANSLATIONS,
    }
    store = get_translation_store()
    for namespace, zh_mapping in translation_specs.items():
        td = TranslationDict(namespace=namespace, store=store)
        pending = zh_mapping
        if not force:
            pending = {key: value for key, value in zh_mapping.items() if td.lookup(key, "zh") is None}
        if pending:
            td.set_many({"zh": pending})


def _builtin_prompt_defs() -> List[Dict[str, Any]]:
    from .prompt import default_prompt_composer, experience_prompt_composer, toolspec_prompt_composer
    from ..exts.autocode import autocode_prompt_composer
    from ..exts.autofunc import autofunc_prompt_composer
    from ..exts.autotask import autotask_prompt_composer

    return [
        {
            "id": "default_prompt",
            "func": default_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True},
        },
        {
            "id": "translation_prompt",
            "func": _translation_prompt,
            "metadata": {"system": True, "translation": True},
        },
        {
            "id": "toolspec_prompt",
            "func": toolspec_prompt_composer,
            "metadata": {"system": True},
        },
        {
            "id": "experience_prompt",
            "func": experience_prompt_composer,
            "metadata": {"system": True},
        },
        {
            "id": "autocode_prompt",
            "func": autocode_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True},
        },
        {
            "id": "autofunc_prompt",
            "func": autofunc_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True},
        },
        {
            "id": "autotask_prompt",
            "func": autotask_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True, "output_schema": {"mode": "base"}},
        },
        {
            "id": "autotask_prompt_base",
            "func": autotask_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True, "output_schema": {"mode": "base"}},
        },
        {
            "id": "autotask_prompt_repr",
            "func": autotask_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True, "output_schema": {"mode": "repr"}},
        },
        {
            "id": "autotask_prompt_json",
            "func": autotask_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True, "output_schema": {"mode": "json", "args": {"indent": 4}}},
        },
        {
            "id": "autotask_prompt_code",
            "func": autotask_prompt_composer,
            "metadata": {"system": True, "fast_prompt_section": True, "output_schema": {"mode": "code"}},
        },
    ]


def setup_system_prompts(force: bool = False) -> Dict[str, "PromptSpec"]:
    """Register built-in prompt specs into PromptStore through PM_AHVN."""
    with _system_prompt_lock:
        get_prompt_manager()
        registered: Dict[str, PromptSpec] = {}
        for item in _builtin_prompt_defs():
            prompt_id = item["id"]
            existing = PM_AHVN.get(prompt_id, version=_SYSTEM_PROMPT_VERSION)
            if (existing is not None) and (not force):
                if isinstance(existing, PromptSpec):
                    registered[prompt_id] = existing
                    continue
                if callable(existing) and isinstance(getattr(existing, "__prompt_spec__", None), PromptSpec):
                    registered[prompt_id] = existing.__prompt_spec__
                    continue
            registered[prompt_id] = PromptSpec.from_func(
                item["func"],
                id=prompt_id,
                version=_SYSTEM_PROMPT_VERSION,
                metadata=item["metadata"],
            )
        _seed_system_prompt_translations(force=force)
        return registered


def ensure_system_prompts() -> Dict[str, "PromptSpec"]:
    return setup_system_prompts(force=False)


def get_system_prompt_spec(
    prompt_id: str = "default_prompt",
    version: Optional[Union[int, str]] = None,
) -> "PromptSpec":
    spec = PM_AHVN.get(prompt_id, version=version)
    if isinstance(spec, PromptSpec):
        return spec
    if callable(spec) and isinstance(getattr(spec, "__prompt_spec__", None), PromptSpec):
        return spec.__prompt_spec__
    raise ValueError(f"Prompt '{prompt_id}' not found in PM_AHVN.")


# ------------------------------------------------------------------ #
#  TranslationManager deferred translation helper
# ------------------------------------------------------------------ #


class TranslationManager:
    """Convenience facade for adding translations to prompt namespaces.

    Translations are stored in the shared ``TranslationStore`` singleton
    and are **not** versioned they are simple key鈫抳alue pairs that can
    be updated at any time, independent of the prompt version.

    Usage::

        TR_AHVN.set("greet", "Hello", "zh", "浣犲ソ")
        TR_AHVN.set("greet", "Hello", "ja", "銇撱倱銇仭銇?)

        # Bulk
        TR_AHVN.set_many("greet", {"zh": {"Hello": "浣犲ソ"}, "ja": {"Hello": "銇撱倱銇仭銇?}})

    The *namespace* normally matches the prompt ``id`` (which is
    auto-created when the prompt is registered).
    """

    def set(self, namespace: str, key: str, lang: str, value: str) -> None:
        """Set a single translation entry in *namespace*."""
        from .translate import TranslationDict

        store = get_translation_store()
        td = TranslationDict(namespace=namespace, store=store)
        td.set(key, lang, value)

    def set_many(self, namespace: str, translations: Dict[str, Dict[str, str]]) -> None:
        """Bulk-set translations: ``{lang: {key: value, ...}, ...}``.

        This matches ``TranslationDict.set_many`` language code is the
        outer key, source text is the inner key.
        """
        from .translate import TranslationDict

        store = get_translation_store()
        td = TranslationDict(namespace=namespace, store=store)
        td.set_many(translations)

    def delete(self, namespace: str, key: str, lang: str) -> None:
        """Remove a single translation entry."""
        from .translate import TranslationDict

        store = get_translation_store()
        td = TranslationDict(namespace=namespace, store=store)
        td.delete(key, lang)

    def remove(self, namespace: str) -> None:
        """Delete an entire translation namespace and all its entries."""
        store = get_translation_store()
        if store.exists(namespace):
            store.remove(namespace)

    def get(self, namespace: str) -> Any:
        """Return the ``TranslationDict`` for *namespace*."""
        from .translate import TranslationDict

        store = get_translation_store()
        return TranslationDict(namespace=namespace, store=store)

    @staticmethod
    def _rendered_to_text(rendered: Any) -> str:
        """Convert prompt render output into plain text."""
        if rendered is None:
            return ""
        if isinstance(rendered, str):
            return rendered
        if isinstance(rendered, bytes):
            return rendered.decode("utf-8", errors="ignore")

        from ..basic.serialize_utils import dumps_json

        if isinstance(rendered, dict):
            content = rendered.get("content")
            if isinstance(content, str):
                return content
            return dumps_json(rendered, ensure_ascii=False, indent=2)

        if isinstance(rendered, list):
            # Most prompt composers return Messages; flatten to readable text.
            chunks: List[str] = []
            for item in rendered:
                if isinstance(item, str):
                    if item.strip():
                        chunks.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str):
                    if content.strip():
                        chunks.append(content.strip())
                elif content is not None:
                    chunks.append(dumps_json(content, ensure_ascii=False, indent=2))
            if chunks:
                return "\n\n".join(chunks)
            return dumps_json(rendered, ensure_ascii=False, indent=2)

        return str(rendered)

    def render(
        self,
        namespace: str,
        *,
        args: Optional[Dict[str, Any]] = None,
        lang: Optional[str] = None,
        version: Optional[Union[int, str]] = None,
        as_text: bool = True,
    ) -> Any:
        """Render a prompt by id/namespace with optional translation language."""
        prompt = PM_AHVN.get(namespace, version=version, lang=lang)
        if prompt is None:
            # Try registering built-ins lazily for system prompts.
            ensure_system_prompts()
            prompt = PM_AHVN.get(namespace, version=version, lang=lang)
        if prompt is None:
            return None

        try:
            rendered = prompt(**(args or {}))
        except RuntimeError as exc:
            # Migration fallback: stale stored rows may resolve to a stub.
            if "could not be loaded" not in str(exc):
                raise
            setup_system_prompts(force=True)
            prompt = PM_AHVN.get(namespace, version=version, lang=lang)
            if prompt is None:
                return None
            rendered = prompt(**(args or {}))

        if as_text:
            return self._rendered_to_text(rendered)
        return rendered

    def render_prompt(
        self,
        namespace: str,
        *,
        args: Optional[Dict[str, Any]] = None,
        lang: Optional[str] = None,
        version: Optional[Union[int, str]] = None,
        as_text: bool = True,
    ) -> Any:
        """Backward-compatible alias of :meth:`render`."""
        return self.render(
            namespace,
            args=args,
            lang=lang,
            version=version,
            as_text=as_text,
        )


class _LazyTR:
    """Module-level proxy so ``TR_AHVN`` can be imported without
    triggering DB initialization on import."""

    def __getattr__(self, name):
        return getattr(_tr_manager_instance(), name)

    def __repr__(self):
        return repr(_tr_manager_instance())


_tr_mgr: Optional[TranslationManager] = None
_tr_mgr_lock = threading.Lock()


def _tr_manager_instance() -> TranslationManager:
    global _tr_mgr
    if _tr_mgr is None:
        with _tr_mgr_lock:
            if _tr_mgr is None:
                _tr_mgr = TranslationManager()
    return _tr_mgr


TR_AHVN: TranslationManager = _LazyTR()  # type: ignore[assignment]
