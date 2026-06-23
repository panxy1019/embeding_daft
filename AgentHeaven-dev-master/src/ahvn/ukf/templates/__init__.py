import importlib

_EXPORT_MAP = {
    "DummyUKFT": ".basic",
    "KnowledgeUKFT": ".basic",
    "ExperienceUKFT": ".basic",
    "ResourceUKFT": ".basic",
    "DocumentUKFT": ".basic",
    "TemplateUKFT": ".basic",
    "PromptUKFT": ".basic",
    "ToolUKFT": ".basic",
}

_SUBMODULES = ["basic"]


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
