"""
Corner-case unit tests for the matching algorithm internals.

Exercises TemplateSpec, _verify_match, _substitute, _trigrams,
and TranslationMatcher directly, covering paths that the higher-level
TranslationDict tests do not reach.
"""

import pytest

from ahvn.utils.prompt.translate_match import (
    TemplateSpec,
    TranslationMatcher,
    _substitute,
    _trigrams,
    _verify_match,
)

# ------------------------------------------------------------------ #
#  Trigram helper
# ------------------------------------------------------------------ #


class TestTrigrams:

    def test_empty_returns_empty(self):
        assert _trigrams("") == set()

    def test_one_char_returns_empty(self):
        assert _trigrams("a") == set()

    def test_two_chars_returns_empty(self):
        assert _trigrams("ab") == set()

    def test_exactly_three_chars(self):
        assert _trigrams("abc") == {"abc"}

    def test_four_chars_two_grams(self):
        assert _trigrams("abcd") == {"abc", "bcd"}

    def test_whitespace_grams(self):
        grams = _trigrams(" is ")
        assert " is" in grams
        assert "is " in grams


# ------------------------------------------------------------------ #
#  TemplateSpec.parse
# ------------------------------------------------------------------ #


class TestTemplateSpecParse:

    def test_plain_text_not_a_pattern(self):
        spec = TemplateSpec.parse("Hello World")
        assert not spec.is_pattern
        assert spec.literals == ("Hello World",)
        assert spec.placeholders == ()
        assert spec.probe == "Hello World"
        assert not spec.structurally_ambiguous

    def test_bare_single_placeholder(self):
        # "{x}" → empty probe, not ambiguous (only one placeholder)
        spec = TemplateSpec.parse("{x}")
        assert spec.is_pattern
        assert spec.literals == ("", "")
        assert spec.placeholders == ("x",)
        assert spec.probe == ""
        assert not spec.structurally_ambiguous

    def test_prefix_and_suffix_around_placeholder(self):
        spec = TemplateSpec.parse("Hello {name}!")
        assert spec.is_pattern
        assert spec.literals == ("Hello ", "!")
        assert spec.placeholders == ("name",)
        assert spec.probe == "Hello "  # "Hello " > "!"
        assert not spec.structurally_ambiguous

    def test_two_placeholders_with_delimiter(self):
        spec = TemplateSpec.parse("{a} and {b}")
        assert spec.literals == ("", " and ", "")
        assert spec.placeholders == ("a", "b")
        assert spec.probe == " and "
        assert not spec.structurally_ambiguous

    def test_adjacent_placeholders_are_ambiguous(self):
        spec = TemplateSpec.parse("{a}{b}")
        assert spec.is_pattern
        assert spec.structurally_ambiguous

    def test_space_separated_placeholders_not_ambiguous(self):
        spec = TemplateSpec.parse("{a} {b}")
        assert not spec.structurally_ambiguous

    def test_probe_is_longest_literal(self):
        # literals: ("", " in the ", " next to ", "")
        # " in the " = 8 chars, " next to " = 9 chars → longest wins
        spec = TemplateSpec.parse("{verb} in the {noun} next to {adj}")
        assert spec.probe == " next to "
        assert len(spec.probe) == 9

    def test_short_probe_two_chars(self):
        # "A {x} B" → literals ("A ", " B"), both 2 chars
        spec = TemplateSpec.parse("A {x} B")
        assert len(spec.probe) == 2

    def test_repeated_placeholder_name(self):
        spec = TemplateSpec.parse("{name} is {name}")
        assert spec.placeholders == ("name", "name")
        assert spec.literals == ("", " is ", "")
        assert not spec.structurally_ambiguous

    def test_three_adjacent_placeholders_ambiguous(self):
        spec = TemplateSpec.parse("{a}{b}{c}")
        assert spec.structurally_ambiguous


# ------------------------------------------------------------------ #
#  _verify_match
# ------------------------------------------------------------------ #


class TestVerifyMatch:

    def test_ambiguous_spec_uses_leftmost_shortest(self):
        spec = TemplateSpec.parse("{a}{b}")
        # leftmost-shortest: {a}="" , {b}="foobar"
        result = _verify_match(spec, "foobar")
        assert result == ["", "foobar"]
        # empty input: both empty
        result = _verify_match(spec, "")
        assert result == ["", ""]

    def test_prefix_mismatch_returns_none(self):
        spec = TemplateSpec.parse("Hello {name}!")
        assert _verify_match(spec, "Hi Alice!") is None

    def test_suffix_mismatch_returns_none(self):
        spec = TemplateSpec.parse("Hello {name}!")
        assert _verify_match(spec, "Hello Alice.") is None

    def test_successful_single_placeholder(self):
        spec = TemplateSpec.parse("Hello {name}!")
        assert _verify_match(spec, "Hello Alice!") == ["Alice"]

    def test_empty_placeholder_value_is_allowed(self):
        # Previously rejected; now allowed after the refactor
        spec = TemplateSpec.parse("Hello {name}!")
        assert _verify_match(spec, "Hello !") == [""]

    def test_multi_placeholder_extraction(self):
        spec = TemplateSpec.parse("{a} and {b}")
        assert _verify_match(spec, "cats and dogs") == ["cats", "dogs"]

    def test_repeated_placeholder_same_value_matches(self):
        spec = TemplateSpec.parse("{x} is {x}")
        assert _verify_match(spec, "Alice is Alice") == ["Alice", "Alice"]

    def test_repeated_placeholder_different_value_fails(self):
        spec = TemplateSpec.parse("{x} is {x}")
        assert _verify_match(spec, "Alice is Bob") is None

    def test_bare_placeholder_matches_any_string(self):
        # {x} with no surrounding text — should capture everything
        spec = TemplateSpec.parse("{x}")
        assert _verify_match(spec, "literally anything 123") == ["literally anything 123"]

    def test_bare_placeholder_matches_empty_string(self):
        spec = TemplateSpec.parse("{x}")
        assert _verify_match(spec, "") == [""]

    def test_text_shorter_than_literals_fails(self):
        spec = TemplateSpec.parse("Hello {name} World!")
        # minimum valid text is "Hello  World!" (15 chars with empty name)
        assert _verify_match(spec, "Hi!") is None

    def test_placeholder_only_at_start(self):
        spec = TemplateSpec.parse("{greeting}, how are you?")
        assert _verify_match(spec, "Hello, how are you?") == ["Hello"]
        assert _verify_match(spec, "Hi, how are you?") == ["Hi"]

    def test_placeholder_only_at_end(self):
        spec = TemplateSpec.parse("Error code: {code}")
        assert _verify_match(spec, "Error code: 404") == ["404"]
        assert _verify_match(spec, "Error code: ") == [""]

    def test_special_chars_in_literals(self):
        # Regex-special chars in surrounding text must not cause false failures
        spec = TemplateSpec.parse("1+1 = {result} (always)")
        assert _verify_match(spec, "1+1 = 2 (always)") == ["2"]
        assert _verify_match(spec, "1+1 = 999 (always)") == ["999"]

    def test_multiword_placeholder_value(self):
        spec = TemplateSpec.parse("I love {fruit}")
        assert _verify_match(spec, "I love red apples and green pears") == ["red apples and green pears"]

    def test_greedy_last_delimiter_anchored_to_suffix(self):
        # "The {x} is {y}." — last placeholder is delimited by "." at the end
        spec = TemplateSpec.parse("The {x} is {y}.")
        result = _verify_match(spec, "The cat is very cute.")
        # x should end at first " is ", y gets the rest before "."
        assert result == ["cat", "very cute"]


# ------------------------------------------------------------------ #
#  _substitute
# ------------------------------------------------------------------ #


class TestSubstitute:

    def test_single_substitution(self):
        assert _substitute("我爱{fruit}", ("fruit",), ["apples"]) == "我爱apples"

    def test_multiple_different_placeholders(self):
        result = _substitute("你好，{name}！欢迎来到{place}。", ("name", "place"), ["Alice", "Wonderland"])
        assert result == "你好，Alice！欢迎来到Wonderland。"

    def test_all_occurrences_of_same_name_replaced(self):
        # Bug fix: previously only replaced first occurrence
        result = _substitute("{name} is always {name}", ("name",), ["Alice"])
        assert result == "Alice is always Alice"

    def test_three_occurrences_in_target(self):
        result = _substitute("{x}! {x}! {x}!", ("x",), ["Go"])
        assert result == "Go! Go! Go!"

    def test_empty_value_substitution(self):
        assert _substitute("Hello {name}!", ("name",), [""]) == "Hello !"

    def test_placeholder_not_in_names_is_preserved(self):
        # Target contains {extra} which is not in the source binding
        result = _substitute("{greeting} and {other}", ("greeting",), ["Hi"])
        assert result == "Hi and {other}"


# ------------------------------------------------------------------ #
#  TranslationMatcher
# ------------------------------------------------------------------ #


def _make_matcher(patterns_by_lang):
    """Build a TranslationMatcher from {lang: {source_key: target_value}}."""
    m = TranslationMatcher()
    for lang, entries in patterns_by_lang.items():
        for src, tgt in entries.items():
            spec = TemplateSpec.parse(src)
            m.add_template(spec)
            m.add_value(src, lang, tgt)
    m.rebuild_indexes()
    return m


class TestTranslationMatcher:

    # -- Index structure ----------------------------------------------- #

    def test_long_probe_goes_to_gram_index(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        # probe "I love " has trigrams → in gram index, not residual
        assert "I love {fruit}" not in m._residual_keys
        assert any("I love {fruit}" in bucket for bucket in m._gram_index.values())

    def test_short_probe_goes_to_residual(self):
        # "A {x} B" → both literals "A " and " B" are 2 chars → no trigrams
        m = _make_matcher({"zh": {"A {x} B": "甲{x}乙"}})
        spec = m.templates["A {x} B"]
        assert len(spec.probe) < 3
        assert "A {x} B" in m._residual_keys

    def test_bare_placeholder_goes_to_residual(self):
        m = _make_matcher({"zh": {"{x}": "【{x}】"}})
        assert "{x}" in m._residual_keys

    # -- Lookup correctness -------------------------------------------- #

    def test_indexed_pattern_matches(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        assert m.lookup("I love apples", "zh", "en") == "我爱apples"

    def test_residual_pattern_matches(self):
        # bare {x} matches any text via residual path
        m = _make_matcher({"zh": {"{x}": "【{x}】"}})
        assert m.lookup("hello world", "zh", "en") == "【hello world】"

    def test_short_probe_residual_still_matches(self):
        m = _make_matcher({"zh": {"A {x} B": "甲{x}乙"}})
        assert m.lookup("A foo B", "zh", "en") == "甲foo乙"

    def test_main_lang_passthrough(self):
        m = _make_matcher({"en": {"I love {fruit}": "I love {fruit}"}})
        assert m.lookup("I love apples", "en", "en") == "I love apples"

    def test_exact_beats_pattern(self):
        m = TranslationMatcher()
        m.add_template(TemplateSpec.parse("I love {fruit}"))
        m.add_value("I love {fruit}", "zh", "我爱{fruit}")
        m.add_template(TemplateSpec.parse("I love apples"))
        m.add_value("I love apples", "zh", "我特别爱苹果")
        m.rebuild_indexes()
        assert m.lookup("I love apples", "zh", "en") == "我特别爱苹果"
        assert m.lookup("I love oranges", "zh", "en") == "我爱oranges"

    def test_no_match_returns_none(self):
        m = _make_matcher({"zh": {"I love {fruit}!": "我爱{fruit}！"}})
        # "I love apples" doesn't end with "!" → no match
        assert m.lookup("I love apples", "zh", "en") is None

    def test_no_pattern_for_lang_returns_none(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        assert m.lookup("I love apples", "ja", "en") is None

    # -- Ambiguity ----------------------------------------------------- #

    def test_ambiguous_two_patterns_picks_first(self):
        # Both patterns have identical structure, only placeholder name differs;
        # the matcher should pick the first candidate in work order and warn.
        m = _make_matcher(
            {
                "zh": {
                    "The {x} is ready": "该{x}已就绪",
                    "The {y} is ready": "【{y}】准备好了",
                }
            }
        )
        result = m.lookup("The cat is ready", "zh", "en")
        # Should return one of the two, not None
        assert result is not None
        assert result in ("该cat已就绪", "【cat】准备好了")

    # -- Mutation & index updates -------------------------------------- #

    def test_remove_value_stops_matching(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        assert m.lookup("I love apples", "zh", "en") == "我爱apples"
        m.remove_value("I love {fruit}", "zh")
        assert m.lookup("I love apples", "zh", "en") is None

    def test_unindex_pattern_removes_from_gram_index(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        spec = m.templates["I love {fruit}"]
        assert any("I love {fruit}" in bucket for bucket in m._gram_index.values())
        m._unindex_pattern(spec)
        assert not any("I love {fruit}" in bucket for bucket in m._gram_index.values())

    def test_unindex_residual_removes_from_residual(self):
        m = _make_matcher({"zh": {"{x}": "【{x}】"}})
        spec = m.templates["{x}"]
        assert "{x}" in m._residual_keys
        m._unindex_pattern(spec)
        assert "{x}" not in m._residual_keys

    def test_clear_resets_all_state(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        m.clear()
        assert not m.templates
        assert not m.exact_map
        assert not m._gram_index
        assert not m._residual_keys

    # -- Correctness of substitution via matcher ----------------------- #

    def test_repeated_name_all_substituted_in_target(self):
        m = _make_matcher({"zh": {"{name} is {name}": "{name}就是{name}"}})
        assert m.lookup("Alice is Alice", "zh", "en") == "Alice就是Alice"
        assert m.lookup("Alice is Bob", "zh", "en") is None

    def test_empty_placeholder_value_matched(self):
        m = _make_matcher({"zh": {"Error: {msg}": "错误：{msg}"}})
        assert m.lookup("Error: ", "zh", "en") == "错误："

    def test_multiword_value_captured(self):
        m = _make_matcher({"zh": {"I love {fruit}": "我爱{fruit}"}})
        assert m.lookup("I love red apples and pears", "zh", "en") == "我爱red apples and pears"


# ------------------------------------------------------------------ #
#  Focused batch: lenient ambiguity, long probe, incremental ops
# ------------------------------------------------------------------ #


class TestLenientAmbiguityPolicy:
    """Tests for the lenient ambiguity policy (Issue 1)."""

    def test_structural_ambiguity_returns_translation(self):
        """Structurally ambiguous template like {a}{b} returns a translation, not None."""
        m = _make_matcher({"zh": {"{a}{b}": "【{a}|{b}】"}})
        result = m.lookup("hello", "zh", "en")
        assert result is not None
        # leftmost-shortest: {a}="" , {b}="hello"
        assert result == "【|hello】"

    def test_structural_ambiguity_three_adjacent(self):
        """{a}{b}{c} with leftmost-shortest gives a="" b="" c=text."""
        spec = TemplateSpec.parse("{a}{b}{c}")
        result = _verify_match(spec, "xyz")
        assert result == ["", "", "xyz"]

    def test_multi_template_ambiguity_picks_first(self):
        """When 2+ templates verify, the first in work order is picked."""
        m = _make_matcher(
            {
                "zh": {
                    "Item: {x}!": "项目：{x}！",
                    "Item: {y}!": "条目：{y}！",
                }
            }
        )
        result = m.lookup("Item: foo!", "zh", "en")
        assert result is not None
        assert result in ("项目：foo！", "条目：foo！")

    def test_structural_ambiguity_with_suffix(self):
        """{a}{b} end → leftmost-shortest: {a}="" , {b}=matched text."""
        spec = TemplateSpec.parse("{a}{b} end")
        result = _verify_match(spec, "hello world end")
        assert result is not None
        assert result == ["", "hello world"]

    def test_structural_ambiguity_with_prefix(self):
        """start {a}{b} → leftmost-shortest: {a}="" , {b}=rest."""
        spec = TemplateSpec.parse("start {a}{b}")
        result = _verify_match(spec, "start foobar")
        assert result == ["", "foobar"]


class TestLongProbe:
    """Tests for probe >255 chars (Issue 2)."""

    def test_long_probe_not_truncated_in_spec(self):
        long_lit = "A" * 300
        key = f"{long_lit} {{x}} end"
        spec = TemplateSpec.parse(key)
        # probe is "AAA...A " (300 A's + space) = 301 chars, longest literal
        assert len(spec.probe) == 301
        db_dict = spec.to_db_dict()
        assert len(db_dict["probe"]) == 301

    def test_long_probe_round_trip_via_matcher(self):
        """After rebuild, a template with probe >255 chars still matches."""
        long_lit = "B" * 300
        key = f"{long_lit} {{x}} end"
        m = _make_matcher({"zh": {key: "翻译 {x} 结束"}})
        result = m.lookup(f"{long_lit} hello end", "zh", "en")
        assert result == "翻译 hello 结束"


class TestBraceEscaping:
    """Tests for {{ / }} escape convention (Python standard)."""

    def test_double_braces_in_source_are_literal(self):
        """{{x}} in a source key is literal '{x}', NOT a placeholder."""
        spec = TemplateSpec.parse("show {{x}} literally")
        assert not spec.is_pattern
        assert spec.literals == ("show {x} literally",)

    def test_mixed_escape_and_placeholder(self):
        """A key can have both escaped braces and real placeholders."""
        spec = TemplateSpec.parse("{{literal}} and {real}")
        assert spec.is_pattern
        assert spec.literals == ("{literal} and ", "")
        assert spec.placeholders == ("real",)

    def test_substitute_double_braces_produce_literal(self):
        """In target values, {{ / }} become literal { / } after substitution."""
        result = _substitute("result: {{not_replaced}} {x}", ("x",), ["hello"])
        assert result == "result: {not_replaced} hello"

    def test_substitute_no_double_braces(self):
        """Normal single-brace placeholders still work."""
        result = _substitute("{a} and {b}", ("a", "b"), ["X", "Y"])
        assert result == "X and Y"

    def test_roundtrip_escaped_braces_via_matcher(self):
        """Escaped braces in the target value produce literal braces in output."""
        m = _make_matcher({"zh": {"say {x}": "说 {{literal}} {x}"}})
        result = m.lookup("say hello", "zh", "en")
        assert result == "说 {literal} hello"


class TestIncrementalIndexOps:
    """Tests for incremental index maintenance (Issue 3)."""

    def test_index_pattern_adds_to_gram_index(self):
        m = TranslationMatcher()
        spec = TemplateSpec.parse("I love {fruit}")
        m.add_template(spec)
        m._index_pattern(spec)
        assert any("I love {fruit}" in bucket for bucket in m._gram_index.values())

    def test_repeated_set_does_not_grow_index(self):
        """Calling set() on an existing key should not duplicate index entries."""
        from ahvn.utils.prompt.translate import TranslationDict

        td = TranslationDict(namespace="test_incr")
        td.set("I love {fruit}", "zh", "我爱{fruit}")

        # Count gram index entries for this key
        def count_refs():
            return sum(1 for bucket in td._matcher._gram_index.values() if "I love {fruit}" in bucket)

        c1 = count_refs()
        td.set("I love {fruit}", "zh", "我爱{fruit}v2")
        c2 = count_refs()
        assert c1 == c2

    def test_delete_last_lang_removes_template(self):
        """Deleting the last translation of a key removes it from templates and index."""
        from ahvn.utils.prompt.translate import TranslationDict

        td = TranslationDict(namespace="test_del_incr")
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        assert "I love {fruit}" in td._matcher.templates
        td.delete("I love {fruit}", "zh")
        assert "I love {fruit}" not in td._matcher.templates
        assert not any("I love {fruit}" in bucket for bucket in td._matcher._gram_index.values())

    def test_delete_one_lang_keeps_template(self):
        """Deleting one lang while another remains keeps the template indexed."""
        from ahvn.utils.prompt.translate import TranslationDict

        td = TranslationDict(namespace="test_del_partial")
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        td.set("I love {fruit}", "ja", "私は{fruit}が大好きです")
        td.delete("I love {fruit}", "zh")
        assert "I love {fruit}" in td._matcher.templates
        assert td.lookup("I love apples", "ja") == "私はapplesが大好きです"
        assert td.lookup("I love apples", "zh") is None


class TestIndexSnapshotAndDeterminism:
    """Snapshot hydration and deterministic lenient-ambiguity tests."""

    @staticmethod
    def _build_matcher(lang_map):
        matcher = TranslationMatcher()
        for lang, mapping in lang_map.items():
            for source_key, target_value in mapping.items():
                matcher.add_template(TemplateSpec.parse(source_key))
                matcher.add_value(source_key, lang, target_value)
        return matcher

    def test_ambiguity_tie_break_is_deterministic(self):
        matcher = self._build_matcher(
            {
                "zh": {
                    "Scope {a} done": "L:{a}",
                    "Scope {b} done": "R:{b}",
                }
            }
        )
        matcher.rebuild_indexes()
        assert matcher.lookup("Scope cat done", "zh", "en") == "L:cat"

    def test_export_and_load_snapshot_roundtrip(self):
        source = {
            "zh": {"I love {fruit}": "我爱{fruit}"},
            "ja": {"I love {fruit}": "私は{fruit}が大好きです"},
        }
        matcher_1 = self._build_matcher(source)
        matcher_1.rebuild_indexes()
        snapshot = matcher_1.export_index_snapshot()

        matcher_2 = self._build_matcher(source)
        assert matcher_2.try_load_index_snapshot(snapshot)
        assert matcher_2.lookup("I love apples", "zh", "en") == "我爱apples"
        assert matcher_2.lookup("I love apples", "ja", "en") == "私はapplesが大好きです"

    def test_stale_snapshot_key_set_is_rejected(self):
        source = {"zh": {"I love {fruit}": "我爱{fruit}"}}
        matcher_1 = self._build_matcher(source)
        matcher_1.rebuild_indexes()
        snapshot = matcher_1.export_index_snapshot()
        snapshot["residual_keys"] = ["ghost-key"]

        matcher_2 = self._build_matcher(source)
        assert not matcher_2.try_load_index_snapshot(snapshot)
