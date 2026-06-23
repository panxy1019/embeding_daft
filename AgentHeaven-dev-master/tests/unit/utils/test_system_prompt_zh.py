"""Tests for zh translations on PM_AHVN built-in prompts."""

from typing import Any, Dict, Iterable, Union

from ahvn.utils.prompt import PromptSpec, PM_AHVN, setup_system_prompts
from ahvn.utils.prompt.prompt_spec import _PROMPT_REGISTRY
from ahvn.utils.prompt.prompt_store import PromptStore
from ahvn.utils.prompt.translate import TranslationDict, TranslationStore


def _patch_prompt_backends(monkeypatch, tmp_path):
    prompt_store = PromptStore(provider="sqlite", database=f"file:{tmp_path / 'system_prompt_zh.db'}")
    tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "system_prompt_zh_tr.db"))
    monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)
    monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)
    return prompt_store, tr_store


def _to_text(rendered: Union[str, Iterable[Dict[str, Any]]]) -> str:
    if isinstance(rendered, str):
        return rendered
    blocks = []
    for message in rendered:
        content = message.get("content")
        if isinstance(content, str):
            blocks.append(content)
    return "\n".join(blocks)


class _DummySkill:
    def text(self) -> str:
        return "<skill><name>dummy_skill</name></skill>"


def _demo_add(x: int, y: int) -> int:
    return x + y


def test_setup_system_prompts_registers_all_builtin_prompt_ids(monkeypatch, tmp_path):
    _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()

    registered = setup_system_prompts(force=True)
    assert set(registered.keys()) == {
        "default_prompt",
        "translation_prompt",
        "toolspec_prompt",
        "experience_prompt",
        "autocode_prompt",
        "autofunc_prompt",
        "autotask_prompt",
        "autotask_prompt_base",
        "autotask_prompt_repr",
        "autotask_prompt_json",
        "autotask_prompt_code",
    }
    assert all(isinstance(spec, PromptSpec) for spec in registered.values())
    assert all(spec.version == 0 for spec in registered.values())


def test_system_prompts_render_in_chinese(monkeypatch, tmp_path):
    _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()
    setup_system_prompts(force=True)

    cases = [
        (
            "default_prompt",
            {
                "system": "You are a test assistant.",
                "descriptions": ["Solve the task."],
                "instructions": ["Reply in one line."],
                "skillspecs": [_DummySkill()],
            },
            "任务描述",
        ),
        (
            "translation_prompt",
            {
                "source_lang": "English",
                "target_lang": "Chinese",
                "content": "Hello world",
            },
            "将以下文本从 English 翻译为 Chinese。",
        ),
        (
            "toolspec_prompt",
            {
                "sig": "add(x, y=2)",
                "docstring": "Add two numbers.",
            },
            "- `add(x, y=2)`",
        ),
        (
            "experience_prompt",
            {
                "instance": {
                    "inputs": {"x": 1, "y": 2},
                    "output": 3,
                    "metadata": {"hints": ["Hint"]},
                },
            },
            "输入",
        ),
        (
            "autocode_prompt",
            {
                "func_spec": _demo_add,
            },
            "实现以下函数",
        ),
        (
            "autofunc_prompt",
            {
                "func_spec": _demo_add,
            },
            "函数规格",
        ),
        (
            "autotask_prompt",
            {},
            "请将最终答案包裹在 `<output></output>` 标签中。",
        ),
        (
            "autotask_prompt_base",
            {
                "output_schema": {"mode": "base"},
            },
            "请将最终答案包裹在 `<output></output>` 标签中。",
        ),
        (
            "autotask_prompt_repr",
            {
                "output_schema": {"mode": "repr"},
            },
            "支持 Python `repr` 的字符串",
        ),
        (
            "autotask_prompt_json",
            {
                "output_schema": {"mode": "json"},
            },
            "合法 JSON 对象",
        ),
        (
            "autotask_prompt_code",
            {
                "output_schema": {"mode": "code"},
            },
            "markdown 代码块",
        ),
    ]

    for prompt_id, kwargs, expected in cases:
        prompt = PM_AHVN.get(prompt_id, version=0)
        assert isinstance(prompt, PromptSpec)
        rendered = prompt(lang="zh", **kwargs)
        content = _to_text(rendered)
        assert expected in content, f"{prompt_id} did not include expected zh text"


def test_system_prompt_zh_translations_are_seeded(monkeypatch, tmp_path):
    _prompt_store, tr_store = _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()
    setup_system_prompts(force=True)

    default_td = TranslationDict(namespace="default_prompt", store=tr_store)
    autocode_td = TranslationDict(namespace="autocode_prompt", store=tr_store)
    translation_td = TranslationDict(namespace="translation_prompt", store=tr_store)
    experience_td = TranslationDict(namespace="experience_prompt", store=tr_store)

    assert default_td.lookup("Task Descriptions", "zh") == "任务描述"
    assert default_td.lookup("Skills", "zh") == "技能"
    assert autocode_td.lookup("Implement the following function:\n```python\n{impl_block}\n```", "zh") == "实现以下函数：\n```python\n{impl_block}\n```"
    assert translation_td.lookup("Rules:", "zh") == "规则："
    assert experience_td.lookup("Inputs", "zh") == "输入"
