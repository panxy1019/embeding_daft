"""
Systematic version control tests for PromptStore, CapsuleStore, and ToolkitStore.

Tests cover the version lifecycle contract for each store:
  - PromptStore: multi-version CRUD, auto-versioning via decorator, checksum skip
  - CapsuleStore: single-version upsert, checksum-based change detection
  - ToolkitStore: single-version upsert, checksum computation
  - Cross-store: verify contract consistency

Version control semantics:
  - PromptStore:  explicit multi-version  (prompt_id, version) 鈫?multiple rows
  - CapsuleStore: single-version upsert   capsule_id 鈫?one row (latest wins)
  - ToolkitStore: single-version upsert   toolkit_name 鈫?one row (latest wins)
"""

import pytest

from ahvn.utils.registry.contracts import (
    next_version,
    resolve_version,
    normalize_registry_record,
    REGISTRY_STANDARD_FIELDS,
    VERSIONED_REGISTRY_FIELDS,
)
from ahvn.utils.prompt.prompt_store import PromptStore
from ahvn.utils.prompt.prompt_spec import (
    PromptSpec,
    PromptManager,
    _PROMPT_REGISTRY,
    _register,
    _lookup,
    _compute_checksum,
    _pack_store_metadata,
)
from ahvn.utils.capsule import Capsule, CapsuleStore
from ahvn.tool import ToolSpec
from ahvn.tool.toolkit import Toolkit
from ahvn.tool.store import ToolkitStore


@pytest.fixture
def prompt_store(tmp_path):
    return PromptStore(provider="sqlite", database=str(tmp_path / "prompts.db"))


@pytest.fixture
def capsule_store(tmp_path):
    return CapsuleStore(provider="sqlite", database=str(tmp_path / "capsules.db"))


@pytest.fixture
def toolkit_store(tmp_path):
    return ToolkitStore(provider="sqlite", database=str(tmp_path / "toolkits.db"))


@pytest.fixture(autouse=True)
def _clear_prompt_registry():
    _PROMPT_REGISTRY.clear()
    yield
    _PROMPT_REGISTRY.clear()


def _fibonacci(n: int) -> int:
    """Fibonacci number.

    Args:
        n (int): Index.

    Returns:
        int: Result.
    """
    return n if n <= 1 else _fibonacci(n - 1) + _fibonacci(n - 2)


def _adder(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a (int): First.
        b (int): Second.

    Returns:
        int: Sum.
    """
    return a + b


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


# ================================================================== #
#  1. PromptStore multi-version lifecycle
# ================================================================== #


class TestPromptVersionLifecycle:
    """PromptStore supports explicit multi-version storage."""

    def test_first_save_is_v1(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "chk_a", source_code="def p(): pass")
        assert prompt_store.get_latest_version("p") == 1

    def test_multiple_versions_coexist(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "chk_a", source_code="v1")
        _save_prompt(prompt_store, "p", 2, "chk_b", source_code="v2")
        _save_prompt(prompt_store, "p", 3, "chk_c", source_code="v3")
        assert prompt_store.list_versions("p") == [1, 2, 3]
        assert prompt_store.get("p", 1)["checksum"] == "chk_a"
        assert prompt_store.get("p", 3)["checksum"] == "chk_c"

    def test_get_latest_returns_highest(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "chk_a")
        _save_prompt(prompt_store, "p", 5, "chk_e")
        _save_prompt(prompt_store, "p", 3, "chk_c")
        row = prompt_store.get("p")
        assert row["version"] == 5

    def test_remove_specific_version(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "a")
        _save_prompt(prompt_store, "p", 2, "b")
        _save_prompt(prompt_store, "p", 3, "c")
        prompt_store.remove("p", 2)
        assert prompt_store.list_versions("p") == [1, 3]
        assert prompt_store.get("p", 2) is None

    def test_remove_all_versions(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "a")
        _save_prompt(prompt_store, "p", 2, "b")
        prompt_store.remove("p")
        assert not prompt_store.exists("p")
        assert prompt_store.list_versions("p") == []

    def test_upsert_same_version_overwrites(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "chk_old", source_code="old")
        _save_prompt(prompt_store, "p", 1, "chk_new", source_code="new")
        row = prompt_store.get("p", 1)
        assert row["checksum"] == "chk_new"
        assert row["source_code"] == "new"
        assert prompt_store.list_versions("p") == [1]

    def test_checksum_unchanged_reuses_version(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "same_checksum")
        assert prompt_store.get_checksum("p") == "same_checksum"
        assert prompt_store.get_latest_version("p") == 1

    def test_checksum_changed_bumps_version(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "chk_old")
        versions = prompt_store.list_versions("p")
        new_ver = next_version(versions)
        _save_prompt(prompt_store, "p", new_ver, "chk_new")
        assert prompt_store.get_latest_version("p") == 2

    def test_clear_removes_all(self, prompt_store):
        _save_prompt(prompt_store, "a", 1, "x")
        _save_prompt(prompt_store, "a", 2, "y")
        _save_prompt(prompt_store, "b", 1, "z")
        count = prompt_store.clear()
        assert count == 3
        assert prompt_store.list() == []

    def test_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "persist.db")
        s1 = PromptStore(provider="sqlite", database=db)
        _save_prompt(s1, "p", 1, "chk", source_code="def p(): pass")
        _save_prompt(s1, "p", 2, "chk2", source_code="def p(): return 1")

        s2 = PromptStore(provider="sqlite", database=db)
        assert s2.list_versions("p") == [1, 2]
        assert s2.get("p", 2)["checksum"] == "chk2"


# ================================================================== #
#  2. PromptSpec decorator version control
# ================================================================== #


class TestPromptDecoratorVersionControl:
    """@PromptSpec.prompt auto-versioning with store."""

    def test_first_decoration_is_v1(self, prompt_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: prompt_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)

        @PromptSpec.prompt(id="deco-v1")
        def my_prompt(tr=None):
            return "hello"

        assert my_prompt.version == 1

    def test_changed_source_upserts_latest(self, prompt_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: prompt_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)

        _save_prompt(prompt_store, "bump-test", 1, "old_checksum")

        @PromptSpec.prompt(id="bump-test")
        def my_prompt(tr=None):
            return "new body"

        # Upsert semantics: stays at v1 but checksum is updated
        assert my_prompt.version == 1
        assert prompt_store.get_checksum("bump-test") == my_prompt.checksum

    def test_explicit_version_creates_new(self, prompt_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: prompt_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)

        @PromptSpec.prompt(id="ver-explicit")
        def my_prompt_v1(tr=None):
            return "v1"

        assert my_prompt_v1.version == 1

        # Explicit version=2 creates a new version
        @PromptSpec.prompt(id="ver-explicit", version=2)
        def my_prompt_v2(tr=None):
            return "v2"

        assert my_prompt_v2.version == 2
        assert prompt_store.list_versions("ver-explicit") == [1, 2]

    def test_unchanged_source_reuses_version(self, prompt_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: prompt_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)

        @PromptSpec.prompt(id="stable")
        def my_prompt(tr=None):
            return "fixed"

        v1 = my_prompt.version
        chk = my_prompt.checksum

        _PROMPT_REGISTRY.clear()
        _save_prompt(prompt_store, "stable", v1, chk)

        wrapped = PromptSpec.prompt(id="stable")(my_prompt.__wrapped__)
        assert wrapped.version == v1

    def test_from_func_also_versions(self, prompt_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.prompt.prompt_store.get_prompt_store", lambda: prompt_store)
        monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)

        def my_fn(tr=None):
            return "api"

        spec = PromptSpec.from_func(my_fn, id="api-test")
        assert spec.version == 1
        assert prompt_store.exists("api-test")


# ================================================================== #
#  3. CapsuleStore checksum-based version control
# ================================================================== #


class TestCapsuleVersionControl:
    """CapsuleStore: single-version upsert with checksum tracking."""

    def test_save_records_checksum(self, capsule_store):
        cap = Capsule.from_func(_fibonacci).to_dict()
        capsule_store.add(cap)
        cid = cap["capsule_id"]
        chk = capsule_store.get_checksum(cid)
        assert chk is not None and len(chk) > 0

    def test_same_capsule_same_checksum(self, capsule_store):
        cap1 = Capsule.from_func(_fibonacci).to_dict()
        cap2 = Capsule.from_func(_fibonacci).to_dict()
        assert cap1.get("checksum") == cap2.get("checksum")

    def test_different_function_different_checksum(self, capsule_store):
        cap1 = Capsule.from_func(_fibonacci).to_dict()
        cap2 = Capsule.from_func(_adder).to_dict()
        assert cap1.get("checksum") != cap2.get("checksum")

    def test_upsert_overwrites_single_record(self, capsule_store):
        cap = Capsule.from_func(_fibonacci).to_dict()
        capsule_store.add(cap, tags=["v1"])
        capsule_store.add(cap, tags=["v2"])
        items = capsule_store.list()
        assert len(items) == 1
        assert items[0]["tags"] == ["v2"]

    def test_checksum_skip_on_identical_save(self, capsule_store):
        """Saving the same capsule dict twice keeps one row, same checksum."""
        cap = Capsule.from_func(_fibonacci).to_dict()
        cid = cap["capsule_id"]
        capsule_store.add(cap)
        chk1 = capsule_store.get_checksum(cid)
        capsule_store.add(cap)
        chk2 = capsule_store.get_checksum(cid)
        assert chk1 == chk2
        assert len(capsule_store.list()) == 1

    def test_capsule_decorator_updates_when_changed(self, capsule_store, monkeypatch):
        monkeypatch.setattr("ahvn.utils.capsule.store.get_capsule_store", lambda: capsule_store)

        @Capsule.capsule(identifier="myfunc")
        def myfunc(n: int) -> int:
            """Version 1.

            Args:
                n (int): Input.

            Returns:
                int: Output.
            """
            return n

        chk_v1 = capsule_store.get_checksum(myfunc.id)

        @Capsule.capsule(identifier="myfunc")
        def myfunc(n: int) -> int:
            """Version 2 changed body.

            Args:
                n (int): Input.

            Returns:
                int: Output.
            """
            return n + 1

        chk_v2 = capsule_store.get_checksum(myfunc.id)
        assert chk_v1 != chk_v2

    def test_clear_then_save(self, capsule_store):
        cap = Capsule.from_func(_fibonacci).to_dict()
        capsule_store.add(cap)
        capsule_store.clear()
        assert capsule_store.list() == []
        capsule_store.add(cap)
        assert len(capsule_store.list()) == 1


# ================================================================== #
#  4. ToolkitStore checksum-based version control
# ================================================================== #


class TestToolkitVersionControl:
    """ToolkitStore: single-version upsert with checksum tracking."""

    def _make_toolkit_payload(self, name="math", description="Math tools"):
        ts1 = ToolSpec.from_func(_adder)
        ts2 = ToolSpec.from_func(_fibonacci)
        tk = Toolkit(name=name, description=description, tools={"adder": ts1, "fibonacci": ts2})
        return tk

    def test_save_computes_checksum(self, toolkit_store):
        tk = self._make_toolkit_payload()
        toolkit_store.save_toolkit(tk)
        items = toolkit_store.list()
        assert len(items) == 1
        assert items[0].get("checksum") is not None

    def test_upsert_overwrites(self, toolkit_store):
        tk1 = self._make_toolkit_payload(description="v1")
        toolkit_store.save_toolkit(tk1)
        tk2 = self._make_toolkit_payload(description="v2")
        toolkit_store.save_toolkit(tk2)
        items = toolkit_store.list()
        assert len(items) == 1

    def test_clear_then_save(self, toolkit_store):
        tk = self._make_toolkit_payload()
        toolkit_store.save_toolkit(tk)
        toolkit_store.clear()
        assert toolkit_store.list() == []
        toolkit_store.save_toolkit(tk)
        assert len(toolkit_store.list()) == 1

    def test_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "persist_tk.db")
        s1 = ToolkitStore(provider="sqlite", database=db)
        tk = self._make_toolkit_payload()
        s1.save_toolkit(tk)

        s2 = ToolkitStore(provider="sqlite", database=db)
        items = s2.list()
        assert len(items) == 1
        assert items[0]["name"] == "math"


# ================================================================== #
#  5. Cross-store contract consistency
# ================================================================== #


class TestVersionContractConsistency:
    """Ensure versioning contracts are consistent."""

    def test_versioned_fields_include_standard(self):
        for f in REGISTRY_STANDARD_FIELDS:
            assert f in VERSIONED_REGISTRY_FIELDS

    def test_next_version_empty(self):
        assert next_version([]) == 1

    def test_next_version_sequential(self):
        assert next_version([1, 2, 3]) == 4

    def test_next_version_gaps(self):
        assert next_version([1, 5, 3]) == 6

    def test_resolve_latest(self):
        assert resolve_version(None, [1, 2, 3]) == 3
        assert resolve_version("latest", [1, 2, 3]) == 3

    def test_resolve_specific(self):
        assert resolve_version(2, [1, 2, 3]) == 2
        assert resolve_version(99, [1, 2, 3]) is None

    def test_resolve_empty(self):
        assert resolve_version("latest", []) is None

    def test_prompt_store_uses_next_version(self, prompt_store):
        _save_prompt(prompt_store, "p", 1, "a")
        _save_prompt(prompt_store, "p", 2, "b")
        versions = prompt_store.list_versions("p")
        assert next_version(versions) == 3

    def test_all_stores_support_clear(self, prompt_store, capsule_store, toolkit_store):
        assert hasattr(prompt_store, "clear")
        assert hasattr(capsule_store, "clear")
        assert hasattr(toolkit_store, "clear")

    def test_all_stores_support_list(self, prompt_store, capsule_store, toolkit_store):
        assert hasattr(prompt_store, "list")
        assert hasattr(capsule_store, "list")
        assert hasattr(toolkit_store, "list")
