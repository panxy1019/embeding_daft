"""
submit_kls tool for RubikSQL (Extraction Workflow).
"""

__all__ = [
    "submit_kls",
    "SubmitKlsToolSpec",
]

from typing import Dict, Any, List, Optional
import re

from ahvn.tool import ToolSpec
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

_LABEL_RE = re.compile(r"^\[?([ECTDK]\d{2})\]?$")


def _norm_label(s: str) -> Optional[str]:
    """
    Normalize label from "T01" or "[T01]" to "T01".
    Return None if format invalid.
    """
    if s is None:
        return None
    m = _LABEL_RE.match(str(s).strip())
    if not m:
        return None
    return m.group(1)


def submit_kls(knowledgespace: Dict[str, List[str]], kls: List[str]) -> Dict[str, Any]:
    """
    Validate and submit selected knowledge labels.

    Args:
        knowledgespace: {"E": [...], "C": [...], "T": [...], "D": [...], "K": [...]}
        kls: list of labels like ["T01", "C01", "E02"]

    Returns:
        Dict[str, Any]:
            - kls: normalized unique labels in input order (List[str]) if success, else None
            - err: error message if failed, else None
    """
    if not kls:
        return {"kls": None, "err": "`kls` cannot be empty."}

    seen = set()
    out: List[str] = []

    for raw in kls:
        lab = _norm_label(raw)
        if lab is None:
            return {"kls": None, "err": f"Invalid label format: {raw!r}. Expected like 'T01' or '[T01]'."}

        if lab in seen:
            continue
        seen.add(lab)

        cat = lab[0]
        idx = int(lab[1:])

        if cat not in knowledgespace:
            return {"kls": None, "err": f"Unknown label category '{cat}' in {lab}."}

        if idx < 1 or idx > len(knowledgespace[cat]):
            return {"kls": None, "err": f"Label {lab} out of range. Category {cat} has {len(knowledgespace[cat])} items."}

        out.append(lab)

    return {"kls": out, "err": None}


class SubmitKlsToolSpec(ToolSpec):
    @classmethod
    def from_knowledgespace(
        cls,
        knowledgespace: Dict[str, List[str]],
        name: str = "submit_kls",
    ):
        """
        Create a ToolSpec that validates submitted labels against the shared knowledgespace.
        """

        def wrapper(kls: List[str]) -> str:
            """
            Submit selected labels for extraction phase.

            Args:
                kls: A list of labels like ["D01", "T01", "C01", "E01", "K01"].

            Returns:
                str:
                    - On success: "D01,T01,C01,E01,K01"
                    - On failure: "[ERROR] <reason>"
            """
            result = submit_kls(knowledgespace=knowledgespace, kls=kls)
            if result["err"]:
                return f"[ERROR] {result['err']}"
            return ",".join(result["kls"])

        return ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
