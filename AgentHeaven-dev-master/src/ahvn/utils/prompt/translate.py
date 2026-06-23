"""\
Public translation API.

``TranslationDict`` is the single high-level object developers interact with.
It delegates persistence to ``TranslationStore`` and lookup to
``TranslationMatcher``.

Usage::

    td = TranslationDict(namespace="fibonacci", main_lang="en", store=store)
    td.set("Task Descriptions", "zh", "任务描述")
    tr = td.tr("zh")
    print(tr("Task Descriptions"))   # → 任务描述
"""

__all__ = [
    "TranslationDict",
    "TranslationStore",
    "get_translation_store",
]

import threading
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from ..basic.log_utils import get_logger
from .translate_match import TemplateSpec, TranslationMatcher
from .translate_store import TranslationStore

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
#  Singleton store factory
# ------------------------------------------------------------------ #

_store_instance: Optional[TranslationStore] = None
_store_lock = threading.Lock()


def get_translation_store() -> TranslationStore:
    """Return the process-wide ``TranslationStore`` singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = TranslationStore()
    return _store_instance


# ------------------------------------------------------------------ #
#  System translation prompt (translation_prompt from PM_AHVN)
# ------------------------------------------------------------------ #


def _get_translation_prompt():
    from .prompt_spec import PM_AHVN, PromptSpec, setup_system_prompts

    prompt = PM_AHVN.get("translation_prompt")
    if prompt is None:
        setup_system_prompts(force=False)
        prompt = PM_AHVN.get("translation_prompt")
    if isinstance(prompt, PromptSpec):
        return prompt
    if callable(prompt) and isinstance(getattr(prompt, "__prompt_spec__", None), PromptSpec):
        return prompt.__prompt_spec__
    raise RuntimeError("Failed to load 'translation_prompt' from PM_AHVN.")


def _llm_translate(text: str, source_lang: str, target_lang: str) -> Optional[str]:
    """Translate *text* via ``LLM(preset="translator")``."""
    try:
        from ..llm.base import LLM

        prompt = _get_translation_prompt()
        message = prompt(
            source_lang=source_lang,
            target_lang=target_lang,
            content=text,
        )
        llm = LLM(preset="translator", name="translation_prompt", cache=True)
        result = llm.oracle(message, include=["content"], reduce=True)
        if isinstance(result, str):
            result = result.strip()
            if result:
                return result
    except Exception as exc:
        logger.debug("LLM translation failed: %s", exc)
    return None


# ------------------------------------------------------------------ #
#  Language resolution helper
# ------------------------------------------------------------------ #


def _resolve_lang(lang: Optional[str], main_lang: str) -> str:
    """Resolve *lang*: explicit arg → config → main_lang."""
    if lang is not None:
        return lang
    try:
        from ..basic.config_utils import CM_AHVN

        cfg_lang = CM_AHVN.get("prompts.lang")
        if cfg_lang:
            return str(cfg_lang)
    except Exception:
        pass
    return main_lang


# ------------------------------------------------------------------ #
#  TranslationDict
# ------------------------------------------------------------------ #


class TranslationDict:
    """Namespace-scoped translation engine with optional DB persistence.

    Supports exact-match and ``{placeholder}``-pattern matching.
    When *store* is provided, every mutation writes through immediately.
    """

    def __init__(
        self,
        namespace: str = "",
        main_lang: str = "en",
        store: Optional[TranslationStore] = None,
    ):
        self.namespace = namespace
        self.main_lang = main_lang
        self._store = store
        self._matcher = TranslationMatcher()

        if store is not None:
            self._init_from_store()

    def _init_from_store(self) -> None:
        meta = self._store.get_namespace(self.namespace)
        if meta is not None:
            self.main_lang = str(meta["main_lang"])
        else:
            self._store.ensure_namespace(self.namespace, self.main_lang)

        templates, values, index_snapshot = self._store.load_namespace_bundle(self.namespace)
        id_to_key: Dict[int, str] = {}

        for t in templates:
            source_key = str(t["source_key"])
            id_to_key[int(t["id"])] = source_key
            spec = TemplateSpec(
                source_key=source_key,
                is_pattern=bool(t["is_pattern"]),
                literals=tuple(t["literals_json"]) if t["literals_json"] else ("",),
                placeholders=tuple(t["placeholders_json"]) if t["placeholders_json"] else (),
                probe=str(t.get("probe") or ""),
                structurally_ambiguous=bool(t.get("structurally_ambiguous", False)),
            )
            self._matcher.add_template(spec)

        for v in values:
            self._matcher.add_value(str(v["source_key"]), str(v["lang"]), str(v["target_value"]))

        hydrated_snapshot = self._expand_runtime_index_snapshot(index_snapshot, id_to_key)
        if not self._matcher.try_load_index_snapshot(hydrated_snapshot):
            self._matcher.rebuild_indexes()
            self._persist_runtime_index_snapshot()

    def _expand_runtime_index_snapshot(
        self,
        snapshot: Optional[Dict[str, Any]],
        id_to_key: Dict[int, str],
    ) -> Optional[Dict[str, Any]]:
        """Expand persisted ID-based snapshot into key-based snapshot."""
        if not snapshot or not isinstance(snapshot, dict):
            return None

        def _decode_list(items: Any) -> Optional[List[str]]:
            if not isinstance(items, list):
                return None
            seen = set()
            out: List[str] = []
            for item in items:
                if not isinstance(item, int):
                    return None
                key = id_to_key.get(item)
                if key is None:
                    return None
                if key not in seen:
                    seen.add(key)
                    out.append(key)
            return out

        gram_raw = snapshot.get("gram_index")
        residual_raw = snapshot.get("residual_keys")
        by_lang_raw = snapshot.get("pattern_keys_by_lang")
        if not isinstance(gram_raw, dict) or not isinstance(by_lang_raw, dict):
            return None
        residual = _decode_list(residual_raw)
        if residual is None:
            return None

        gram_index: Dict[str, List[str]] = {}
        for gram, items in gram_raw.items():
            if not isinstance(gram, str):
                return None
            keys = _decode_list(items)
            if keys is None:
                return None
            if keys:
                gram_index[gram] = keys

        pattern_keys_by_lang: Dict[str, List[str]] = {}
        for lang, items in by_lang_raw.items():
            if not isinstance(lang, str):
                return None
            keys = _decode_list(items)
            if keys is None:
                return None
            if keys:
                pattern_keys_by_lang[lang] = keys

        return {
            "version": int(snapshot.get("version") or 1),
            "gram_index": gram_index,
            "residual_keys": residual,
            "pattern_keys_by_lang": pattern_keys_by_lang,
        }

    def _compact_runtime_index_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Compact key-based matcher snapshot into persisted template-id form."""
        if self._store is None:
            return snapshot

        key_to_id = {key: self._store.template_id(self.namespace, key) for key in self._matcher.templates.keys()}

        def _encode_list(keys: Any) -> List[int]:
            if not isinstance(keys, list):
                return []
            out = set()
            for key in keys:
                if not isinstance(key, str):
                    continue
                template_id = key_to_id.get(key)
                if template_id is not None:
                    out.add(int(template_id))
            return sorted(out)

        gram_index_raw = snapshot.get("gram_index") or {}
        gram_index: Dict[str, List[int]] = {}
        for gram, keys in gram_index_raw.items():
            if not isinstance(gram, str):
                continue
            encoded = _encode_list(keys)
            if encoded:
                gram_index[gram] = encoded

        pattern_keys_by_lang_raw = snapshot.get("pattern_keys_by_lang") or {}
        pattern_keys_by_lang: Dict[str, List[int]] = {}
        for lang, keys in pattern_keys_by_lang_raw.items():
            if not isinstance(lang, str):
                continue
            encoded = _encode_list(keys)
            if encoded:
                pattern_keys_by_lang[lang] = encoded

        return {
            "version": int(snapshot.get("version") or 1),
            "gram_index": gram_index,
            "residual_keys": _encode_list(snapshot.get("residual_keys") or []),
            "pattern_keys_by_lang": pattern_keys_by_lang,
        }

    def _persist_runtime_index_snapshot(self) -> None:
        if self._store is None:
            return
        snapshot = self._matcher.export_index_snapshot()
        compact_snapshot = self._compact_runtime_index_snapshot(snapshot)
        self._store.save_index_snapshot(self.namespace, compact_snapshot)

    # ------------------------------------------------------------------ #
    #  Mutations
    # ------------------------------------------------------------------ #

    def set(self, key: str, lang: str, value: str) -> "TranslationDict":
        """Set a single translation entry."""
        spec = self._matcher.templates.get(key)
        if spec is None:
            spec = TemplateSpec.parse(key)
            self._matcher.add_template(spec)
            if spec.is_pattern:
                self._matcher._index_pattern(spec)
        self._matcher.add_value(key, lang, value)
        if self._store is not None:
            try:
                with self._store.write_tx():
                    self._store.save_entry(self.namespace, key, lang, value)
                    self._persist_runtime_index_snapshot()
            except Exception:
                self.reload()
                raise
        return self

    def set_many(self, translations: Dict[str, Dict[str, str]]) -> "TranslationDict":
        """Set translations in bulk: ``{lang: {key: value, ...}, ...}``.

        Language code is the outer key, source text the inner key.
        All ``set_many`` APIs across the codebase (``TranslationDict``,
        ``_PromptTR``, ``TranslationManager``) share this convention.
        """
        entries: List[Dict[str, str]] = []
        for lang, mapping in translations.items():
            for key, value in mapping.items():
                spec = self._matcher.templates.get(key)
                if spec is None:
                    spec = TemplateSpec.parse(key)
                    self._matcher.add_template(spec)
                    if spec.is_pattern:
                        self._matcher._index_pattern(spec)
                self._matcher.add_value(key, lang, value)
                entries.append({"key": key, "lang": lang, "value": value})
        if self._store is not None and entries:
            try:
                with self._store.write_tx():
                    self._store.save_entries_batch(self.namespace, entries)
                    self._persist_runtime_index_snapshot()
            except Exception:
                self.reload()
                raise
        return self

    def delete(self, key: str, lang: str) -> "TranslationDict":
        """Remove a single translation entry."""
        self._matcher.remove_value(key, lang)
        # If no languages remain for this key, remove the template and unindex
        has_any = any(k == key for (_, k) in self._matcher.exact_map)
        if not has_any:
            spec = self._matcher.templates.pop(key, None)
            if spec is not None and spec.is_pattern:
                self._matcher._unindex_pattern(spec)
        if self._store is not None:
            try:
                with self._store.write_tx():
                    self._store.delete_entry(self.namespace, key, lang)
                    self._persist_runtime_index_snapshot()
            except Exception:
                self.reload()
                raise
        return self

    # ------------------------------------------------------------------ #
    #  Lookup
    # ------------------------------------------------------------------ #

    def _normalize_fallbacks(self, fallbacks: Optional["TranslationDict | Sequence[TranslationDict]"]) -> List["TranslationDict"]:
        if fallbacks is None:
            return []
        if isinstance(fallbacks, TranslationDict):
            candidates = [fallbacks]
        elif isinstance(fallbacks, Sequence) and not isinstance(fallbacks, (str, bytes)):
            candidates = list(fallbacks)
        else:
            raise TypeError("fallbacks must be a TranslationDict or a sequence of TranslationDict.")

        seen = {id(self)}
        chain: List["TranslationDict"] = []
        for candidate in candidates:
            if not isinstance(candidate, TranslationDict):
                raise TypeError("fallbacks must contain only TranslationDict objects.")
            if id(candidate) in seen:
                continue
            seen.add(id(candidate))
            chain.append(candidate)
        return chain

    def _lookup_chain(self, text: str, target_lang: str, chain: List["TranslationDict"]) -> Optional[str]:
        result = self._matcher.lookup(text, target_lang, self.main_lang)
        if result is not None:
            return result
        for td in chain:
            result = td._matcher.lookup(text, target_lang, td.main_lang)
            if result is not None:
                return result
        return None

    def lookup(
        self,
        text: str,
        lang: Optional[str] = None,
        *,
        fallbacks: Optional["TranslationDict | Sequence[TranslationDict]"] = None,
    ) -> Optional[str]:
        """Look up a translation with optional ordered fallback dictionaries."""
        target_lang = _resolve_lang(lang, self.main_lang)
        chain = self._normalize_fallbacks(fallbacks)
        return self._lookup_chain(text, target_lang, chain)

    # ------------------------------------------------------------------ #
    #  tr factory
    # ------------------------------------------------------------------ #

    def tr(
        self,
        lang: Optional[str] = None,
        elicit: Literal["none", "human", "llm"] = "none",
        *,
        fallbacks: Optional["TranslationDict | Sequence[TranslationDict]"] = None,
    ) -> Callable[[str], str]:
        """Return a translation callable ``(str) -> str``.

        *lang* resolved at call time: explicit arg → config → main_lang.
        """
        td = self
        explicit_lang = lang
        fallback_chain = td._normalize_fallbacks(fallbacks)

        def _translate(text: str) -> str:
            text = str(text)
            resolved = _resolve_lang(explicit_lang, td.main_lang)
            result = td._lookup_chain(text, resolved, fallback_chain)
            if result is not None:
                return result
            if elicit == "human":
                return td._elicit_human(text, resolved)
            elif elicit == "llm":
                return td._elicit_llm(text, resolved)
            return text

        return _translate

    # ------------------------------------------------------------------ #
    #  Elicit helpers
    # ------------------------------------------------------------------ #

    def _elicit_human(self, text: str, lang: str) -> str:
        hint = f"[{self.namespace}] " if self.namespace else ""
        user_input = input(f"{hint}Translate to '{lang}':\n" f"  Source ({self.main_lang}): {text}\n" f"  Target ({lang}): ")
        translated = user_input.strip()
        if translated:
            self.set(text, lang, translated)
            return translated
        return text

    def _elicit_llm(self, text: str, lang: str) -> str:
        """Translate *text* to *lang* using an LLM and persist the result."""
        translated = _llm_translate(text, self.main_lang, lang)
        if translated and translated != text:
            self.set(text, lang, translated)
        return translated or text

    # ------------------------------------------------------------------ #
    #  Query helpers
    # ------------------------------------------------------------------ #

    def languages(self) -> List[str]:
        """Return registered target languages."""
        langs = set()
        for lang, _key in self._matcher.exact_map:
            langs.add(lang)
        return sorted(langs)

    def keys(self, lang: Optional[str] = None) -> List[str]:
        """Return registered source keys, optionally for a specific lang."""
        if lang is not None:
            return sorted(k for (l, k) in self._matcher.exact_map if l == lang)
        return sorted(self._matcher.templates.keys())

    def search_keys(self, prefix: str, lang: Optional[str] = None) -> List[str]:
        """Return keys starting with *prefix*."""
        matching = set()
        if lang is not None:
            for l, k in self._matcher.exact_map:
                if l == lang and k.startswith(prefix):
                    matching.add(k)
        else:
            for k in self._matcher.templates:
                if k.startswith(prefix):
                    matching.add(k)
        return sorted(matching)

    def missing_keys(self, lang: str, ref_lang: Optional[str] = None) -> List[str]:
        """Keys present in *ref_lang* (or all keys) but missing in *lang*."""
        target_keys = {k for (l, k) in self._matcher.exact_map if l == lang}
        if ref_lang is not None:
            ref_keys = {k for (l, k) in self._matcher.exact_map if l == ref_lang}
        else:
            ref_keys = set(self._matcher.templates.keys())
        return sorted(ref_keys - target_keys)

    # ------------------------------------------------------------------ #
    #  Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict."""
        translations: Dict[str, Dict[str, str]] = {}
        for (lang, key), value in self._matcher.exact_map.items():
            translations.setdefault(lang, {})[key] = value
        return {
            "namespace": self.namespace,
            "main_lang": self.main_lang,
            "translations": translations,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        store: Optional[TranslationStore] = None,
        replace: bool = True,
    ) -> "TranslationDict":
        """Restore from serialized dict.

        If *replace* is True (default) and *store* is given, namespace
        contents are replaced entirely.
        """
        namespace = data.get("namespace", "")
        main_lang = data.get("main_lang", "en")
        translations = data.get("translations", {})

        if replace and store is not None:
            entries: List[Dict[str, str]] = []
            for lang, mapping in translations.items():
                for key, value in mapping.items():
                    entries.append({"key": str(key), "lang": str(lang), "value": str(value)})
            with store.write_tx():
                store.delete_namespace(namespace)
                store.ensure_namespace(namespace, main_lang)
                if entries:
                    store.save_entries_batch(namespace, entries)
            return cls(namespace=namespace, main_lang=main_lang, store=store)

        td = cls(namespace=namespace, main_lang=main_lang, store=store)
        if translations:
            td.set_many(translations)
        return td

    def reload(self) -> "TranslationDict":
        """Re-load from DB store (no-op if no store)."""
        if self._store is not None:
            self._matcher.clear()
            self._init_from_store()
        return self

    def __repr__(self) -> str:
        langs = self.languages()
        n_keys = len(self._matcher.exact_map)
        persisted = self._store is not None
        return f"TranslationDict(namespace={self.namespace!r}, " f"main_lang={self.main_lang!r}, langs={langs}, " f"keys={n_keys}, persisted={persisted})"
