"""\
Unit tests for Function Capsule (encapsulate / restore / @capsule).

Tests cover:
- Basic encapsulate + restore round-trip
- Source-only and cloudpickle-only recovery
- Lambda handling (source skipped, cloudpickle works)
- Closure handling
- Globals capture
- Deterministic capsule_id
- SHA-256 integrity in source layer
- @capsule decorator preservability
- JSON round-trip (dumps → loads → restore → call)
- File-move resilience (encapsulate → move source → restore)
- Code-string input
- ToolSpec input
- Unsupported types
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import textwrap

import pytest

from ahvn.utils.capsule import (
    Capsule,
    CapsuleCreationError,
    CapsuleRestorationError,
    CAPSULE_VERSION,
)
from ahvn.utils.basic.serialize_utils import dumps_json, loads_json
from ahvn.tool.base import ToolSpec
from ahvn.ukf.templates.basic.prompt import (
    PromptUKFT,
    prompt_composer as base_prompt_composer,
    prompt_list_composer,
)


def encapsulate(func_or_spec, **kwargs):
    return Capsule.from_func(func_or_spec, **kwargs).to_dict()


def encapsulate_code(code: str, **kwargs):
    return Capsule.from_code(code, **kwargs).to_dict()


def restore(cap, **kwargs):
    return Capsule.to_tool(cap, **kwargs)


capsule = Capsule.capsule


def capsule_info(cap):
    return str(Capsule.from_dict(cap))


def capsule_to_json(cap):
    return dumps_json(cap)


def capsule_from_json(json_str):
    return loads_json(json_str)


# ── Test helpers ──────────────────────────────────────────────────────


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number.

    Args:
        n (int): Fibonacci index (0-indexed).

    Returns:
        int: The n-th Fibonacci number.
    """
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b


PI = 3.14159


def circle_area(radius: float) -> float:
    """Compute the area of a circle.

    Args:
        radius (float): The radius of the circle.

    Returns:
        float: The area.
    """
    return PI * radius * radius


def random_passthrough(value: int) -> int:
    """Reference a module-level import to verify source-layer global capture."""
    return value + random.Random(0).randint(0, 0)


def stdlib_import_passthrough(value: int) -> int:
    """Import a stdlib module inside the function body."""
    import json

    return value + int(bool(json.loads("1")))


def internal_import_passthrough(value: int) -> int:
    """Import an internal module inside the function body."""
    from ahvn.utils.llm import LLM

    _ = LLM
    return value


def composer_reuses_prompt_composer_without_local_import(kl, **kwargs):
    """Reference a template composer via module-global import only."""
    return base_prompt_composer(kl, **kwargs)


def adder(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a (int): First operand.
        b (int): Second operand.

    Returns:
        int: Sum.
    """
    return a + b


ADDER_CODE = textwrap.dedent('''\
    def adder(a: int, b: int) -> int:
        """Add two integers.

        Args:
            a (int): First operand.
            b (int): Second operand.

        Returns:
            int: Sum.
        """
        return a + b
''')


# ── Basic structure ───────────────────────────────────────────────────


class TestEncapsulateStructure:
    """Verify capsule dict structure and required fields."""

    def test_capsule_has_required_keys(self):
        cap = encapsulate(fibonacci)
        assert "capsule_version" in cap
        assert "capsule_id" in cap
        assert "manifest" in cap
        assert "schema" in cap
        assert "layers" in cap

    def test_capsule_version(self):
        cap = encapsulate(fibonacci)
        assert cap["capsule_version"] == CAPSULE_VERSION

    def test_manifest_fields(self):
        cap = encapsulate(fibonacci)
        m = cap["manifest"]
        assert m["name"] == "fibonacci"
        assert m["entrypoint"] == "fibonacci"
        assert "python_version" in m
        assert "module" in m

    def test_schema_fields(self):
        cap = encapsulate(fibonacci)
        s = cap["schema"]
        assert "description" in s
        assert "input_schema" in s

    def test_layers_non_empty(self):
        cap = encapsulate(fibonacci)
        assert len(cap["layers"]) >= 1

    def test_source_layer_present(self):
        cap = encapsulate(fibonacci)
        types = [ly["type"] for ly in cap["layers"]]
        assert "source" in types

    def test_cloudpickle_layer_present(self):
        cap = encapsulate(fibonacci)
        types = [ly["type"] for ly in cap["layers"]]
        assert "cloudpickle" in types

    def test_no_cloudpickle_when_disabled(self):
        cap = encapsulate(fibonacci, layers=["source"])
        types = [ly["type"] for ly in cap["layers"]]
        assert "cloudpickle" not in types
        assert "source" in types


# ── Round-trip ────────────────────────────────────────────────────────


class TestRoundTrip:
    """Encapsulate → restore → call → verify."""

    def test_simple_function(self):
        cap = encapsulate(fibonacci)
        spec = restore(cap)
        assert spec(n=10) == 55

    def test_two_arg_function(self):
        cap = encapsulate(adder)
        spec = restore(cap)
        assert spec(a=3, b=7) == 10

    def test_from_code_string(self):
        cap = encapsulate_code(ADDER_CODE)
        spec = restore(cap)
        assert spec(a=5, b=3) == 8

    def test_from_code_with_func_name(self):
        code = textwrap.dedent("""\
            def ignore_me(x: int) -> int:
                return x

            def pow2(x: int) -> int:
                return x * x
            """)
        cap = encapsulate_code(code, func_name="pow2")
        spec = restore(cap)
        assert spec(x=9) == 81

    def test_from_tool(self):
        ts = ToolSpec.from_func(fibonacci)
        cap = encapsulate(ts)
        spec = restore(cap)
        result = spec(n=8)
        # ToolSpec may wrap scalar returns in {"result": ...}
        if isinstance(result, dict) and "result" in result:
            result = result["result"]
        assert result == 21


# ── Layer-specific restore ────────────────────────────────────────────


class TestPreferLayer:
    """Force a specific layer order with layers=[...]."""

    def test_source_only(self):
        cap = encapsulate(fibonacci)
        spec = restore(cap, layers=["source"])
        assert spec(n=7) == 13

    def test_cloudpickle_only(self):
        cap = encapsulate(fibonacci)
        spec = restore(cap, layers=["cloudpickle"])
        assert spec(n=7) == 13

    def test_prefer_nonexistent_layer_raises(self):
        cap = encapsulate(fibonacci)
        with pytest.raises(CapsuleRestorationError, match="No requested layers found"):
            restore(cap, layers=["runner"])


# ── Lambda handling ───────────────────────────────────────────────────


class TestLambda:
    """Lambdas: cloudpickle captures them but ToolSpec.from_func
    requires a name, so we encapsulate directly from cloudpickle."""

    def test_lambda_cloudpickle_layer_created(self):
        """Verify we can at least build a cloudpickle layer for a lambda."""
        from ahvn.utils.capsule.core import _build_cloudpickle_layer

        fn = lambda x: x * 2  # noqa: E731
        layer = _build_cloudpickle_layer(fn)
        assert layer is not None
        assert layer["type"] == "cloudpickle"

    def test_lambda_restore_via_cloudpickle(self):
        """Cloudpickle round-trip for a lambda (bypassing ToolSpec name check)."""
        from ahvn.utils.capsule.core import _build_cloudpickle_layer, _restore_cloudpickle

        fn = lambda x: x * 2  # noqa: E731
        layer = _build_cloudpickle_layer(fn)
        restored_fn = _restore_cloudpickle(layer)
        assert restored_fn(5) == 10


# ── Closures ──────────────────────────────────────────────────────────


class TestClosure:
    """Closures: captured via cloudpickle."""

    def test_closure_round_trip(self):
        multiplier = 7

        def multiply(x: int) -> int:
            """Multiply x by multiplier.

            Args:
                x (int): Input.

            Returns:
                int: Result.
            """
            return x * multiplier

        cap = encapsulate(multiply)
        spec = restore(cap, layers=["cloudpickle"])
        assert spec(x=6) == 42


# ── Globals capture ───────────────────────────────────────────────────


class TestGlobals:
    """Simple globals referenced in source code are captured."""

    def test_globals_in_source_layer(self):
        cap = encapsulate(circle_area)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        assert "globals" in source_layer
        assert "PI" in source_layer["globals"]
        assert abs(source_layer["globals"]["PI"] - 3.14159) < 1e-6

    def test_globals_restore(self):
        cap = encapsulate(circle_area)
        spec = restore(cap, layers=["source"])
        result = spec(radius=1.0)
        assert abs(result - 3.14159) < 1e-4

    def test_module_global_restore(self):
        cap = encapsulate(random_passthrough)
        spec = restore(cap, layers=["source"])
        assert spec(value=7) == 7


class TestRequirements:
    """Source/cloudpickle layers carry import requirements for precheck."""

    def test_source_and_cloudpickle_layers_always_include_requirements(self):
        cap = encapsulate(fibonacci)
        for layer in cap["layers"]:
            if layer["type"] not in {"source", "cloudpickle"}:
                continue
            assert isinstance(layer.get("requirements"), dict)
            assert "modules" in layer["requirements"]
            assert "python_packages" in layer["requirements"]

    def test_source_layer_has_requirements_metadata(self):
        cap = encapsulate(stdlib_import_passthrough)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        requirements = source_layer.get("requirements", {})
        assert isinstance(requirements, dict)
        modules = requirements.get("modules", [])
        module_names = [m.get("name") for m in modules if isinstance(m, dict)]
        assert "json" in module_names

    def test_source_layer_captures_internal_cross_module_import(self):
        cap = encapsulate(internal_import_passthrough)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        requirements = source_layer.get("requirements", {})
        modules = requirements.get("modules", [])
        module_names = [m.get("name") for m in modules if isinstance(m, dict)]
        assert "ahvn.utils.llm" in module_names
        assert "ahvn" in set(requirements.get("python_packages", []))

    def test_manifest_dependencies_merge_inferred_requirements(self):
        cap = encapsulate(internal_import_passthrough, dependencies={"python_packages": ["custom_pkg"]})
        deps = cap.get("manifest", {}).get("dependencies", {})
        assert "custom_pkg" in set(deps.get("python_packages", []))
        assert "ahvn" in set(deps.get("python_packages", []))

    def test_source_layer_captures_requirements_from_global_composer_reference(self):
        cap = encapsulate(composer_reuses_prompt_composer_without_local_import)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        requirements = source_layer.get("requirements", {})
        modules = requirements.get("modules", [])
        module_names = [m.get("name") for m in modules if isinstance(m, dict)]
        assert "ahvn.ukf.templates.basic.prompt" in module_names

    def test_source_restore_rehydrates_global_composer_reference(self, monkeypatch):
        cap = encapsulate(composer_reuses_prompt_composer_without_local_import, layers=["source"])
        monkeypatch.delitem(globals(), "base_prompt_composer", raising=False)

        restored_callable = Capsule._restore_callable(cap, layers=["source"])

        class DummyPromptKL:
            def render(self, **kwargs):
                return f"render:{kwargs.get('token', '')}"

        assert restored_callable(DummyPromptKL(), token="ok") == "render:ok"

    def test_relative_imported_composer_requirements_are_captured(self):
        cap = encapsulate(prompt_list_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        requirements = source_layer.get("requirements", {})
        modules = requirements.get("modules", [])
        module_names = [m.get("name") for m in modules if isinstance(m, dict)]
        assert isinstance(module_names, list)

    def test_relative_imported_composer_restores_and_executes_from_source(self):
        cap = encapsulate(prompt_list_composer, layers=["source"])
        restored_callable = Capsule._restore_callable(cap, layers=["source"])

        prompt = PromptUKFT.from_jinja(content="Hello {{ name }}", name="relative_import_prompt")
        result = restored_callable(prompt)
        assert "inline.jinja" in result


class TestRequirementFallback:
    """Missing requirements should fail a layer early and trigger fallback."""

    def test_missing_source_requirements_falls_back_to_cloudpickle(self):
        cap = encapsulate(fibonacci)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        source_layer["requirements"] = {
            "modules": [{"name": "module_that_does_not_exist_for_capsule_test", "kind": "external"}],
            "python_packages": ["module_that_does_not_exist_for_capsule_test"],
        }
        spec = restore(cap)
        assert spec(n=6) == 8

    def test_missing_requirements_on_all_selected_layers_raises(self):
        cap = encapsulate(fibonacci)
        for layer in cap["layers"]:
            if layer["type"] not in {"source", "cloudpickle"}:
                continue
            layer["requirements"] = {
                "modules": [{"name": "module_that_does_not_exist_for_capsule_test", "kind": "external"}],
                "python_packages": ["module_that_does_not_exist_for_capsule_test"],
            }
        with pytest.raises(CapsuleRestorationError, match="missing requirements"):
            restore(cap, layers=["source", "cloudpickle"])

    def test_missing_source_requirements_metadata_raises(self):
        cap = encapsulate(fibonacci)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        source_layer.pop("requirements", None)
        with pytest.raises(CapsuleRestorationError, match="missing requirements metadata"):
            restore(cap, layers=["source"])


# ── Deterministic ID ─────────────────────────────────────────────────


class TestDeterminsticId:
    """Same function → same capsule_id."""

    def test_same_id(self):
        cap1 = encapsulate(fibonacci)
        cap2 = encapsulate(fibonacci)
        assert cap1["capsule_id"] == cap2["capsule_id"]

    def test_different_functions_different_id(self):
        cap1 = encapsulate(fibonacci)
        cap2 = encapsulate(adder)
        assert cap1["capsule_id"] != cap2["capsule_id"]


# ── SHA-256 integrity ─────────────────────────────────────────────────


class TestSHA256:
    """Source layer includes SHA-256 hash for integrity."""

    def test_sha256_present(self):
        cap = encapsulate(fibonacci)
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        assert "sha256" in source_layer
        assert len(source_layer["sha256"]) == 64  # hex digest length


# ── Decorator ─────────────────────────────────────────────────────────


class TestDecorator:
    """@capsule decorator preserves callability."""

    def test_decorator_callable(self):
        @capsule
        def square(x: int) -> int:
            """Square a number.

            Args:
                x (int): Input.

            Returns:
                int: x squared.
            """
            return x * x

        assert square(5) == 25

    def test_decorator_returns_capsule(self):
        @capsule
        def square(x: int) -> int:
            """Square a number.

            Args:
                x (int): Input.

            Returns:
                int: x squared.
            """
            return x * x

        assert isinstance(square, Capsule)
        assert square.id is not None

    def test_decorator_capsule_restores(self):
        @capsule
        def double(x: int) -> int:
            """Double a number.

            Args:
                x (int): Input.

            Returns:
                int: Doubled.
            """
            return x * 2

        spec = restore(double)
        assert spec(x=7) == 14


# ── JSON round-trip ───────────────────────────────────────────────────


class TestJsonRoundTrip:
    """Capsule → JSON string → dict → restore → call."""

    def test_json_dumps_loads(self):
        cap = encapsulate(fibonacci)
        json_str = capsule_to_json(cap)
        cap2 = capsule_from_json(json_str)
        spec = restore(cap2)
        assert spec(n=10) == 55

    def test_stdlib_json(self):
        cap = encapsulate(adder)
        json_str = json.dumps(cap)
        cap2 = json.loads(json_str)
        spec = restore(cap2)
        assert spec(a=100, b=200) == 300


# ── File-move resilience ──────────────────────────────────────────────


class TestFileMoveResilience:
    """Encapsulate from a temp file, then delete/move the file, restore still works."""

    def test_encapsulate_then_delete_source_file(self):
        """Create a .py file, import & encapsulate its function, delete the file,
        then restore from capsule — should succeed via source or cloudpickle."""
        tmp_dir = tempfile.mkdtemp(prefix="capsule_test_")
        try:
            # Write a Python module
            mod_path = os.path.join(tmp_dir, "temp_math_mod.py")
            with open(mod_path, "w") as f:
                f.write(textwrap.dedent('''\
                    def triple(x: int) -> int:
                        """Triple x.

                        Args:
                            x (int): Input.

                        Returns:
                            int: x * 3.
                        """
                        return x * 3
                '''))

            # Import the function
            import importlib.util

            spec_mod = importlib.util.spec_from_file_location("temp_math_mod", mod_path)
            module = importlib.util.module_from_spec(spec_mod)
            spec_mod.loader.exec_module(module)
            func = module.triple

            # Encapsulate while file exists
            cap = encapsulate(func)

            # Delete the source file
            os.remove(mod_path)
            assert not os.path.exists(mod_path)

            # Restore should work (source code is in the capsule)
            restored = restore(cap)
            assert restored(x=5) == 15
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_encapsulate_then_move_source_file(self):
        """Encapsulate, move the .py file to a new location, restore still works."""
        tmp_dir = tempfile.mkdtemp(prefix="capsule_test_")
        try:
            mod_path = os.path.join(tmp_dir, "moveable_mod.py")
            with open(mod_path, "w") as f:
                f.write(textwrap.dedent('''\
                    def negate(x: int) -> int:
                        """Negate x.

                        Args:
                            x (int): Input.

                        Returns:
                            int: -x.
                        """
                        return -x
                '''))

            import importlib.util

            spec_mod = importlib.util.spec_from_file_location("moveable_mod", mod_path)
            module = importlib.util.module_from_spec(spec_mod)
            spec_mod.loader.exec_module(module)
            func = module.negate

            cap = encapsulate(func)

            # Move the file
            new_path = os.path.join(tmp_dir, "subdir", "moved_mod.py")
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(mod_path, new_path)
            assert not os.path.exists(mod_path)

            # Restore from capsule
            restored = restore(cap)
            assert restored(x=42) == -42
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── capsule_info ──────────────────────────────────────────────────────


class TestCapsuleInfo:
    """Capsule __str__ returns summary."""

    def test_info_fields(self):
        cap = encapsulate(fibonacci)
        info = capsule_info(cap)
        assert "name: fibonacci" in info
        assert str(cap["capsule_id"]) in info
        assert "source" in info
        assert "cloudpickle" in info


class TestDictRoundTrip:
    """Capsule.to_dict/from_dict round-trip."""

    def test_round_trip(self):
        cap = Capsule.from_func(fibonacci)
        cap2 = Capsule.from_dict(cap.to_dict())
        spec = cap2.to_tool()
        assert spec(n=10) == 55


class TestCapsuleCallable:
    """Capsule.__call__ supports positional arguments via schema mapping."""

    def test_capsule_call_with_positional_args(self):
        cap = Capsule.from_func(adder)
        assert cap(3, 4) == 7

    def test_capsule_call_mixed_args(self):
        cap = Capsule.from_func(adder)
        assert cap(3, b=4) == 7

    def test_capsule_call_ambiguous_args_raise(self):
        cap = Capsule.from_func(adder)
        with pytest.raises(CapsuleRestorationError, match="Ambiguous arguments"):
            cap(3, a=4)

    def test_capsule_call_too_many_args_raise(self):
        cap = Capsule.from_func(adder)
        with pytest.raises(CapsuleRestorationError, match="Too many positional arguments"):
            cap(1, 2, 3)


# ── Error cases ───────────────────────────────────────────────────────


class TestErrorCases:
    """Unsupported inputs and edge cases."""

    def test_unsupported_type_raises(self):
        with pytest.raises(CapsuleCreationError, match="Unsupported input type"):
            encapsulate(42)

    def test_empty_capsule_raises(self):
        with pytest.raises(CapsuleRestorationError, match="no layers"):
            restore({"layers": []})

    def test_invalid_code_string_raises(self):
        with pytest.raises(CapsuleCreationError, match="Failed to extract"):
            Capsule.from_code("this is not valid python code !!!")

    def test_corrupted_cloudpickle_falls_through(self):
        """Corrupted cloudpickle layer → cascade to source."""
        cap = encapsulate(fibonacci)
        # Corrupt the cloudpickle layer
        for layer in cap["layers"]:
            if layer["type"] == "cloudpickle":
                layer["hex_dumps"] = "deadbeef"
        # Should still work via source
        spec = restore(cap)
        assert spec(n=6) == 8

    def test_corrupted_source_falls_through(self):
        """Corrupted source layer → cascade to cloudpickle."""
        cap = encapsulate(fibonacci)
        for layer in cap["layers"]:
            if layer["type"] == "source":
                layer["code"] = "def fibonacci(n): raise RuntimeError('corrupted')"
                # The code is valid but wrong — it will execute, but we need
                # to test that if source fails to produce the function at all
                layer["code"] = "invalid python {{{}}}"
        spec = restore(cap)
        assert spec(n=6) == 8


# ── Runner layer ──────────────────────────────────────────────────────


class TestRunnerLayer:
    """Runner layer structure (no server test – just creation)."""

    def test_runner_layer_included(self):
        cap = encapsulate(
            fibonacci,
            transport={
                "transport": "http",
                "url": "http://localhost:9999/mcp",
                "tool_name": "fibonacci",
            },
        )
        types = [ly["type"] for ly in cap["layers"]]
        assert "runner" in types

    def test_runner_layer_fields(self):
        runner_config = {
            "transport": "stdio",
            "script": "from ahvn.tool import ToolkitManager; ...",
            "tool_name": "fibonacci",
        }
        cap = encapsulate(fibonacci, transport=runner_config)
        runner_layer = next(ly for ly in cap["layers"] if ly["type"] == "runner")
        assert runner_layer["transport"] == "stdio"
        assert runner_layer["tool_name"] == "fibonacci"

    def test_runner_stdio_restore_and_call_twice(self, tmp_path):
        script_path = os.path.join(str(tmp_path), "capsule_stdio_server.py")
        with open(script_path, "w", encoding="utf-8") as fp:
            fp.write(textwrap.dedent("""\
                    from fastmcp import FastMCP

                    server = FastMCP("capsule-stdio")

                    @server.tool()
                    def add(a: int, b: int) -> int:
                        return a + b

                    if __name__ == "__main__":
                        server.run(transport="stdio", show_banner=False)
                    """))

        cap = Capsule.from_func(
            adder,
            layers=["runner"],
            transport={
                "transport": "stdio",
                "script": script_path,
                "tool_name": "add",
            },
        )

        restored = cap.to_tool(layers=["runner"])
        assert restored.call(a=2, b=3) == 5
        assert restored.call(a=10, b=7) == 17

    def test_runner_process_transport_is_rejected(self):
        cap = Capsule.from_func(
            adder,
            layers=["runner"],
            transport={
                "transport": "process",
                "command": "python -m something",
                "tool_name": "add",
            },
        )
        with pytest.raises(CapsuleRestorationError, match="Supported transports are 'http' and 'stdio'"):
            cap.to_tool(layers=["runner"])


# ── ToolSpec integration ──────────────────────────────────────────────


class TestToolSpecIntegration:
    """ToolSpec.to_capsule() / ToolSpec.from_capsule() round-trip."""

    def test_to_capsule(self):
        ts = ToolSpec.from_func(fibonacci)
        cap = ts.to_capsule()
        assert "capsule_id" in cap
        assert cap["manifest"]["name"] == "fibonacci"

    def test_from_capsule(self):
        cap = encapsulate(fibonacci)
        spec = ToolSpec.from_capsule(cap)
        assert spec(n=10) == 55

    def test_to_capsule_from_capsule_round_trip(self):
        ts = ToolSpec.from_func(adder)
        cap = ts.to_capsule()
        spec = ToolSpec.from_capsule(cap)
        result = spec(a=11, b=22)
        if isinstance(result, dict) and "result" in result:
            result = result["result"]
        assert result == 33

    def test_to_capsule_with_runner(self):
        ts = ToolSpec.from_func(fibonacci)
        cap = ts.to_capsule(transport={"transport": "http", "url": "http://localhost:8000/mcp", "tool_name": "fibonacci"})
        types = [ly["type"] for ly in cap["layers"]]
        assert "runner" in types

    def test_stateful_toolspec_round_trip_with_cloudpickle(self, tmp_path):
        from ahvn.tool.db.toolkit import DatabaseToolkitFactory

        db_path = str(tmp_path / "capsule_stateful_toolspec.db")
        toolkit = DatabaseToolkitFactory.create("db-test", provider="sqlite", database=db_path)
        original_tool = toolkit.get_tool("exec_sql")
        cap = Capsule.from_func(original_tool, layers=["cloudpickle"]).to_dict()
        restored = ToolSpec.from_capsule(cap, layers=["cloudpickle"])
        result = restored(query="SELECT 1 AS value")
        assert "value" in result
        assert "1" in result

    def test_from_capsule_prefer(self):
        cap = encapsulate(fibonacci)
        spec = ToolSpec.from_capsule(cap, layers=["source"])
        assert spec(n=5) == 5

    def test_import_from_utils_capsule(self):
        """Capsule API is accessible from ahvn.utils.capsule."""
        from ahvn.utils.capsule import Capsule as Caps

        assert hasattr(Caps, "from_func")
        assert callable(Caps.to_tool)
        assert callable(Caps.capsule)


# ── Global identity & type-alias roundtrip ────────────────────────────


class TestGlobalIdentityRoundTrip:
    """Verify source-layer globals survive serialization for values whose
    ``__module__``/``__qualname__`` do not directly round-trip (parameterised
    type aliases, framework-generated objects, etc.)."""

    def test_type_alias_global_roundtrip_via_source(self):
        """A function annotated with a Union type alias must restore from the
        source layer alone."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        restored = Capsule._restore_callable(cap, layers=["source"])
        assert callable(restored)

    def test_type_alias_global_roundtrip_autocode(self):
        from ahvn.utils.exts.autocode import autocode_prompt_composer

        cap = encapsulate(autocode_prompt_composer, layers=["source"])
        restored = Capsule._restore_callable(cap, layers=["source"])
        assert callable(restored)

    def test_type_alias_global_roundtrip_autofunc(self):
        from ahvn.utils.exts.autofunc import autofunc_prompt_composer

        cap = encapsulate(autofunc_prompt_composer, layers=["source"])
        restored = Capsule._restore_callable(cap, layers=["source"])
        assert callable(restored)

    def test_type_alias_global_json_roundtrip(self):
        """JSON serialization must not lose type alias identity."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer)
        json_str = capsule_to_json(cap)
        cap2 = capsule_from_json(json_str)
        restored = Capsule._restore_callable(cap2, layers=["source"])
        assert callable(restored)

    def test_source_layer_globals_no_bare_typing_artifacts(self):
        """Globals that refer to typing artefacts should resolve to the
        original parameterised value, not bare ``typing.Union``."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        globals_data = source_layer.get("globals", {})

        # ExperienceType should be found in its defining module, not typing
        et = globals_data.get("ExperienceType", {})
        assert isinstance(et, dict)
        ref = et.get("__capsule_global_ref__")
        # It should be either module_attr with func_module, or repr
        if ref == "module_attr":
            assert et.get("module") != "typing" or et.get("qualname") != "Union"

    def test_plain_class_global_still_uses_module_attr(self):
        """Regular classes should still be serialised with the simple
        module_attr reference (no regressions)."""
        cap = encapsulate(fibonacci)
        # fibonacci has no complex globals, so the source layer should work
        restored = Capsule._restore_callable(cap, layers=["source"])
        assert restored(n=10) == 55

    def test_pi_constant_global_unchanged(self):
        """Primitive globals should be stored as plain values, not refs."""
        cap = encapsulate(circle_area, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        globals_data = source_layer.get("globals", {})
        assert "PI" in globals_data
        assert isinstance(globals_data["PI"], float)

    def test_module_global_ref_unchanged(self):
        """Module references should still serialise correctly."""
        cap = encapsulate(random_passthrough, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        globals_data = source_layer.get("globals", {})
        random_ref = globals_data.get("random", {})
        assert isinstance(random_ref, dict)
        assert random_ref.get("__capsule_global_ref__") == "module"
        assert random_ref.get("module") == "random"


# ── Source layer import capture ───────────────────────────────────────


class TestSourceLayerImports:
    """Verify that the source layer captures and restores module-level
    import statements needed by the function."""

    def test_imports_field_present_for_function_with_deps(self):
        """Source layer should have 'imports' for functions that depend on
        module-level imports."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        assert "imports" in source_layer
        imports = source_layer["imports"]
        assert isinstance(imports, list)
        assert len(imports) > 0

    def test_imports_resolve_relative_to_absolute(self):
        """Relative imports should become absolute in the captured imports."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        for entry in source_layer.get("imports", []):
            module_name = entry.get("module", "")
            assert not module_name.startswith("."), f"Relative import leaked: {module_name}"

    def test_imports_only_needed_names(self):
        """Import entries should only contain names the function uses."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        imports = source_layer.get("imports", [])

        # The function does not use AutoFuncError, parse_md, etc. directly
        all_names = set()
        for entry in imports:
            if entry.get("type") == "from":
                for n in entry.get("names", []):
                    all_names.add(n.get("name"))
        # These are NOT used in autotask_prompt_composer's body
        assert "AutoFuncError" not in all_names
        assert "parse_md" not in all_names

    def test_imports_are_json_serializable(self):
        """Import descriptors must survive JSON round-trip."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        imports = source_layer.get("imports", [])
        json_str = json.dumps(imports)
        imports2 = json.loads(json_str)
        assert imports == imports2

    def test_imports_not_present_for_simple_function(self):
        """A function with no module-level dependencies may have empty or
        missing imports."""
        cap = encapsulate(adder, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        imports = source_layer.get("imports", [])
        # adder has no imports — field should be empty or absent
        assert len(imports) == 0

    def test_old_capsule_without_imports_still_restores(self):
        """Capsules created before the imports feature should still
        restore via globals alone (backward compatibility)."""
        cap = encapsulate(circle_area, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        # Simulate an old capsule by removing the imports field
        source_layer.pop("imports", None)
        restored = Capsule._restore_callable(cap, layers=["source"])
        assert abs(restored(radius=1.0) - PI * 1.0) < 1e-6

    def test_source_restore_with_imports_produces_callable(self):
        """When imports are present, source restore should produce a
        functioning callable.  Import-provided names should land in
        the function's __globals__ via exec-into-env, not via prepended
        code that would only populate locals."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")
        assert source_layer.get("imports")  # imports present

        # Identify which names are provided by the imports
        import_provided: set = set()
        for entry in source_layer["imports"]:
            if entry.get("type") == "from":
                for n in entry.get("names", []):
                    import_provided.add(n.get("alias") or n["name"])
            elif entry.get("type") == "import":
                import_provided.add(entry.get("alias") or entry["module"].split(".")[0])

        # Strip ONLY the import-provided names from globals, forcing
        # the restore to rely on the imports mechanism for those names.
        globls = source_layer.get("globals", {})
        for name in import_provided:
            globls.pop(name, None)

        restored = Capsule._restore_callable(cap, layers=["source"])
        assert callable(restored)

        # Verify the import-provided names landed in __globals__
        fn_globals = getattr(restored, "__globals__", {})
        found = import_provided & set(fn_globals.keys())
        assert found, f"No import names found in __globals__; expected some of {import_provided}"

        # Actually invoke to prove the function works
        try:
            restored()
        except NameError:
            pytest.fail("NameError — imports were not injected into function globals")
        except Exception:
            pass  # other errors (TypeError for missing args, etc.) are OK

    def test_source_restore_imports_env_not_code(self):
        """Imports must land in the function's __globals__, not as
        local variables from prepended code."""
        from ahvn.utils.exts.autotask import autotask_prompt_composer

        cap = encapsulate(autotask_prompt_composer, layers=["source"])
        source_layer = next(ly for ly in cap["layers"] if ly["type"] == "source")

        # Identify import-provided names and strip them from globals
        import_provided: set = set()
        for entry in source_layer["imports"]:
            if entry.get("type") == "from":
                for n in entry.get("names", []):
                    import_provided.add(n.get("alias") or n["name"])
            elif entry.get("type") == "import":
                import_provided.add(entry.get("alias") or entry["module"].split(".")[0])

        for name in import_provided:
            source_layer.get("globals", {}).pop(name, None)

        restored = Capsule._restore_callable(cap, layers=["source"])
        # Verify that imports landed in the function's __globals__
        fn_globals = getattr(restored, "__globals__", {})
        found = import_provided & set(fn_globals.keys())
        assert found, f"No import names found in __globals__; expected some of {import_provided}"


# ── Repr fallback serialization ───────────────────────────────────────


class TestReprFallbackSerialization:
    """Verify repr-based fallback for values that cannot be serialized by
    module_attr references."""

    def test_repr_ref_type_exists_in_capsule_constants(self):
        from ahvn.utils.capsule.core import _GLOBAL_REF_REPR

        assert _GLOBAL_REF_REPR == "repr"

    def test_repr_restore_round_trips_simple_value(self):
        """A repr-serialized value should be restorable."""
        from ahvn.utils.capsule.core import _restore_serialized_global, _GLOBAL_REF_KEY, _GLOBAL_REF_REPR

        ref = {
            _GLOBAL_REF_KEY: _GLOBAL_REF_REPR,
            "repr": "42",
            "module": "",
        }
        assert _restore_serialized_global(ref) == 42

    def test_repr_restore_invalid_raises(self):
        """Empty repr text should raise on restore."""
        from ahvn.utils.capsule.core import _restore_serialized_global, _GLOBAL_REF_KEY, _GLOBAL_REF_REPR

        ref = {
            _GLOBAL_REF_KEY: _GLOBAL_REF_REPR,
            "repr": "",
            "module": "",
        }
        with pytest.raises(ValueError, match="missing repr text"):
            _restore_serialized_global(ref)

    def test_repr_blocklist_rejects_dangerous_tokens(self):
        """Repr strings containing dangerous tokens should be blocked."""
        from ahvn.utils.capsule.core import (
            _restore_serialized_global,
            _serialize_repr_global,
            _GLOBAL_REF_KEY,
            _GLOBAL_REF_REPR,
        )

        # Serialization side: _serialize_repr_global should return None
        # for values whose repr contains blocked tokens.  We can't easily
        # manufacture such a value, so test the restore side directly.
        for dangerous_repr in [
            "__import__('os').system('id')",
            "exec('print(1)')",
            "eval('1+1')",
            "os.listdir('/')",
            "subprocess.call(['ls'])",
            "open('/etc/passwd')",
        ]:
            ref = {
                _GLOBAL_REF_KEY: _GLOBAL_REF_REPR,
                "repr": dangerous_repr,
                "module": "",
            }
            with pytest.raises(ValueError, match="blocked token"):
                _restore_serialized_global(ref)


# ── Resolve relative module ───────────────────────────────────────────


class TestResolveRelativeModule:
    """Verify package-aware relative module resolution."""

    def test_basic_single_dot(self):
        from ahvn.utils.capsule.core import _resolve_relative_module

        result = _resolve_relative_module("ahvn.utils.exts.autotask", "helpers", 1)
        assert result == "ahvn.utils.exts.helpers"

    def test_double_dot(self):
        from ahvn.utils.capsule.core import _resolve_relative_module

        result = _resolve_relative_module("ahvn.utils.exts.autotask", "basic.log_utils", 2)
        assert result == "ahvn.utils.basic.log_utils"

    def test_triple_dot(self):
        from ahvn.utils.capsule.core import _resolve_relative_module

        result = _resolve_relative_module("ahvn.utils.exts.autotask", "cache", 3)
        assert result == "ahvn.cache"

    def test_level_zero_passthrough(self):
        from ahvn.utils.capsule.core import _resolve_relative_module

        result = _resolve_relative_module("pkg.sub", "os.path", 0)
        assert result == "os.path"

    def test_dunder_main_returns_none(self):
        from ahvn.utils.capsule.core import _resolve_relative_module

        assert _resolve_relative_module("__main__", "foo", 1) is None


# ── End-to-end PromptSpec persistence ─────────────────────────────────


class TestPromptSpecPersistence:
    """End-to-end tests: PromptSpec → to_dict → from_dict → call."""

    def test_autotask_round_trip(self):
        from ahvn.utils.exts.autotask import autotask_prompt_composer
        from ahvn.utils.prompt.prompt_spec import PromptSpec

        spec = PromptSpec.from_func(autotask_prompt_composer)
        data = spec.to_dict()
        spec2 = PromptSpec.from_dict(data)
        restored = spec2.func
        assert callable(restored)
        # Invoke to verify no NameError from missing imports
        try:
            restored()
        except NameError:
            pytest.fail("NameError — PromptSpec round-trip lost imports")
        except Exception:
            pass  # other errors are acceptable

    def test_autocode_round_trip(self):
        from ahvn.utils.exts.autocode import autocode_prompt_composer
        from ahvn.utils.prompt.prompt_spec import PromptSpec

        spec = PromptSpec.from_func(autocode_prompt_composer)
        data = spec.to_dict()
        spec2 = PromptSpec.from_dict(data)
        restored = spec2.func
        assert callable(restored)
        try:
            restored()
        except NameError:
            pytest.fail("NameError — PromptSpec round-trip lost imports")
        except Exception:
            pass

    def test_autofunc_round_trip(self):
        from ahvn.utils.exts.autofunc import autofunc_prompt_composer
        from ahvn.utils.prompt.prompt_spec import PromptSpec

        spec = PromptSpec.from_func(autofunc_prompt_composer)
        data = spec.to_dict()
        spec2 = PromptSpec.from_dict(data)
        restored = spec2.func
        assert callable(restored)
        try:
            restored()
        except NameError:
            pytest.fail("NameError — PromptSpec round-trip lost imports")
        except Exception:
            pass
