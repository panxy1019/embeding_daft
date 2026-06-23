"""Pytest coverage for UKF composer capsule cache write/read round-trips."""

from __future__ import annotations

import json
from pathlib import Path

from ahvn.cache.json_cache import JsonCache
from ahvn.klstore.cache_store import CacheKLStore
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.utils.llm import LLM as _LLM


def _fib(n: int) -> int:
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


def _cache_fibonacci_composer(kl, **kwargs):
    """Composer used by cache round-trip tests (no network call)."""
    _ = _LLM
    n = kwargs.get("n", kl.content_resources.get("n", 10))
    return f"Fibonacci({n}) = {_fib(int(n))}"


def _load_single_cache_json(cache_dir: Path) -> dict:
    files = sorted(cache_dir.glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_capsule_cache_write_contains_layer_requirements(tmp_path):
    cache_dir = tmp_path / "test_ukf_capsule"
    klstore = CacheKLStore(JsonCache(str(cache_dir)))

    kl = KnowledgeUKFT(name="test_fibonacci_knowledge", content_resources={"n": 20})
    kl.set_composer("fib", _cache_fibonacci_composer)
    klstore.upsert(kl)

    payload = _load_single_cache_json(cache_dir)
    capsule = payload["output"]["content_composers"]["fib"]
    source_layer = next(layer for layer in capsule["layers"] if layer["type"] == "source")
    cloudpickle_layer = next(layer for layer in capsule["layers"] if layer["type"] == "cloudpickle")

    for layer in [source_layer, cloudpickle_layer]:
        requirements = layer.get("requirements")
        assert isinstance(requirements, dict)
        modules = requirements.get("modules", [])
        module_names = [item.get("name") for item in modules if isinstance(item, dict)]
        assert any(name.startswith("ahvn.utils.llm") for name in module_names if isinstance(name, str))


def test_capsule_cache_retrieve_restores_composer_callable(tmp_path):
    cache_dir = tmp_path / "test_ukf_capsule"

    writer_store = CacheKLStore(JsonCache(str(cache_dir)))
    writer_kl = KnowledgeUKFT(name="test_fibonacci_knowledge", content_resources={"n": 20})
    writer_kl.set_composer("fib", _cache_fibonacci_composer)
    writer_store.upsert(writer_kl)

    reader_store = CacheKLStore(JsonCache(str(cache_dir)))
    restored_items = list(reader_store)
    assert len(restored_items) == 1
    restored_kl = restored_items[0]

    assert restored_kl.text(composer="fib") == "Fibonacci(20) = 6765"
    assert restored_kl.text(composer="fib", n=15) == "Fibonacci(15) = 610"
