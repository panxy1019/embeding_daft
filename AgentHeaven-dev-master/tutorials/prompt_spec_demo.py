"""Minimal PromptSpec demo: function, from_str, from_jinja, and PM_AHVN retrieval.

Run:
    python tutorials/prompt_spec_demo.py
"""

from typing import Callable

from ahvn.utils.prompt import PM_AHVN, PromptSpec


@PromptSpec.prompt(id="demo_func_prompt", version=0)
def greet(name: str, *, tr: Callable = str) -> str:
    return f"{tr('Hello')}, {name}!"


def main() -> None:
    # Function-style prompt.
    greet.tr.set("Hello", "zh", "你好")
    print("1) from_func")
    print("en:", greet("Alice"))
    print("zh:", greet("Alice", lang="zh"))
    print()

    # Python format-string prompt.
    fast = PromptSpec.from_str(
        "Task: {task}\nLanguage: {language}",
        id="demo_str_prompt",
        version=0,
        trs=["task", "language"],
    )
    fast.tr.set("Task: {task}\nLanguage: {language}", "zh", "任务: {task}\n语言: {language}")
    fast.tr.set("Summarize logs", "zh", "总结日志")
    fast.tr.set("Chinese", "zh", "中文")

    print("2) from_str")
    print("en:", fast(task="Summarize logs", language="Chinese"))
    print("zh:", fast(task="Summarize logs", language="Chinese", lang="zh"))
    print()

    # In-memory jinja prompt (no filesystem, no Babel).
    jinja_prompt = PromptSpec.from_jinja(
        """
{{ "Skills" | tr }}:
{% for skill in skills %}
- {{ skill | tr }}
{% endfor %}
{{ "Owner" | tr }}: {{ owner }}
""".strip(),
        id="demo_jinja_prompt",
        version=0,
    )
    jinja_prompt.tr.set("Skills", "zh", "技能")
    jinja_prompt.tr.set("Owner", "zh", "负责人")
    jinja_prompt.tr.set("Plan", "zh", "规划")
    jinja_prompt.tr.set("Implement", "zh", "实现")

    print("3) from_jinja")
    print("en:")
    print(jinja_prompt(skills=["Plan", "Implement"], owner="Alice"))
    print("zh:")
    print(jinja_prompt(skills=["Plan", "Implement"], owner="Alice", lang="zh"))
    print()

    # Retrieve from the global prompt manager with language frozen.
    fixed_zh = PM_AHVN.get("demo_jinja_prompt", version=0, lang="zh")
    if fixed_zh is None:
        raise RuntimeError("demo_jinja_prompt not found in PM_AHVN")

    print("4) PM_AHVN.get(lang='zh')")
    print(fixed_zh(skills=["Plan", "Implement"], owner="Bob"))


if __name__ == "__main__":
    main()
