__all__ = [
    "MagicNumberToolSpec",
]

from ahvn.tool import ToolSpec
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

class MagicNumberToolSpec(ToolSpec):
    @classmethod
    def from_number(
        cls,
        number: int = 666,
    ):

        def wrapper() -> str:
            """\
            Return the magic number.

            Returns:
                str: The magic number.
            """
            return str(number)

        toolspec = ToolSpec.from_function(func=wrapper, name='magic_number', parse_docstring=True)
        return toolspec
