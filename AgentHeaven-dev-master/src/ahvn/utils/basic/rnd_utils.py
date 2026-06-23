"""\
Random utilities with stable seeding that don't interfere with global random state.
"""

__all__ = [
    "StableRNG",
    "interpret_seed",
    "evolve_seed",
]

import heapq
import math
from typing import Iterable, List, Any, Tuple, Optional, Literal, Union
from .hash_utils import md5hash
from .misc_utils import lreshape
from uuid import uuid4
import time
import numpy as np

_BITGENERATORS = {
    "pcg": np.random.PCG64,
    "pcg64": np.random.PCG64,
    "philox": np.random.Philox,
    "mt19937": np.random.MT19937,
    "sfc64": np.random.SFC64,
}
_HASH_MODULO = 1061109589


def interpret_seed(seed: Optional[int], fallback: Literal["time", "uuid", "none"] = "none") -> Optional[int]:
    """\
    Interpret a seed value, providing a fallback for None seeds.

    This function unifies the seed interpretation logic across all stable random functions.
    When seed is None, it provides a deterministic fallback based on the specified strategy.

    Args:
        seed: The seed value to interpret. If None, a fallback is generated.
        fallback: The fallback strategy to use when seed is None:
            - "time": Use current time (default for single-value generation)
            - "uuid": Use a random UUID-based seed
            - "none": Use `None` to skip seeding (unstable)

    Returns:
        The interpreted seed value.

    Example:
        >>> interpret_seed(42)
        42
        >>> interpret_seed(None, fallback="random")  # doctest: +SKIP
        1234567890  # Random value between 0 and 2**31-1
    """
    if seed is not None:
        return seed
    if fallback == "time":
        return int(time.time() * 1000000)
    elif fallback == "uuid":
        return int(uuid4())
    elif fallback == "none":
        return None


def evolve_seed(seed: Optional[int], *args, **kwargs) -> Optional[int]:
    """
    Evolve a seed deterministically based on additional arguments.

    This is used to create new seeds from a base seed and context-specific
    information (like iteration number, combination key, etc.)

    Args:
        seed: The base seed
        *args: Additional arguments to mix into the seed
        **kwargs: Additional keyword arguments to mix into the seed

    Returns:
        A new deterministic seed
    """
    if seed is None:
        return None
    return md5hash([args, kwargs], salt=seed)


class StableRNG:
    """\
    A stable random generator that doesn't interfere with global random state.

    This generator provides a high-performance alternative to individual stable random
    functions by caching and restoring the random state only once instead of on every call.

    The generator supports both context manager usage (with statement) and manual state management.
    All generation methods support an optional `n` parameter for batch generation.

    Args:
        seed: The base seed for the generator. If None, uses time-based seed.
        fallback: Fallback strategy for None seeds ("time", "uuid", "none").
            Defaults to "none" to make the default RNG behaves unstably just like normal RNGs.
        backend: The underlying NumPy bit generator to use ("pcg", "pcg64", "mt19937", "philox", "sfc64").
            Defaults to "philox" to guarantee n-independent parallel random generation.

    Example:
        >>> # Context manager usage (recommended)
        >>> with StableRNG(seed=42) as gen:
        ...     value = gen.rnd()  # Single value
        ...     values = gen.rnd(10)  # List of 10 values

        >>> # Manual state management
        >>> gen = StableRNG(seed=42)
        >>> gen.enter()  # Save global state
        >>> values = [gen.rnd() for _ in range(10)]
        >>> gen.exit()  # Restore global state

        >>> # Auto-context mode (no explicit context needed)
        >>> gen = StableRNG(seed=42)
        >>> values = gen.rnd(100)  # Automatically manages context
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        fallback: Literal["time", "uuid", "none"] = "none",
        backend: Literal["pcg", "pcg64", "mt19937", "philox", "sfc64"] = "philox",
    ):
        """Initialize the generator with a seed."""
        seed = interpret_seed(seed, fallback=fallback)
        self._stack = list()
        self._stack.append(seed)
        self._backend = backend
        self._rng = None
        self._in_context = False
        self._set()

    @property
    def seed(self) -> Optional[int]:
        return self._stack[-1]

    @property
    def initial_seed(self) -> Optional[int]:
        return self._stack[0]

    @property
    def rng(self) -> np.random.Generator:
        """Get the underlying NumPy random generator."""
        if not self._in_context:
            raise RuntimeError("StableRNG.rng can only be accessed within a context (enter/exit or with statement).")
        return self._rng

    def _set(self) -> "StableRNG":
        if self.seed is not None:
            self._rng = np.random.Generator(_BITGENERATORS[self._backend](self.seed))
        else:
            self._rng = np.random.Generator(_BITGENERATORS[self._backend]())

    def enter(self) -> "StableRNG":
        """\
        Enter the generator context: save current seed.

        This method saves the current seed so it can be restored on exit.
        Multiple calls to enter() without an intervening exit() will have
        no effect (idempotent).

        Returns:
            Self for method chaining.

        Example:
            >>> gen = StableRNG(seed=42)
            >>> gen.enter()
            >>> value = gen.rnd()
            >>> gen.exit()  # Seed restored, next rnd() will give same result
        """
        if self._in_context:
            return self
        self._in_context = True
        self._set()
        return self

    def exit(self) -> None:
        """\
        Exit the generator context: restore the saved seed.

        This method restores the previously saved seed by recreating the RNG.
        Multiple calls to exit() without an intervening enter() will have
        no effect (idempotent).

        Example:
            >>> gen = StableRNG(seed=42)
            >>> gen.enter()
            >>> value = gen.rnd()
            >>> gen.exit()  # Seed restored
        """
        if not self._in_context:
            return
        self._set()
        self._in_context = False

    def __enter__(self) -> "StableRNG":
        """Context manager entry."""
        return self.enter()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ARG002
        """Context manager exit."""
        self.exit()

    def evolve(self, *args) -> "StableRNG":
        """\
        Evolve the seed deterministically (in-place).

        This advances the seed by mixing in additional arguments,
        allowing you to get different random sequences from the same generator.
        The evolution is deterministic - evolving with the same args will
        always produce the same new seed.
        The old seed is pushed onto a stack for potential revert.

        Args:
            *args: Additional arguments to mix into the seed.

        Returns:
            Self for method chaining.

        Example:
            >>> gen = StableRNG(seed=42)
            >>> val1 = gen.rnd()  # First value
            >>> gen.evolve("step1")
            >>> val2 = gen.rnd()  # Different value from evolved seed
            >>> gen.revert()
            >>> val3 = gen.rnd()  # Same as val1
        """
        self._stack.append(evolve_seed(self.seed, *args))
        self._set()
        return self

    def revert(self) -> "StableRNG":
        """\
        Revert to the previous seed (undo last evolve).

        This pops the most recent seed from the stack and restores it,
        effectively undoing the last evolve() call.

        Returns:
            Self for method chaining.

        Example:
            >>> gen = StableRNG(seed=42)
            >>> val1 = gen.rnd()
            >>> gen.evolve("step1")
            >>> val2 = gen.rnd()
            >>> gen.revert()
            >>> val3 = gen.rnd()  # Same as val1
        """
        if len(self._stack) > 1:
            self._stack.pop()
        self._set()
        return self

    def reset(self) -> "StableRNG":
        """\
        Reset the generator to its initial seed.

        This restores the generator to its initial state and clears the seed stack,
        allowing you to reproduce the same random sequence again.

        Returns:
            Self for method chaining.

        Example:
            >>> gen = StableRNG(seed=42)
            >>> val1 = gen.rnd()
            >>> gen.evolve("step1")
            >>> val2 = gen.rnd()
            >>> gen.reset()
            >>> val3 = gen.rnd()  # Same as val1
        """
        self._stack = [self.initial_seed]
        self._set()
        return self

    def rnd(self, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[float, List[float], List[List[float]]]:
        """\
        Generate random float(s) in [0.0, 1.0).

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            n: Shape of the output. If None, returns a single float; if int, returns a 1D list of that many floats;
                    if tuple, returns a multi-dimensional list with that shape.

        Returns:
            A random float if n is None, or a list (or nested lists for multi-dimensional shapes) of random floats.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     value = gen.rnd()  # Returns single float
            ...     values = gen.rnd(10)  # Returns list of 10 floats
            ...     matrix = gen.rnd((3, 4))  # Returns 3x4 matrix (list of lists)
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            result = self.rng.random(size=n)
            return float(result) if n is None else result.tolist()
        finally:
            if not was_in_context:
                self.exit()

    def rnd_int(self, min: int = 0, max: int = 2**31, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[int, List[int], List[List[int]]]:
        """\
        Generate random integer(s) between [min, max).

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            min: Lower bound (inclusive).
            max: Upper bound (exclusive).
            n: Shape of the output. If None, returns a single int; if int, returns a 1D list of that many ints;
                    if tuple, returns a multi-dimensional list with that shape.

        Returns:
            A random integer if n is None, or a list (or nested lists for multi-dimensional shapes) of random integers.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     value = gen.rnd_int(1, 10)  # Returns single int
            ...     values = gen.rnd_int(1, 10, 10)  # Returns list of 10 ints
            ...     matrix = gen.rnd_int(1, 10, (3, 4))  # Returns 3x4 matrix (list of lists)
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            result = self.rng.integers(min, max, size=n)
            return int(result) if n is None else result.tolist()
        finally:
            if not was_in_context:
                self.exit()

    def rnd_normal(self, mu: float = 0.0, sigma: float = 1.0, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[float, List[float], List[List[float]]]:
        """\
        Generate random float(s) from a normal distribution.

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            mu: Mean of the normal distribution.
            sigma: Standard deviation of the normal distribution.
            n: Shape of the output. If None, returns a single int; if int, returns a 1D list of that many ints;
                    if tuple, returns a multi-dimensional list with that shape.

        Returns:
            A random float from N(mu, sigma^2) if n is None, or a list (or nested lists for multi-dimensional shapes) of random floats.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     value = gen.rnd_normal(0.0, 1.0)  # Returns single float
            ...     values = gen.rnd_normal(0.0, 1.0, 10)  # Returns list of 10 floats
            ...     matrix = gen.rnd_normal(0.0, 1.0, (3, 4))  # Returns 3x4 matrix (list of lists)
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            result = self.rng.normal(loc=mu, scale=sigma, size=n)
            return float(result) if n is None else result.tolist()
        finally:
            if not was_in_context:
                self.exit()

    def rnd_float(self, min: float, max: float, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[float, List[float], List[List[float]]]:
        """\
        Generate random float(s) in [min, max).

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            min: Lower bound (inclusive).
            max: Upper bound (exclusive).
            n: Shape of the output. If None, returns a single int; if int, returns a 1D list of that many ints;
                    if tuple, returns a multi-dimensional list with that shape.

        Returns:
            A random float in [min, max) if n is None, or a list (or nested lists for multi-dimensional shapes) of random floats.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     value = gen.rnd_float(1.0, 10.0)  # Returns single float
            ...     values = gen.rnd_float(1.0, 10.0, 10)  # Returns list of 10 floats
            ...     matrix = gen.rnd_float(1.0, 10.0, (3, 4))  # Returns 3x4 matrix (list of lists)
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            result = self.rng.uniform(min, max, size=n)
            return float(result) if n is None else result.tolist()
        finally:
            if not was_in_context:
                self.exit()

    def rnd_str(
        self, length: int, charset: str = "abcdefghijklmnopqrstuvwxyz0123456789", n: Optional[Union[int, Tuple[int, ...]]] = None
    ) -> Union[List[str], List[List[str]]]:
        """\
        Generate stable random string(s) by sampling characters from a charset.

        Uses batch random generation for efficiency - generates all random indices
        at once and constructs strings from them.

        Args:
            length: Length of each string to generate.
            charset: Character set to sample from.
            n: Number of strings to generate. If None, returns a single string; if specified, returns a list of n strings.

        Returns:
            A randomly generated string if n is None, or a list of n random strings.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     s = gen.rnd_str(10)  # Single string
            ...     strings = gen.rnd_str(10, n=5)  # List of 5 strings
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            flattened_n = math.prod(actual_n)
            indices = self.rng.integers(0, len(charset), size=(flattened_n, length))
            results = ["".join([charset[i] for i in indices[j]]) for j in range(flattened_n)]
            return lreshape(results, shape=actual_n)[0] if n is None else results
        finally:
            if not was_in_context:
                self.exit()

    def choice(
        self, seq: List[Any], k: int = 1, replace: bool = True, p: Optional[List[float]] = None, n: Optional[Union[int, Tuple[int, ...]]] = None
    ) -> Union[Any, List[Any], List[List[Any]]]:
        """\
        Randomly select element(s) from a sequence.

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            seq: The sequence to sample from.
            k: Number of elements to select in one sample. Default is 1.
            replace: Whether to sample with replacement. Default is True.
            p: Optional list of probabilities associated with each entry in seq.
            n: Shape of the output. If None, returns a single int; if int, returns a 1D list of that many ints;
                    if tuple, returns a multi-dimensional list with that shape.

        Returns:
            - If k == 1 and n is None: a single selected element.
            - If k == 1: a list/nested lists of selected elements.
            - If k > 1 and n is None: a list of k selected elements.
            - If k > 1: a list/nested lists of lists of k selected elements.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     value = gen.choice([1, 2, 3, 4, 5])  # Single element
            ...     values = gen.choice([1, 2, 3, 4, 5], k=3)  # List of 3 elements
            ...     values = gen.choice([1, 2, 3, 4, 5], k=1, n=4)  # List of 4 elements
            ...     values = gen.choice([1, 2, 3, 4, 5], k=2, n=4)  # List of 4 lists of 2 elements each
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            # TODO: Numpy's random choice does not support parallel sampling of n, so we do it in a loop here. To be optimized later.
            flattened_n = math.prod(actual_n)
            results = [self.rng.choice(range(len(seq)), size=k, replace=replace, p=p) for _ in range(flattened_n)]
            results = [[seq[int(i)] for i in res] for res in results]
            if k == 1:
                results = [res[0] for res in results]
            return lreshape(results, shape=actual_n) if n is not None else results[0]
        finally:
            if not was_in_context:
                self.exit()

    def perm(self, dim: int, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[List[int], List[List[int]]]:
        """\
        Generate random permutation(s) of integers [0, dim).

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            dim: The size of the permutation.
            n: Number of permutations to generate. If None, returns a single permutation; if specified, returns a list of n permutations.

        Returns:
            A list of integers representing a permutation if n is None, or a list of n such lists.
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            results = self.rng.permuted(x=np.broadcast_to(np.arange(dim), actual_n + (dim,)), axis=-1).tolist()
            return results[0] if n is None else results
        finally:
            if not was_in_context:
                self.exit()

    def shuffled(self, seq: List[Any], n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[List[Any], List[List[Any]]]:
        """\
        Return shuffled version(s) of the input sequence.

        If not in context, temporarily enters context for this call (auto-context mode).

        Args:
            seq: The sequence to shuffle.
            n: Number of shuffled sequences to generate. If None, returns a single shuffled list; if specified, returns a list of n shuffled lists.

        Returns:
            A shuffled list if n is None, or a list of n shuffled lists.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     shuffled_seq = gen.shuffled([1, 2, 3, 4, 5])  # Single shuffled list
            ...     shuffled_seqs = gen.shuffled([1, 2, 3, 4, 5], 3)  # List of 3 shuffled lists
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            perms = self.perm(len(seq), n=actual_n)
            results = [[seq[i] for i in perm] for perm in perms]
            return results[0] if n is None else results
        finally:
            if not was_in_context:
                self.exit()

    def hash_split(
        self, seq: Iterable[Any], r: float = 0.10, n: Optional[Union[int, Tuple[int, ...]]] = None
    ) -> Union[Tuple[List[Any], List[Any]], List[Tuple[List[Any], List[Any]]]]:
        """\
        Split sequence(s) into two parts based on stable hash-based selection.

        This creates a stable split that is resilient to adding/removing/reordering items.
        The exact ratio may vary slightly due to the discrete nature of item selection.

        Args:
            seq: The sequence to split.
            r: Ratio for the first group (default: 0.10 for 10%).
            n: Number of independent splits to perform. If None, returns single split tuple; if specified, returns list of n split tuples.

        Returns:
            A tuple of (selected_items, remaining_items) if n is None, or a list of n such tuples.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     small, large = gen.split(my_list, 0.1)  # Single split
            ...     splits = gen.split(my_list, 0.1, n=3)  # 3 independent splits
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            flattened_n = math.prod(actual_n)
            threshold = int(r * _HASH_MODULO)
            hashes = np.asarray([[md5hash(item, salt=(self.seed, i)) % _HASH_MODULO for item in seq] for i in range(flattened_n)])
            indices = [np.where(hashes[i] < threshold)[0].tolist() for i in range(flattened_n)]
            results = [([seq[i] for i in idx_set], [seq[i] for i in range(len(seq)) if i not in idx_set]) for idx_set in indices]
            return lreshape(results, shape=actual_n)[0] if n is None else results
        finally:
            if not was_in_context:
                self.exit()

    def hash_sample(self, seq: Iterable[Any], k: int = 1, n: Optional[Union[int, Tuple[int, ...]]] = None) -> Union[List[Any], List[List[Any]]]:
        """\
        Sample element(s) from a sequence based on stable hash-based selection.

        This creates a stable sample that is resilient to adding/removing/reordering items.

        Args:
            seq: The sequence to sample from.
            k: Number of elements to sample.
            n: Number of independent samples to perform. If None, returns single sample list; if specified, returns list of n sample lists.

        Returns:
            A list of sampled items if n is None, or a list of n such lists.
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            flattened_n = math.prod(actual_n)
            hashes = np.asarray([[md5hash(item, salt=(self.seed, i)) % _HASH_MODULO for item in seq] for i in range(flattened_n)])
            indices = [heapq.nsmallest(k, range(len(seq)), key=lambda i: hashes[j][i]) for j in range(flattened_n)]
            results = [[seq[i] for i in idx_set] for idx_set in indices]
            return lreshape(results, shape=actual_n)[0] if n is None else results[0]
        finally:
            if not was_in_context:
                self.exit()

    def rnd_vec(self, dim: int = 384, n: Optional[int] = None) -> Union[List[float], List[List[float]]]:
        """\
        Generate stable random vector(s) to imitate text embeddings.

        Args:
            dim: Dimensionality of the vector(s). Default is 384.
            n: Number of vectors to generate. If None, returns single vector; if specified, returns list of n vectors.

        Returns:
            A normalized vector of length `dim` with unit L2 norm if n is None, or a list of n such vectors.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     vec = gen.rnd_vec()  # Single vector
            ...     vectors = gen.rnd_vec(n=10)  # 10 vectors
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            result = self.rng.normal(loc=0.0, scale=1.0, size=(*actual_n, dim))
            norms = np.linalg.norm(result, axis=-1, keepdims=True)
            normalized = (result / norms).tolist()
            return normalized[0] if n is None else normalized
        finally:
            if not was_in_context:
                self.exit()

    def abm(self, base: float, t: float = 0.0, trend: float = 0.0, vol_ratio: float = 0.035, n: Optional[int] = None) -> Union[float, List[float]]:
        """\
        Generate Arithmetic Brownian Motion (ABM) value at time unit t.

        The standard ABM formula is:
        X(t) = base + mu*t + sigma*W(t)
        where W(t) is standard Brownian motion at time t, W(t) = Z*sqrt(t) with Z ~ N(0,1).

        For ease-of-use, we use `trend` to represent `mu/base`, and `vol_ratio` to represent `sigma/base`.
        X(t) = base * (1 + trend*t) + vol_ratio * base * Z * sqrt(t)

        Args:
            base (float): Base value at time 0.
            t (float): Time unit.
            trend (float, optional): Trend term representing relative drift per time unit. Default is 0.0.
            vol_ratio (float, optional): Diffusion term.
                Notice: To scale vol_ratio according to time unit, multiply by `sqrt(time unit)`.
                For example, if you expect an average +-15% swing (max +-30% swing) in 10 units, set `vol_ratio=0.15/math.sqrt(10)`.
                Default is 0.035, which is approximately +-12% swing in 12 time units (i.e., monthly swing in yearly scale).
            n (int, optional): Number of independent ABM values to generate. If None, returns a single value; if specified, returns a list of n values.

        Returns:
            float: The ABM value at time t.

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     val = gen.abm(100.0, 10)  # Single value at t=10
            ...     vals = gen.abm(100.0, 10, n=5)  # 5 independent values
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            zs = self.rng.normal(loc=0.0, scale=1.0, size=actual_n)
            values = base * (1 + trend * t) + (vol_ratio * base * zs * math.sqrt(t))
            return float(values[0]) if n is None else values.tolist()
        finally:
            if not was_in_context:
                self.exit()

    def abm_path(self, base: float, t: int = 0, trend: float = 0.0, vol_ratio: float = 0.035, n: Optional[int] = None) -> Union[List[float], List[List[float]]]:
        """\
        Generate the full Arithmetic Brownian Motion (ABM) path from time 0 to t.

        The standard ABM formula is:
        X(t) = base + mu*t + sigma*W(t)
        where W(t) is standard Brownian motion at time t, W(t) = Z*sqrt(t) with Z ~ N(0,1).

        To obtain a continuous path, we iteratively apply the ABM step at each integer time unit.
        X(t+1) = X(t) + mu + sigma*(W(t+1) - W(t)) = X(t) + mu + sigma*Z_t
        where Z_t ~ N(0,1) are independent normal variables for each time step.

        Warning: The path after t units does NOT match a single abm() call at time t
        due to the difference between pointwise evaluation and path generation.

        Args:
            base (float): Base value at time 0.
            t (int): Time unit.
            trend (float, optional): Trend term representing relative drift per time unit. Default is 0.
            vol_ratio (float, optional): Diffusion term.
                Notice: To scale vol_ratio according to time unit, multiply by `sqrt(time unit)`.
                For example, if you expect an average +-15% swing (max +-30% swing) in 10 units, set `vol_ratio=0.15/math.sqrt(10)`.
                Default is 0.035, which is approximately +-12% swing in 12 time units (i.e., monthly swing in yearly scale).
            n (int, optional): Number of independent ABM values to generate. If None, returns a single value; if specified, returns a list of n values.

        Returns:
            The ABM values at each time unit from 0 to t (inclusive, t+1 values).

        Example:
            >>> with StableRNG(seed=42) as gen:
            ...     path = gen.abm_path(100.0, 10)  # Path from t=0 to t=10
            ...     paths = gen.abm_path(100.0, 10, n=5)  # 5 independent paths
        """
        was_in_context = self._in_context
        if not was_in_context:
            self.enter()

        try:
            actual_n = 1 if n is None else n
            actual_n = tuple([actual_n]) if isinstance(actual_n, int) else actual_n
            zs = self.rng.normal(loc=0.0, scale=1.0, size=(*actual_n, t))
            cum_zs = np.cumsum(zs, axis=-1)
            steps = np.arange(1, t + 1)
            paths = np.empty((*actual_n, t + 1))
            paths[..., 0] = base
            paths[..., 1:] = base + (trend * base * steps) + (vol_ratio * base * cum_zs)
            results = paths.tolist()
            return results[0] if n is None else results
        finally:
            if not was_in_context:
                self.exit()
