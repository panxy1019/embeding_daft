"""\
Unit tests for CapsuleStore (database-backed capsule persistence).
"""

from __future__ import annotations

import os
import tempfile

import pytest

from ahvn.utils.capsule import Capsule, CapsuleStore


def encapsulate(func_or_spec, **kwargs):
    return Capsule.from_func(func_or_spec, **kwargs).to_dict()


def restore(cap, **kwargs):
    return Capsule.to_tool(cap, **kwargs)


# ── Test helpers ──────────────────────────────────────────────────────


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number.

    Args:
        n (int): Fibonacci index (0-indexed).

    Returns:
        int: The n-th Fibonacci number.
    """
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


def adder(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a (int): First operand.
        b (int): Second operand.

    Returns:
        int: Sum.
    """
    return a + b


def multiplier(a: int, b: int) -> int:
    """Multiply two integers.

    Args:
        a (int): First operand.
        b (int): Second operand.

    Returns:
        int: Product.
    """
    return a * b


@pytest.fixture
def store(tmp_path):
    """Create a file-backed CapsuleStore for testing."""
    db_path = str(tmp_path / "test_capsules.db")
    return CapsuleStore(provider="sqlite", database=db_path)


@pytest.fixture
def fib_capsule():
    return encapsulate(fibonacci)


@pytest.fixture
def add_capsule():
    return encapsulate(adder)


@pytest.fixture
def mul_capsule():
    return encapsulate(multiplier)


# ── Add + Get round-trip ─────────────────────────────────────────────


class TestAddGet:
    """add + get round-trip."""

    def test_simple_round_trip(self, store, fib_capsule):
        store.add(fib_capsule)
        loaded = store.get(fib_capsule["capsule_id"])
        assert loaded is not None
        assert loaded["capsule_id"] == fib_capsule["capsule_id"]
        assert loaded["manifest"]["name"] == "fibonacci"

    def test_restored_capsule_is_callable(self, store, fib_capsule):
        store.add(fib_capsule)
        loaded = store.get(fib_capsule["capsule_id"])
        spec = restore(loaded)
        assert spec(n=10) == 55

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("nonexistent_id_000") is None

    def test_get_by_qualname_handles_zero_padded_registry_ids(self, store, add_capsule):
        capsule = dict(add_capsule)
        capsule["capsule_id"] = "0000000000000000000000000000000000000042"
        store.add(capsule)

        loaded = store.get_by_qualname(capsule["manifest"]["qualname"])
        assert loaded is not None
        assert loaded["capsule_id"] == capsule["capsule_id"]


# ── Add + List ───────────────────────────────────────────────────────


class TestList:
    """add + list."""

    def test_list_summaries(self, store, fib_capsule, add_capsule):
        store.add(fib_capsule)
        store.add(add_capsule)
        items = store.list()
        assert len(items) == 2
        names = {item["name"] for item in items}
        assert "fibonacci" in names
        assert "adder" in names

    def test_list_no_payload(self, store, fib_capsule):
        store.add(fib_capsule)
        items = store.list()
        assert len(items) == 1
        assert "payload" not in items[0]

    def test_list_filter_by_tag(self, store, fib_capsule, add_capsule):
        store.add(fib_capsule, tags=["math"])
        store.add(add_capsule, tags=["arithmetic"])
        items = store.list(tag="math")
        assert len(items) == 1
        assert items[0]["name"] == "fibonacci"


# ── Delete ────────────────────────────────────────────────────────────


class TestDelete:
    """add + delete + get returns None."""

    def test_delete(self, store, fib_capsule):
        store.add(fib_capsule)
        assert store.get(fib_capsule["capsule_id"]) is not None
        store.delete(fib_capsule["capsule_id"])
        assert store.get(fib_capsule["capsule_id"]) is None


# ── Upsert ────────────────────────────────────────────────────────────


class TestUpsert:
    """add twice — get returns latest."""

    def test_upsert_overwrites(self, store, fib_capsule):
        store.add(fib_capsule, tags=["v1"])
        store.add(fib_capsule, tags=["v2"])
        items = store.list()
        assert len(items) == 1
        assert items[0]["tags"] == ["v2"]


# ── Clear ─────────────────────────────────────────────────────────────


class TestClear:
    """add multiple, clear, list empty."""

    def test_clear(self, store, fib_capsule, add_capsule, mul_capsule):
        store.add(fib_capsule)
        store.add(add_capsule)
        store.add(mul_capsule)
        count = store.clear()
        assert count == 3
        assert store.list() == []


# ── Search ────────────────────────────────────────────────────────────


class TestSearch:
    """search by name pattern and by tag."""

    def test_search_by_name(self, store, fib_capsule, add_capsule):
        store.add(fib_capsule)
        store.add(add_capsule)
        results = store.search(name="fib")
        assert len(results) == 1
        assert results[0]["name"] == "fibonacci"

    def test_search_by_tag(self, store, fib_capsule, add_capsule):
        store.add(fib_capsule, tags=["math", "sequence"])
        store.add(add_capsule, tags=["math", "arithmetic"])
        results = store.search(tag="sequence")
        assert len(results) == 1
        assert results[0]["name"] == "fibonacci"

    def test_search_by_name_and_tag(self, store, fib_capsule, add_capsule, mul_capsule):
        store.add(fib_capsule, tags=["math"])
        store.add(add_capsule, tags=["math"])
        store.add(mul_capsule, tags=["math"])
        results = store.search(name="mult", tag="math")
        assert len(results) == 1
        assert results[0]["name"] == "multiplier"


# ── Exists ────────────────────────────────────────────────────────────


class TestExists:
    """exists() check."""

    def test_exists_true(self, store, fib_capsule):
        store.add(fib_capsule)
        assert store.exists(fib_capsule["capsule_id"]) is True

    def test_exists_false(self, store):
        assert store.exists("nonexistent") is False


# ── Get + to_tool round-trip ──────────────────────────────────────────


class TestGetAndRestore:
    """add + get → Capsule.from_dict().to_tool() → call → verify."""

    def test_get_and_restore_toolspec(self, store, fib_capsule):
        from ahvn.utils.capsule.core import Capsule

        store.add(fib_capsule)
        cap_dict = store.get(fib_capsule["capsule_id"])
        spec = Capsule.from_dict(cap_dict).to_tool()
        assert spec(n=10) == 55

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("nonexistent") is None


# ── Integrity ─────────────────────────────────────────────────────────


class TestIntegrity:
    """Decompressed payload matches original capsule dict."""

    def test_payload_integrity(self, store, fib_capsule):
        store.add(fib_capsule)
        loaded = store.get(fib_capsule["capsule_id"])
        assert loaded["capsule_version"] == fib_capsule["capsule_version"]
        assert loaded["manifest"] == fib_capsule["manifest"]
        assert loaded["schema"] == fib_capsule["schema"]
        assert len(loaded["layers"]) == len(fib_capsule["layers"])
        for orig, reloaded in zip(fib_capsule["layers"], loaded["layers"]):
            assert orig["type"] == reloaded["type"]


def test_capsule_store_tx_yields_database_handle(tmp_path):
    store = CapsuleStore(provider="sqlite", database=str(tmp_path / "test_capsules_tx.db"))
    with store.tx(write=True) as db:
        assert db is not None
    with store.tx(write=False) as db:
        assert db is not None
