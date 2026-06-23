"""
Math Toolkit Demo — ToolkitFactory + ToolkitManager
====================================================

A self-contained example showing how to define a ToolkitFactory,
register it, and manage it through the ToolkitManager.

Topics:
    1. Define plain Python functions
    2. Create a ToolkitFactory (simple form via ``tools()`` override)
    3. Create and use via ToolkitManager
    4. Run tools programmatically
    5. Inspect tool schemas
    6. Export as a Skill package
    7. Generate MCP client config / serve
"""

import json
from typing import Dict
from ahvn.tool import ToolSpec, Toolkit, ToolkitFactory, ToolkitManager, TK_AHVN, register_factory

# ── 1. Define functions ──────────────────────────────────────────────────


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def mul(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number (0-indexed: fib(0)=0, fib(1)=1, fib(2)=1, ...)."""
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


# ── 2. Define ToolkitFactory ─────────────────────────────────────────────


@register_factory
class MathToolkitFactory(ToolkitFactory):
    """Factory for creating math toolkits with basic arithmetic and fibonacci."""

    name = "math"
    description = "A simple math toolkit with add, mul, and fibonacci."

    @classmethod
    def tools(cls, **args) -> Dict[str, ToolSpec]:
        return {
            "add": ToolSpec.from_func(add),
            "mul": ToolSpec.from_func(mul),
            "fibonacci": ToolSpec.from_func(fibonacci),
        }


# ── 3. Create via ToolkitManager ─────────────────────────────────────────

if __name__ == "__main__":
    # TK_AHVN is a module-level singleton — no need to instantiate.
    # You can also use ToolkitManager() for a standalone instance.
    manager = TK_AHVN

    # Clean up any previous run
    try:
        manager.remove("math")
    except KeyError:
        pass

    toolkit = manager.create("math", "math")
    print(toolkit)
    # Toolkit(name='math', tools=[add, mul, fibonacci])

    # ── 4. Run tools ─────────────────────────────────────────────────────

    print("add(3, 5)       =", manager.run("math.add", a=3, b=5))
    print("mul(4, 7)       =", manager.run("math.mul", a=4, b=7))
    print("fibonacci(8)    =", manager.run("math.fibonacci", n=8))
    print("fibonacci(12)   =", manager.run("math.fibonacci", n=12))

    # ── 5. Inspect schemas ──────────────────────────────────────────────

    print("\n--- JSON schemas ---")
    for schema in toolkit.to_jsonschema_list():
        print(json.dumps(schema, indent=2))

    # ── 6. Export as Skill ───────────────────────────────────────────────

    import os

    skill_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
    toolkit.export(skill_dir)
    print(f"\nExported skill to: {os.path.join(skill_dir, 'math')}")

    # ── 7. MCP client config ────────────────────────────────────────────

    print("\n--- MCP client config ---")
    print(toolkit.to_mcp_json())

    # Uncomment to serve:
    # toolkit.serve()                          # http on 127.0.0.1:7001 (blocking)
    # toolkit.serve(wait=False)                # background, returns ServeHandle
    # toolkit.serve(transport="http", port=9000)
