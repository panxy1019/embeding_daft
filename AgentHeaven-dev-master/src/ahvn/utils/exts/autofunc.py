"""LLM-assisted callable function inference utilities."""

__all__ = [
    "autofunc",
    "autofunc_prompt_composer",
    "build_autofunc_base_prompt",
]

from typing import Any, Callable, Dict, Iterable, List, Optional, Union

from ahvn.cache import CacheEntry
from ahvn.klbase.base import KLBase
from ahvn.klengine.base import BaseKLEngine
from ahvn.klstore.base import BaseKLStore
from ahvn.tool import ToolSpec
from ahvn.utils.basic.debug_utils import AutoFuncError
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.parser_utils import parse_md
from ahvn.utils.llm import LLM
from ahvn.utils.prompt import PM_AHVN, PromptSpec, fast_prompt_section, get_lang_instruction, setup_system_prompts
from ahvn.utils.exts.examples_utils import normalize_examples

logger = get_logger(__name__)

_DEFAULT_SYSTEM = "You are a skillful Python expert. Your task is to act as a function and produce output given its specification and inputs."
_DEFAULT_INSTRUCTIONS = [
    "Keep your reasoning or response as brief as possible.",
    "The final answer must be a string that supports python `repr`.",
    "Wrap the final answer in `<output></output>` tags.",
]


def _ensure_toolspec(func_spec: Union[Callable, ToolSpec]) -> ToolSpec:
    return func_spec if isinstance(func_spec, ToolSpec) else ToolSpec.from_func(func_spec)


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


def autofunc_prompt_composer(
    func_spec: Union[Callable, ToolSpec],
    *,
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    examples: Optional[
        Union[
            Iterable[Union[Dict[str, Any], CacheEntry]],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    instance: Optional[CacheEntry] = None,
    search_encoder: Optional[Callable[[CacheEntry], Dict[str, Any]]] = None,
    default_system: Optional[str] = None,
    default_descriptions: Optional[List[str]] = None,
    default_examples: Optional[
        Union[
            Iterable[Union[Dict[str, Any], CacheEntry]],
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
    resolved_spec = _ensure_toolspec(func_spec)

    examples_list = list(normalize_examples(examples, search_encoder=search_encoder, instance=instance))
    examples_list += list(normalize_examples(default_examples, search_encoder=search_encoder, instance=instance))

    desc_list = ["## Function Specification", f"```python\n{resolved_spec.code}\n```"]
    desc_list += list(default_descriptions or [])
    desc_list += _to_list(descriptions)

    inst_list = list(default_instructions or _DEFAULT_INSTRUCTIONS)
    inst_list += _to_list(instructions)
    if lang:
        inst_list.append(get_lang_instruction(lang))

    return fast_prompt_section(
        system=system or default_system or _DEFAULT_SYSTEM,
        descriptions=[d for d in desc_list if d is not None],
        examples=[example for example in examples_list if example is not None],
        instructions=[i for i in inst_list if i is not None],
        instance=instance,
        tr=translator,
    )


def build_autofunc_base_prompt() -> PromptSpec:
    spec = PM_AHVN.get("autofunc_prompt")
    if not isinstance(spec, PromptSpec):
        setup_system_prompts(force=False)
        spec = PM_AHVN.get("autofunc_prompt")
    if not isinstance(spec, PromptSpec):
        spec = PromptSpec.from_func(
            autofunc_prompt_composer,
            id="autofunc_prompt",
            version=0,
            metadata={"system": True, "fast_prompt_section": True},
        )
    return spec


def _normalize_prompt(prompt: Optional[Any]) -> PromptSpec:
    if prompt is None:
        return build_autofunc_base_prompt()
    if isinstance(prompt, PromptSpec):
        return prompt
    if hasattr(prompt, "to_spec") and callable(prompt.to_spec):
        return prompt.to_spec()
    if callable(prompt):
        return PromptSpec.from_func(prompt, id=getattr(prompt, "__name__", "autofunc_prompt"))
    raise TypeError(f"prompt must be PromptSpec/callable, got {type(prompt)}")


def autofunc(
    func_spec: Optional[Union[Callable, ToolSpec]] = None,
    prompt: Optional[Any] = None,
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    examples: Optional[
        Union[
            Iterable[Union[Dict[str, Any], CacheEntry]],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    composer: str = "autofunc",
    lang: Optional[str] = None,
    llm_args: Optional[Dict] = None,
    search_args: Optional[Dict] = None,
    capture: Optional[Dict] = None,
    **kwargs,
) -> Callable:
    del composer
    prompt_spec = _normalize_prompt(prompt)
    if capture is not None:
        capture["prompt"] = prompt_spec

    llm = LLM(**(llm_args or {}))
    search_encoder = _resolve_search_encoder(search_args)

    def _create_autofunc(spec_or_func: Union[Callable, ToolSpec]) -> Callable:
        resolved_spec = _ensure_toolspec(spec_or_func)

        def autofunc_func(
            hints: Optional[Union[str, List[str]]] = None,
            **inputs: Dict[str, Any],
        ) -> Any:
            hints_list = ([hints] if isinstance(hints, str) else hints) or []
            instance = CacheEntry.from_args(**inputs, output=..., metadata={"hints": hints_list})

            try:
                prompt_messages = prompt_spec(
                    func_spec=resolved_spec,
                    system=system,
                    descriptions=descriptions,
                    examples=examples,
                    instructions=instructions,
                    instance=instance,
                    search_encoder=search_encoder,
                    lang=lang,
                    **kwargs,
                )
            except Exception as exc:
                raise AutoFuncError(f"Failed to render autofunc prompt.\nInstance: {instance}\nError: {exc}") from exc
            logger.debug("Autofunc prompt: %s", prompt_messages)

            try:
                response = llm.oracle(prompt_messages)
            except Exception as exc:
                raise AutoFuncError(f"LLM failed for autofunc.\nPrompt: {prompt_messages}\nError: {exc}") from exc
            logger.debug("Autofunc response: %s", response)

            try:
                parsed = parse_md(response)
                output_repr = parsed.get("output", "").strip()
                try:
                    return eval(output_repr)
                except Exception as exc:
                    logger.debug("Failed to eval autofunc output repr. Returning raw output. Error: %s", exc)
                    return output_repr
            except Exception as exc:
                raise AutoFuncError(f"Failed to parse autofunc response.\nResponse: {response}\nError: {exc}") from exc

        return autofunc_func

    if func_spec is not None:
        return _create_autofunc(func_spec)

    def decorator(spec_or_func: Union[Callable, ToolSpec]) -> Callable:
        return _create_autofunc(spec_or_func)

    return decorator
