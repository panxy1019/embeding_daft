import importlib

from .examples_utils import *

__all__ = [
    # examples_utils
    "ExampleType",
    "ExampleSource",
    "normalize_examples",
    # auto modules
    "autoi18n",
    "autotask",
    "autofunc",
    "autocode",
]

_EXPORT_MAP = {
    "autoi18n": ".autoi18n",
    "autotask": ".autotask",
    "autofunc": ".autofunc",
    "autocode": ".autocode",
}


def __getattr__(name):
    if name in _EXPORT_MAP:
        mod = importlib.import_module(_EXPORT_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
