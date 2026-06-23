__all__ = [
    "Parallelized",
]

from .log_utils import get_logger
from .progress_utils import Progress, NoProgress, TqdmProgress

logger = get_logger(__name__)

import asyncio
import inspect
from typing import Generator, AsyncGenerator, Callable, Iterable, Any, Tuple, Dict, List, Optional, Type
from concurrent.futures import ThreadPoolExecutor, as_completed, Future


class Parallelized:
    """Run sync or async callables with a shared interface.

    Mode selection:
    - ``is_async=None`` (default): infer from callable type.
    - ``is_async=True``: force async mode.
    - ``is_async=False``: force sync mode.
    """

    def __init__(
        self,
        func: Callable,
        args: Iterable[Dict[str, Any]] = None,
        num_threads: Optional[int] = None,
        desc: str = None,
        progress: Optional[Type[Progress]] = None,
        is_async: Optional[bool] = None,
    ):
        self.func = func
        self.args = list(args) if args is not None else []
        self.total = len(self.args)
        self.num_threads = self._normalize_num_threads(num_threads)
        self.desc = desc
        self._progress_cls = progress or NoProgress
        self._is_async = inspect.iscoroutinefunction(func) if is_async is None else bool(is_async)
        self._use_concurrency = self.num_threads is None or self.num_threads > 0

        self._thread_tasks: List[Future] = []
        self._async_tasks: List[asyncio.Task] = []
        self._validated_args: Optional[List[Dict[str, Any]]] = None
        self._progress: Optional[Progress] = None
        self._executor = None
        self._interrupted = False

    @staticmethod
    def _normalize_num_threads(num_threads: Optional[int]) -> Optional[int]:
        if num_threads is None:
            return None
        try:
            return int(num_threads)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"num_threads must be an integer or None, got {num_threads!r}.") from exc

    def _get_func_name(self) -> str:
        return getattr(self.func, "__name__", repr(self.func))

    def _ensure_mode(self, mode: str):
        if mode == "sync" and self._is_async:
            raise TypeError("Parallelized is in async mode. Use 'async with' and 'async for'.")
        if mode == "async" and not self._is_async:
            raise TypeError("Parallelized is in sync mode. Use 'with' and 'for'.")

    def _ensure_progress_ready(self):
        if self._progress is None:
            raise RuntimeError("Parallelized must be used as a context manager before iteration.")

    def _validate_args(self) -> List[Dict[str, Any]]:
        validated: List[Dict[str, Any]] = []
        for kwargs in self.args:
            if not isinstance(kwargs, dict):
                raise TypeError(f"All arguments must be dictionaries, got {type(kwargs)}: {kwargs}")
            validated.append(kwargs)
        return validated

    def _init_context(self):
        self._interrupted = False
        self._progress = self._progress_cls(total=self.total, desc=self.desc)
        self._validated_args = self._validate_args()

    def _close_progress(self):
        if self._progress is not None:
            self._progress.close()

    def _write_progress(self, message: str):
        if self._progress is not None:
            self._progress.write(message)

    def __enter__(self):
        self._ensure_mode("sync")
        self._init_context()
        self._thread_tasks = []
        if self._use_concurrency:
            self._executor = ThreadPoolExecutor(max_workers=self.num_threads)
            for kwargs in self._validated_args:
                self._thread_tasks.append(self._executor.submit(self._execute_sync_task, kwargs))
        return self

    async def __aenter__(self):
        self._ensure_mode("async")
        self._init_context()
        self._async_tasks = []
        return self

    def _execute_sync_task(self, kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], Any, Optional[Exception]]:
        if self._interrupted:
            return (kwargs, None, KeyboardInterrupt("Task cancelled due to interruption or exit."))
        try:
            result = self.func(**kwargs)
            return (kwargs, result, None)
        except Exception as e:
            logger.error(f"Task failed: {self._get_func_name()}. kwargs: {repr(kwargs)}.")
            return (kwargs, None, e)

    async def _execute_async_task(self, kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], Any, Optional[Exception]]:
        if self._interrupted:
            return (kwargs, None, KeyboardInterrupt("Task cancelled due to interruption or exit."))
        try:
            result = self.func(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return (kwargs, result, None)
        except Exception as e:
            logger.error(f"Async task failed: {self._get_func_name()}. kwargs: {repr(kwargs)}.")
            return (kwargs, None, e)

    def _handle_sync_interrupt(self):
        self._interrupted = True
        logger.warning("\nKeyboardInterrupt/SystemExit received, cancelling pending tasks...")
        self._write_progress("\nKeyboardInterrupt/SystemExit received, cancelling pending tasks...")
        cancelled = 0
        for future in self._thread_tasks:
            if not future.done():
                future.cancel()
                cancelled += 1
        logger.info(f"Cancelled {cancelled} pending tasks.")
        self._write_progress(f"Cancelled {cancelled} pending tasks.")

    async def _handle_async_interrupt(self):
        self._interrupted = True
        logger.warning("\nKeyboardInterrupt/SystemExit received, cancelling pending async tasks...")
        self._write_progress("\nKeyboardInterrupt/SystemExit received, cancelling pending async tasks...")
        cancelled = 0
        for task in self._async_tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
        if self._async_tasks:
            await asyncio.gather(*self._async_tasks, return_exceptions=True)
        logger.info(f"Cancelled {cancelled} pending async tasks.")
        self._write_progress(f"Cancelled {cancelled} pending async tasks.")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type in [KeyboardInterrupt, SystemExit]:
            self._handle_sync_interrupt()
        if self._executor is not None:
            self._executor.shutdown(wait=not self._interrupted)
        self._close_progress()
        return exc_type in [KeyboardInterrupt, SystemExit]

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type in [KeyboardInterrupt, SystemExit]:
            await self._handle_async_interrupt()
        self._close_progress()
        return exc_type in [KeyboardInterrupt, SystemExit]

    def __iter__(self) -> Generator[Tuple[Dict[str, Any], Any, Optional[Exception]], None, None]:
        self._ensure_mode("sync")
        self._ensure_progress_ready()
        if self._validated_args is None:
            self._validated_args = self._validate_args()
        if self._use_concurrency:
            try:
                for future in as_completed(self._thread_tasks):
                    if self._interrupted:
                        break
                    kwargs, result, error = future.result()
                    self._progress.update(1)
                    yield (kwargs, result, error)
            except KeyboardInterrupt as e:
                self._handle_sync_interrupt()
                raise e
            except SystemExit as e:
                self._handle_sync_interrupt()
                raise e
        else:
            try:
                for kwargs in self._validated_args:
                    if self._interrupted:
                        break
                    kwargs_result, result, error = self._execute_sync_task(kwargs)
                    self._progress.update(1)
                    yield (kwargs_result, result, error)
            except KeyboardInterrupt as e:
                self._interrupted = True
                raise e
            except SystemExit as e:
                self._interrupted = True
                raise e

    async def __aiter__(self) -> AsyncGenerator[Tuple[Dict[str, Any], Any, Optional[Exception]], None]:
        self._ensure_mode("async")
        self._ensure_progress_ready()
        if self._validated_args is None:
            self._validated_args = self._validate_args()

        if self._use_concurrency:
            sem = asyncio.Semaphore(self.num_threads) if self.num_threads is not None else None

            async def _guarded(kw: Dict[str, Any]):
                if sem is not None:
                    async with sem:
                        return await self._execute_async_task(kw)
                return await self._execute_async_task(kw)

            self._async_tasks = [asyncio.create_task(_guarded(kwargs)) for kwargs in self._validated_args]

            try:
                for coro in asyncio.as_completed(self._async_tasks):
                    if self._interrupted:
                        break
                    kwargs_out, result, error = await coro
                    self._progress.update(1)
                    yield (kwargs_out, result, error)
            except KeyboardInterrupt as e:
                await self._handle_async_interrupt()
                raise e
            except SystemExit as e:
                await self._handle_async_interrupt()
                raise e
        else:
            try:
                for kwargs in self._validated_args:
                    if self._interrupted:
                        break
                    kwargs_out, result, error = await self._execute_async_task(kwargs)
                    self._progress.update(1)
                    yield (kwargs_out, result, error)
            except KeyboardInterrupt as e:
                self._interrupted = True
                raise e
            except SystemExit as e:
                self._interrupted = True
                raise e

    @property
    def progress(self) -> Progress:
        """Access the progress bar."""
        return self._progress

    @property
    def pbar(self) -> Progress:
        """Alias for progress (backward compatibility)."""
        return self._progress
