__all__ = [
    "fuzzy_enum",
    "FuzzyEnumToolSpec",
]

from ahvn.tool import ToolSpec
from ahvn.utils.basic.config_utils import dget
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ..utils.config_utils import RUBIK_CM
from ..klbase import RubikSQLKLBase

from typing import List, Optional, Dict, Any


def fuzzy_enum(
    kb: RubikSQLKLBase,
    keyword: str,
    tab_ids: Optional[List[str]] = None,
    topk: Optional[int] = None,
    fetchk: Optional[int] = None,
    thres: Optional[float] = None,
) -> Dict[str, Any]:
    """\
    Find enum values that are likely related to the given keyword phrase.

    This function uses vector similarity search to find enum values in the knowledge base
    that semantically match the given keyword. When tab_ids is specified, only search within
    the given tables.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        keyword (str): The keyword phrase to search for matching enum values.
        tab_ids (Optional[List[str]]): If specified, only search enums within these tables.
            If None, search across all tables.
        topk (Optional[int]): The number of top results to return.
            If None, the default value from configuration (`tools.fuzzy_enum.topk`) will be used.
        fetchk (Optional[int]): The number of results to fetch from the search engine before post-filtering.
            If None, the default value from configuration (`tools.fuzzy_enum.fetchk`) will be used.
        thres (Optional[float]): The minimum similarity score threshold for considering a match.
            If None, the default value from configuration (`tools.fuzzy_enum.thres`) will be used.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
            - output: List of matching enum results with table, column, value, and score.
            - msg: Optional informational message.
            - err: Optional error message string.

    Example:
        >>> result = fuzzy_enum(kb, "riverside", tab_ids=["schools"])
        >>> if result["err"]:
        >>>     print(result["err"])
        >>> else:
        >>>     for item in result["output"]:
        >>>         print(f"{item['tab_id']}.{item['col_id']} = {item['enum']} (score: {item['score']})")
    """
    topk = topk if topk is not None else RUBIK_CM.get("tools.fuzzy_enum.topk", 10)
    fetchk = fetchk if fetchk is not None else RUBIK_CM.get("tools.fuzzy_enum.fetchk", 100)
    thres = thres if thres is not None else RUBIK_CM.get("tools.fuzzy_enum.thres", 0.3)

    if not keyword or not keyword.strip():
        return {"output": None, "msg": None, "err": "`keyword` cannot be empty."}
    try:
        results = kb.search(engine="vec-enums", query=keyword, topk=fetchk)
    except Exception as e:
        logger.error(f"Enum search failed: {e}")
        return {"output": None, "msg": None, "err": f"Search failed: {str(e)}"}

    tab_id_set = set(tab_ids) if tab_ids else None
    enum_kls = list()
    for r in results:
        if r.get("kl") is None:
            continue
        if (r.get("score") is None) or (r.get("score") < thres):
            continue  # or break
        if tab_id_set and (r.get("kl").tab_id not in tab_id_set):
            continue
        kl = r.get("kl")
        enum_kls.append(kl)
        if len(enum_kls) >= topk:
            break
    return {"output": enum_kls, "msg": None, "err": None}


class FuzzyEnumToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        db_id: str,
        name: str = "fuzzy_enum",
        topk: Optional[int] = None,
        fetchk: Optional[int] = None,
        thres: Optional[float] = None,
    ):
        topk = topk if topk is not None else RUBIK_CM.get("tools.fuzzy_enum.topk", 10)
        fetchk = fetchk if fetchk is not None else RUBIK_CM.get("tools.fuzzy_enum.fetchk", 100)
        thres = thres if thres is not None else RUBIK_CM.get("tools.fuzzy_enum.thres", 0.3)

        def wrapper(keyword: str, tab_ids: Optional[List[str]] = None) -> str:
            """\
            Find enum values that match the given keyword phrase via vector similarity search.
            Use this tool when you need to find specific cell values (enums) in the database that are semantically related to a keyword.

            Args:
                keyword (str): The keyword or phrase to search for.
                tab_ids (Optional[List[str]]): If specified, only search within these tables.
                    If None, search across all tables.

            Returns:
                str: A formatted list of matching enum values with their table, column, and similarity score.
            """
            result = fuzzy_enum(kb=kb, keyword=keyword, tab_ids=tab_ids, topk=topk)
            parts = []
            if result["msg"]:
                parts.append(f"[INFO] {result['msg']}")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"]:
                parts.append("\n".join(f"{enum_kl.name} (score: {dget(enum_kl.metadata, 'search.returns.score'):.3f})" for enum_kl in result["output"]))
            else:
                parts.append(f"No enums found matching '{keyword}', try adjusting the `keyword` or `tab_ids`.")
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec
