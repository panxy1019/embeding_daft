"""Tests for auto* prompt migration to PromptSpec + fast_prompt_section."""

import pytest

from ahvn.cache import CacheEntry
from ahvn.utils.exts import autocode as autocode_mod
from ahvn.utils.exts import autofunc as autofunc_mod
from ahvn.utils.exts import autoi18n as autoi18n_mod
from ahvn.utils.exts import autotask as autotask_mod
from ahvn.utils.prompt import default_prompt_composer, experience_prompt_composer, toolspec_prompt_composer


class _StubBinded:
    name = "demo_func"


class _StubToolSpec:
    binded = _StubBinded()
    code = "def demo_func(x: int) -> int:\n    return x + 1"

    @staticmethod
    def to_func():
        def _f(x: int) -> int:
            return x + 1

        return _f


class _StubSkillSpec:
    @staticmethod
    def text():
        return "<skill><name>demo_skill</name></skill>"


def test_build_base_prompts_are_promptspec_backed():
    autocode_spec = autocode_mod.build_autocode_base_prompt()
    assert autocode_spec.id == "autocode_prompt"
    assert autocode_spec.metadata.get("fast_prompt_section") is True

    autofunc_spec = autofunc_mod.build_autofunc_base_prompt()
    assert autofunc_spec.id == "autofunc_prompt"
    assert autofunc_spec.metadata.get("fast_prompt_section") is True

    autotask_spec = autotask_mod.build_autotask_base_prompt(output_schema={"mode": "json"})
    assert autotask_spec.id == "autotask_prompt_json"
    assert autotask_spec.metadata.get("fast_prompt_section") is True


def test_autocode_prompt_composer_uses_fast_prompt_section(monkeypatch):
    monkeypatch.setattr(autocode_mod, "_ensure_toolspec", lambda _func_spec: _StubToolSpec())

    messages = autocode_mod.autocode_prompt_composer(
        func_spec=object(),
        descriptions=["extra description"],
        instructions=["extra instruction"],
    )

    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert "Task Descriptions" in content
    assert "Implement the following function" in content
    assert "def demo_func" in content
    assert "Instructions" in content


def test_autofunc_prompt_composer_uses_fast_prompt_section(monkeypatch):
    monkeypatch.setattr(autofunc_mod, "_ensure_toolspec", lambda _func_spec: _StubToolSpec())

    instance = CacheEntry.from_args(x=1, output=..., metadata={"hints": ["compute next"]})
    messages = autofunc_mod.autofunc_prompt_composer(
        func_spec=object(),
        instance=instance,
    )

    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert "Function Specification" in content
    assert "def demo_func" in content
    assert "New Instance" in content


def test_autotask_prompt_composer_modes_with_fast_prompt_section():
    instance = CacheEntry.from_args(text="hello", output=..., metadata={"hints": []})
    messages = autotask_mod.autotask_prompt_composer(
        descriptions=["convert input"],
        instance=instance,
        output_schema={"mode": "json"},
    )

    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert "Task Descriptions" in content
    assert "valid JSON object" in content
    assert "New Instance" in content


def test_default_prompt_composer_includes_skills_section_only():
    messages = default_prompt_composer(
        system="You are a helpful assistant.",
        descriptions=["Complete the task."],
        skillspecs=[_StubSkillSpec()],
        instructions=["Respond with <output></output>."],
    )

    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert "## Skills" in content
    assert "Check if any of the available skills below can help complete the task more effectively." in content
    assert "<skill><name>demo_skill</name></skill>" in content


def test_toolspec_prompt_composer_renders_sig_and_docstring():
    rendered = toolspec_prompt_composer(
        sig="demo_func(x, y=1)",
        docstring="Add two integers.\nReturn their sum.",
    )
    assert rendered.startswith("- `demo_func(x, y=1)`:")
    assert "\n    Add two integers." in rendered
    assert "\n    Return their sum." in rendered


def test_experience_prompt_composer_renders_tagged_instance():
    rendered = experience_prompt_composer(
        instance={
            "inputs": {"x": 1, "y": 2},
            "output": 3,
            "expected": 3,
            "metadata": {"hints": ["use integer math"], "notes": ["sanity check"]},
        }
    )
    assert "<instance>" in rendered
    assert "Inputs:" in rendered
    assert "<output>" in rendered
    assert "<expected>" in rendered
    assert "Notes:" in rendered


def test_autoi18n_removed_raises_runtime_error():
    with pytest.raises(RuntimeError, match="autoi18n"):
        autoi18n_mod.autoi18n()
