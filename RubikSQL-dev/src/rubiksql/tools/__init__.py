from rubiksql.tools.magic_number import MagicNumberToolSpec
from .exec_sql import *
from .db_info import *
from .fuzzy_enum import *
from .submit import *
from .fd_check import *
from .add_knowledge import *
from .submit_kls import *
from .extract_synonyms import *

from ..klbase import RubikSQLKLBase
from ahvn.utils.db import Database
from ahvn.utils.basic.config_utils import dunflat
from ahvn.tool import ToolSpec

from typing import Optional, List


class RubikSQLToolkit:
    def __init__(self, kb: RubikSQLKLBase, db: Database, db_id: str, **kwargs):
        tool_kwargs = dunflat(kwargs)
        self.knowledgespace = dict()
        self.workspace = dict()
        self.tools = {
            "exec_sql": ExecSQLToolSpec.from_db(db, **tool_kwargs.get("exec_sql", {})),
            "db_info": DatabaseInfoToolSpec.from_kb(kb, db_id=db_id, **tool_kwargs.get("db_info", {})),
            "tab_info": TableInfoToolSpec.from_kb(kb, db_id=db_id, **tool_kwargs.get("tab_info", {})),
            "col_info": ColumnInfoToolSpec.from_kb(kb, db_id=db_id, **tool_kwargs.get("col_info", {})),
            "fuzzy_enum": FuzzyEnumToolSpec.from_kb(kb, db_id=db_id, **tool_kwargs.get("fuzzy_enum", {})),
            "submit_sql": SubmitSQLToolSpec.from_db(db, **tool_kwargs.get("submit_sql", {})),
            "fd_check": FDCheckToolSpec.from_kb_and_db(kb, db, db_id=db_id, **tool_kwargs.get("fd_check", {})),
            "add_knowledge": AddKnowledgeToolSpec.from_kb(kb, db_id=db_id, knowledgespace=self.knowledgespace, **tool_kwargs.get("add_knowledge", {})),
            "submit_kls": SubmitKlsToolSpec.from_knowledgespace(self.knowledgespace, **tool_kwargs.get("submit_kls", {})),
            "extract_synonyms": ExtractSynonymsToolSpec.from_kb(kb, **tool_kwargs.get("extract_synonyms", {})),
            "magic_number": MagicNumberToolSpec.from_number(**tool_kwargs.get("magic_number", {})),
        }

    def __contains__(self, name: str) -> bool:
        return name in self.tools

    def get_tool(self, name: str) -> Optional[ToolSpec]:
        return self.tools.get(name)

    def get_tools(self, names: Optional[List[str]] = None) -> List[ToolSpec]:
        if names is None:
            return list(self.tools.values())
        return [self.tools[name] for name in names if name in self.tools]

    def list_tools(self) -> List[str]:
        return list(self.tools.keys())

    def subset_kit(self, names: List[str]) -> "RubikSQLToolkit":
        sub_tools = {name: self.tools[name] for name in names if name in self.tools}
        sub_kit = RubikSQLToolkit.__new__(RubikSQLToolkit)
        sub_kit.tools = sub_tools
        return sub_kit
