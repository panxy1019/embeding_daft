"""Demo: PM_AHVN built-in prompts with zh translations."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Union

from ahvn.utils.prompt import PM_AHVN, PromptSpec, setup_system_prompts


class _DemoSkill:
    def text(self) -> str:
        return "<skill><name>demo_skill</name><description>Demo skill for rendering.</description></skill>"


def _demo_add(x: int, y: int) -> int:
    """Add two integers."""
    return x + y


def _to_text(rendered: Union[str, Iterable[Dict[str, Any]]]) -> str:
    if isinstance(rendered, str):
        return rendered
    blocks = []
    for message in rendered:
        content = message.get("content")
        if isinstance(content, str):
            blocks.append(content)
    return "\n\n".join(blocks)


def _render(prompt_id: str, **kwargs) -> str:
    prompt = PM_AHVN.get(prompt_id, version=0)
    if not isinstance(prompt, PromptSpec):
        raise RuntimeError(f"Prompt '{prompt_id}' is not available in PM_AHVN.")
    return _to_text(prompt(lang="zh", **kwargs))


def main() -> None:
    setup_system_prompts(force=True)

    print("=" * 80)
    print("default_prompt (zh)")
    print("=" * 80)
    print(
        _render(
            "default_prompt",
            system="You are a practical coding assistant.",
            descriptions=["Solve the user request."],
            instructions=["Reply with concise actionable steps."],
            skillspecs=[_DemoSkill()],
            instance={"inputs": {"task": "demo"}, "metadata": {}},
        )
    )
    print()

    print("=" * 80)
    print("translation_prompt (zh)")
    print("=" * 80)
    print(
        _render(
            "translation_prompt",
            source_lang="English",
            target_lang="Chinese",
            content="Hello, {name}! Welcome to AgentHeaven.",
        )
    )
    print()

    print("=" * 80)
    print("autocode_prompt (zh)")
    print("=" * 80)
    try:
        print(_render("autocode_prompt", func_spec=_demo_add))
    except ModuleNotFoundError as exc:
        print(f"[SKIPPED] autocode_prompt demo requires optional dependency: {exc}")
    print()

    print("=" * 80)
    print("autofunc_prompt (zh)")
    print("=" * 80)
    try:
        print(_render("autofunc_prompt", func_spec=_demo_add, instance={"inputs": {"x": 1, "y": 2}, "metadata": {}}))
    except ModuleNotFoundError as exc:
        print(f"[SKIPPED] autofunc_prompt demo requires optional dependency: {exc}")
    print()

    print("=" * 80)
    print("toolspec_prompt (zh)")
    print("=" * 80)
    print(
        _render(
            "toolspec_prompt",
            sig="demo_add(x: int, y: int) -> int",
            docstring="Add two integers.",
        )
    )
    print()

    print("=" * 80)
    print("experience_prompt (zh)")
    print("=" * 80)
    print(
        _render(
            "experience_prompt",
            instance={
                "inputs": {"x": 1, "y": 2},
                "output": 3,
                "metadata": {"hints": ["Ensure deterministic result."], "notes": ["Sample note."]},
            },
        )
    )
    print()

    autotask_modes = [
        ("autotask_prompt", {"mode": "base"}),
        ("autotask_prompt_base", {"mode": "base"}),
        ("autotask_prompt_repr", {"mode": "repr"}),
        ("autotask_prompt_json", {"mode": "json", "args": {"indent": 2}}),
        ("autotask_prompt_code", {"mode": "code", "args": {"language": "python"}}),
    ]
    for prompt_id, output_schema in autotask_modes:
        print("=" * 80)
        print(f"{prompt_id} (zh)")
        print("=" * 80)
        print(
            _render(
                prompt_id,
                descriptions=["Transform input according to examples."],
                output_schema=output_schema,
                instance={"inputs": {"text": "demo"}, "metadata": {}},
            )
        )
        print()


if __name__ == "__main__":
    main()
