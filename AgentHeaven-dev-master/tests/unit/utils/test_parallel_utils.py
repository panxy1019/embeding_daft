"""Tests for ahvn.utils.basic.parallel_utils — Parallelized (sync + async)."""

import asyncio
import time
import pytest
from typing import Dict, Any

from ahvn.utils.basic.parallel_utils import Parallelized
from ahvn.utils.basic.progress_utils import NoProgress, TqdmProgress

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _double(**kwargs) -> int:
    return kwargs["x"] * 2


def _slow_double(**kwargs) -> int:
    time.sleep(kwargs.get("delay", 0.05))
    return kwargs["x"] * 2


def _failing(**kwargs):
    raise ValueError(f"boom: {kwargs['x']}")


async def _async_double(**kwargs) -> int:
    return kwargs["x"] * 2


async def _async_slow_double(**kwargs) -> int:
    await asyncio.sleep(kwargs.get("delay", 0.05))
    return kwargs["x"] * 2


async def _async_failing(**kwargs):
    raise ValueError(f"boom: {kwargs['x']}")


# ===========================================================================
# Parallelized (sync, threaded)
# ===========================================================================


class TestParallelized:
    """Sync Parallelized tests."""

    def test_basic_threaded(self):
        args = [{"x": i} for i in range(5)]
        results = {}
        with Parallelized(func=_double, args=args, num_threads=2) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(5)}

    def test_basic_sequential(self):
        """num_threads <= 0 → no threading, purely sequential."""
        args = [{"x": i} for i in range(5)]
        results = {}
        with Parallelized(func=_double, args=args, num_threads=0) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(5)}

    def test_sequential_negative(self):
        """Negative num_threads also means sequential."""
        args = [{"x": i} for i in range(3)]
        results = {}
        with Parallelized(func=_double, args=args, num_threads=-1) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(3)}

    def test_unlimited_threads(self):
        """num_threads=None → unlimited (ThreadPoolExecutor default)."""
        args = [{"x": i} for i in range(10)]
        results = {}
        with Parallelized(func=_double, args=args, num_threads=None) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(10)}

    def test_error_propagation(self):
        """Errors are captured per-task, not raised globally."""
        args = [{"x": 1}]
        with Parallelized(func=_failing, args=args, num_threads=1) as ptasks:
            for kw, result, error in ptasks:
                assert isinstance(error, ValueError)
                assert result is None

    def test_error_sequential(self):
        args = [{"x": 1}]
        with Parallelized(func=_failing, args=args, num_threads=0) as ptasks:
            for kw, result, error in ptasks:
                assert isinstance(error, ValueError)
                assert result is None

    def test_empty_args(self):
        with Parallelized(func=_double, args=[], num_threads=2) as ptasks:
            results = list(ptasks)
        assert results == []

    def test_single_task(self):
        args = [{"x": 42}]
        with Parallelized(func=_double, args=args, num_threads=1) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                assert result == 84

    def test_progress_default_is_noprogress(self):
        """Default progress class should be NoProgress."""
        p = Parallelized(func=_double, args=[{"x": 1}])
        assert p._progress_cls is NoProgress

    def test_progress_property(self):
        with Parallelized(func=_double, args=[{"x": 1}], num_threads=1) as ptasks:
            assert ptasks.progress is not None
            assert ptasks.pbar is ptasks.progress
            for _ in ptasks:
                pass

    def test_concurrency_actually_parallel(self):
        """With enough threads, tasks with sleep should be faster than sequential."""
        n = 6
        delay = 0.1
        args = [{"x": i, "delay": delay} for i in range(n)]

        t0 = time.perf_counter()
        with Parallelized(func=_slow_double, args=args, num_threads=n) as ptasks:
            for _ in ptasks:
                pass
        elapsed = time.perf_counter() - t0
        # Should be roughly ~delay, not ~n*delay
        assert elapsed < delay * n * 0.6, f"Expected parallel execution, got {elapsed:.2f}s"

    def test_invalid_args_type(self):
        """Non-dict args should raise TypeError."""
        with pytest.raises(TypeError):
            with Parallelized(func=_double, args=[1, 2, 3], num_threads=1) as ptasks:
                for _ in ptasks:
                    pass


class TestParallelizedUnifiedMode:
    """Unified Parallelized class supports both sync and async protocols."""

    @pytest.mark.anyio
    async def test_async_auto_mode(self):
        args = [{"x": i} for i in range(5)]
        results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=2) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(5)}

    def test_sync_mode_rejects_async_context(self):
        p = Parallelized(func=_double, args=[{"x": 1}], num_threads=1)
        with pytest.raises(TypeError):
            asyncio.run(p.__aenter__())

    def test_async_mode_rejects_sync_context(self):
        with pytest.raises(TypeError):
            with Parallelized(func=_async_double, args=[{"x": 1}], num_threads=1) as _:
                pass


# ===========================================================================
# Parallelized (async mode)
# ===========================================================================


class TestParallelizedAsyncMode:
    """Parallelized async-mode tests."""

    @pytest.mark.anyio
    async def test_basic_concurrent(self):
        args = [{"x": i} for i in range(5)]
        results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=2) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(5)}

    @pytest.mark.anyio
    async def test_sequential(self):
        """num_threads <= 0 → sequential."""
        args = [{"x": i} for i in range(5)]
        results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=0) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(5)}

    @pytest.mark.anyio
    async def test_sequential_negative(self):
        args = [{"x": i} for i in range(3)]
        results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=-1) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(3)}

    @pytest.mark.anyio
    async def test_unlimited_workers(self):
        """num_threads=None → unlimited concurrency."""
        args = [{"x": i} for i in range(10)]
        results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=None) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        assert results == {i: i * 2 for i in range(10)}

    @pytest.mark.anyio
    async def test_error_propagation(self):
        args = [{"x": 1}]
        async with Parallelized(func=_async_failing, args=args, num_threads=1) as ptasks:
            async for kw, result, error in ptasks:
                assert isinstance(error, ValueError)
                assert result is None

    @pytest.mark.anyio
    async def test_error_sequential(self):
        args = [{"x": 1}]
        async with Parallelized(func=_async_failing, args=args, num_threads=0) as ptasks:
            async for kw, result, error in ptasks:
                assert isinstance(error, ValueError)
                assert result is None

    @pytest.mark.anyio
    async def test_empty_args(self):
        results = []
        async with Parallelized(func=_async_double, args=[], num_threads=2) as ptasks:
            async for item in ptasks:
                results.append(item)
        assert results == []

    @pytest.mark.anyio
    async def test_single_task(self):
        args = [{"x": 42}]
        async with Parallelized(func=_async_double, args=args, num_threads=1) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                assert result == 84

    @pytest.mark.anyio
    async def test_progress_default_is_noprogress(self):
        p = Parallelized(func=_async_double, args=[{"x": 1}])
        assert p._progress_cls is NoProgress

    @pytest.mark.anyio
    async def test_progress_property(self):
        async with Parallelized(func=_async_double, args=[{"x": 1}], num_threads=1) as ptasks:
            assert ptasks.progress is not None
            assert ptasks.pbar is ptasks.progress
            async for _ in ptasks:
                pass

    @pytest.mark.anyio
    async def test_concurrency_actually_parallel(self):
        """With enough workers, async tasks with sleep should be faster than sequential."""
        n = 6
        delay = 0.1
        args = [{"x": i, "delay": delay} for i in range(n)]

        t0 = time.perf_counter()
        async with Parallelized(func=_async_slow_double, args=args, num_threads=n) as ptasks:
            async for _ in ptasks:
                pass
        elapsed = time.perf_counter() - t0
        assert elapsed < delay * n * 0.6, f"Expected parallel execution, got {elapsed:.2f}s"

    @pytest.mark.anyio
    async def test_invalid_args_type(self):
        with pytest.raises(TypeError):
            async with Parallelized(func=_async_double, args=[1, 2, 3], num_threads=1) as ptasks:
                async for _ in ptasks:
                    pass

    @pytest.mark.anyio
    async def test_semaphore_limits_concurrency(self):
        """With num_threads=1, tasks should run one at a time."""
        n = 4
        delay = 0.05
        args = [{"x": i, "delay": delay} for i in range(n)]

        t0 = time.perf_counter()
        async with Parallelized(func=_async_slow_double, args=args, num_threads=1) as ptasks:
            async for _ in ptasks:
                pass
        elapsed = time.perf_counter() - t0
        # With semaphore=1, all tasks run sequentially → ~n*delay
        assert elapsed >= delay * n * 0.8, f"Expected sequential execution with sem=1, got {elapsed:.2f}s"


# ===========================================================================
# Parity: identical results for sync(sequential) vs sync(threaded) vs async
# ===========================================================================


class TestSyncAsyncParity:
    """Ensure sync and async variants produce the same results."""

    def _run_sync(self, num_threads):
        args = [{"x": i} for i in range(10)]
        results = {}
        with Parallelized(func=_double, args=args, num_threads=num_threads) as ptasks:
            for kw, result, error in ptasks:
                assert error is None
                results[kw["x"]] = result
        return results

    @pytest.mark.anyio
    async def test_parity(self):
        args = [{"x": i} for i in range(10)]

        seq_results = self._run_sync(num_threads=0)
        thr_results = self._run_sync(num_threads=4)

        async_results = {}
        async with Parallelized(func=_async_double, args=args, num_threads=4) as ptasks:
            async for kw, result, error in ptasks:
                assert error is None
                async_results[kw["x"]] = result

        assert seq_results == thr_results == async_results
