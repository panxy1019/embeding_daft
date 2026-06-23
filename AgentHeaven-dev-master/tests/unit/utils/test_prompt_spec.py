"""
Unit tests for the PromptSpec, @prompt decorator, and PromptManager.

Tests cover:
  - Registry contracts (versioning helpers)
  - @prompt decorator registration and auto-versioning
  - Checksum-based skip (no version bump when unchanged)
  - PromptStore CRUD and persistence
  - PromptManager.get() retrieval by version / latest
  - td_refs translation resolution
  - Stale detection
  - Remove specific / all versions
"""

import datetime
import tempfile
import pytest
from ahvn.utils.capsule import CapsuleRestorationError
from ahvn.utils.prompt.prompt_store import PromptStore
from ahvn.utils.prompt.prompt_schema import PromptSpecEntity
from ahvn.utils.prompt.prompt_spec import (
    PromptSpec,
    PromptManager,
    TranslationManager,
    _PROMPT_REGISTRY,
    _register,
    _lookup,
    _unregister,
    _compute_checksum,
    _pack_store_metadata,
    _resolve_tr,
)
from ahvn.utils.registry.contracts import (
    next_version,
    resolve_version,
    VERSIONED_REGISTRY_FIELDS,
)

# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def tmp_store(tmp_path):
    db_path = str(tmp_path / "test_prompts.db")
    return PromptStore(provider="sqlite", database=db_path)


@pytest.fixture(autouse=True)
def _clear_registry():
    """Ensure in-memory registry is clean for every test."""
    _PROMPT_REGISTRY.clear()
    yield
    _PROMPT_REGISTRY.clear()


def _save_prompt(
    store: PromptStore,
    prompt_id: str,
    version: int,
    checksum: str,
    *,
    qualname: str = "",
    source_file: str = "",
    source_code: str = "",
    td_refs=None,
    metadata=None,
) -> int:
    user_metadata = dict(metadata or {})
    func_name = (qualname or prompt_id or "prompt").split(".")[-1]
    fallback_source = source_code or f"def {func_name}(tr=None, **kwargs):\n    return ''"
    capsule_data = {
        "capsule_version": "1.0",
        "capsule_id": f"prompt:{prompt_id}:{version}:{qualname or func_name}",
        "checksum": str(checksum),
        "manifest": {
            "name": prompt_id,
            "entrypoint": prompt_id,
            "prompt_id": prompt_id,
            "prompt_version": int(version),
            "prompt_checksum": str(checksum),
            "qualname": qualname or func_name,
        },
        "layers": [
            {
                "type": "source",
                "code": fallback_source,
                "func_name": func_name,
            }
        ],
        "prompt_spec": {
            "id": prompt_id,
            "version": int(version),
            "checksum": str(checksum),
            "td_refs": list(td_refs or []),
            "qualname": qualname or func_name,
            "source_file": source_file,
            "source_code": source_code or fallback_source,
            "metadata": user_metadata,
        },
    }
    if source_file:
        capsule_data["manifest"]["source_file"] = source_file
    return store.save(
        prompt_id=prompt_id,
        version=version,
        checksum=checksum,
        qualname=qualname,
        source_file=source_file,
        source_code=source_code,
        td_refs=td_refs or [],
        metadata=_pack_store_metadata(user_metadata, capsule_data),
    )


# ------------------------------------------------------------------ #
#  Phase 1: Versioning contract helpers
# ------------------------------------------------------------------ #


class TestVersioningContracts:
    def test_versioned_fields_tuple(self):
        assert "version" in VERSIONED_REGISTRY_FIELDS
        assert "checksum" in VERSIONED_REGISTRY_FIELDS
        assert "id" in VERSIONED_REGISTRY_FIELDS

    def test_next_version_empty(self):
        assert next_version([]) == 1

    def test_next_version_sequential(self):
        assert next_version([1, 2, 3]) == 4

    def test_next_version_non_sequential(self):
        assert next_version([1, 5, 3]) == 6

    def test_resolve_latest_none(self):
        assert resolve_version(None, [1, 2, 3]) == 3

    def test_resolve_latest_string(self):
        assert resolve_version("latest", [1, 2, 3]) == 3

    def test_resolve_specific_int(self):
        assert resolve_version(2, [1, 2, 3]) == 2

    def test_resolve_missing_int(self):
        assert resolve_version(99, [1, 2, 3]) is None

    def test_resolve_empty_available(self):
        assert resolve_version("latest", []) is None


# ------------------------------------------------------------------ #
#  Phase 2: In-memory registry
# ------------------------------------------------------------------ #


class TestInMemoryRegistry:
    def test_register_and_lookup(self):
        spec = PromptSpec(id="test", version=1, checksum="abc", func=lambda: None)
        _register(spec)
        assert _lookup("test") is spec
        assert _lookup("test", 1) is spec

    def test_lookup_latest(self):
        s1 = PromptSpec(id="test", version=1, checksum="a", func=lambda: None)
        s2 = PromptSpec(id="test", version=2, checksum="b", func=lambda: None)
        _register(s1)
        _register(s2)
        assert _lookup("test").version == 2
        assert _lookup("test", 1).version == 1

    def test_lookup_missing(self):
        assert _lookup("nonexistent") is None
        assert _lookup("nonexistent", 1) is None

    def test_unregister_specific(self):
        s1 = PromptSpec(id="test", version=1, checksum="a", func=lambda: None)
        s2 = PromptSpec(id="test", version=2, checksum="b", func=lambda: None)
        _register(s1)
        _register(s2)
        _unregister("test", 1)
        assert _lookup("test", 1) is None
        assert _lookup("test", 2) is not None

    def test_unregister_all(self):
        s1 = PromptSpec(id="test", version=1, checksum="a", func=lambda: None)
        _register(s1)
        _unregister("test")
        assert _lookup("test") is None


# ------------------------------------------------------------------ #
#  Phase 3: PromptStore CRUD
# ------------------------------------------------------------------ #


class TestPromptStore:
    def test_save_and_get(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1", qualname="m.p1", source_code="def p1(): pass")
        row = tmp_store.get("p1", 1)
        assert row is not None
        assert row["prompt_id"] == "p1"
        assert row["version"] == 1
        assert row["checksum"] == "chk1"

    def test_get_latest(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1")
        _save_prompt(tmp_store, "p1", 2, "chk2")
        row = tmp_store.get("p1")
        assert row["version"] == 2

    def test_get_latest_version(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1")
        _save_prompt(tmp_store, "p1", 2, "chk2")
        assert tmp_store.get_latest_version("p1") == 2

    def test_get_latest_version_missing(self, tmp_store):
        assert tmp_store.get_latest_version("nonexistent") is None

    def test_get_checksum_latest(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1")
        _save_prompt(tmp_store, "p1", 2, "chk2")
        assert tmp_store.get_checksum("p1") == "chk2"

    def test_get_checksum_specific(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1")
        _save_prompt(tmp_store, "p1", 2, "chk2")
        assert tmp_store.get_checksum("p1", 1) == "chk1"

    def test_list_versions(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a")
        _save_prompt(tmp_store, "p1", 3, "c")
        _save_prompt(tmp_store, "p1", 2, "b")
        assert tmp_store.list_versions("p1") == [1, 2, 3]

    def test_exists(self, tmp_store):
        assert not tmp_store.exists("p1")
        _save_prompt(tmp_store, "p1", 1, "a")
        assert tmp_store.exists("p1")

    def test_list(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a")
        _save_prompt(tmp_store, "p2", 1, "b")
        _save_prompt(tmp_store, "p2", 2, "c")
        items = tmp_store.list()
        assert len(items) == 2
        ids = {i["id"] for i in items}
        assert ids == {"p1", "p2"}

    def test_info(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a")
        _save_prompt(tmp_store, "p1", 2, "b")
        info = tmp_store.info("p1")
        assert info is not None
        assert info["latest_version"] == 2
        assert info["versions"] == [1, 2]

    def test_info_missing(self, tmp_store):
        assert tmp_store.info("nonexistent") is None

    def test_remove_specific_version(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a")
        _save_prompt(tmp_store, "p1", 2, "b")
        tmp_store.remove("p1", 1)
        assert tmp_store.list_versions("p1") == [2]

    def test_remove_all_versions(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a")
        _save_prompt(tmp_store, "p1", 2, "b")
        tmp_store.remove("p1")
        assert not tmp_store.exists("p1")

    def test_stale_detection(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a", source_file="/nonexistent/path.py")
        stale = tmp_store.stale()
        assert len(stale) == 1
        assert stale[0]["id"] == "p1"

    def test_persistence_across_instances(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        s1 = PromptStore(provider="sqlite", database=db_path)
        _save_prompt(s1, "p1", 1, "chk1", qualname="mod.func", source_code="def func(): pass")

        s2 = PromptStore(provider="sqlite", database=db_path)
        row = s2.get("p1", 1)
        assert row is not None
        assert row["checksum"] == "chk1"

    def test_td_refs_stored(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "a", td_refs=["fibonacci", "system"])
        row = tmp_store.get("p1", 1)
        assert row["td_refs"] == ["fibonacci", "system"]

    def test_upsert_same_version(self, tmp_store):
        _save_prompt(tmp_store, "p1", 1, "chk1", source_code="v1")
        _save_prompt(tmp_store, "p1", 1, "chk2", source_code="v2")
        row = tmp_store.get("p1", 1)
        assert row["checksum"] == "chk2"
        assert row["source_code"] == "v2"


# ------------------------------------------------------------------ #
#  Phase 4: @prompt decorator
# ------------------------------------------------------------------ #


class TestPromptDecorator:
    def test_basic_decoration(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="test-prompt")
        def my_prompt(n, tr=None):
            return f"Calculate F({n})"

        assert isinstance(my_prompt, PromptSpec)
        assert my_prompt.id == "test-prompt"
        assert my_prompt.version == 1

        # Callable auto-injects tr
        assert my_prompt(5) == "Calculate F(5)"

    def test_upsert_latest_on_change(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        # Simulate: save v1 with a different checksum
        _save_prompt(tmp_store, "manual", 1, "old_checksum")

        @prompt(id="manual")
        def my_prompt(tr=None):
            return "hello"

        # New checksum differs but version should stay at 1 (upsert latest)
        assert my_prompt.version == 1
        # But the checksum in store should now be updated
        assert tmp_store.get_checksum("manual") == my_prompt.checksum
        assert my_prompt.checksum != "old_checksum"

    def test_no_bump_when_unchanged(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="stable")
        def my_prompt(tr=None):
            return "hello"

        v1 = my_prompt.version
        checksum = my_prompt.checksum

        # Re-register same function source under same checksum
        _PROMPT_REGISTRY.clear()
        _save_prompt(tmp_store, "stable", v1, checksum)

        # Re-decorate an identical function body (same source same checksum).
        # We reuse the same fn object to guarantee identical source.
        wrapped2 = prompt(id="stable")(my_prompt.__wrapped__)
        assert wrapped2.version == v1

    def test_default_id_from_qualname(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt
        def fibonacci_prompt(tr=None):
            return "fib"

        assert isinstance(fibonacci_prompt, PromptSpec)
        assert fibonacci_prompt.id == "fibonacci_prompt"

    def test_td_refs_stored_via_decorator(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="with-td", tds=["fibonacci", "system"])
        def my_prompt(tr=None):
            return (tr or str)("hello")

        # prompt id is auto-prepended to td_refs
        assert my_prompt.td_refs == ["with-td", "fibonacci", "system"]

        # Also check DB
        row = tmp_store.get("with-td", 1)
        assert row["td_refs"] == ["with-td", "fibonacci", "system"]

    def test_tds_single_string(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="single-td", tds="shared_vocab")
        def my_prompt(tr=None):
            return (tr or str)("hello")

        assert "single-td" in my_prompt.td_refs
        assert "shared_vocab" in my_prompt.td_refs

    def test_tr_shortcut(self, tmp_store, monkeypatch, tmp_path):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="tr-test")
        def my_prompt(name, tr=None):
            return f"{(tr or str)('Hello')}, {name}!"

        # Set translations via .tr shortcut
        my_prompt.tr.set("Hello", "zh", "nihao")
        assert my_prompt.tr.get("Hello", "zh") == "nihao"

        # Check bound tds
        assert "tr-test" in my_prompt.tr.tds

        # Validate td binding
        my_prompt.tr.bind("extra")
        assert "extra" in my_prompt.tr.tds
        my_prompt.tr.unbind("extra")
        assert "extra" not in my_prompt.tr.tds

    def test_set_many_lang_first(self, tmp_store, tmp_path, monkeypatch):
        """set_many uses {lang: {key: val}} same format as TranslationDict."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="sm-test")
        def my_prompt(tr=None):
            return f"{(tr or str)('Hello')} {(tr or str)('World')}"

        my_prompt.tr.set_many(
            {
                "zh": {"Hello": "nihao", "World": "shijie"},
                "ja": {"Hello": "konnichiwa"},
            }
        )

        assert my_prompt.tr.get("Hello", "zh") == "nihao"
        assert my_prompt.tr.get("Hello", "ja") == "konnichiwa"
        assert my_prompt.tr.get("World", "zh") == "shijie"
        assert my_prompt(lang="zh") == "nihao shijie"

    def test_bind_persists_to_store(self, tmp_store, tmp_path, monkeypatch):
        """bind() should persist td_refs to the PromptStore."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="bind-persist")
        def my_prompt(tr=None):
            return (tr or str)("hello")

        my_prompt.tr.bind("shared_ns")

        # Verify DB was updated
        row = tmp_store.get("bind-persist", my_prompt.version)
        assert "shared_ns" in row["td_refs"]

    def test_unbind_persists_to_store(self, tmp_store, tmp_path, monkeypatch):
        """unbind() should persist td_refs removal to the PromptStore."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="unbind-persist", tds=["extra_ns"])
        def my_prompt(tr=None):
            return (tr or str)("hello")

        assert "extra_ns" in my_prompt.td_refs
        my_prompt.tr.unbind("extra_ns")

        row = tmp_store.get("unbind-persist", my_prompt.version)
        assert "extra_ns" not in row["td_refs"]


# ------------------------------------------------------------------ #
#  Phase 5: PromptManager
# ------------------------------------------------------------------ #


class TestPromptManager:
    def test_get_from_memory(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="mem-test")
        def my_prompt(n, tr=None):
            return f"F({n})"

        pm = PromptManager()
        fn = pm.get("mem-test")
        assert fn is not None
        assert fn(n=10) == "F(10)"

    def test_get_specific_version(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        fn_v1 = lambda n, tr=None: f"v1:{n}"  # noqa: E731
        fn_v2 = lambda n, tr=None: f"v2:{n}"  # noqa: E731

        spec1 = PromptSpec(id="ver-test", version=1, checksum="a", func=fn_v1)
        spec2 = PromptSpec(id="ver-test", version=2, checksum="b", func=fn_v2)
        _register(spec1)
        _register(spec2)

        pm = PromptManager()
        # Latest
        fn = pm.get("ver-test")
        assert fn(n=5) == "v2:5"
        # Specific
        fn = pm.get("ver-test", version=1)
        assert fn(n=5) == "v1:5"

    def test_get_from_db(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        def db_test_func(n, tr=None):
            return f"db:{n}"

        PromptSpec.from_func(db_test_func, id="db-test")
        _PROMPT_REGISTRY.clear()

        pm = PromptManager()
        fn = pm.get("db-test")
        assert fn is not None
        # PM_AHVN.get without lang returns the PromptSpec directly
        assert isinstance(fn, PromptSpec)
        assert fn.id == "db-test"
        assert fn.version == 1
        assert "def db_test_func" in fn.source_code

    def test_get_nonexistent(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        pm = PromptManager()
        assert pm.get("nonexistent") is None

    def test_list(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="list-a")
        def prompt_a(tr=None):
            return "a"

        @prompt(id="list-b")
        def prompt_b(tr=None):
            return "b"

        pm = PromptManager()
        items = pm.list()
        ids = {i["id"] for i in items}
        assert "list-a" in ids
        assert "list-b" in ids

    def test_info(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="info-test")
        def my_prompt(tr=None):
            return "x"

        pm = PromptManager()
        info = pm.info("info-test")
        assert info is not None
        assert info["latest_version"] == 1

    def test_remove(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="rm-test")
        def my_prompt(tr=None):
            return "x"

        pm = PromptManager()
        pm.remove("rm-test")
        assert pm.get("rm-test") is None
        assert not tmp_store.exists("rm-test")


# ------------------------------------------------------------------ #
#  Translation resolution
# ------------------------------------------------------------------ #


class TestTranslationResolution:
    def test_resolve_tr_empty_refs(self):
        tr = _resolve_tr([])
        assert tr("hello") == "hello"

    def test_resolve_tr_with_store(self, tmp_path, monkeypatch):
        from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

        db_path = str(tmp_path / "tr_test.db")
        store = TranslationStore(provider="sqlite", database=db_path)

        td = TranslationDict(namespace="test-ns", main_lang="en", store=store)
        td.set("Hello", "zh", "nihao")

        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: store)

        tr = _resolve_tr(["test-ns"], lang="zh")
        assert tr("Hello") == "nihao"
        # Untranslated falls through to original
        assert tr("Unknown") == "Unknown"

    def test_resolve_tr_fallback_chain(self, tmp_path, monkeypatch):
        from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

        db_path = str(tmp_path / "tr_chain.db")
        store = TranslationStore(provider="sqlite", database=db_path)

        td1 = TranslationDict(namespace="primary", main_lang="en", store=store)
        td1.set("A", "zh", "娑撯偓")

        td2 = TranslationDict(namespace="secondary", main_lang="en", store=store)
        td2.set("B", "zh", "B-zh")

        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: store)

        tr = _resolve_tr(["primary", "secondary"], lang="zh")
        assert tr("A") == "娑撯偓"
        assert tr("B") == "B-zh"


# ------------------------------------------------------------------ #
#  Checksum helper
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
#  lang / elicit override in __call__
# ------------------------------------------------------------------ #


class TestLangOverride:
    @pytest.fixture(autouse=True)
    def _sandbox_prompt_lang(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        with CM_AHVN.scoped("test_prompt_lang_override"):
            CM_AHVN.set("prompts.lang", "en")
            yield

    def test_lang_kwarg_in_call(self, tmp_store, tmp_path, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="lang-ov")
        def my_prompt(name, tr=None):
            return f"{(tr or str)('Hello')}, {name}!"

        my_prompt.tr.set("Hello", "zh", "nihao")

        # Default identity
        assert my_prompt("Alice") == "Hello, Alice!"
        # Explicit lang
        assert my_prompt("Alice", lang="zh") == "nihao, Alice!"

    def test_lang_does_not_leak_to_nested(self, tmp_store, tmp_path, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="inner-nest")
        def inner_prompt(tr=None):
            return (tr or str)("World")

        @prompt(id="outer-nest")
        def outer_prompt(tr=None):
            return f"{(tr or str)('Hello')} {inner_prompt()}"

        inner_prompt.tr.set("World", "zh", "shijie")
        outer_prompt.tr.set("Hello", "zh", "nihao")

        # outer gets lang="zh" but inner resolves independently (no lang)
        result = outer_prompt(lang="zh")
        assert result == "nihao World"

    def test_elicit_passed_through(self, tmp_store, tmp_path, monkeypatch):
        """elicit kwarg is popped and forwarded to _resolve_tr."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="elicit-test")
        def my_prompt(tr=None):
            return (tr or str)("Missing")

        # "none" elicit falls back to identity
        assert my_prompt(elicit="none") == "Missing"


# ------------------------------------------------------------------ #
#  PromptSpec.from_str
# ------------------------------------------------------------------ #


class TestFromStr:
    def test_basic_template(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("Hello, {name}!", id="fs-basic")
        assert spec(name="Alice") == "Hello, Alice!"

    def test_template_with_translation(self, tmp_store, tmp_path, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        spec = PromptSpec.from_str(
            "Hello, {name}! Welcome to {place}",
            trs=["place"],
            id="fs-tr",
        )
        # The template itself is the translation key.
        spec.tr.set("Hello, {name}! Welcome to {place}", "zh", "Nihao, {name}! Welcome to {place}")
        # A trs placeholder value can also be translated.
        spec.tr.set("Paris", "zh", "Bali")

        assert spec(name="Alice", place="Paris", lang="zh") == "Nihao, Alice! Welcome to Bali"

    def test_auto_id(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("Greetings, {name}!")
        assert spec.id.startswith("template_")

    def test_custom_id(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("Hi, {name}!", id="my-tpl")
        assert spec.id == "my-tpl"

    def test_template_fields_introspection(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("{greeting}, {name}! {farewell}", id="fs-fields")
        assert set(spec.func._fields) == {"greeting", "name", "farewell"}
        assert spec.func._trs_keys == []


# ------------------------------------------------------------------ #
#  Template persistence round-trip (Issue 1)
# ------------------------------------------------------------------ #


class TestTemplatePersistence:
    def test_from_store_reconstructs_template(self, tmp_store, tmp_path, monkeypatch):
        """from_store should reconstruct a template closure from metadata."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        original = PromptSpec.from_str(
            "Hello, {name}! Welcome to {place}",
            trs=["place"],
            id="persist-tpl",
        )
        assert original(name="Alice", place="Paris") == "Hello, Alice! Welcome to Paris"

        # Clear in-memory registry to force from_store path
        _PROMPT_REGISTRY.clear()

        loaded = PromptSpec.from_store("persist-tpl")
        assert loaded is not None
        assert loaded(name="Bob", place="Tokyo") == "Hello, Bob! Welcome to Tokyo"
        assert loaded.func._template == "Hello, {name}! Welcome to {place}"
        assert set(loaded.func._fields) == {"name", "place"}
        assert loaded.func._trs_keys == ["place"]

    def test_from_dict_reconstructs_template(self, tmp_store, monkeypatch):
        """from_dict should reconstruct a template closure from metadata."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        original = PromptSpec.from_str("Hi {name}!", id="dict-tpl")
        data = original.to_dict()

        restored = PromptSpec.from_dict(data)
        assert restored(name="Carol") == "Hi Carol!"
        assert restored.func._template == "Hi {name}!"

    def test_from_dict_preserves_explicit_zero_version(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        original = PromptSpec.from_str("System {name}", id="system-zero", version=0)
        data = original.to_dict()

        restored = PromptSpec.from_dict(data)
        assert restored.version == 0

    def test_from_dict_defaults_version_to_one_when_missing(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        original = PromptSpec.from_str("No version fallback {name}", id="no-version-fallback")
        data = original.to_dict()
        data["prompt_spec"].pop("version", None)
        if isinstance(data.get("manifest"), dict):
            data["manifest"].pop("prompt_version", None)

        restored = PromptSpec.from_dict(data)
        assert restored.version == 1

    def test_from_dict_requires_capsule_payload(self, tmp_store, monkeypatch):
        """Legacy non-capsule payloads should fail restoration."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        source_code = """
@missing_decorator
def greet(name, *, tr=None):
    return f"{(tr or str)('Hello')}, {name}!"
"""
        with pytest.raises(CapsuleRestorationError, match="Capsule has no layers"):
            PromptSpec.from_dict(
                {
                    "prompt_id": "source-fallback",
                    "version": 1,
                    "checksum": "noop",
                    "td_refs": [],
                    "qualname": "greet",
                    "source_file": "/nonexistent/source.py",
                    "source_code": source_code,
                    "metadata": {},
                }
            )

    def test_from_store_requires_capsule_payload(self, tmp_store, tmp_path, monkeypatch):
        """Rows without capsule metadata are ignored by from_store."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        source_file = tmp_path / "prompt_with_helper.py"
        source_file.write_text(
            """
HELPER_PREFIX = "module"


def helper(name):
    return f"{HELPER_PREFIX}:{name}"


def greet(name, tr=None):
    return helper(name)
""".strip() + "\n",
            encoding="utf-8",
        )

        # This snippet compiles but misses helper/global definitions.
        source_code = """
def greet(name, tr=None):
    return helper(name)
"""

        now = datetime.datetime.now(datetime.timezone.utc)
        entity = PromptSpecEntity(
            id=1,
            prompt_id="source-priority",
            version=1,
            checksum="chk",
            qualname="greet",
            source_file=str(source_file),
            source_code=source_code,
            td_refs=[],
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        for stmt in entity.upsert_stmts():
            tmp_store._db.orm_execute(stmt, autocommit=True)
        _PROMPT_REGISTRY.clear()

        restored = PromptSpec.from_store("source-priority")
        assert restored is None

    def test_from_store_requires_version_payload(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        original = PromptSpec.from_str("Store strict {name}", id="store-version-required")
        capsule_data = original.to_dict()
        capsule_data["prompt_spec"].pop("version", None)
        if isinstance(capsule_data.get("manifest"), dict):
            capsule_data["manifest"].pop("prompt_version", None)

        tmp_store.save(
            prompt_id=original.id,
            version=original.version,
            checksum=original.checksum,
            qualname=original.qualname,
            source_file=original.source_file,
            source_code=original.source_code,
            td_refs=original.td_refs,
            metadata=_pack_store_metadata(original.metadata, capsule_data),
        )

        _PROMPT_REGISTRY.clear()
        assert PromptSpec.from_store(original.id, version=original.version) is None

    def test_prompt_store_save_requires_capsule_metadata(self, tmp_store):
        with pytest.raises(ValueError, match="__prompt_capsule__"):
            tmp_store.save(
                "legacy-template",
                1,
                "chk",
                qualname="legacy_template",
                source_file="",
                source_code="",
                td_refs=[],
                metadata={},
            )

    def test_template_metadata_stored(self, tmp_store, monkeypatch):
        """from_str should persist type/template/fields/trs_keys in metadata."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("{a} and {b}", trs=["b"], id="meta-tpl")
        assert spec.metadata["type"] == "template"
        assert spec.metadata["template"] == "{a} and {b}"
        assert spec.metadata["fields"] == ["a", "b"]
        assert spec.metadata["trs_keys"] == ["b"]


# ------------------------------------------------------------------ #
#  Prompt function export unification
# ------------------------------------------------------------------ #


class TestPromptFunctionExport:
    def test_to_func_from_func(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        def greet(name, tr=None):
            return f"{(tr or str)('Hello')} {name}"

        spec = PromptSpec.from_func(greet, id="export-func")
        fn = spec.to_func()
        assert callable(fn)
        assert fn(name="Alice") == "Hello Alice"
        assert getattr(fn, "__prompt_spec__", None) is spec

    def test_to_func_from_str(self, tmp_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        spec = PromptSpec.from_str("Hi {name}!", id="export-template")
        fn = spec.to_func()
        assert fn(name="Bob") == "Hi Bob!"

    def test_to_func_from_jinja(self, tmp_store, tmp_path, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr_export.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        spec = PromptSpec.from_jinja("{{ 'Hello' | tr }}, {{ name }}!", id="export-jinja")
        spec.tr.set("Hello", "zh", "nihao")
        fn = spec.to_func()
        assert fn(name="Carol", lang="zh") == "nihao, Carol!"
        assert spec.prompt_type == "jinja"


# ------------------------------------------------------------------ #
#  Default translation namespace materialization (Issue 2)
# ------------------------------------------------------------------ #


class TestNamespaceMaterialization:
    def test_from_func_materializes_namespace(self, tmp_store, tmp_path, monkeypatch):
        """from_func should ensure the prompt_id namespace exists in TranslationStore."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="mat-test")
        def my_prompt(tr=None):
            return (tr or str)("Hello")

        # The namespace should exist immediately after registration
        assert tr_store.exists("mat-test")

    def test_resolve_tr_finds_materialized_namespace(self, tmp_store, tmp_path, monkeypatch):
        """_resolve_tr should find the auto-materialized namespace."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore, TranslationDict

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="resolve-ns")
        def my_prompt(tr=None):
            return (tr or str)("Hello")

        # Set a translation (should work because namespace exists)
        my_prompt.tr.set("Hello", "zh", "nihao")
        assert my_prompt(lang="zh") == "nihao"


# ------------------------------------------------------------------ #
#  LLM elicitation (Issue 3)
# ------------------------------------------------------------------ #


class TestLLMElicitation:
    def test_elicit_llm_calls_llm_and_stores(self, tmp_path, monkeypatch):
        """_elicit_llm should call the LLM and persist the translation."""
        from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        td = TranslationDict(namespace="llm-test", main_lang="en", store=tr_store)

        # Mock _llm_translate to avoid actual LLM call
        monkeypatch.setattr(
            "ahvn.utils.prompt.translate._llm_translate",
            lambda text, src, tgt: "娴ｇ姴銈芥稉鏍櫕",
        )

        result = td._elicit_llm("Hello World", "zh")
        assert result == "娴ｇ姴銈芥稉鏍櫕"
        # Translation should be persisted
        assert td.lookup("Hello World", "zh") == "娴ｇ姴銈芥稉鏍櫕"

    def test_elicit_llm_fallback_on_failure(self, tmp_path, monkeypatch):
        """_elicit_llm should return original text when LLM returns None."""
        from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        td = TranslationDict(namespace="llm-fail", main_lang="en", store=tr_store)

        monkeypatch.setattr(
            "ahvn.utils.prompt.translate._llm_translate",
            lambda text, src, tgt: None,
        )

        result = td._elicit_llm("Hello", "zh")
        assert result == "Hello"

    def test_elicit_llm_via_tr_callable(self, tmp_store, tmp_path, monkeypatch):
        """elicit='llm' through the full PromptSpec call path."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        monkeypatch.setattr(
            "ahvn.utils.prompt.translate._llm_translate",
            lambda text, src, tgt: f"[translated:{text}]",
        )

        from ahvn.utils.prompt.prompt_spec import prompt

        @prompt(id="llm-elicit-full")
        def my_prompt(tr=None):
            return (tr or str)("Goodbye")

        result = my_prompt(lang="fr", elicit="llm")
        assert result == "[translated:Goodbye]"
        # Second call should return cached translation
        result2 = my_prompt(lang="fr")
        assert result2 == "[translated:Goodbye]"

    def test_translation_prompt_system_prompt_registration(self, tmp_store, tmp_path, monkeypatch):
        """translation_prompt should be retrievable from PM_AHVN and usable."""
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: tmp_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: tmp_store)

        from ahvn.utils.prompt.translate import TranslationStore

        tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "tr.db"))
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)

        from ahvn.utils.prompt.prompt_spec import setup_system_prompts
        import ahvn.utils.prompt.translate as tr_mod

        setup_system_prompts(force=True)
        spec = tr_mod._get_translation_prompt()
        assert spec is not None
        assert spec.id == "translation_prompt"
        assert spec.metadata.get("system") is True
        assert spec.metadata.get("type") != "template"

        result = spec(content="Hello", source_lang="en", target_lang="zh")
        assert "Hello" in result
        assert "en" in result
        assert "zh" in result


# ------------------------------------------------------------------ #
#  TranslationManager render API
# ------------------------------------------------------------------ #


class TestTranslationManagerRender:
    def test_render_prompt_as_text(self, monkeypatch):
        tr_mgr = TranslationManager()

        class _StubPrompt:
            def __call__(self, **kwargs):
                assert kwargs == {"name": "Alice"}
                return [
                    {"role": "system", "content": "绯荤粺"},
                    {"role": "user", "content": "浣犲ソ锛孉lice"},
                ]

        class _StubPM:
            def get(self, namespace, version=None, lang=None):
                assert namespace == "default_prompt"
                assert version == "0"
                assert lang == "zh"
                return _StubPrompt()

        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.PM_AHVN", _StubPM())

        rendered = tr_mgr.render(
            "default_prompt",
            args={"name": "Alice"},
            lang="zh",
            version="0",
        )
        assert rendered == "绯荤粺\n\n浣犲ソ锛孉lice"

    def test_render_prompt_returns_none_when_prompt_missing(self, monkeypatch):
        tr_mgr = TranslationManager()

        class _StubPM:
            def get(self, namespace, version=None, lang=None):
                return None

        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.PM_AHVN", _StubPM())
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.ensure_system_prompts", lambda: {})

        assert tr_mgr.render("missing_prompt") is None

    def test_render_prompt_alias(self, monkeypatch):
        tr_mgr = TranslationManager()

        class _StubPM:
            def get(self, namespace, version=None, lang=None):
                return lambda **kwargs: "alias-ok"

        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.PM_AHVN", _StubPM())
        assert tr_mgr.render_prompt("default_prompt") == "alias-ok"


# ------------------------------------------------------------------ #
#  Checksum helper
# ------------------------------------------------------------------ #


class TestChecksumHelper:
    def test_same_source_same_checksum(self):
        code = "def f(): return 1"
        assert _compute_checksum(code) == _compute_checksum(code)

    def test_different_source_different_checksum(self):
        assert _compute_checksum("def f(): return 1") != _compute_checksum("def f(): return 2")
