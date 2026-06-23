"""LLM-assisted static code generation utilities."""

__all__ = [
    "autocode",
    "autocode_prompt_composer",
    "build_autocode_base_prompt",
]

from typing import Any, Callable, Dict, List, Optional, Union

from ahvn.cache import CacheEntry
from ahvn.klbase.base import KLBase
from ahvn.klengine.base import BaseKLEngine
from ahvn.klstore.base import BaseKLStore
from ahvn.tool import ToolSpec
from ahvn.utils.basic.debug_utils import AutoFuncError
from ahvn.utils.basic.func_utils import code2func, funcwrap
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.parser_utils import parse_md
from ahvn.utils.llm import LLM
from ahvn.utils.prompt import PM_AHVN, PromptSpec, fast_prompt_section, get_lang_instruction, setup_system_prompts
from ahvn.utils.exts.examples_utils import ExampleSource, normalize_examples

logger = get_logger(__name__)

_DEFAULT_SYSTEM = (
    "You are a skillful Python expert. Your task is to generate a complete Python function implementation " "based on the provided signature and test cases."
)
_DEFAULT_INSTRUCTIONS = [
    "Analyze the function signature and test cases to understand the required logic.",
    "Generate a complete Python function implementation that passes all the test cases.",
    "Preserve the exact function signature including name, parameters, type hints, and return type.",
    "Include necessary imports at the top level if needed.",
    "DO NOT include the test assertions in your output - only generate the function implementation.",
    "Wrap the complete Python code in a single markdown 'python' code block.",
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


def autocode_prompt_composer(
    func_spec: Union[Callable, ToolSpec],
    *,
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    examples: Optional[ExampleSource] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    default_system: Optional[str] = None,
    default_descriptions: Optional[List[str]] = None,
    default_examples: Optional[ExampleSource] = None,
    default_instructions: Optional[List[str]] = None,
    lang: Optional[str] = None,
    search_encoder: Optional[Callable[[CacheEntry], Dict[str, Any]]] = None,
    tr: Optional[Callable] = None,
    **kwargs,
):
    del kwargs
    translator = tr or str
    resolved_spec = _ensure_toolspec(func_spec)

    examples_list = list(normalize_examples(examples, search_encoder=search_encoder))
    examples_list += list(normalize_examples(default_examples, search_encoder=search_encoder))

    if examples_list:
        from ...ukf.templates.basic.experience import ExperienceUKFT

        assertions = [ExperienceUKFT.from_cache_entry(example).text(composer="assertion") for example in examples_list]
        impl_block = resolved_spec.code + f"\n\n# {translator('Test cases that your implementation must pass:')}\n" + "\n".join(assertions)
    else:
        impl_block = resolved_spec.code

    desc_list = [translator("Implement the following function:\n```python\n{impl_block}\n```").format(impl_block=impl_block.strip())]
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
        tr=translator,
    )


def build_autocode_base_prompt() -> PromptSpec:
    spec = PM_AHVN.get("autocode_prompt")
    if not isinstance(spec, PromptSpec):
        setup_system_prompts(force=False)
        spec = PM_AHVN.get("autocode_prompt")
    if not isinstance(spec, PromptSpec):
        spec = PromptSpec.from_func(
            autocode_prompt_composer,
            id="autocode_prompt",
            version=0,
            metadata={"system": True, "fast_prompt_section": True},
        )
    return spec


def _normalize_prompt(prompt: Optional[Any]) -> PromptSpec:
    if prompt is None:
        return build_autocode_base_prompt()
    if isinstance(prompt, PromptSpec):
        return prompt
    if hasattr(prompt, "to_spec") and callable(prompt.to_spec):
        return prompt.to_spec()
    if callable(prompt):
        return PromptSpec.from_func(prompt, id=getattr(prompt, "__name__", "autocode_prompt"))
    raise TypeError(f"prompt must be PromptSpec/callable, got {type(prompt)}")


def autocode(
    func_spec: Optional[Union[Callable, ToolSpec]] = None,
    prompt: Optional[Any] = None,
    system: Optional[str] = None,
    descriptions: Optional[Union[str, List[str]]] = None,
    examples: Optional[
        Union[
            List[Union[Dict[str, Any], CacheEntry]],
            BaseKLStore,
            BaseKLEngine,
            KLBase,
        ]
    ] = None,
    instructions: Optional[Union[str, List[str]]] = None,
    env: Optional[Dict] = None,
    composer: str = "autocode",
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

    def _build_autocode(resolved_spec: Union[Callable, ToolSpec]) -> Callable:
        tool_spec = _ensure_toolspec(resolved_spec)
        func_name = tool_spec.binded.name

        def autocode_function(*args, **func_kwargs) -> Any:
            try:
                prompt_messages = prompt_spec(
                    func_spec=tool_spec,
                    system=system,
                    descriptions=descriptions,
                    examples=examples,
                    instructions=instructions,
                    lang=lang,
                    search_encoder=search_encoder,
                    **kwargs,
                )
            except Exception as exc:
                raise AutoFuncError(f"Failed to render prompt for autocode.\nError: {exc}") from exc
            logger.debug("Autocode prompt: %s", prompt_messages)

            try:
                response = llm.oracle(prompt_messages)
            except Exception as exc:
                raise AutoFuncError(f"LLM failed for autocode.\nPrompt: {prompt_messages}\nError: {exc}") from exc
            logger.debug("Autocode response: %s", response)

            try:
                parsed = parse_md(response)
                code_block = parsed.get("python", "").strip()
                if not code_block:
                    raise ValueError("No python code block found in response")
            except Exception as exc:
                raise AutoFuncError(f"Failed to parse generated code.\nResponse: {response}") from exc

            try:
                generated_func = code2func(code=code_block, func_name=func_name, env=env)
                if generated_func is None or not callable(generated_func):
                    raise ValueError(f"No callable '{func_name}' found in generated code")
                return generated_func(*args, **func_kwargs)
            except Exception as exc:
                raise AutoFuncError(f"Failed to execute generated code.\nCode:\n{code_block}\nError: {exc}") from exc

        return funcwrap(exec_func=autocode_function, sig_func=tool_spec.to_func())

    if func_spec is not None:
        return _build_autocode(func_spec)

    def decorator(spec_or_func: Union[Callable, ToolSpec]) -> Callable:
        return _build_autocode(spec_or_func)

    return decorator
