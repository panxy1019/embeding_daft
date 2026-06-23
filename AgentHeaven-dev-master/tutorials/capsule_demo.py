"""
Capsule Demo — Function Capsule system walkthrough
===================================================

Demonstrates Capsule.from_func / Capsule callable / @Capsule.capsule / CP_AHVN /
auto-store to global ``~/.ahvn/capsules.db``.

Run: ``python -m tutorials.capsule_demo``
"""

from ahvn.utils.capsule import (
    Capsule,
    CP_AHVN,
)
from ahvn.utils.basic.serialize_utils import dumps_json, loads_json

# ── 1. Define functions ──────────────────────────────────────────────


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number (0-indexed)."""
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


@Capsule.capsule
def greet(name: str, greeting: str = "Hello") -> str:
    """Greet someone by name."""
    return f"{greeting}, {name}!"


# ── 2. Encapsulate & restore round-trip ──────────────────────────────


def demo_round_trip():
    print("=" * 50)
    print("1. Encapsulate / Restore round-trip")
    print("=" * 50)

    cap = Capsule.from_func(fibonacci)
    print(f"   Capsule ID : {str(cap.id)[:12]}...")
    print(f"   Layers     : {[ly['type'] for ly in cap.to_dict()['layers']]}")

    # Capsule is directly callable
    result = cap(n=10)
    print(f"   cap(n=10)   = {result}")
    assert result == 55 or result == {"result": 55}

    # Or promote to ToolSpec explicitly
    spec = cap.to_tool()
    result2 = spec(n=10)
    print(f"   spec(n=10)  = {result2}")
    print()


# ── 3. @capsule decorator (auto-stores to global DB) ────────────────


def demo_decorator():
    print("=" * 50)
    print("2. @capsule decorator (auto-stores to global DB)")
    print("=" * 50)

    # The function works normally
    print(f"   greet('World') = {greet('World')}")

    # The decorated symbol is itself a Capsule
    cap = greet
    print(f"   Capsule ID : {str(cap.id)[:12]}...")
    print(f"   Qualname   : {cap.qualname}")
    print(f"   Checksum   : {str(cap.checksum or 'N/A')[:16]}...")

    # The capsule was auto-stored to ~/.ahvn/capsules.db
    loaded = CP_AHVN.get(str(cap.id))
    assert loaded is not None, "Capsule should be auto-stored in global DB!"
    print("   Auto-stored: YES (found in global DB)")

    # Restore from the capsule object
    spec = cap.to_tool()
    result = spec(name="Developer")
    print(f"   Restored greet('Developer') = {result}")
    print()


# ── 4. JSON export / import ──────────────────────────────────────────


def demo_json():
    print("=" * 50)
    print("3. JSON export / import")
    print("=" * 50)

    cap = Capsule.from_func(fibonacci).to_dict()
    json_str = dumps_json(cap)
    print(f"   JSON size  : {len(json_str)} chars")

    # Round-trip through JSON
    cap2 = loads_json(json_str)
    spec = Capsule.from_dict(cap2).to_tool()
    result = spec(n=20)
    print(f"   fibonacci(20) after JSON round-trip = {result}")
    print()


# ── 5. CP_AHVN — global capsule manager ──────────────────────────────


def demo_store():
    print("=" * 50)
    print("4. CP_AHVN — global capsule manager")
    print("=" * 50)

    # Add functions directly via CP_AHVN
    cid1 = CP_AHVN.add(fibonacci, tags=["math"])
    cid2 = CP_AHVN.add(greet, tags=["utils"])

    # List
    items = CP_AHVN.list()
    print(f"   Stored capsules: {len(items)}")
    for item in items:
        print(f"     - {item.name:12s}  id={str(item.id)[:12]}...")

    # Get & restore
    cap1 = CP_AHVN.get(cid1)
    assert cap1 is not None
    spec = cap1.to_tool()
    result = spec(n=15)
    print(f"   CP_AHVN.get → to_tool → fibonacci(15) = {result}")

    cap2 = CP_AHVN.get(cid2)
    assert cap2 is not None
    spec2 = cap2.to_tool()
    result2 = spec2(name="Alice", greeting="Hi")
    print(f"   CP_AHVN.get → to_tool → greet(...) = {result2}")

    # Search
    found = CP_AHVN.search(name="fib")
    print(f"   search('fib') → {len(found)} match(es)")
    print()


# ── 6. File-move resilience (source layer) ───────────────────────────


def demo_file_resilience():
    print("=" * 50)
    print("5. File-move resilience")
    print("=" * 50)

    # Encapsulate captures the source code inside the capsule.
    # Even if the original .py file is moved or deleted, the capsule
    # can restore the function from its stored source code.
    cap = Capsule.from_func(fibonacci)
    d = cap.to_dict()
    source_layer = next(ly for ly in d["layers"] if ly["type"] == "source")
    print(f"   Source layer has {len(source_layer['code'])} chars of code")
    print(f"   SHA-256     : {source_layer['sha256'][:16]}...")

    # Restore purely from source (ignore cloudpickle)
    spec = Capsule.from_dict(d).to_tool(layers=["source"])
    result = spec(n=30)
    print(f"   Restored from source only → fibonacci(30) = {result}")
    print()


# ── 7. ToolSpec integration ──────────────────────────────────────────


def demo_toolspec():
    print("=" * 50)
    print("6. ToolSpec integration")
    print("=" * 50)

    from ahvn.tool import ToolSpec

    spec = ToolSpec.from_func(fibonacci)
    cap = spec.to_capsule()
    print(f"   ToolSpec.to_capsule() → {cap['capsule_id'][:12]}...")

    spec2 = ToolSpec.from_capsule(cap)
    result = spec2(n=12)
    print(f"   ToolSpec.from_capsule() → fibonacci(12) = {result}")
    print()


# ── 8. Code-string encapsulation ─────────────────────────────────────


def demo_code_string():
    print("=" * 50)
    print("7. Encapsulate from code string")
    print("=" * 50)

    code = '''
def square(x: int) -> int:
    """Return x squared."""
    return x * x
'''
    cap = Capsule.from_func(code)
    d = cap.to_dict()
    print(f"   Capsule from code string → {d['manifest']['name']}")

    spec = Capsule.from_dict(d).to_tool()
    result = spec(x=7)
    print(f"   square(7) = {result}")
    print()


# ── 8. Global capsule DB and qualname identity ──────────────────────


def demo_global_db():
    print("=" * 50)
    print("8. CP_AHVN — global capsule DB")
    print("=" * 50)

    # CP_AHVN is a module-level singleton wrapping the global capsule store.
    # No need to instantiate anything — just import and use.

    # List all capsules saved so far
    items = CP_AHVN.list()
    print(f"   Total capsules in global DB: {len(items)}")
    for item in items:
        print(f"     - {item.name:12s}  qualname={item.qualname}")

    # Search by keyword
    results = CP_AHVN.search("fib")
    print(f"   Search 'fib': {len(results)} match(es)")

    # Stale check
    stale = CP_AHVN.stale()
    print(f"   Stale capsules (missing source): {len(stale)}")

    print()
    print("   TIP: Run 'ahvn capsule ls' to browse capsules from the CLI.")
    print()


# ── Main ─────────────────────────────────────────────────────────────


def run_demo():
    print("\n  Function Capsule Demo")
    print("  " + "─" * 48 + "\n")
    demo_round_trip()
    demo_decorator()
    demo_json()
    demo_store()
    demo_file_resilience()
    demo_toolspec()
    demo_code_string()
    demo_global_db()
    print("All demos completed successfully.\n")


if __name__ == "__main__":
    run_demo()
