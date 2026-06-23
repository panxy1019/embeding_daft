__all__ = [
    "db_info",
    "DatabaseInfoToolSpec",
    "tab_info",
    "TableInfoToolSpec",
    "col_info",
    "ColumnInfoToolSpec",
]

from ahvn.tool import ToolSpec
from ahvn.utils.klop import KLOp
from ahvn.utils.basic.debug_utils import value_match
from ahvn.utils.basic.str_utils import indent
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ..utils.config_utils import RUBIK_CM
from ..klbase import RubikSQLKLBase

from typing import List, Optional, Dict, Any


def get_db_kl(kb: RubikSQLKLBase, db_id: str):
    db_kls = list(
        r["kl"]
        for r in kb.search(
            engine="facet",
            type="db-database",
            inactive_mark=False,
            tags=KLOp.NF(slot="DATABASE", value=db_id),
        )
    )
    if len(db_kls) == 0:
        raise ValueError(f'Database "{db_id}" not found.')
    return max(db_kls, key=lambda kl: kl.priority)


def list_tab_ids(kb: RubikSQLKLBase, db_id: str) -> List[str]:
    return get_db_kl(kb, db_id=db_id).get("tabs", list())


def get_tab_kl(kb: RubikSQLKLBase, db_id: str, tab_id: str):
    tab_kls = list(
        r["kl"]
        for r in kb.search(
            engine="facet",
            type="db-table",
            inactive_mark=False,
            tags=KLOp.AND(
                [
                    KLOp.NF(slot="DATABASE", value=db_id),
                    KLOp.NF(slot="TABLE", value=tab_id),
                ]
            ),
        )
    )
    if len(tab_kls) == 0:
        raise ValueError(f'Table "{tab_id}" not found in database "{db_id}".')
    return max(tab_kls, key=lambda kl: kl.priority)


def list_col_ids(kb: RubikSQLKLBase, db_id: str, tab_id: str) -> List[str]:
    return get_tab_kl(kb, db_id=db_id, tab_id=tab_id).get("cols", list())


def get_col_kl(kb: RubikSQLKLBase, db_id: str, tab_id: str, col_id: str):
    col_kls = list(
        r["kl"]
        for r in kb.search(
            engine="facet",
            type="db-column",
            inactive_mark=False,
            tags=KLOp.AND(
                [
                    KLOp.NF(slot="DATABASE", value=db_id),
                    KLOp.NF(slot="TABLE", value=tab_id),
                    KLOp.NF(slot="COLUMN", value=col_id),
                ]
            ),
        )
    )
    if len(col_kls) == 0:
        raise ValueError(f'Column "{col_id}" not found in table "{tab_id}" of database "{db_id}".')
    return max(col_kls, key=lambda kl: kl.priority)


def _format_suggestions(matches: List, topk: int = 3, entity: str = "") -> str:
    """Format top-k suggestions from value_match results."""
    if not matches:
        return ""
    top_matches = matches[:topk]
    suggestions = ", ".join(f'"{m[0]}"' for m in top_matches[:-1])
    suggestions = " or ".join([suggestions, f'"{top_matches[-1][0]}"']) if len(top_matches) > 1 else f'"{top_matches[0][0]}"'
    return f"Did you mean{(' ' + entity) if entity else ''}: {suggestions}?"


def db_info(kb: RubikSQLKLBase, db_id: str) -> Dict[str, Any]:
    """\
    Retrieve database information from the knowledge base and display it as prompt.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        db_id (str): Database KL id to specify which database to retrieve.
            When multiple databases with the same id exist, the one with the highest priority will be used.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
    """
    try:
        db_kl = get_db_kl(kb, db_id)
    except ValueError:
        return {"output": None, "msg": None, "err": f'Database "{db_id}" not found.'}
    tab_kls = kb.storages["main"].batch_get(db_kl.obj_ids(rel="has_table"))
    tab_kls = list(filter(lambda kl: kl.is_active, tab_kls))
    return {"output": "Database Schema Information:\n" + indent(db_kl.text(composer="default", tab_kls=tab_kls)), "msg": None, "err": None}


class DatabaseInfoToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        db_id: str,
        name: str = "db_info",
    ):
        def wrapper() -> str:
            f"""\
            Return the database schema information of database "{db_id}".

            Returns:
                str: The database schema information.
            """
            result = db_info(kb=kb, db_id=db_id)
            parts = []
            if result["msg"]:
                parts.append(f"[WARNING] {result['msg']}\n")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"]:
                parts.append(result["output"])
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec


def tab_info(
    kb: RubikSQLKLBase,
    db_id: str,
    tab_id: str,
    tab_suggest_thres: Optional[float] = None,
    tab_suggest_candid: Optional[int] = None,
) -> Dict[str, Any]:
    """\
    Retrieve table information from the knowledge base and display it as prompt.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        db_id (str): Database id to specify which database the table belongs to.
        tab_id (str): Table id to specify which table to retrieve.
            When multiple tables with the same id exist, the one with the highest priority will be used.
        tab_suggest_thres (Optional[float]): The threshold for suggesting similar table ids when a mismatch occurs.
            If None, the default value from configuration (`tools.tab_info.tab_suggest_thres`) will be used.
        tab_suggest_candid (Optional[int]): The number of candidate suggestions to consider for table ids.
            If None, the default value from configuration (`tools.tab_info.tab_suggest_candid`) will be used.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
    """
    tab_suggest_thres = tab_suggest_thres if tab_suggest_thres is not None else RUBIK_CM.get("tools.tab_info.tab_suggest_thres", 0.3)
    tab_suggest_candid = tab_suggest_candid if tab_suggest_candid is not None else RUBIK_CM.get("tools.tab_info.tab_suggest_candid", 3)

    msgs = list()
    try:
        tab_kl = get_tab_kl(kb, db_id=db_id, tab_id=tab_id)
    except ValueError as e:
        msgs.append(str(e))
        all_tab_ids = list_tab_ids(kb, db_id=db_id)
        matches = value_match(all_tab_ids, tab_id, thres=tab_suggest_thres)
        if matches:
            hint = _format_suggestions(matches, topk=tab_suggest_candid, entity="table")
            matched_tab_id = matches[0][0]
            msgs.append(hint)
            msgs.append(f'Proceeding with "{matched_tab_id}".')
        else:
            err = str(e)
            return {"output": None, "msg": None, "err": err}
        tab_kl = get_tab_kl(kb, db_id=db_id, tab_id=matched_tab_id)
    col_kls = kb.storages["main"].batch_get(tab_kl.obj_ids(rel="has_column"))
    col_kls = list(filter(lambda kl: kl.is_active, col_kls))
    return {
        "output": "Table Schema Information:\n" + indent(tab_kl.text(composer="brief", col_kls=col_kls)),
        "msg": "\n".join(msgs) if msgs else None,
        "err": None,
    }


class TableInfoToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        db_id: str,
        name: str = "tab_info",
        tab_suggest_thres: Optional[float] = None,
        tab_suggest_candid: Optional[int] = None,
    ):
        def wrapper(tab_id: str) -> str:
            """\
            Return the table schema information of a given table.

            Args:
                tab_id (str): The id of the table.

            Returns:
                str: The table schema information.
            """
            result = tab_info(
                kb=kb,
                db_id=db_id,
                tab_id=tab_id,
                tab_suggest_thres=tab_suggest_thres,
                tab_suggest_candid=tab_suggest_candid,
            )
            parts = []
            if result["msg"]:
                parts.append(f"[WARNING] {result['msg']}\n")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"]:
                parts.append(result["output"])
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec


def col_info(
    kb: RubikSQLKLBase,
    db_id: str,
    tab_id: str,
    col_id: str,
    col_suggest_thres: Optional[float] = None,
    col_suggest_candid: Optional[int] = None,
    tab_suggest_thres: Optional[float] = None,
    tab_suggest_candid: Optional[int] = None,
) -> Dict[str, Any]:
    """\
    Retrieve column information from the knowledge base and display it as prompt.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        db_id (str): Database id to specify which database the column belongs to.
        tab_id (str): Table id to specify which table the column belongs to.
        col_id (str): Column id to specify which column to retrieve.
        col_suggest_thres (Optional[float]): The threshold for suggesting similar column ids when a mismatch occurs.
            If None, the default value from configuration (`tools.col_info.col_suggest_thres`) will be used.
        col_suggest_candid (Optional[int]): The number of candidate suggestions to consider for column ids.
            If None, the default value from configuration (`tools.col_info.col_suggest_candid`) will be used.
        tab_suggest_thres (Optional[float]): The threshold for suggesting similar table ids when a mismatch occurs.
            If None, the default value from configuration (`tools.col_info.tab_suggest_thres`) will be used.
        tab_suggest_candid (Optional[int]): The number of candidate suggestions to consider for table ids.
            If None, the default value from configuration (`tools.col_info.tab_suggest_candid`) will be used.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
    """
    col_suggest_thres = col_suggest_thres if col_suggest_thres is not None else RUBIK_CM.get("tools.col_info.col_suggest_thres", 0.3)
    col_suggest_candid = col_suggest_candid if col_suggest_candid is not None else RUBIK_CM.get("tools.col_info.col_suggest_candid", 3)
    tab_suggest_thres = tab_suggest_thres if tab_suggest_thres is not None else RUBIK_CM.get("tools.col_info.tab_suggest_thres", 0.3)
    tab_suggest_candid = tab_suggest_candid if tab_suggest_candid is not None else RUBIK_CM.get("tools.col_info.tab_suggest_candid", 3)

    msgs = list()
    try:
        col_kl = get_col_kl(kb, db_id=db_id, tab_id=tab_id, col_id=col_id)
    except ValueError as e:
        msgs.append(str(e))
        all_tab_ids = list_tab_ids(kb, db_id=db_id)
        tab_matches = value_match(all_tab_ids, tab_id, thres=tab_suggest_thres)
        if tab_matches:
            hint = _format_suggestions(tab_matches, topk=tab_suggest_candid, entity="table")
            matched_tab_id = tab_matches[0][0]
            msgs.append(hint)
            msgs.append(f'Proceeding with table "{matched_tab_id}".')
        else:
            err = str(e)
            return {"output": None, "msg": None, "err": err}
        try:
            col_kl = get_col_kl(kb, db_id=db_id, tab_id=matched_tab_id, col_id=col_id)
        except ValueError as e:
            # msgs.append(str(e))
            all_col_ids = list_col_ids(kb, db_id=db_id, tab_id=matched_tab_id)
            col_matches = value_match(all_col_ids, col_id, thres=col_suggest_thres)
            if col_matches:
                hint = _format_suggestions(col_matches, topk=col_suggest_candid, entity="column")
                matched_col_id = col_matches[0][0]
                msgs.append(hint)
                msgs.append(f'Proceeding with column "{matched_col_id}".')
            else:
                err = str(e)
                return {"output": None, "msg": None, "err": err}
            col_kl = get_col_kl(kb, db_id=db_id, tab_id=matched_tab_id, col_id=matched_col_id)
    return {"output": "Column Schema Information:\n" + indent(col_kl.text(composer="detail")), "msg": "\n".join(msgs) if msgs else None, "err": None}


class ColumnInfoToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        db_id: str,
        name: str = "col_info",
        col_suggest_thres: Optional[float] = None,
        col_suggest_candid: Optional[int] = None,
        tab_suggest_thres: Optional[float] = None,
        tab_suggest_candid: Optional[int] = None,
    ):
        def wrapper(tab_id: str, col_id: str) -> str:
            """\
            Return the column schema information of a given column.

            Args:
                tab_id (str): The id of the table that the column belongs to.
                col_id (str): The id of the column.

            Returns:
                str: The column schema information.
            """
            result = col_info(
                kb=kb,
                db_id=db_id,
                tab_id=tab_id,
                col_id=col_id,
                col_suggest_thres=col_suggest_thres,
                col_suggest_candid=col_suggest_candid,
                tab_suggest_thres=tab_suggest_thres,
                tab_suggest_candid=tab_suggest_candid,
            )
            parts = []
            if result["msg"]:
                parts.append(f"[WARNING] {result['msg']}\n")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")
            if result["output"]:
                parts.append(result["output"])
            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec
