"""\
Unit tests for CP_AHVN (CapsuleManager) and TK_AHVN (ToolkitManager) singletons.
"""

from __future__ import annotations

import tempfile

import pytest

from ahvn.utils.capsule import Capsule, CapsuleStore
from ahvn.utils.capsule.store import CapsuleManager, CP_AHVN, get_capsule_manager
from ahvn.tool import ToolSpec, Toolkit, ToolkitManager, TK_AHVN
from ahvn.tool.manager import get_toolkit_manager

# ── Test helpers ──────────────────────────────────────────────────────


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number."""
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


def adder(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


# ── CapsuleManager tests ─────────────────────────────────────────────


@pytest.fixture
def cp_manager(tmp_path):
    """CapsuleManager with a temp DB."""
    db_path = str(tmp_path / "test_capsules.db")
    store = CapsuleStore(provider="sqlite", database=db_path)
    return CapsuleManager(store=store)


class TestCapsuleManager:
    def test_add_and_get_callable(self, cp_manager):
        cid = cp_manager.add(fibonacci, tags=["math"])
        cap = cp_manager.get(cid)
        assert cap is not None
        assert isinstance(cap, Capsule)
        assert cap.name == "fibonacci"

    def test_add_and_get_capsule_obj(self, cp_manager):
        cap_obj = Capsule.from_func(fibonacci)
        cid = cp_manager.add(cap_obj, tags=["math"])
        cap = cp_manager.get(cid)
        assert cap is not None
        assert isinstance(cap, Capsule)

    def test_add_and_get_dict(self, cp_manager):
        cap_dict = Capsule.from_func(fibonacci).to_dict()
        cid = cp_manager.add(cap_dict)
        cap = cp_manager.get(cid)
        assert cap is not None
        assert isinstance(cap, Capsule)

    def test_get_to_tool_restores_toolspec(self, cp_manager):
        cid = cp_manager.add(fibonacci, tags=["math"])
        cap = cp_manager.get(cid)
        assert cap is not None
        restored = cap.to_tool()
        assert isinstance(restored, ToolSpec)
        assert restored(n=8) == 21

    def test_get_by_qualname(self, cp_manager):
        """CapsuleManager.get falls back to search by qualname."""
        cap_dict = Capsule.from_func(fibonacci).to_dict()
        cid = cp_manager.add(cap_dict)
        # Direct id lookup works
        cap = cp_manager.get(cid)
        assert cap is not None
        assert isinstance(cap, Capsule)
        # Qualname lookup also works
        by_qualname = cp_manager.get(fibonacci.__qualname__)
        assert by_qualname is not None
        assert isinstance(by_qualname, Capsule)
        assert by_qualname.id == cap.id

    def test_list(self, cp_manager):
        cp_manager.add(fibonacci)
        cp_manager.add(adder)
        items = cp_manager.list()
        assert len(items) >= 2
        assert all(isinstance(item, Capsule) for item in items)

    def test_search(self, cp_manager):
        cp_manager.add(fibonacci)
        cp_manager.add(adder)
        found = cp_manager.search(name="fib")
        assert len(found) == 1
        assert isinstance(found[0], Capsule)
        assert found[0].name == "fibonacci"

    def test_remove(self, cp_manager):
        cid = cp_manager.add(fibonacci)
        cp_manager.remove(cid)
        assert cp_manager.get(cid) is None

    def test_exists(self, cp_manager):
        cid = cp_manager.add(fibonacci)
        assert cp_manager.exists(cid)
        cp_manager.remove(cid)
        assert not cp_manager.exists(cid)

    def test_clear(self, cp_manager):
        cp_manager.add(fibonacci)
        cp_manager.add(adder)
        count = cp_manager.clear()
        assert count == 2
        assert len(cp_manager.list()) == 0

    def test_get_missing_returns_none(self, cp_manager):
        assert cp_manager.get("nonexistent") is None

    def test_add_invalid_type(self, cp_manager):
        with pytest.raises(TypeError):
            cp_manager.add(42)


class TestCPAHVNProxy:
    """Test that CP_AHVN lazy proxy works."""

    def test_cp_ahvn_is_importable(self):
        assert CP_AHVN is not None

    def test_get_capsule_manager_returns_singleton(self):
        m1 = get_capsule_manager()
        m2 = get_capsule_manager()
        assert m1 is m2


# ── TK_AHVN tests ────────────────────────────────────────────────────


class TestTKAHVNProxy:
    """Test that TK_AHVN lazy proxy works."""

    def test_tk_ahvn_is_importable(self):
        assert TK_AHVN is not None

    def test_get_toolkit_manager_returns_singleton(self):
        m1 = get_toolkit_manager()
        m2 = get_toolkit_manager()
        assert m1 is m2
