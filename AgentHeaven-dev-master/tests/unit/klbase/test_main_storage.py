"""Simple tests for the main storage of KLBase."""

from unittest.mock import patch

import pytest

from ahvn.cache import InMemCache
from ahvn.klbase import KLBase
from ahvn.klstore import CacheKLStore
from ahvn.ukf.base import BaseUKF


@pytest.fixture
def simple_klbase():
    klbase = KLBase(name="test_klbase")

    yield klbase

    klbase.clear()


def test_add_main_storage(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    assert simple_klbase.main_storage is None
    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)
    assert simple_klbase.main_storage == "store1"


def test_del_main_storage(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    assert simple_klbase.main_storage == "store1"
    simple_klbase.del_storage("store1")
    assert simple_klbase.main_storage is None


def test_upsert_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.upsert(kl)

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False

    kl_get2 = store2.get(kl.id)
    assert isinstance(kl_get2, BaseUKF)
    assert kl_get2.is_processing is False


def test_upsert_failed(simple_klbase):
    idx = 0

    def cache_upsert_mock(self, kl, **kwargs):
        nonlocal idx
        idx += 1
        if idx <= 1:
            self.cache.set(func="kl_store", output=kl.to_dict(), kid=kl.id)
        else:
            raise ValueError("upsert failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._upsert", autospec=True) as mock:
        mock.side_effect = cache_upsert_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        # Only the store1 was upserted successfully
        with pytest.raises(ValueError):
            simple_klbase.upsert(kl)

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True

        assert kl.id not in store2


def test_upsert_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl)
        assert mock.call_count == 1


def test_upsert_disable(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl, disable_set_processing=True)
        assert mock.call_count == 1


def test_batch_upsert_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.batch_upsert([kl])

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False

    kl_get2 = store2.get(kl.id)
    assert isinstance(kl_get2, BaseUKF)
    assert kl_get2.is_processing is False


def test_batch_upsert_failed(simple_klbase):
    idx = 0

    def cache_upsert_mock(self, kl, **kwargs):
        nonlocal idx
        idx += 1
        if idx <= 1:
            self.cache.set(func="kl_store", output=kl.to_dict(), kid=kl.id)
        else:
            raise ValueError("upsert failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._upsert", autospec=True) as mock:
        mock.side_effect = cache_upsert_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        # Only the store1 was upserted successfully
        with pytest.raises(ValueError):
            simple_klbase.batch_upsert([kl])

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True

        assert kl.id not in store2


def test_batch_upsert_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.batch_upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.batch_upsert([kl])
        assert mock.call_count == 1


def test_batch_upsert_disable(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.batch_upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)

        kl = BaseUKF(name="ukf1")
        simple_klbase.batch_upsert([kl], disable_set_processing=True)
        assert mock.call_count == 1


def test_insert_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.insert(kl)

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False

    kl_get2 = store2.get(kl.id)
    assert isinstance(kl_get2, BaseUKF)
    assert kl_get2.is_processing is False


def test_insert_failed(simple_klbase):
    idx = 0

    def cache_upsert_mock(self, kl, **kwargs):
        nonlocal idx
        idx += 1
        if idx <= 1:
            self.cache.set(func="kl_store", output=kl.to_dict(), kid=kl.id)
        else:
            raise ValueError("upsert failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._upsert", autospec=True) as mock:
        mock.side_effect = cache_upsert_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        # Only the store1 was inserted successfully
        with pytest.raises(ValueError):
            simple_klbase.insert(kl)

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True

        assert kl.id not in store2


def test_insert_already_in(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())
    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1", description="first insert")
    simple_klbase.insert(kl)

    # The kl is already in the klbase, so no any upsert operations will be performed
    kl.description = "second insert"
    simple_klbase.insert(kl)

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False
    assert kl_get1.description == "first insert"


def test_insert_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.insert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.insert(kl)
        assert mock.call_count == 1


def test_insert_disable(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.insert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)

        kl = BaseUKF(name="ukf1")
        simple_klbase.insert(kl, disable_set_processing=True)
        assert mock.call_count == 1


def test_batch_insert_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.batch_insert([kl])

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False

    kl_get2 = store2.get(kl.id)
    assert isinstance(kl_get2, BaseUKF)
    assert kl_get2.is_processing is False


def test_batch_insert_failed(simple_klbase):
    idx = 0

    def cache_upsert_mock(self, kl, **kwargs):
        nonlocal idx
        idx += 1
        if idx <= 1:
            self.cache.set(func="kl_store", output=kl.to_dict(), kid=kl.id)
        else:
            raise ValueError("upsert failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._upsert", autospec=True) as mock:
        mock.side_effect = cache_upsert_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        # Only the store1 was upserted successfully
        with pytest.raises(ValueError):
            simple_klbase.batch_insert([kl])

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True

        assert kl.id not in store2


def test_batch_insert_already_in(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())
    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1", description="first insert")
    simple_klbase.batch_insert([kl])

    # The kl is already in the klbase, so no any upsert operations will be performed
    kl.description = "second insert"
    simple_klbase.batch_insert([kl])

    kl_get1 = store1.get(kl.id)
    assert isinstance(kl_get1, BaseUKF)
    assert kl_get1.is_processing is False
    assert kl_get1.description == "first insert"


def test_batch_insert_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.batch_insert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.batch_insert([kl])
        assert mock.call_count == 1


def test_batch_insert_disable(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.batch_insert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)

        kl = BaseUKF(name="ukf1")
        simple_klbase.batch_insert([kl], disable_set_processing=True)
        assert mock.call_count == 1


def test_remove_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.upsert(kl)
    simple_klbase.remove(kl)

    assert kl.id not in store1
    assert kl.id not in store2


def test_remove_failed(simple_klbase):
    idx = 0

    def cache_remove_mock(self, key, **kwargs):
        nonlocal idx
        idx += 1
        raise ValueError("remove failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._remove", autospec=True) as mock:
        mock.side_effect = cache_remove_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl)

        with pytest.raises(ValueError):
            simple_klbase.remove(kl.id)

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True


def test_remove_not_in(simple_klbase):
    idx = 0

    def cache_remove_mock(self, key, **kwargs):
        nonlocal idx
        idx += 1
        raise ValueError("remove failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._remove", autospec=True) as mock:
        mock.side_effect = cache_remove_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        # The kl is not in the klbase, so no any upsert operations will be performed
        kl = BaseUKF(name="ukf1")
        with pytest.raises(ValueError):
            simple_klbase.remove(kl.id)

        assert kl.id not in store1


def test_remove_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl)
        simple_klbase.remove(kl)
        assert mock.call_count == 1


def test_remove_disable(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl, disable_set_processing=True)
        simple_klbase.remove(kl, disable_set_processing=True)
        assert mock.call_count == 1


def test_batch_remove_succeed(simple_klbase):
    store1 = CacheKLStore(name="store1", cache=InMemCache())
    store2 = CacheKLStore(name="store2", cache=InMemCache())

    simple_klbase.add_storage(store1, main=True)
    simple_klbase.add_storage(store2)

    kl = BaseUKF(name="ukf1")
    simple_klbase.batch_upsert([kl])
    simple_klbase.batch_remove([kl.id])

    assert kl.id not in store1
    assert kl.id not in store2


def test_batch_remove_failed(simple_klbase):
    idx = 0

    def cache_remove_mock(self, key, **kwargs):
        nonlocal idx
        idx += 1
        raise ValueError("remove failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._remove", autospec=True) as mock:
        mock.side_effect = cache_remove_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        kl = BaseUKF(name="ukf1")
        simple_klbase.batch_upsert([kl])

        with pytest.raises(ValueError):
            simple_klbase.batch_remove([kl.id])

        kl_get1 = store1.get(kl.id)
        assert isinstance(kl_get1, BaseUKF)
        assert kl_get1.is_processing is True


def test_batch_remove_not_in(simple_klbase):
    idx = 0

    def cache_remove_mock(self, key, **kwargs):
        nonlocal idx
        idx += 1
        raise ValueError("remove failed #{idx}")

    with patch("ahvn.klstore.cache_store.CacheKLStore._remove", autospec=True) as mock:
        mock.side_effect = cache_remove_mock

        store1 = CacheKLStore(name="store1", cache=InMemCache())
        store2 = CacheKLStore(name="store2", cache=InMemCache())
        simple_klbase.add_storage(store1, main=True)
        simple_klbase.add_storage(store2)

        # The kl is not in the klbase, so no any upsert operations will be performed
        kl = BaseUKF(name="ukf1")
        with pytest.raises(ValueError):
            simple_klbase.batch_remove([kl.id])

        assert kl.id not in store1


def test_batch_remove_no_main_storage(simple_klbase):
    with patch("ahvn.klstore.cache_store.CacheKLStore.upsert", autospec=True) as mock:
        store1 = CacheKLStore(name="store1", cache=InMemCache())
        simple_klbase.add_storage(store1)

        kl = BaseUKF(name="ukf1")
        simple_klbase.upsert(kl)
        simple_klbase.batch_remove([kl.id])
        assert mock.call_count == 1
