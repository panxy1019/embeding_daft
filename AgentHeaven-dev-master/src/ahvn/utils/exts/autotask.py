"""LLM-assisted generic task inference utilities."""

__all__ = [
    "autotask",
    "autotask_prompt_composer",
    "build_autotask_base_prompt",
]

from typing import Any, Callable, Dict, Iterable, List, Optional, Union

from ahvn.cache import CacheEntry
from ahvn.klbase.base import KLBase
from ahvn.klengine.base import BaseKLEngine
from ahvn.klstore.base import BaseKLStore
from ahvn.ukf.templates.basic.experience import ExperienceUKFT, ExperienceType
from ahvn.utils.basic.debug_utils import AutoFuncError
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.parser_utils import parse_md
from ahvn.utils.basic.serialize_utils import dumps_json, loads_json
from ahvn.utils.llm import LLM
from ahvn.utils.prompt import PM_AHVN, PromptSpec, fast_prompt_section, get_lang_instruction, setup_system_prompts
from ahvn.utils.exts.examples_utils import normalize_examples

logger = get_logger(__name__)

_DEFAULT_SYSTEM = (
    "You are a helpful AI assistant. Your task is to complete a task given its description, examples, and new inputs. "
    "Infer the task's logic from the examples and apply it to the new inputs."
)


def _to_list(value: Optional[Union[str, List[str]]]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _resolve_search_encoder(search_args: Optional[Dict[str, Any]] = None):
    if not search_args:
        return None

    def _encoder(_instance: CacheEntry):
        return dict(search_args)

    return _encoder


def _default_instructions_by_mode(mode: str) -> List[str]:
    instructions = ["Keep your reasoning or response as brief as possible."]
    if mode == "repr":
        instructions.append("The final answer must be a string that supports python `repr`.")
    elif mode == "json":
        instructions.append("The final answer must be a markdown code block containing a valid JSON object using '```json'.")
    elif mode == "code":
        instructions.append("The final answer must be a markdown code block using '```'.")
    instructions.append("Wrap the final answer in `<output></output>` tags.")
    return instructions


def autotask_prompt_composer(
    *,
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    examples: Optional[
        Union[
            Iterable[ExperienceType],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    instance: Optional[CacheEntry] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    search_encoder: Optional[Callable[[CacheEntry], Dict[str, Any]]] = None,
    default_system: Optional[str] = None,
    default_descriptions: Optional[List[str]] = None,
    default_examples: Optional[
        Union[
            Iterable[ExperienceType],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    default_instructions: Optional[List[str]] = None,
    lang: Optional[str] = None,
    tr: Optional[Callable] = None,
    **kwargs,
):
    del kwargs
    translator = tr or str
    resolved_schema = output_schema or {"mode": "base"}
    mode = str(resolved_schema.get("mode", "base"))

    desc_list = list(default_descriptions or []) + _to_list(descriptions)
    inst_list = list(default_instructions or _default_instructions_by_mode(mode))
    inst_list += _to_list(instructions)
    if lang:
        inst_list.append(get_lang_instruction(lang))

    examples_list = list(normalize_examples(examples, search_encoder=search_encoder, instance=instance))
    examples_list += list(normalize_examples(default_examples, search_encoder=search_encoder, instance=instance))

    return fast_prompt_section(
        system=system or default_system or _DEFAULT_SYSTEM,
        descriptions=[d for d in desc_list if d is not None],
        examples=[example for example in examples_list if example is not None],
        instructions=[i for i in inst_list if i is not None],
        instance=instance,
        output_schema=resolved_schema,
        tr=translator,
    )


def build_autotask_base_prompt(output_schema: Dict[str, Any]) -> PromptSpec:
    mode = "base" if output_schema is None else output_schema.get("mode", "base")
    prompt_id = "autotask_prompt" if mode == "base" else f"autotask_prompt_{mode}"

    spec = PM_AHVN.get(prompt_id)
    if not isinstance(spec, PromptSpec):
        setup_system_prompts(force=False)
        spec = PM_AHVN.get(prompt_id)
    if not isinstance(spec, PromptSpec):
        spec = PromptSpec.from_func(
            autotask_prompt_composer,
            id=prompt_id,
            version=0,
            metadata={"system": True, "fast_prompt_section": True, "output_schema": output_schema or {"mode": "base"}},
        )
    return spec


def _normalize_prompt(prompt: Optional[Any], output_schema: Optional[Dict[str, Any]]) -> PromptSpec:
    if prompt is None:
        return build_autotask_base_prompt(output_schema=output_schema or {"mode": "base"})
    if isinstance(prompt, PromptSpec):
        return prompt
    if hasattr(prompt, "to_spec") and callable(prompt.to_spec):
        return prompt.to_spec()
    if callable(prompt):
        return PromptSpec.from_func(prompt, id=getattr(prompt, "__name__", "autotask_prompt"))
    raise TypeError(f"prompt must be PromptSpec/callable, got {type(prompt)}")


def autotask(
    prompt: Optional[Any] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    system: Optional[str] = None,
    examples: Optional[
        Union[
            Iterable[Union[Dict[str, Any], CacheEntry, ExperienceUKFT]],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    composer: str = "autotask",
    lang: Optional[str] = None,
    llm_args: Optional[Dict] = None,
    search_args: Optional[Dict] = None,
    capture: Optional[Dict] = None,
    **kwargs,
) -> Callable:
    del composer
    prompt_spec = _normalize_prompt(prompt, output_schema=output_schema)
    if capture is not None:
        capture["prompt"] = prompt_spec

    resolved_schema = output_schema or prompt_spec.metadata.get("output_schema") or {"mode": "base"}
    mode = str(resolved_schema.get("mode", "base"))
    code_lang = resolved_schema.get("args", {}).get("language", "python")
    logger.debug("Autotask output schema: %s", dumps_json(resolved_schema))

    llm = LLM(**(llm_args or {}))
    search_encoder = _resolve_search_encoder(search_args)

    def autotask_func(
        hints: Optional[Union[str, List[str]]] = None,
        **inputs: Dict[str, Any],
    ) -> Any:
        hints_list = ([hints] if isinstance(hints, str) else hints) or []
        instance = CacheEntry.from_args(**inputs, output=..., metadata={"hints": hints_list})

        try:
            prompt_messages = prompt_spec(
                system=system,
                descriptions=descriptions,
                examples=examples,
                instructions=instructions,
                instance=instance,
                output_schema=resolved_schema,
                search_encoder=search_encoder,
                lang=lang,
                **kwargs,
            )
        except Exception as exc:
            raise AutoFuncError(f"Failed to render autotask prompt.\nInstance: {instance}\nError: {exc}") from exc
        logger.debug("Autotask prompt: %s", prompt_messages)

        try:
            response = llm.oracle(prompt_messages)
        except Exception as exc:
            raise AutoFuncError(f"LLM failed for autotask.\nPrompt: {prompt_messages}\nError: {exc}") from exc
        logger.debug("Autotask response: %s", response)

        try:
            parsed = parse_md(response, recurse=True)
        except Exception as exc:
            raise AutoFuncError(f"Failed to parse autotask response.\nResponse: {response}\nError: {exc}") from exc

        if mode == "base":
            return str(parsed.get("output.text", parsed.get("output", ""))).strip()
        if mode == "json":
            try:
                return loads_json(str(parsed.get("output.json", parsed.get("json", "{}"))).strip())
            except Exception as exc:
                raise AutoFuncError(f"Failed to parse autotask output JSON.\nResponse: {response}\nParsed: {dumps_json(parsed)}\nError: {exc}") from exc
        if mode == "code":
            try:
                return str(parsed.get(f"output.{code_lang}", parsed.get("code", ""))).strip()
            except Exception as exc:
                raise AutoFuncError(f"Failed to extract autotask code output.\nResponse: {response}\nParsed: {dumps_json(parsed)}\nError: {exc}") from exc
        if mode == "repr":
            try:
                return eval(str(parsed.get("output.text", parsed.get("output", ""))).strip())
            except Exception as exc:
                raise AutoFuncError(f"Failed to eval autotask repr output.\nResponse: {response}\nParsed: {dumps_json(parsed)}\nError: {exc}") from exc
        return str(parsed.get("output.text", parsed.get("output", ""))).strip()

    return autotask_func
