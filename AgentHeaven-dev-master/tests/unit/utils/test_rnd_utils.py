import pytest
import numpy as np
import math
from ahvn.utils.basic.rnd_utils import StableRNG, interpret_seed


class TestStableRNG:
    def test_initialization(self):
        """Test initialization with various seed types."""
        rng = StableRNG(seed=42)
        assert rng.seed == 42

        # Test fallback='time' (should be int)
        rng_time = StableRNG(seed=None, fallback="time")
        assert isinstance(rng_time.seed, int)

        # Test fallback='uuid' (should be int)
        rng_uuid = StableRNG(seed=None, fallback="uuid")
        assert isinstance(rng_uuid.seed, int)

        # Test fallback='none' (should be None)
        rng_none = StableRNG(seed=None, fallback="none")
        assert rng_none.seed is None

    def test_interpret_seed(self):
        """Test seed interpretation helper."""
        assert interpret_seed(42) == 42
        assert interpret_seed(None, fallback="none") is None
        assert isinstance(interpret_seed(None, fallback="time"), int)
        assert isinstance(interpret_seed(None, fallback="uuid"), int)

    def test_context_management(self):
        """Test enter, exit, and context manager protocols."""
        rng = StableRNG(seed=123)

        # Manual enter/exit
        rng.enter()
        val1 = rng.rnd()
        rng.exit()

        # Context manager
        with rng:
            val2 = rng.rnd()

        # Should be same sequence because state is reset on enter/exit boundary if implemented correctly?
        # Wait, StableRNG resets the RNG state to the seed upon enter/exit or set.
        # Actually, let's verify behavior.
        # enter() sets the seed.
        # exit() sets the seed (implied "reset").

        assert val1 == val2

        # Test nesting (idempotency)
        with rng:
            rng.enter()  # Should be no-op if already in context
            val3 = rng.rnd()
            rng.exit()  # Should be no-op
            val4 = rng.rnd()

        assert val3 == val1
        # exit() resets seed to stored seed (before enter).
        # So val4 (generated after exit()) is a replay of the sequence starting from the original seed.
        assert val4 == val1

    def test_auto_context(self):
        """Test that methods work without explicit context."""
        rng = StableRNG(seed=42)
        # Calling rnd without context should temporarily enter context
        val1 = rng.rnd()
        val2 = rng.rnd()
        assert val1 == val2  # Should be same because it resets seed every time in auto-context mode?
        # documentation says: "If not in context, temporarily enters context for this call (auto-context mode)."
        # Auto-context mode means: enter -> op -> exit.
        # Since exit restores the seed, the next call starts from same seed.
        # Yes, auto-context means stateless operations relative to the seed.

    def test_evolve_revert(self):
        """Test seed evolution and reversion."""
        rng = StableRNG(seed=100)

        with rng:
            base_val = rng.rnd()

            rng.evolve("branch1")
            branch1_val = rng.rnd()

            rng.revert()
            reverted_val = rng.rnd()

            assert base_val == reverted_val
            assert branch1_val != base_val

            # Re-evolve
            rng.evolve("branch1")
            branch1_val_2 = rng.rnd()
            assert branch1_val == branch1_val_2

            # Stack depth
            rng.evolve("sub-branch")
            _ = rng.rnd()
            rng.revert()
            assert rng.rnd() == branch1_val  # After reverting sub-branch, we are back to branch1

    def test_reset(self):
        """Test resetting to initial seed."""
        rng = StableRNG(seed=55)
        with rng:
            v1 = rng.rnd()
            rng.evolve("step1")
            rng.rnd()
            rng.reset()
            v2 = rng.rnd()
            assert v1 == v2

    def test_rnd_floats(self):
        """Test float generation."""
        rng = StableRNG(seed=42)

        # range [0, 1)
        with rng:
            val = rng.rnd()
            assert 0.0 <= val < 1.0

            vals = rng.rnd(10)
            assert len(vals) == 10
            assert all(0.0 <= v < 1.0 for v in vals)

            mat = rng.rnd((2, 3))
            assert len(mat) == 2
            assert len(mat[0]) == 3

    def test_rnd_int(self):
        """Test integer generation."""
        rng = StableRNG(seed=42)
        with rng:
            # [0, 10)
            val = rng.rnd_int(0, 10)
            assert 0 <= val < 10

            # Check range coverage (probabilistic but with fixed seed deterministic)
            vals = rng.rnd_int(0, 5, n=100)
            assert min(vals) >= 0
            assert max(vals) < 5

            # multidimensional
            mat = rng.rnd_int(0, 10, (2, 2))
            assert len(mat) == 2

    def test_rnd_normal(self):
        """Test normal distribution generation."""
        rng = StableRNG(seed=42)
        with rng:
            val = rng.rnd_normal()
            assert isinstance(val, float)

            vals = rng.rnd_normal(mu=10, sigma=2, n=100)
            # Basic statistical check (weak)
            mean = sum(vals) / len(vals)
            assert 8 < mean < 12

    def test_rnd_float_range(self):
        """Test float range generation."""
        rng = StableRNG(seed=42)
        with rng:
            val = rng.rnd_float(1.5, 3.5)
            assert 1.5 <= val < 3.5

            vals = rng.rnd_float(1.5, 3.5, n=10)
            assert all(1.5 <= v < 3.5 for v in vals)

    def test_choice(self):
        """Test random choice."""
        rng = StableRNG(seed=42)
        options = ["a", "b", "c"]
        with rng:
            # Single
            val = rng.choice(options)
            assert val in options

            # Multiple
            vals = rng.choice(options, k=2)
            assert len(vals) == 2
            assert all(v in options for v in vals)

            # independent samples
            samples = rng.choice(options, k=2, n=3)
            assert len(samples) == 3
            assert len(samples[0]) == 2

    def test_perm(self):
        """Test permutation."""
        rng = StableRNG(seed=42)
        with rng:
            p = rng.perm(5)
            assert sorted(p) == [0, 1, 2, 3, 4]
            assert len(p) == 5

            ps = rng.perm(3, n=2)
            assert len(ps) == 2
            assert sorted(ps[0]) == [0, 1, 2]

    def test_shuffled(self):
        """Test shuffling."""
        rng = StableRNG(seed=42)
        orig = [1, 2, 3, 4, 5]
        with rng:
            s = rng.shuffled(orig)
            assert sorted(s) == sorted(orig)
            assert s != orig  # Highly likely

            # Verify original not modified
            assert orig == [1, 2, 3, 4, 5]

    def test_rnd_str(self):
        """Test string generation."""
        rng = StableRNG(seed=42)
        with rng:
            s = rng.rnd_str(10)
            assert len(s) == 10
            assert isinstance(s, str)

            ss = rng.rnd_str(5, n=3)
            assert len(ss) == 3
            assert len(ss[0]) == 5

    def test_hash_split(self):
        """Test hash split."""
        rng = StableRNG(seed=42)
        items = list(range(100))
        with rng:
            sel, rem = rng.hash_split(items, r=0.1)
            # Size should be approx 10
            assert 0 < len(sel) < 20
            # Disjoint and complete
            assert set(sel) | set(rem) == set(items)
            assert not (set(sel) & set(rem))

            # Stability check for split
            sel2, rem2 = rng.hash_split(items, r=0.1)
            # Since we are in context, generic RNG advances?
            # Wait, hash_split doc says "stable hash-based selection".
            # It uses `hash_utils` internally probably, but salted by seed?
            # rng implementation of existing utils typically evolves seed or uses current seed as salt.
            # Let's assume stability within context depends on whether arguments change, or if it consumes randomness.
            # Usually hash_split uses the rng seed as salt.
            # If it consumes randomness state, then sel2 != sel.
            # If it just uses current `seed` property as salt without advancing RNG state?
            # Rnd utils usually don't advance RNG state for hash operations unless they evolve the seed explicitly.
            # Let's check `rnd_utils.py` implementation if I could...
            # But based on "StableRNG" design, it likely uses `md5hash` with current seed.
            # If it uses current seed, and doesn't evolve it, successive calls return same result?
            # No, usually regular RG calls advance state. But `hash_split` might be different.
            # If `hash_split` is purely functional on (seed, args), then it returns same result unless we evolve.
            # Let's verify this assumption.
            assert sel == sel2

    def test_hash_sample(self):
        """Test hash sample."""
        rng = StableRNG(seed=42)
        items = [f"item_{i}" for i in range(100)]
        with rng:
            sample = rng.hash_sample(items, k=5)
            assert len(sample) == 5
            assert all(x in items for x in sample)

            # Stability check
            sample2 = rng.hash_sample(items, k=5)
            assert sample == sample2

    def test_rnd_vec(self):
        """Test random vector."""
        rng = StableRNG(seed=42)
        with rng:
            vec = rng.rnd_vec(dim=10)
            assert len(vec) == 10
            # Unit norm
            norm = math.sqrt(sum(x * x for x in vec))
            assert abs(norm - 1.0) < 1e-6

            vecs = rng.rnd_vec(dim=5, n=3)
            assert len(vecs) == 3

    def test_abm(self):
        """Test ABM."""
        rng = StableRNG(seed=42)
        with rng:
            val = rng.abm(base=100, t=10)
            assert isinstance(val, float)

            path = rng.abm_path(base=100, t=10)
            assert len(path) == 11  # 0 to 10
            assert path[0] == 100
