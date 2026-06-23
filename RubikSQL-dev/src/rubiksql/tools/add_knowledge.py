"""
add_knowledge tool for RubikSQL (Extraction Workflow).
"""

__all__ = [
    "add_knowledge",
    "labelled_knowledge",
    "AddKnowledgeToolSpec",
]

from typing import Optional, Dict, Any, List

from ahvn.tool import ToolSpec
from ahvn.ukf import BaseUKF
from ahvn.utils.klop import KLOp
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ..klbase import RubikSQLKLBase


def add_knowledge(
    kb: RubikSQLKLBase,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    enum_val: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch an exact knowledge item from KB using identifiers.

    Priority: enum_val > col_id > tab_id

    Args:
        kb: RubikSQLKLBase instance.
        tab_id: Table identifier (required when col_id or enum_val is provided).
        col_id: Column identifier (required when enum_val is provided).
        enum_val: Enum value (requires tab_id + col_id).

    Returns:
        Dict[str, Any]:
            - output: BaseUKF on success, None on failure
            - msg: optional informational message
            - err: error message if failed, else None
    """
    if tab_id is None and col_id is None and enum_val is None:
        return {"output": None, "msg": None, "err": "At least one of (tab_id, col_id, enum_val) must be provided."}

    if enum_val is not None:
        if not tab_id or not col_id:
            return {"output": None, "msg": None, "err": "`enum_val` requires both `tab_id` and `col_id`."}
        tags = KLOp.AND(
            [
                KLOp.NF(slot="TABLE", value=tab_id),
                KLOp.NF(slot="COLUMN", value=col_id),
                KLOp.NF(slot="ENUM", value=enum_val),
            ]
        )
        results = kb.search(engine="facet", type="db-enum", tags=tags)

    elif col_id is not None:
        if not tab_id:
            return {"output": None, "msg": None, "err": "`col_id` requires `tab_id`."}
        tags = KLOp.AND(
            [
                KLOp.NF(slot="TABLE", value=tab_id),
                KLOp.NF(slot="COLUMN", value=col_id),
            ]
        )
        results = kb.search(engine="facet", type="db-column", tags=tags)

    else:
        tags = KLOp.AND([KLOp.NF(slot="TABLE", value=tab_id)])
        results = kb.search(engine="facet", type="db-table", tags=tags)

    kls: List[BaseUKF] = [r["kl"] for r in results if r.get("kl") is not None]

    if not kls:
        return {"output": None, "msg": None, "err": "Knowledge not found."}

    if len(kls) > 1:
        chosen = max(kls, key=lambda kl: getattr(kl, "priority", 0))
        return {
            "output": chosen,
            "msg": f"Multiple matches found ({len(kls)}). Proceeding with the one with highest priority.",
            "err": None,
        }

    return {"output": kls[0], "msg": None, "err": None}


def _category_for_kl(kl: BaseUKF) -> str:
    """
    Map UKF type -> label category.

    Per user rule:
      - db-enum     -> E
      - db-column   -> C
      - db-table    -> T
      - db-database -> D
      - anything else -> K
    """
    t = getattr(kl, "type", "") or ""
    if t == "db-enum":
        return "E"
    if t == "db-column":
        return "C"
    if t == "db-table":
        return "T"
    if t == "db-database":
        return "D"
    return "K"


def _strip_label_prefix(line: str) -> str:
    """
    Convert "[C01] payload" -> "payload".
    If format does not match, return the original string.
    """
    if isinstance(line, str) and line.startswith("[") and "] " in line:
        return line.split("] ", 1)[1]
    return str(line)


def labelled_knowledge(knowledgespace: Dict[str, List[str]], knowledge: BaseUKF) -> str:
    """
    Append a labelled knowledge string into knowledgespace and return the stored line.

    Requirements:
      - knowledgespace stores only strings
      - each string includes label prefix "[X##] "
      - label numbering is 1-based and two-digit inside each category list
    """
    category = _category_for_kl(knowledge)

    # Ensure category key exists
    if category not in knowledgespace or knowledgespace[category] is None:
        knowledgespace[category] = []

    # Convert BaseUKF to text
    try:
        payload = knowledge.text(composer="default")
    except Exception:
        payload = str(knowledge)

    # Dedup by payload within category
    for existing in knowledgespace[category]:
        if _strip_label_prefix(existing) == payload:
            return existing

    idx = len(knowledgespace[category]) + 1
    label = f"{category}{idx:02d}"
    line = f"[{label}] {payload}"
    knowledgespace[category].append(line)
    return line


class AddKnowledgeToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        db_id: str,
        name: str = "add_knowledge",
        knowledgespace: Optional[Dict[str, List[str]]] = None,
    ):
        """
        Create a ToolSpec that commits one knowledge item (table/column/enum) into knowledgespace.
        """
        if knowledgespace is None:
            knowledgespace = {"E": [], "C": [], "T": [], "D": [], "K": []}

        def wrapper(
            tab_id: Optional[str] = None,
            col_id: Optional[str] = None,
            enum_val: Optional[str] = None,
        ) -> str:
            """
            Commit a knowledge item into knowledgespace with a generated label.

            Args:
                tab_id: Table identifier; required when querying a column or enum.
                col_id: Column identifier; requires tab_id.
                enum_val: Enum value; requires tab_id and col_id.

            Returns:
                str:
                    - On success: a labelled string "[X##] ..."
                    - On failure: an error string prefixed with "[ERROR]"
            """
            result = add_knowledge(kb=kb, tab_id=tab_id, col_id=col_id, enum_val=enum_val)

            parts: List[str] = []
            if result["msg"]:
                parts.append(f"[INFO] {result['msg']}")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"]:
                parts.append(labelled_knowledge(knowledgespace, result["output"]))

            return "\n".join(parts)

        return ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
