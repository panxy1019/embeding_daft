"""
Unit tests for the refactored translation module.

Tests cover in-memory mode (store=None) and DB-backed mode
(store=TranslationStore with temporary SQLite).
"""

import tempfile
import pytest
from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

# ------------------------------------------------------------------ #
#  Fixtures & test data
# ------------------------------------------------------------------ #

_ZH_TRANSLATIONS = {
    "You are a helpful assistant for calculating Fibonacci numbers.": "你是一个帮助计算斐波那契数的助手。",
    "The Fibonacci sequence is defined as follows: F(0) = 0, F(1) = 1, and F(n) = F(n-1) + F(n-2) for n > 1.": "斐波那契数列定义如下：F(0) = 0，F(1) = 1，且对于 n > 1，F(n) = F(n-1) + F(n-2)。",
    "Please calculate the Fibonacci number for the following input.": "请计算以下输入的斐波那契数。",
    "Task Descriptions": "任务描述",
    "Examples": "示例",
    "Instructions": "指令",
}

_JA_TRANSLATIONS = {
    "You are a helpful assistant for calculating Fibonacci numbers.": "あなたはフィボナッチ数を計算するための便利なアシスタントです。",
    "Task Descriptions": "タスクの説明",
}

_PATTERN_ZH = {
    "I love {fruit}": "我爱{fruit}",
    "Hello, {name}! Welcome to {place}.": "HelloZH:{name}:{place}",
    "The answer is {answer}.": "AnswerZH:{answer}",
}

_PATTERN_JA = {
    "I love {fruit}": "私は{fruit}が大好きです",
}


def _populate_td(td):
    td.set_many({"zh": _ZH_TRANSLATIONS, "ja": _JA_TRANSLATIONS})
    return td


def _populate_patterns(td):
    td.set_many({"zh": _PATTERN_ZH, "ja": _PATTERN_JA})
    return td


@pytest.fixture
def td():
    return _populate_td(TranslationDict(namespace="fibonacci", main_lang="en"))


@pytest.fixture
def td_patterns():
    return _populate_patterns(TranslationDict(namespace="patterns", main_lang="en"))


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test_translations.db")
    return TranslationStore(provider="sqlite", database=db_path)


@pytest.fixture
def td_db(tmp_db):
    return _populate_td(TranslationDict(namespace="fibonacci", main_lang="en", store=tmp_db))


@pytest.fixture
def td_patterns_db(tmp_db):
    return _populate_patterns(TranslationDict(namespace="patterns", main_lang="en", store=tmp_db))


def test_write_tx_yields_database_handle(tmp_db):
    import sqlalchemy as sa
    from ahvn.utils.prompt.translate_schema import TranslationNamespaceEntity

    with tmp_db.write_tx() as db:
        assert db is not None
        result = db.orm_execute(
            sa.select(sa.func.count()).select_from(TranslationNamespaceEntity.__table__),
            readonly=True,
        )
        assert result.scalar() == 0

    with tmp_db.tx(write=True) as db:
        assert db is not None

    with tmp_db.tx(write=False) as db:
        assert db is not None


# ------------------------------------------------------------------ #
#  Exact lookup
# ------------------------------------------------------------------ #


class TestExactLookup:

    def test_exact_match(self, td):
        assert td.lookup("Task Descriptions", "zh") == "任务描述"
        assert td.lookup("Examples", "zh") == "示例"

    def test_exact_full_sentence(self, td):
        result = td.lookup(
            "You are a helpful assistant for calculating Fibonacci numbers.",
            "zh",
        )
        assert result == "你是一个帮助计算斐波那契数的助手。"

    def test_main_lang_returns_self(self, td):
        assert td.lookup("anything at all", "en") == "anything at all"

    def test_missing_key_returns_none(self, td):
        assert td.lookup("nonexistent key", "zh") is None

    def test_missing_lang_returns_none(self, td):
        assert td.lookup("Task Descriptions", "fr") is None

    def test_multi_lang(self, td):
        assert td.lookup("Task Descriptions", "zh") == "任务描述"
        assert td.lookup("Task Descriptions", "ja") == "タスクの説明"


# ------------------------------------------------------------------ #
#  Pattern matching
# ------------------------------------------------------------------ #


class TestPatternLookup:

    def test_single_placeholder(self, td_patterns):
        assert td_patterns.lookup("I love apples", "zh") == "我爱apples"
        assert td_patterns.lookup("I love oranges", "zh") == "我爱oranges"

    def test_single_placeholder_other_lang(self, td_patterns):
        assert td_patterns.lookup("I love apples", "ja") == "私はapplesが大好きです"

    def test_multi_placeholder(self, td_patterns):
        result = td_patterns.lookup("Hello, Alice! Welcome to Wonderland.", "zh")
        assert result == "HelloZH:Alice:Wonderland"

    def test_pattern_no_match_in_lang(self, td_patterns):
        assert td_patterns.lookup("Hello, Alice! Welcome to Wonderland.", "ja") is None

    def test_pattern_with_numbers(self, td_patterns):
        assert td_patterns.lookup("The answer is 42.", "zh") == "AnswerZH:42"

    def test_pattern_with_complex_content(self, td_patterns):
        result = td_patterns.lookup("I love red apples and green pears", "zh")
        assert result == "我爱red apples and green pears"


# ------------------------------------------------------------------ #
#  tr() callable factory
# ------------------------------------------------------------------ #


class TestTrFunction:

    def test_tr_basic(self, td):
        tr = td.tr("zh")
        assert tr("Task Descriptions") == "任务描述"
        assert tr("Instructions") == "指令"

    def test_tr_unknown_key_none_mode(self, td):
        tr = td.tr("zh", elicit="none")
        assert tr("unknown key") == "unknown key"

    def test_tr_main_lang(self, td):
        tr = td.tr("en")
        assert tr("anything") == "anything"

    def test_tr_pattern_matching(self, td_patterns):
        tr = td_patterns.tr("zh")
        assert tr("I love bananas") == "我爱bananas"
        assert tr("The answer is 7.") == "AnswerZH:7"

    def test_tr_converts_non_string(self, td):
        tr = td.tr("zh")
        assert tr(42) == "42"

    def test_tr_human_elicit_sets(self, td, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "clé manquante traduite")
        tr = td.tr("fr", elicit="human")
        result = tr("missing key")
        assert result == "clé manquante traduite"
        assert td.lookup("missing key", "fr") == "clé manquante traduite"

    def test_tr_human_elicit_empty_input(self, td, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        tr = td.tr("fr", elicit="human")
        result = tr("missing key")
        assert result == "missing key"

    def test_tr_llm_elicit_translates(self, td, monkeypatch):
        monkeypatch.setattr(
            "ahvn.utils.prompt.translate._llm_translate",
            lambda text, src, tgt: f"[llm:{text}]",
        )
        tr = td.tr("zh", elicit="llm")
        result = tr("some untranslated text")
        assert result == "[llm:some untranslated text]"

    def test_tr_fallback_single_dict(self):
        primary = TranslationDict(namespace="primary", main_lang="en")
        fallback = TranslationDict(namespace="shared", main_lang="en")
        fallback.set("Task Descriptions", "zh", "shared-task-descriptions")

        tr = primary.tr("zh", fallbacks=fallback)
        assert tr("Task Descriptions") == "shared-task-descriptions"

    def test_tr_fallback_order_first_hit(self):
        primary = TranslationDict(namespace="primary", main_lang="en")
        fallback_1 = TranslationDict(namespace="fb_1", main_lang="en")
        fallback_2 = TranslationDict(namespace="fb_2", main_lang="en")
        fallback_1.set("Instructions", "zh", "from-fallback-1")
        fallback_2.set("Instructions", "zh", "from-fallback-2")

        tr = primary.tr("zh", fallbacks=[fallback_1, fallback_2])
        assert tr("Instructions") == "from-fallback-1"

    def test_lookup_prefers_primary_over_fallback(self):
        primary = TranslationDict(namespace="primary", main_lang="en")
        fallback = TranslationDict(namespace="shared", main_lang="en")
        primary.set("Examples", "zh", "from-primary")
        fallback.set("Examples", "zh", "from-fallback")

        result = primary.lookup("Examples", "zh", fallbacks=[fallback])
        assert result == "from-primary"

    def test_lookup_rejects_invalid_fallback_item(self):
        td = TranslationDict(namespace="primary", main_lang="en")
        with pytest.raises(TypeError):
            td.lookup("Examples", "zh", fallbacks=["not-a-translation-dict"])


# ------------------------------------------------------------------ #
#  set / set_many / delete API
# ------------------------------------------------------------------ #


class TestMutations:

    def test_set_chaining(self):
        td = TranslationDict()
        result = td.set("hello", "zh", "你好").set("world", "zh", "世界")
        assert isinstance(result, TranslationDict)
        assert td.lookup("hello", "zh") == "你好"
        assert td.lookup("world", "zh") == "世界"

    def test_set_overwrite(self):
        td = TranslationDict()
        td.set("hello", "zh", "你好旧版")
        td.set("hello", "zh", "你好新版")
        assert td.lookup("hello", "zh") == "你好新版"

    def test_set_many_multiple_langs(self):
        td = TranslationDict()
        td.set_many(
            {
                "zh": {"hi": "nihao"},
                "ja": {"hi": "konnichiwa"},
                "fr": {"hi": "salut"},
            }
        )
        assert td.lookup("hi", "zh") == "nihao"
        assert td.lookup("hi", "ja") == "konnichiwa"
        assert td.lookup("hi", "fr") == "salut"

    def test_delete(self):
        td = TranslationDict()
        td.set("hello", "zh", "你好")
        assert td.lookup("hello", "zh") == "你好"
        td.delete("hello", "zh")
        assert td.lookup("hello", "zh") is None


# ------------------------------------------------------------------ #
#  Serialization
# ------------------------------------------------------------------ #


class TestSerialization:

    def test_roundtrip(self, td):
        data = td.to_dict()
        restored = TranslationDict.from_dict(data)
        assert restored.namespace == td.namespace
        assert restored.main_lang == td.main_lang
        assert restored.lookup("Task Descriptions", "zh") == "任务描述"
        assert restored.lookup("Task Descriptions", "ja") == "タスクの説明"

    def test_roundtrip_with_patterns(self, td_patterns):
        data = td_patterns.to_dict()
        restored = TranslationDict.from_dict(data)
        assert restored.lookup("I love apples", "zh") == "我爱apples"
        assert restored.lookup("Hello, Bob! Welcome to Paris.", "zh") == "HelloZH:Bob:Paris"

    def test_to_dict_structure(self):
        td = TranslationDict(namespace="test", main_lang="en")
        td.set("hi", "zh", "你好")
        d = td.to_dict()
        assert d == {
            "namespace": "test",
            "main_lang": "en",
            "translations": {"zh": {"hi": "你好"}},
        }


# ------------------------------------------------------------------ #
#  Edge cases
# ------------------------------------------------------------------ #


class TestEdgeCases:

    def test_empty_translation_dict(self):
        td = TranslationDict()
        assert td.lookup("anything", "zh") is None
        tr = td.tr("zh")
        assert tr("anything") == "anything"

    def test_repr(self, td):
        r = repr(td)
        assert "fibonacci" in r
        assert "en" in r

    def test_pattern_exact_takes_priority(self):
        td = TranslationDict()
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        td.set("I love apples", "zh", "我特别爱苹果")
        assert td.lookup("I love apples", "zh") == "我特别爱苹果"
        assert td.lookup("I love oranges", "zh") == "我爱oranges"

    def test_placeholder_in_source_but_not_target(self):
        td = TranslationDict()
        td.set("Error code: {code}", "zh", "发生了错误")
        assert td.lookup("Error code: 404", "zh") == "发生了错误"

    def test_multiple_same_placeholder(self):
        td = TranslationDict()
        td.set("{name} is {name}", "zh", "{name}就是{name}")
        assert td.lookup("Alice is Alice", "zh") == "Alice就是Alice"

    def test_repeated_placeholder_inequality_fails(self):
        """Same placeholder name but different values should not match."""
        td = TranslationDict()
        td.set("{name} is {name}", "zh", "{name}就是{name}")
        assert td.lookup("Alice is Bob", "zh") is None

    def test_special_regex_chars_in_key(self):
        td = TranslationDict()
        td.set("What is 1+1? (hint: {answer})", "zh", "1+1等于？（提示：{answer}）")
        assert td.lookup("What is 1+1? (hint: 2)", "zh") == "1+1等于？（提示：2）"

    def test_bracket_term_passthrough(self):
        td = TranslationDict()
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        tr = td.tr("zh")
        result = tr("I love {fruit}")
        assert result == "我爱{fruit}"
        assert result.format(fruit="苹果") == "我爱苹果"

    def test_bracket_term_after_format(self):
        td = TranslationDict()
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        tr = td.tr("zh")
        text = "I love {fruit}".format(fruit="apples")
        result = tr(text)
        assert result == "我爱apples"

    def test_args_normalisation(self):
        td = TranslationDict()
        td.set("I love {fruit}", "zh", "我爱{fruit}")
        td.set("I love {thing}", "ja", "私は{thing}が大好きです")
        assert td.lookup("I love apples", "zh") == "我爱apples"
        assert td.lookup("I love apples", "ja") == "私はapplesが大好きです"

    def test_search_keys_prefix(self):
        td = TranslationDict()
        td.set("Task Descriptions", "zh", "任务描述")
        td.set("Task Input", "zh", "任务输入")
        td.set("Examples", "zh", "示例")
        assert set(td.search_keys("Task")) == {"Task Descriptions", "Task Input"}
        assert td.search_keys("Exa") == ["Examples"]
        assert td.search_keys("Nothing") == []


# ================================================================== #
#  DB-backed tests
# ================================================================== #


class TestDBExactLookup:

    def test_exact_match(self, td_db):
        assert td_db.lookup("Task Descriptions", "zh") == "任务描述"
        assert td_db.lookup("Examples", "zh") == "示例"

    def test_main_lang_returns_self(self, td_db):
        assert td_db.lookup("anything at all", "en") == "anything at all"

    def test_missing_key_returns_none(self, td_db):
        assert td_db.lookup("nonexistent key", "zh") is None

    def test_multi_lang(self, td_db):
        assert td_db.lookup("Task Descriptions", "zh") == "任务描述"
        assert td_db.lookup("Task Descriptions", "ja") == "タスクの説明"


class TestDBPatternLookup:

    def test_single_placeholder(self, td_patterns_db):
        assert td_patterns_db.lookup("I love apples", "zh") == "我爱apples"

    def test_multi_placeholder(self, td_patterns_db):
        result = td_patterns_db.lookup("Hello, Alice! Welcome to Wonderland.", "zh")
        assert result == "HelloZH:Alice:Wonderland"


class TestDBPersistence:

    def test_reload_from_store(self, tmp_db):
        td1 = TranslationDict(namespace="persist_test", main_lang="en", store=tmp_db)
        td1.set("hello", "zh", "你好")
        td1.set("I love {fruit}", "zh", "我爱{fruit}")

        td2 = TranslationDict(namespace="persist_test", main_lang="en", store=tmp_db)
        assert td2.lookup("hello", "zh") == "你好"
        assert td2.lookup("I love apples", "zh") == "我爱apples"

    def test_set_writes_through(self, tmp_db):
        td = TranslationDict(namespace="wt_test", main_lang="en", store=tmp_db)
        td.set("foo", "ja", "フー")
        val = tmp_db.get_value("wt_test", "ja", "foo")
        assert val == "フー"

    def test_set_many_batch_persist(self, tmp_db):
        td = TranslationDict(namespace="batch_test", main_lang="en", store=tmp_db)
        td.set_many(
            {
                "zh": {"a": "A1", "b": "B1"},
                "ja": {"a": "A2"},
            }
        )
        assert tmp_db.entry_count("batch_test") == 3
        assert tmp_db.entry_count("batch_test", lang="zh") == 2
        assert tmp_db.entry_count("batch_test", lang="ja") == 1

    def test_delete_removes_from_db(self, tmp_db):
        td = TranslationDict(namespace="del_test", main_lang="en", store=tmp_db)
        td.set("hello", "zh", "你好")
        assert td.lookup("hello", "zh") == "你好"
        td.delete("hello", "zh")
        assert td.lookup("hello", "zh") is None
        assert tmp_db.get_value("del_test", "zh", "hello") is None

    def test_overwrite_persists(self, tmp_db):
        td = TranslationDict(namespace="ow_test", main_lang="en", store=tmp_db)
        td.set("hello", "zh", "你好旧版")
        td.set("hello", "zh", "你好新版")
        val = tmp_db.get_value("ow_test", "zh", "hello")
        assert val == "你好新版"

    def test_reload(self, tmp_db):
        td = TranslationDict(namespace="reload_test", main_lang="en", store=tmp_db)
        td.set("x", "zh", "旧")
        tmp_db.save_entry("reload_test", "x", "zh", "新")
        assert td.lookup("x", "zh") == "旧"
        td.reload()
        assert td.lookup("x", "zh") == "新"

    def test_namespace_metadata_persists(self, tmp_db):
        TranslationDict(namespace="meta_test", main_lang="fr", store=tmp_db)
        meta = tmp_db.get_namespace("meta_test")
        assert meta is not None
        assert meta["main_lang"] == "fr"

    def test_multiple_namespaces_isolated(self, tmp_db):
        td1 = TranslationDict(namespace="ns_A", main_lang="en", store=tmp_db)
        td2 = TranslationDict(namespace="ns_B", main_lang="en", store=tmp_db)
        td1.set("hello", "zh", "你好A")
        td2.set("hello", "zh", "你好B")
        assert td1.lookup("hello", "zh") == "你好A"
        assert td2.lookup("hello", "zh") == "你好B"


class TestDBStoreAPI:

    def test_list_namespaces(self, tmp_db):
        tmp_db.ensure_namespace("p1", "en")
        tmp_db.ensure_namespace("p2", "fr")
        ns_list = tmp_db.list_namespaces()
        ids = [p["id"] for p in ns_list]
        assert "p1" in ids
        assert "p2" in ids

    def test_list_languages(self, tmp_db):
        td = TranslationDict(namespace="lang_test", main_lang="en", store=tmp_db)
        td.set_many(
            {
                "zh": {"a": "za"},
                "ja": {"a": "ja"},
                "fr": {"a": "la"},
            }
        )
        langs = tmp_db.list_languages("lang_test")
        assert set(langs) == {"zh", "ja", "fr"}

    def test_delete_namespace_cascades(self, tmp_db):
        td = TranslationDict(namespace="del_test", main_lang="en", store=tmp_db)
        td.set("x", "zh", "甲")
        assert tmp_db.entry_count("del_test") == 1
        tmp_db.delete_namespace("del_test")
        assert tmp_db.get_namespace("del_test") is None
        assert tmp_db.entry_count("del_test") == 0

    def test_get_entries_prefix(self, tmp_db):
        td = TranslationDict(namespace="search_test", main_lang="en", store=tmp_db)
        td.set("Task Descriptions", "zh", "任务描述")
        td.set("Task Input", "zh", "任务输入")
        td.set("Examples", "zh", "示例")
        results = tmp_db.get_entries("search_test", lang="zh", prefix="Task")
        keys = {r["key"] for r in results}
        assert keys == {"Task Descriptions", "Task Input"}


class TestDBSerialization:

    def test_from_dict_with_store(self, tmp_db):
        data = {
            "namespace": "ser_test",
            "main_lang": "en",
            "translations": {
                "zh": {"hi": "你好", "bye": "再见"},
            },
        }
        td = TranslationDict.from_dict(data, store=tmp_db, replace=True)
        assert td.lookup("hi", "zh") == "你好"
        assert tmp_db.entry_count("ser_test") == 2

    def test_to_dict_from_db(self, td_db):
        data = td_db.to_dict()
        assert data["namespace"] == "fibonacci"
        assert "zh" in data["translations"]
        assert data["translations"]["zh"]["Task Descriptions"] == "任务描述"


class TestQueryHelpers:

    def test_languages(self, td):
        langs = td.languages()
        assert set(langs) == {"zh", "ja"}

    def test_keys_all(self, td):
        all_keys = td.keys()
        assert "Task Descriptions" in all_keys
        assert "Examples" in all_keys

    def test_keys_for_lang(self, td):
        ja_keys = td.keys(lang="ja")
        assert len(ja_keys) == 2

    def test_missing_keys(self, td):
        missing = td.missing_keys("ja", ref_lang="zh")
        assert "Examples" in missing
        assert "Instructions" in missing
        assert "Task Descriptions" not in missing


class TestDBRuntimeIndexSnapshot:

    @staticmethod
    def _snapshot_ids(snapshot):
        ids = set(int(v) for v in (snapshot.get("residual_keys") or []))
        for bucket in (snapshot.get("gram_index") or {}).values():
            ids.update(int(v) for v in bucket)
        return ids

    def test_snapshot_saved_and_delta_updated(self, tmp_db):
        td = TranslationDict(namespace="idx_delta", main_lang="en", store=tmp_db)
        td.set("I love {fruit}", "zh", "LOVE:{fruit}")
        expected_tid = tmp_db.template_id("idx_delta", "I love {fruit}")

        snap_after_set = tmp_db.get_index_snapshot("idx_delta")
        assert snap_after_set is not None
        assert expected_tid in self._snapshot_ids(snap_after_set)

        td.delete("I love {fruit}", "zh")
        snap_after_delete = tmp_db.get_index_snapshot("idx_delta")
        assert snap_after_delete is not None
        assert expected_tid not in self._snapshot_ids(snap_after_delete)

    def test_valid_snapshot_avoids_rebuild(self, tmp_db, monkeypatch):
        from ahvn.utils.prompt.translate_match import TranslationMatcher

        td = TranslationDict(namespace="idx_cached", main_lang="en", store=tmp_db)
        td.set("I love {fruit}", "zh", "LOVE:{fruit}")

        def _boom(_self):
            raise AssertionError("rebuild_indexes should not be called when snapshot is valid")

        monkeypatch.setattr(TranslationMatcher, "rebuild_indexes", _boom)
        td2 = TranslationDict(namespace="idx_cached", main_lang="en", store=tmp_db)
        assert td2.lookup("I love apples", "zh") == "LOVE:apples"

    def test_stale_snapshot_falls_back_to_rebuild(self, tmp_db, monkeypatch):
        from ahvn.utils.prompt.translate_match import TranslationMatcher

        td = TranslationDict(namespace="idx_fallback", main_lang="en", store=tmp_db)
        td.set("I love {fruit}", "zh", "LOVE:{fruit}")

        tmp_db.save_index_snapshot(
            "idx_fallback",
            {
                "version": 1,
                "gram_index": {},
                "residual_keys": [],
                "pattern_keys_by_lang": {},
            },
        )

        calls = {"count": 0}
        original = TranslationMatcher.rebuild_indexes

        def _counted(self):
            calls["count"] += 1
            return original(self)

        monkeypatch.setattr(TranslationMatcher, "rebuild_indexes", _counted)
        td2 = TranslationDict(namespace="idx_fallback", main_lang="en", store=tmp_db)

        assert calls["count"] == 1
        assert td2.lookup("I love apples", "zh") == "LOVE:apples"

    def test_set_write_is_atomic_when_snapshot_write_fails(self, tmp_db, monkeypatch):
        td = TranslationDict(namespace="idx_atomic", main_lang="en", store=tmp_db)

        def _boom(_namespace, _snapshot):
            raise RuntimeError("forced snapshot failure")

        monkeypatch.setattr(tmp_db, "save_index_snapshot", _boom)
        with pytest.raises(RuntimeError, match="forced snapshot failure"):
            td.set("I love {fruit}", "zh", "LOVE:{fruit}")

        td_reloaded = TranslationDict(namespace="idx_atomic", main_lang="en", store=tmp_db)
        assert td_reloaded.lookup("I love apples", "zh") is None

    def test_store_clear_removes_values_namespaces_and_indexes(self, tmp_db):
        td = TranslationDict(namespace="idx_clear", main_lang="en", store=tmp_db)
        td.set("I love {fruit}", "zh", "LOVE:{fruit}")
        assert tmp_db.entry_count("idx_clear") == 1
        assert tmp_db.get_namespace("idx_clear") is not None
        assert tmp_db.get_index_snapshot("idx_clear") is not None

        removed = tmp_db.clear()
        assert removed >= 1
        assert tmp_db.entry_count("idx_clear") == 0
        assert tmp_db.get_namespace("idx_clear") is None
        assert tmp_db.get_index_snapshot("idx_clear") is None
