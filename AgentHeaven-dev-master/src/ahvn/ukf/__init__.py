__all__ = [
    "default_trigger",
    "default_composer",
    "BaseUKF",
    "UKFTypeRegistry",
    "HEAVEN_UR",
    "register_ukft",
    "UKF_TYPES",
    "UKFIdType",
    "UKFIntegerType",
    "UKFBooleanType",
    "UKFShortTextType",
    "UKFMediumTextType",
    "UKFLongTextType",
    "UKFTimestampType",
    "UKFDurationType",
    "UKFJsonType",
    "UKFSetType",
    "UKFTagsType",
    "tag_s",
    "tag_v",
    "tag_t",
    "ptags",
    "gtags",
    "has_tag",
    "has_related",
    "next_ver",
    "DummyUKFT",
    "KnowledgeUKFT",
    "ExperienceUKFT",
    "ResourceUKFT",
    "DocumentUKFT",
    "TemplateUKFT",
    "PromptUKFT",
    "ToolUKFT",
    "templates",
]

from .ukf_utils import *

from .types import *

from .registry import *

from .base import *

import importlib

_EXPORT_MAP = {
    "DummyUKFT": ".templates.basic",
    "KnowledgeUKFT": ".templates.basic",
    "ExperienceUKFT": ".templates.basic",
    "ResourceUKFT": ".templates.basic",
    "DocumentUKFT": ".templates.basic",
    "TemplateUKFT": ".templates.basic",
    "PromptUKFT": ".templates.basic",
    "ToolUKFT": ".templates.basic",
}

_SUBMODULES = ["templates"]


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
