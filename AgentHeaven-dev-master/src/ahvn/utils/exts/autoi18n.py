__all__ = [
    "autoi18n",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)


def autoi18n(*args, **kwargs):
    """Deprecated: translation migration is handled by `ahvn tr` + elicitation."""
    del args, kwargs
    raise RuntimeError("`autoi18n` has been removed. Use `ahvn tr` commands with elicitation instead.")
