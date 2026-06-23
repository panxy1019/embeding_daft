__all__ = [
    "PromptUKFT",
    "PromptType",
    "prompt_composer",
    "prompt_list_composer",
]

from typing import Any, Callable, ClassVar, Dict, List, Optional, Union

from ...base import BaseUKF, ptags
from ...registry import register_ukft
from ....utils.prompt import PromptSpec


def prompt_composer(kl, **kwargs):
    """Compose prompt output by executing the wrapped PromptSpec."""
    return kl.render(**kwargs)


def prompt_list_composer(kl, **kwargs):
    """List available logical template slots for this PromptSpec-backed prompt."""
    spec = kl.to_spec()
    spec_type = spec.prompt_type
    if spec_type == "jinja":
        return "- inline.jinja"
    if spec_type == "template":
        return "- inline.template"
    return "- inline.prompt"


@register_ukft
class PromptUKFT(BaseUKF):
    """UKF wrapper for PromptSpec payloads."""

    type_default: ClassVar[str] = "prompt"

    @classmethod
    def from_spec(
        cls,
        prompt_spec: PromptSpec,
        *,
        name: Optional[str] = None,
        binds: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "PromptUKFT":
        if not isinstance(prompt_spec, PromptSpec):
            raise TypeError(f"prompt_spec must be PromptSpec, got {type(prompt_spec)}")

        return cls(
            name=name or prompt_spec.id,
            content_resources={
                "prompt_spec": prompt_spec.to_dict(),
                "binds": binds or {},
            },
            content_composers={
                "default": prompt_composer,
                "prompt": prompt_composer,
                "list": prompt_list_composer,
            },
            tags=ptags(PROMPT_ID=prompt_spec.id),
            **updates,
        )

    @classmethod
    def from_func(
        cls,
        func: Callable,
        *,
        id: Optional[str] = None,
        version: Optional[int] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        binds: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "PromptUKFT":
        return cls.from_spec(
            PromptSpec.from_func(
                func=func,
                id=id,
                version=version,
                tds=tds,
                metadata=metadata,
            ),
            name=name,
            binds=binds,
            **updates,
        )

    @classmethod
    def from_str(
        cls,
        template: str,
        *,
        id: Optional[str] = None,
        trs: Optional[List[str]] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        binds: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "PromptUKFT":
        return cls.from_spec(
            PromptSpec.from_str(
                template=template,
                id=id,
                trs=trs,
                tds=tds,
                version=version,
                metadata=metadata,
            ),
            name=name,
            binds=binds,
            **updates,
        )

    @classmethod
    def from_jinja(
        cls,
        content: str,
        *,
        id: Optional[str] = None,
        tds: Optional[Union[str, Dict, List]] = None,
        version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        binds: Optional[Dict[str, Any]] = None,
        **updates,
    ) -> "PromptUKFT":
        return cls.from_spec(
            PromptSpec.from_jinja(
                template=content,
                id=id,
                tds=tds,
                version=version,
                metadata=metadata,
            ),
            name=name or id,
            binds=binds,
            **updates,
        )

    def to_spec(self) -> PromptSpec:
        payload = self.get("prompt_spec")
        if not isinstance(payload, dict):
            raise ValueError("PromptUKFT missing content_resources['prompt_spec']")
        return PromptSpec.from_dict(payload)

    def bind(self, **binds) -> "PromptUKFT":
        self.content_resources["binds"] = (self.get("binds") or {}) | binds
        return self

    def unbind(self, *keys: str) -> "PromptUKFT":
        binds = self.get("binds") or {}
        for key in keys:
            binds.pop(key, None)
        self.content_resources["binds"] = binds
        return self

    def render(self, **kwargs):
        binds = self.get("binds") or {}
        return self.to_spec()(**(binds | kwargs))

    def format(self, composer: Optional[Union[str, Callable]] = "default", **kwargs):
        return self.text(composer=composer, **kwargs)

    def to_func(self, *, name: Optional[str] = None, bind: Optional[Dict[str, Any]] = None) -> Callable:
        return self.to_spec().to_func(name=name, bind=bind)

    def list_templates(self) -> List[str]:
        spec_type = self.to_spec().prompt_type
        if spec_type == "jinja":
            return ["inline.jinja"]
        if spec_type == "template":
            return ["inline.template"]
        return ["inline.prompt"]


PromptType = Union[PromptSpec, PromptUKFT]
