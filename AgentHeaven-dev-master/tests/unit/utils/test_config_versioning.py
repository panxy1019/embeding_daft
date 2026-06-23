"""
Unit tests for the versioned configuration system.

Tests the compatibility table (register),
package_version property, version-filtered config CRUD, remove,
and the ConfigManager integration.
"""

import pytest
import os
import threading

from ahvn.utils.basic.config_utils import (
    ConfigStorage,
    ConfigManager,
    ConfigSnapshot,
    VersionConflictError,
)

# Fixtures


@pytest.fixture
def storage(tmp_path):
    """Fresh ConfigStorage backed by a temp DB."""
    return ConfigStorage(package="testpkg", database=str(tmp_path / "test_config.db"))


def _make_cm(tmp_path, pkg_version="1.0.0"):
    cm = object.__new__(ConfigManager)
    cm.package = "testpkg"
    cm.distribution = "testpkg"
    cm.base_scope = "testpkg"
    cm.root = str(tmp_path / ".testpkg")
    os.makedirs(cm.root, exist_ok=True)
    cm.storage = ConfigStorage(package="testpkg", database=str(tmp_path / "config.db"))
    cm._cache = {}
    cm._cache_lock = threading.Lock()
    cm._max_retries = 16
    _pkg_version = pkg_version

    class _TestCM(ConfigManager):
        @property
        def package_version(self):
            return _pkg_version

        def load_default(self):
            return {}

    cm.__class__ = _TestCM
    return cm


# TestRegister


class TestRegister:
    def test_single_self_compatible_version(self, storage):
        n = storage.register("pkg", {"1.0.0": "1.0.0"})
        assert n == 1
        assert storage.compatibles("pkg", "1.0.0") == ["1.0.0"]

    def test_multiple_versions_backward_compat(self, storage):
        storage.register(
            "pkg",
            {
                "1.0.0": "1.0.0",
                "1.1.0": "1.0.0",
                "1.2.0": "1.0.0",
            },
        )
        assert set(storage.compatibles("pkg", "1.2.0")) == {"1.0.0", "1.1.0", "1.2.0"}
        assert set(storage.compatibles("pkg", "1.1.0")) == {"1.0.0", "1.1.0"}
        assert storage.compatibles("pkg", "1.0.0") == ["1.0.0"]

    def test_breaking_version(self, storage):
        storage.register(
            "pkg",
            {
                "1.0.0": "1.0.0",
                "1.1.0": "1.0.0",
                "2.0.0": "2.0.0",
            },
        )
        assert storage.compatibles("pkg", "2.0.0") == ["2.0.0"]
        assert set(storage.compatibles("pkg", "1.1.0")) == {"1.0.0", "1.1.0"}

    def test_unregistered_version_returns_none(self, storage):
        storage.register("pkg", {"1.0.0": "1.0.0"})
        assert storage.compatibles("pkg", "9.9.9") is None

    def test_empty_package_returns_none(self, storage):
        assert storage.compatibles("pkg", "1.0.0") is None

    def test_register_is_idempotent(self, storage):
        storage.register("pkg", {"1.0.0": "1.0.0"})
        storage.register("pkg", {"1.0.0": "1.0.0"})
        assert storage.compatibles("pkg", "1.0.0") == ["1.0.0"]

    def test_register_replaces_existing(self, storage):
        storage.register("pkg", {"1.0.0": "1.0.0", "1.1.0": "1.0.0"})
        storage.register("pkg", {"2.0.0": "2.0.0"})
        assert storage.compatibles("pkg", "1.0.0") is None
        assert storage.compatibles("pkg", "2.0.0") == ["2.0.0"]

    def test_register_empty_dict(self, storage):
        n = storage.register("pkg", {})
        assert n == 0

    def test_different_packages_isolated(self, storage):
        storage.register("pkg_a", {"1.0": "1.0"})
        storage.register("pkg_b", {"2.0": "2.0"})
        assert storage.compatibles("pkg_a", "1.0") == ["1.0"]
        assert storage.compatibles("pkg_b", "2.0") == ["2.0"]
        assert storage.compatibles("pkg_a", "2.0") is None
        assert storage.compatibles("pkg_b", "1.0") is None


# TestCompatibles


class TestCompatibles:
    def test_self_compatible(self, storage):
        storage.register("pkg", {"1.0.0": "1.0.0"})
        assert storage.compatibles("pkg", "1.0.0") == ["1.0.0"]

    def test_three_version_chain(self, storage):
        storage.register("pkg", {"1.0.0": "1.0.0", "1.1.0": "1.0.0", "1.2.0": "1.0.0"})
        result = storage.compatibles("pkg", "1.2.0")
        assert set(result) == {"1.0.0", "1.1.0", "1.2.0"}

    def test_breaking_change_resets_min(self, storage):
        storage.register(
            "pkg",
            {
                "1.0.0": "1.0.0",
                "1.1.0": "1.0.0",
                "2.0.0": "2.0.0",
                "2.1.0": "2.0.0",
            },
        )
        assert set(storage.compatibles("pkg", "2.1.0")) == {"2.0.0", "2.1.0"}
        assert set(storage.compatibles("pkg", "1.1.0")) == {"1.0.0", "1.1.0"}


# TestStorageVersionFiltering


class TestStorageVersionFiltering:
    def _seed(self, storage):
        storage.register("pkg", {"1.0": "1.0", "2.0": "2.0"})

    def test_set_records_package_version(self, storage):
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        snap = storage.get("pkg", "s", snapshot=True)
        assert snap.package_version == "1.0"

    def test_set_without_package_version(self, storage):
        storage.set("pkg", "s", {"a": 1})
        snap = storage.get("pkg", "s", snapshot=True)
        assert snap.package_version is None

    def test_version_filtered_by_compat(self, storage):
        self._seed(storage)
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="2.0")
        assert storage.version("pkg", "s", package_version="1.0") == 1
        assert storage.version("pkg", "s", package_version="2.0") == 2

    def test_get_filtered_by_compat(self, storage):
        self._seed(storage)
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="2.0")
        assert storage.get("pkg", "s", package_version="1.0") == {"a": 1}
        assert storage.get("pkg", "s", package_version="2.0") == {"a": 2}

    def test_versions_list_filtered(self, storage):
        self._seed(storage)
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="2.0")
        storage.set("pkg", "s", {"a": 3}, package_version="2.0")
        assert storage.versions("pkg", "s", package_version="1.0") == [1]
        assert storage.versions("pkg", "s", package_version="2.0") == [3, 2]

    def test_null_package_version_always_visible(self, storage):
        self._seed(storage)
        storage.set("pkg", "s", {"old": True})
        storage.set("pkg", "s", {"new": True}, package_version="2.0")
        data = storage.get("pkg", "s", package_version="1.0")
        assert data == {"old": True}
        data = storage.get("pkg", "s", package_version="2.0")
        assert data == {"new": True}

    def test_no_filter_when_package_version_is_none(self, storage):
        self._seed(storage)
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="2.0")
        data = storage.get("pkg", "s", package_version=None)
        assert data == {"a": 2}

    def test_no_filter_when_version_not_registered(self, storage):
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="2.0")
        data = storage.get("pkg", "s", package_version="1.0")
        assert data == {"a": 2}


# TestCompact


class TestCompact:
    def test_compact_preserves_package_version(self, storage):
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="1.0")
        storage.compact("pkg", "s", keep_last_n=1)
        snap = storage.get("pkg", "s", snapshot=True)
        assert snap.package_version == "1.0"
        assert snap.version == 2
        assert storage.versions("pkg", "s") == [2]
        assert dict(snap) == {"a": 2}

    def test_compact_reset_legacy_behavior(self, storage):
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.set("pkg", "s", {"a": 2}, package_version="1.0")
        storage.compact("pkg", "s", reset=True)
        snap = storage.get("pkg", "s", snapshot=True)
        assert snap.version == 1
        assert dict(snap) == {"a": 2}

    def test_set_auto_compacts_old_versions(self, storage):
        for i in range(1, 6):
            storage.set("pkg", "s", {"a": i}, keep_last_n=3)
        assert storage.versions("pkg", "s") == [5, 4, 3]
        assert storage.get("pkg", "s") == {"a": 5}

    def test_keep_last_n_default_fallback_is_20(self, monkeypatch):
        ConfigStorage._KEEP_LAST_N_CACHE.clear()
        monkeypatch.setattr(
            ConfigStorage,
            "_bundled_default_config_path",
            classmethod(lambda cls, package="ahvn": None),
        )
        assert ConfigStorage._default_keep_last_n("pkg") == 20


class TestTrimQuerySafety:
    def test_trim_delete_avoids_self_referencing_subquery(self, storage, monkeypatch):
        delete_sql = []
        orig_orm_execute = storage.db.orm_execute

        def wrapped(query, *args, **kwargs):
            sql_text = str(query)
            if sql_text.lstrip().upper().startswith("DELETE"):
                delete_sql.append(sql_text.upper())
            return orig_orm_execute(query, *args, **kwargs)

        monkeypatch.setattr(storage.db, "orm_execute", wrapped)

        for i in range(1, 5):
            storage.set("pkg", "s", {"a": i}, keep_last_n=2)

        assert delete_sql, "Expected at least one trim delete statement."
        for sql in delete_sql:
            assert "SELECT" not in sql

    def test_set_rolls_back_when_trim_delete_fails(self, storage, monkeypatch):
        orig_orm_execute = storage.db.orm_execute

        def wrapped(query, *args, **kwargs):
            sql_text = str(query).lstrip().upper()
            if sql_text.startswith("DELETE") and "CONFIGS" in sql_text:
                raise RuntimeError("forced trim delete failure")
            return orig_orm_execute(query, *args, **kwargs)

        monkeypatch.setattr(storage.db, "orm_execute", wrapped)

        with pytest.raises(VersionConflictError):
            storage.set("pkg", "s", {"a": 1}, keep_last_n=1)

        assert storage.versions("pkg", "s") == []
        assert storage.get("pkg", "s") == {}


# TestStorageClear


class TestStorageClear:
    def test_clear_removes_compat_and_configs(self, storage):
        storage.register("pkg", {"1.0": "1.0"})
        storage.set("pkg", "s", {"a": 1}, package_version="1.0")
        storage.clear()
        assert storage.compatibles("pkg", "1.0") is None
        assert storage.get("pkg", "s") == {}


# TestConfigSnapshot


class TestConfigSnapshot:
    def test_snapshot_has_package_version(self, storage):
        storage.set("pkg", "s", {"x": 1}, package_version="1.0")
        snap = storage.get("pkg", "s", snapshot=True)
        assert isinstance(snap, ConfigSnapshot)
        assert snap.package_version == "1.0"
        assert snap["x"] == 1

    def test_snapshot_null_package_version(self, storage):
        storage.set("pkg", "s", {"x": 1})
        snap = storage.get("pkg", "s", snapshot=True)
        assert snap.package_version is None


# TestCMRegister


class TestCMRegister:
    def test_basic(self, tmp_path):
        cm = _make_cm(tmp_path)
        cm.register({"1.0.0": "1.0.0"})
        assert cm.storage.compatibles("testpkg", "1.0.0") == ["1.0.0"]

    def test_idempotent(self, tmp_path):
        cm = _make_cm(tmp_path)
        cm.register({"1.0.0": "1.0.0"})
        cm.register({"1.0.0": "1.0.0"})
        assert cm.storage.compatibles("testpkg", "1.0.0") == ["1.0.0"]

    def test_replaces(self, tmp_path):
        cm = _make_cm(tmp_path)
        cm.register({"1.0.0": "1.0.0", "1.1.0": "1.0.0"})
        cm.register({"2.0.0": "2.0.0"})
        assert cm.storage.compatibles("testpkg", "1.0.0") is None
        assert cm.storage.compatibles("testpkg", "2.0.0") == ["2.0.0"]

    def test_returns_count(self, tmp_path):
        cm = _make_cm(tmp_path)
        assert cm.register({"1.0.0": "1.0.0", "1.1.0": "1.0.0"}) == 2


# TestRemove


class TestRemove:
    def test_remove_all_versions(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("x", 1)
        cm.set("x", 2)
        cm.remove()  # remove current scope, all versions
        cm._cache = {}
        assert cm.storage.versions(cm.package, cm.scope) == []

    def test_remove_specific_version(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("x", 1)  # version 2
        cm.set("x", 2)  # version 3
        cm.remove(version=2)
        cm._cache = {}
        remaining = cm.storage.versions(cm.package, cm.scope)
        assert 2 not in remaining
        assert 3 in remaining

    def test_remove_named_scope(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        with cm.scoped("sub"):
            cm.init(data={"y": 99})
        cm.remove(scope="testpkg.sub")
        assert cm.storage.versions(cm.package, "testpkg.sub") == []

    def test_remove_returns_true(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        assert cm.remove() is True


# TestConfigManagerVersioning


class TestConfigManagerVersioning:
    def test_package_version_property(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        assert cm.package_version == "1.0.0"

    def test_set_and_get_basic(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("core.debug", True)
        cm._cache = {}
        assert cm.get("core.debug") is True

    def test_scoped_set_and_get(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("core.debug", False)
        with cm.scoped("demo"):
            cm.init(data={})
            cm.set("core.debug", True)
            cm._cache = {}
            assert cm.get("core.debug") is True
        cm._cache = {}
        assert cm.get("core.debug") is False

    def test_unset_with_versioning(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("a.b", 42)
        cm._cache = {}
        assert cm.get("a.b") == 42
        cm.unset("a.b")
        cm._cache = {}
        assert cm.get("a.b") is None

    def test_setdef_with_versioning(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.setdef("core.newkey", "default_val")
        cm._cache = {}
        assert cm.get("core.newkey") == "default_val"
        cm.setdef("core.newkey", "other_val")
        cm._cache = {}
        assert cm.get("core.newkey") == "default_val"

    def test_compact_with_versioning(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("a", 1)
        cm.set("a", 2)
        cm.set("a", 3)
        removed = cm.compact(keep_last_n=2)
        cm._cache = {}
        assert removed == 2
        assert cm.get("a") == 3
        assert cm.storage.version("testpkg", "testpkg") == 4
        assert cm.storage.versions("testpkg", "testpkg") == [4, 3]

    def test_compact_reset_with_versioning(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("a", 1)
        cm.set("a", 2)
        removed = cm.compact(reset=True)
        cm._cache = {}
        assert removed == 1
        assert cm.get("a") == 2
        assert cm.storage.version("testpkg", "testpkg") == 1


class TestConfigManagerSingleton:
    def test_same_package_returns_same_instance(self, tmp_path, monkeypatch):
        fake_home = str(tmp_path)
        monkeypatch.setenv("HOME", fake_home)
        monkeypatch.setenv("USERPROFILE", fake_home)

        ConfigManager._drop_singleton("singletonpkg")
        try:
            cm1 = ConfigManager(package="singletonpkg", distribution="singletonpkg", setup=False)
            cm2 = ConfigManager(package="singletonpkg", distribution="singletonpkg", setup=False)
            assert cm1 is cm2
        finally:
            ConfigManager._drop_singleton("singletonpkg")

    def test_rejects_reconfigure_singleton(self, tmp_path, monkeypatch):
        fake_home = str(tmp_path)
        monkeypatch.setenv("HOME", fake_home)
        monkeypatch.setenv("USERPROFILE", fake_home)

        ConfigManager._drop_singleton("singletonpkg")
        try:
            _ = ConfigManager(package="singletonpkg", distribution="singletonpkg", scope="singletonpkg", setup=False)
            with pytest.raises(ValueError):
                _ = ConfigManager(package="singletonpkg", distribution="otherpkg", scope="singletonpkg", setup=False)
        finally:
            ConfigManager._drop_singleton("singletonpkg")


class TestHybridConfigCache:
    def test_hot_read_uses_zero_sql_after_warmup(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("core.debug", True)
        with cm.scoped("demo"):
            cm.init(data={})
            cm.set("core.debug", False)

        calls = {"n": 0}
        orig = cm.storage.db.orm_execute

        def wrapped(*args, **kwargs):
            calls["n"] += 1
            return orig(*args, **kwargs)

        cm.storage.db.orm_execute = wrapped
        try:
            _ = cm.get("core.debug")  # warm
            calls["n"] = 0
            _ = cm.get("core.debug")  # hot
            assert calls["n"] == 0
        finally:
            cm.storage.db.orm_execute = orig

    def test_set_updates_layer_cache_and_serves_next_read_from_memory(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        _ = cm.get("core.debug")  # warm cache

        cm.set("core.debug", True)
        calls = {"n": 0}
        orig = cm.storage.db.orm_execute

        def wrapped(*args, **kwargs):
            calls["n"] += 1
            return orig(*args, **kwargs)

        cm.storage.db.orm_execute = wrapped
        try:
            assert cm.get("core.debug") is True
            assert calls["n"] == 0
        finally:
            cm.storage.db.orm_execute = orig

    def test_load_uses_copied_layers(self, tmp_path):
        cm = _make_cm(tmp_path, "1.0.0")
        cm.setup(reset=True)
        cm.set("a.b", 1)

        loaded = cm.load()
        loaded["a"]["b"] = 999
        _, latest = cm.storage.latest(cm.package, cm.scope, package_version=cm.package_version)
        assert latest["a"]["b"] == 1

    def test_incompatible_package_version_still_can_write(self, tmp_path):
        cm_v1 = _make_cm(tmp_path, "1.0.0")
        cm_v2 = _make_cm(tmp_path, "2.0.0")

        cm_v1.register({"1.0.0": "1.0.0", "2.0.0": "2.0.0"})
        cm_v1.setup(reset=True)
        cm_v1.set("a", 1)

        cm_v2.set("a", 2)
        assert cm_v2.get("a") == 2


# TestCMAHVNSmoke


class TestCMAHVNSmoke:
    def test_package_version_is_populated(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        pv = CM_AHVN.package_version
        assert pv is not None
        assert "0.9.4" in pv

    def test_compat_table_seeded(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        pv = CM_AHVN.package_version
        assert CM_AHVN.storage.compatibles("ahvn", pv) is not None

    def test_register_idempotent(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        CM_AHVN.register(versions=CM_AHVN.compatibility_table)
        pv = CM_AHVN.package_version
        assert CM_AHVN.storage.compatibles("ahvn", pv) is not None

    def test_basic_get(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        val = CM_AHVN.get("core.debug")
        assert val is not None or val is None

    def test_scoped_roundtrip(self):
        from ahvn.utils.basic.config_utils import CM_AHVN

        test_scope = "ahvn._smoke_roundtrip"
        # Clean up any leftover scope data from a previous run
        CM_AHVN.remove(scope=test_scope)
        original = CM_AHVN.get("core.debug")
        try:
            with CM_AHVN.scoped("_smoke_roundtrip"):
                CM_AHVN.init()
                CM_AHVN.set("core.debug", not original if isinstance(original, bool) else True)
                inner = CM_AHVN.get("core.debug")
                assert inner != original or not isinstance(original, bool)
            CM_AHVN._cache = {}
            assert CM_AHVN.get("core.debug") == original
        finally:
            # Always clean up the test scope from the real DB
            CM_AHVN.remove(scope=test_scope)
            CM_AHVN._cache = {}
