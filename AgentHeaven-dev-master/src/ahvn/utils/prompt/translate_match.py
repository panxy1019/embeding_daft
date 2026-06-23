"""\
Template parsing, trigram indexing, and exact verification for
pattern-based translation matching.

The matcher is a pure in-memory structure that can be rebuilt from DB data
or hydrated from a persisted runtime-index snapshot.
It implements a two-stage lookup:

1. **Coarse candidate retrieval** — 3-gram inverted index over one probe literal per template
2. **Exact verification** — ordered segment scan with repeated-placeholder equality

Ambiguity policy (lenient):
- Structurally ambiguous templates (e.g. ``{a}{b}``) are matched via
  leftmost-shortest split and a warning is logged.
- When multiple templates verify against the same input, the first
  candidate in work order is chosen and a warning is logged.
- Ambiguity never blocks translation.
"""

__all__ = [
    "TemplateSpec",
    "TranslationMatcher",
]

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from ..basic.log_utils import get_logger

logger = get_logger(__name__)

# Matches {name} placeholders
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Sentinels for {{ / }} escape handling (Python convention)
_ESC_OPEN = "\x00LB\x00"
_ESC_CLOSE = "\x00RB\x00"

# Persisted runtime-index snapshot format version
_RUNTIME_INDEX_VERSION = 1


# ------------------------------------------------------------------ #
#  Trigram helpers
# ------------------------------------------------------------------ #


def _trigrams(text: str) -> Set[str]:
    """Return the set of character 3-grams in *text*."""
    if len(text) < 3:
        return set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


# ------------------------------------------------------------------ #
#  TemplateSpec — parsed source template
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class TemplateSpec:
    """Parsed representation of a source key containing ``{placeholder}`` tokens."""

    source_key: str
    is_pattern: bool
    literals: Tuple[str, ...]
    placeholders: Tuple[str, ...]
    probe: str  # longest non-empty literal (for coarse filtering)
    structurally_ambiguous: bool  # True if adjacent placeholders lack delimiter

    @staticmethod
    def parse(source_key: str) -> "TemplateSpec":
        """Parse a source key into a TemplateSpec.

        Follows Python brace-escape convention: ``{{`` and ``}}`` produce
        literal ``{`` and ``}`` and are **not** treated as placeholders.
        """
        # Protect escaped braces before regex split
        escaped = source_key.replace("{{", _ESC_OPEN).replace("}}", _ESC_CLOSE)
        parts = _PLACEHOLDER_RE.split(escaped)
        # parts alternates: [literal, name, literal, name, ...]
        # Restore escaped braces in literal segments
        literals = tuple(parts[i].replace(_ESC_OPEN, "{").replace(_ESC_CLOSE, "}") for i in range(0, len(parts), 2))
        placeholders = tuple(parts[i] for i in range(1, len(parts), 2))
        is_pattern = len(placeholders) > 0
        probe = max(literals, key=len) if literals else ""

        # Detect structural ambiguity: adjacent placeholders with no delimiter
        ambiguous = False
        if is_pattern:
            # literals has len(placeholders) + 1 entries;
            # interior literals[i] for 0 < i < len(literals)-1 separate adjacent placeholders
            for i in range(1, len(literals) - 1):
                if literals[i] == "":
                    ambiguous = True
                    break
            # Also check if first or last literal is empty when it shouldn't be:
            # {a}{b} → literals=("", "", ""), ambiguous
            # But {a}X → literals=("", "X"), not ambiguous between a and end
            if len(placeholders) >= 2:
                # first literal empty means first placeholder has nothing before it,
                # but that's fine unless the second literal is also empty
                pass  # already caught by interior check above

        return TemplateSpec(
            source_key=source_key,
            is_pattern=is_pattern,
            literals=literals,
            placeholders=placeholders,
            probe=probe,
            structurally_ambiguous=ambiguous,
        )

    def to_db_dict(self) -> Dict[str, Any]:
        """Return fields suitable for DB storage."""
        return {
            "is_pattern": self.is_pattern,
            "literals_json": list(self.literals),
            "placeholders_json": list(self.placeholders),
            "probe": self.probe,
            "structurally_ambiguous": self.structurally_ambiguous,
        }


# ------------------------------------------------------------------ #
#  Exact verifier
# ------------------------------------------------------------------ #


def _verify_match(spec: TemplateSpec, text: str) -> Optional[List[str]]:
    """Verify *text* matches *spec* exactly and extract placeholder values.

    Returns a list of captured values on success, or ``None`` on failure.
    Enforces repeated-placeholder equality (same name → same value).
    Structurally ambiguous templates use leftmost-shortest split and
    a warning is logged (ambiguity is tolerated, not rejected).
    """
    literals = spec.literals
    n_lits = len(literals)

    # Quick length lower bound
    total_literal_len = sum(len(lit) for lit in literals)
    if len(text) < total_literal_len:
        return None

    # Prefix check
    if literals[0] and not text.startswith(literals[0]):
        return None
    # Suffix check
    if literals[-1] and not text.endswith(literals[-1]):
        return None

    # Ordered segment scan
    values: List[str] = []
    pos = 0

    for i in range(n_lits - 1):
        lit = literals[i]
        next_lit = literals[i + 1]

        # Move past current literal
        if lit:
            if not text[pos:].startswith(lit):
                return None
            pos += len(lit)

        # Find the next literal to delimit this placeholder's value
        if next_lit:
            if i == n_lits - 2:
                # Last placeholder — next_lit must be at end
                end = len(text) - len(next_lit)
            else:
                # Find nearest occurrence after pos
                end = text.find(next_lit, pos)
            if end < pos:
                return None
            values.append(text[pos:end])
            pos = end
        else:
            # next_lit is empty — placeholder runs to end or uses leftmost-shortest
            if i == n_lits - 2:
                values.append(text[pos:])
                pos = len(text)
            else:
                # Adjacent empty literal (structurally ambiguous):
                # use leftmost-shortest split — assign empty string to this placeholder
                values.append("")

    # Consume trailing literal
    if literals[-1]:
        if not text[pos:] == literals[-1]:
            return None

    # Enforce repeated-placeholder equality
    seen: Dict[str, str] = {}
    for name, val in zip(spec.placeholders, values):
        if name in seen:
            if seen[name] != val:
                return None
        else:
            seen[name] = val

    if spec.structurally_ambiguous:
        logger.warning(
            "Structurally ambiguous template matched with leftmost-shortest split: " "%r → %s",
            spec.source_key,
            values,
        )

    return values


def _substitute(template: str, names: Tuple[str, ...], values: List[str]) -> str:
    """Replace ``{name}`` placeholders in *template* using a name→value map.

    Follows Python brace-escape convention: ``{{`` / ``}}`` in the template
    produce literal ``{`` / ``}`` in the output and are never substituted.
    """
    # Protect escaped braces
    result = template.replace("{{", _ESC_OPEN).replace("}}", _ESC_CLOSE)
    binding: Dict[str, str] = {}
    for name, val in zip(names, values):
        binding[name] = val
    for name, val in binding.items():
        result = result.replace("{" + name + "}", val)
    # Restore escaped braces
    result = result.replace(_ESC_OPEN, "{").replace(_ESC_CLOSE, "}")
    return result


# ------------------------------------------------------------------ #
#  TranslationMatcher — per-namespace compiled runtime index
# ------------------------------------------------------------------ #


class TranslationMatcher:
    """In-memory index for fast translation lookup within a namespace.

    Uses a fixed 3-gram inverted index over one probe literal per template
    for coarse candidate retrieval, then an exact verifier for correctness.
    """

    def __init__(self):
        # {source_key: TemplateSpec}
        self.templates: Dict[str, TemplateSpec] = {}
        # {(lang, source_key): target_value}
        self.exact_map: Dict[Tuple[str, str], str] = {}
        # {lang: set(source_keys that have a value and are patterns)}
        self._pattern_keys_by_lang: Dict[str, Set[str]] = {}
        # Trigram inverted index: {trigram: set[source_key]}
        self._gram_index: Dict[str, Set[str]] = {}
        # Patterns with probe < 3 chars (no usable trigrams)
        self._residual_keys: Set[str] = set()

    # -- Build / rebuild ---------------------------------------------- #

    def clear(self):
        self.templates.clear()
        self.exact_map.clear()
        self._pattern_keys_by_lang.clear()
        self._gram_index.clear()
        self._residual_keys.clear()

    def add_template(self, spec: TemplateSpec):
        self.templates[spec.source_key] = spec

    def add_value(self, source_key: str, lang: str, target_value: str):
        self.exact_map[(lang, source_key)] = target_value
        spec = self.templates.get(source_key)
        if spec and spec.is_pattern:
            self._pattern_keys_by_lang.setdefault(lang, set()).add(source_key)

    def remove_value(self, source_key: str, lang: str):
        self.exact_map.pop((lang, source_key), None)
        lang_keys = self._pattern_keys_by_lang.get(lang)
        if lang_keys:
            lang_keys.discard(source_key)
            if not lang_keys:
                self._pattern_keys_by_lang.pop(lang, None)

    def rebuild_indexes(self):
        """Rebuild trigram indexes after bulk loading."""
        self._gram_index.clear()
        self._residual_keys.clear()

        for spec in self.templates.values():
            if not spec.is_pattern:
                continue
            self._index_pattern(spec)

    def export_index_snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of runtime index structures."""
        gram_index = {gram: sorted(keys) for gram, keys in self._gram_index.items() if keys}
        residual_keys = sorted(self._residual_keys)
        pattern_keys_by_lang = {lang: sorted(keys) for lang, keys in self._pattern_keys_by_lang.items() if keys}
        return {
            "version": _RUNTIME_INDEX_VERSION,
            "gram_index": gram_index,
            "residual_keys": residual_keys,
            "pattern_keys_by_lang": pattern_keys_by_lang,
        }

    def try_load_index_snapshot(self, snapshot: Optional[Dict[str, Any]]) -> bool:
        """Load persisted runtime indexes if valid for current templates/values."""
        if not snapshot or not isinstance(snapshot, dict):
            return False

        version = snapshot.get("version")
        if version != _RUNTIME_INDEX_VERSION:
            return False

        gram_raw = snapshot.get("gram_index")
        residual_raw = snapshot.get("residual_keys")
        by_lang_raw = snapshot.get("pattern_keys_by_lang")
        if not isinstance(gram_raw, dict) or not isinstance(residual_raw, list) or not isinstance(by_lang_raw, dict):
            return False

        pattern_keys = {key for key, spec in self.templates.items() if spec.is_pattern}

        gram_index: Dict[str, Set[str]] = {}
        indexed_keys: Set[str] = set()
        for gram, keys in gram_raw.items():
            if not isinstance(gram, str) or len(gram) != 3 or not isinstance(keys, list):
                return False
            key_set: Set[str] = set()
            for key in keys:
                if not isinstance(key, str):
                    return False
                key_set.add(key)
            if key_set:
                gram_index[gram] = key_set
                indexed_keys.update(key_set)

        residual_keys: Set[str] = set()
        for key in residual_raw:
            if not isinstance(key, str):
                return False
            residual_keys.add(key)
        indexed_keys.update(residual_keys)

        # Index snapshot must cover exactly the currently known pattern keys.
        if indexed_keys != pattern_keys:
            return False

        pattern_keys_by_lang: Dict[str, Set[str]] = {}
        for lang, keys in by_lang_raw.items():
            if not isinstance(lang, str) or not isinstance(keys, list):
                return False
            key_set: Set[str] = set()
            for key in keys:
                if not isinstance(key, str):
                    return False
                key_set.add(key)
            if not key_set.issubset(pattern_keys):
                return False
            if key_set:
                pattern_keys_by_lang[lang] = key_set

        expected_by_lang: Dict[str, Set[str]] = {}
        for (lang, source_key), _value in self.exact_map.items():
            spec = self.templates.get(source_key)
            if spec and spec.is_pattern:
                expected_by_lang.setdefault(lang, set()).add(source_key)
        if pattern_keys_by_lang != expected_by_lang:
            return False

        self._gram_index = gram_index
        self._residual_keys = residual_keys
        self._pattern_keys_by_lang = pattern_keys_by_lang
        return True

    def _index_pattern(self, spec: TemplateSpec):
        """Add a single pattern spec to the trigram/residual index."""
        grams = _trigrams(spec.probe)
        if grams:
            for gram in grams:
                self._gram_index.setdefault(gram, set()).add(spec.source_key)
        else:
            self._residual_keys.add(spec.source_key)

    def _unindex_pattern(self, spec: TemplateSpec):
        """Remove a single pattern spec from the trigram/residual index."""
        grams = _trigrams(spec.probe)
        if grams:
            for gram in grams:
                bucket = self._gram_index.get(gram)
                if bucket:
                    bucket.discard(spec.source_key)
                    if not bucket:
                        del self._gram_index[gram]
        else:
            self._residual_keys.discard(spec.source_key)

    # -- Lookup ------------------------------------------------------- #

    def lookup(self, text: str, lang: str, main_lang: str) -> Optional[str]:
        """Full lookup: main_lang passthrough → exact → pattern → None."""
        if lang == main_lang:
            return text

        # 1. Exact string lookup
        result = self.exact_map.get((lang, text))
        if result is not None:
            return result

        # 2. Pattern match
        return self._lookup_pattern(text, lang)

    def _lookup_pattern(self, text: str, lang: str) -> Optional[str]:
        """Two-stage pattern matching: coarse candidates → exact verification."""
        lang_keys = self._pattern_keys_by_lang.get(lang)
        if not lang_keys:
            return None

        # Step 1: coarse candidates via trigram matching
        text_grams = _trigrams(text)
        candidate_hits: Dict[str, int] = {}  # source_key → matched gram count

        if text_grams:
            for gram in text_grams:
                bucket = self._gram_index.get(gram)
                if bucket:
                    for key in bucket:
                        if key in lang_keys:
                            candidate_hits[key] = candidate_hits.get(key, 0) + 1

        # Always include residual keys
        for key in self._residual_keys:
            if key in lang_keys:
                candidate_hits.setdefault(key, 0)

        if not candidate_hits:
            return None

        # Step 2: work ordering (efficiency only, not correctness)
        def _sort_key(key: str) -> Tuple[int, int, int, str]:
            spec = self.templates[key]
            total_lit = sum(len(lit) for lit in spec.literals)
            return (-candidate_hits.get(key, 0), -len(spec.probe), -total_lit, key)

        ordered = sorted(candidate_hits.keys(), key=_sort_key)

        # Step 3: exact verification
        matches: List[Tuple[str, List[str]]] = []
        for key in ordered:
            spec = self.templates[key]
            values = _verify_match(spec, text)
            if values is not None:
                target_template = self.exact_map.get((lang, key))
                if target_template is not None:
                    matches.append((key, values))

        # Step 4: resolve
        if len(matches) == 0:
            return None

        if len(matches) > 1:
            keys = [m[0] for m in matches]
            logger.warning(
                "Ambiguous pattern match for %r — %d templates matched: %s; " "using first candidate.",
                text,
                len(matches),
                keys,
            )

        key, values = matches[0]
        spec = self.templates[key]
        target = self.exact_map[(lang, key)]
        return _substitute(target, spec.placeholders, values)
