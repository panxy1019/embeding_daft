__all__ = [
    "fast_prompt_section",
    "default_prompt_composer",
    "toolspec_prompt_composer",
    "experience_prompt_composer",
    "get_lang_instruction",
]


from ahvn.utils.basic.str_utils import md_section, bullet_list, indent, tag_block, truncate
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.cache.base import CacheEntry
from ahvn.utils.llm.llm_utils import Messages
from typing import Any, Optional, List, Union, Dict, Callable


def fast_prompt_section(
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str], Dict[str, Union[str, List[str]]]]] = None,
    examples: Optional[List[CacheEntry]] = None,
    inputs_schema: Optional[Union[str, dict]] = None,
    output_schema: Optional[Union[str, dict]] = None,
    instructions: Optional[Union[str, List[str], Dict[str, Union[str, List[str]]]]] = None,
    instance: Optional[CacheEntry] = None,
    separate_system: bool = False,
    tr: Optional[callable] = None,
) -> Messages:
    """\
    Create a prompt section with optional system message, descriptions, examples, instructions, and instance.

    Args:
        system (str, optional): The system message to set the context for the prompt. Defaults to None.
        descriptions (Union[str, List[str], Dict[str, Union[str, List[str]]]], optional): A string, list of strings, or dictionary describing the task or providing additional context. Defaults to None.
        examples (List[CacheEntry], optional): A list of CacheEntry objects representing example interactions. Defaults to None.
        instructions (Union[str, List[str], Dict[str, Union[str, List[str]]]], optional): A string, list of strings, or dictionary providing specific instructions for the task. Defaults to None.
        inputs_schema (Union[str, dict], optional): The input schema for the examples and instance. Defaults to None.
        output_schema (Union[str, dict], optional): The output schema for the examples. Defaults to None.
        instance (CacheEntry, optional): A CacheEntry object representing a specific instance or query. Defaults to None.
        separate_system (bool): Whether to put the system message in a separate section. Defaults to False.
        tr (callable, optional): A translation function to localize the section titles and content. Defaults to None.

    Returns:
        Messages: A Messages object containing the formatted prompt sections.
    """

    messages = list()
    if tr is None:

        def tr(x):
            return str(x)

    if separate_system and (system is not None):
        messages.append({"role": "system", "content": tr(system)})
        system = None
    if not isinstance(descriptions, dict):
        descriptions = {"Task Descriptions": descriptions}
    descriptions = {title: (descs if isinstance(descs, list) else [descs] if descs is not None else []) for title, descs in descriptions.items()}
    if not isinstance(instructions, dict):
        instructions = {"Instructions": instructions}
    instructions = {title: (instrs if isinstance(instrs, list) else [instrs] if instrs is not None else []) for title, instrs in instructions.items()}
    examples = [
        (example if isinstance(example, CacheEntry) else CacheEntry.from_dict(data=example) if isinstance(example, dict) else None)
        for example in (examples or [])
    ]
    instance = instance if isinstance(instance, CacheEntry) else CacheEntry.from_dict(data=instance) if isinstance(instance, dict) else None
    sections = (
        [{"title": tr(title), "content": bullet_list([tr(desc) for desc in descs if desc is not None])} for title, descs in descriptions.items() if descs]
        + [
            (
                {
                    "title": tr("Examples"),
                    "content": "\n\n".join(
                        example.to_str(
                            inputs_schema=inputs_schema,
                            output_schema=output_schema or {"mode": "tag", "kwargs": {"schema": "repr", "inline": True}},
                            tag=f"example_{i}",
                            tr=tr,
                        )
                        for i, example in enumerate(examples, start=1)
                    ),
                }
                if examples
                else None
            )
        ]
        + [{"title": tr(title), "content": bullet_list([tr(inst) for inst in insts if inst is not None])} for title, insts in instructions.items() if insts]
        + [
            (
                {
                    "title": tr("New Instance"),
                    "content": instance.clone(output="TODO").to_str(
                        inputs_schema=inputs_schema,
                        output_schema="todo",
                        tag="new_instance",
                        tr=tr,
                    ),
                }
                if instance
                else None
            ),
        ]
    )
    prompt = md_section(
        content=tr(system) if system is not None else None,
        sections=[section for section in sections if section is not None],
    )
    messages.append({"role": "user", "content": prompt})
    return messages


def get_lang_instruction(lang: str) -> str:
    instructions = {
        "en": "Output in English.",
        "zh": "Output in Simplified Chinese.",
    }
    return instructions.get(lang, f"Output in {lang}.")


def toolspec_prompt_composer(
    sig: str,
    docstring: Optional[str] = None,
    *,
    tr: Optional[Callable] = None,
) -> str:
    del tr
    sig_text = str(sig or "").strip()
    docstring_text = docstring.strip() if isinstance(docstring, str) else ""
    if not docstring_text:
        return f"- `{sig_text}`"
    return f"- `{sig_text}`:\n{indent(docstring_text, 4)}"


def experience_prompt_composer(
    instance: Union[CacheEntry, Dict[str, Any]],
    input_schema: Optional[Dict[str, Any]] = None,
    inputs_schema: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Union[str, Dict[str, Any]]] = None,
    tag: Optional[str] = "instance",
    idx: Optional[int] = None,
    cutoff: Optional[int] = None,
    instance_todo: bool = False,
    *,
    tr: Optional[Callable] = None,
) -> str:
    translator = tr or str
    entry = instance if isinstance(instance, CacheEntry) else CacheEntry.from_dict(data=dict(instance))
    default_output_schema: Union[str, Dict[str, Any]] = {"mode": "tag", "kwargs": {"schema": "repr", "inline": False}}
    resolved_output_schema: Union[str, Dict[str, Any]]
    if instance_todo:
        entry = entry.clone(output=..., expected=...)
        resolved_output_schema = {"mode": "tag", "kwargs": {"schema": "todo", "inline": False}}
    else:
        resolved_output_schema = output_schema if output_schema is not None else default_output_schema
    body = entry.to_str(
        inputs_schema=input_schema or inputs_schema,
        output_schema=resolved_output_schema,
        tag=None,
        tr=translator,
    )
    if isinstance(cutoff, int) and cutoff > 0:
        body = truncate(body, cutoff=cutoff)
    if not tag:
        return body
    tag_name = f"{tag}_{idx}" if idx is not None else str(tag)
    return tag_block(tag=tag_name, content=body, inline=False)


def _render_skillspec_section(
    skillspecs: Optional[List[Any]],
    tr: callable,
) -> Optional[Dict[str, str]]:
    skillspecs = [skill for skill in (skillspecs or []) if skill is not None]
    if not skillspecs:
        return None

    rendered_skills = []
    for skill in skillspecs:
        rendered = None
        try:
            if hasattr(skill, "text") and callable(skill.text):
                rendered = skill.text()
            elif hasattr(skill, "to_prompt") and callable(skill.to_prompt):
                rendered = skill.to_prompt()
        except Exception:
            rendered = None
        if rendered is None:
            rendered = str(skill)
        rendered = str(rendered).strip()
        if rendered:
            rendered_skills.append(rendered)

    if not rendered_skills:
        return None

    guidance = [
        tr("Skills are a series of documents that provide specialized knowledge or capabilities to help complete the task."),
        tr("Check if any of the available skills below can help complete the task more effectively."),
        tr(
            'To view a skill, call tool `Skill(skill_name: str, path: Optional[str] = "SKILL.md")`, where `skill_name` is the skill name and `path` is the path to the specific resource within the skill, which defaults to `SKILL.md` if not provided.'
        ),
        tr(
            "The resources structure within each skill will be provided upon calling the skill. Do not invoke a skill that is already loaded or not listed below."
        ),
        tr("Here are some skills potentially useful for completing the task:"),
    ]
    content = "\n\n".join(
        [
            bullet_list(guidance),
            bullet_list(rendered_skills),
        ]
    )
    return {"title": tr("Skills"), "content": content}


def default_prompt_composer(
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str], Dict[str, Union[str, List[str]]]]] = None,
    toolspecs: Optional[List[Any]] = None,
    skillspecs: Optional[List[Any]] = None,
    examples: Optional[List[CacheEntry]] = None,
    inputs_schema: Optional[Union[str, dict]] = None,
    output_schema: Optional[Union[str, dict]] = None,
    instructions: Optional[Union[str, List[str], Dict[str, Union[str, List[str]]]]] = None,
    instance: Optional[CacheEntry] = None,
    separate_system: bool = False,
    tr: Optional[callable] = None,
    **kwargs,
) -> Messages:
    """PromptSpec-native default prompt composer with skill sections."""
    del kwargs
    # TODO: toolspecs are currently passed via LLM's tools args, not embedded in prompts.
    #       In the future, default_prompt_composer should support natural language toolspec
    #       rendering for prompt-based tool descriptions.
    del toolspecs
    messages = fast_prompt_section(
        system=system,
        descriptions=descriptions,
        examples=examples,
        inputs_schema=inputs_schema,
        output_schema=output_schema,
        instructions=instructions,
        instance=instance,
        separate_system=separate_system,
        tr=tr,
    )
    if not messages:
        return messages

    tr = tr or (lambda x: str(x))
    extra_sections = []
    skills_section = _render_skillspec_section(skillspecs=skillspecs, tr=tr)
    if skills_section:
        extra_sections.append(skills_section)
    if not extra_sections:
        return messages

    content = messages[-1].get("content")
    if not isinstance(content, str):
        return messages
    messages[-1]["content"] = md_section(content=content.strip(), sections=extra_sections).strip()
    return messages
