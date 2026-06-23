__all__ = [
    "EnumUKFT",
    "enum_info_composer",
    "enum_ac_searched_composer",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.utils.basic.str_utils import value_repr, indent
from ahvn.utils.exts.autotask import autotask, AutoFuncError
from ahvn.ukf import ptags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT

from ..utils.config_utils import RUBIK_CM, rpj
from ..utils.pred_utils import pred_to_sql
from ..resources.rubik_kb import RUBIK_KB

from typing import Set, Optional, Any, Union, List, ClassVar


def enum_info_composer(kl, **kwargs):
    """\
    Compose a brief description of the enum value.

    Generates a text description of the enum value in the format:
    "'<tab>'.'<col>' = '<enum>'"

    Recommended Knowledge Types:
        EnumUKFT

    Args:
        kl (BaseUKF): Knowledge object containing enum metadata.

    Returns:
        str: Formatted description of the enum value.
    """
    tab_id = kl.get("tab_id", "")
    col_id = kl.get("col_id", "")
    enum_val = kl.get("enum", "")
    return f"'{tab_id}'.'{col_id}' = '{enum_val}'"


def enum_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for an enum value.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Value: ..."

    Recommended Knowledge Types:
        EnumUKFT

    Args:
        kl (BaseUKF): Knowledge object containing enum metadata and search results.

    Returns:
        str: Formatted string describing the search match.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))
    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="enum_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=enum_info_composer(kl, **kwargs),
    )


@register_ukft
class EnumUKFT(KnowledgeUKFT):
    """\
    Database table column enums.

    UKF Type: knowledge >> enum
    Recommended Tags:
        - DATABASE
        - TABLE
        - COLUMN
        - ENUM

    Recommended Relations:
        - EnumUKFT -- in_column -> ColumnUKFT

    Recommended Components of `content_resources`:
        None

    Recommended Composers:
        Any
    """

    type_default: ClassVar[str] = "db-enum"

    @classmethod
    def from_enum(
        cls,
        db_id: str,
        tab_id: str,
        col_id: str,
        enum_val: Any,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
    ) -> "EnumUKFT":
        full_name = f"{value_repr(tab_id)}.{value_repr(col_id)}={value_repr(enum_val)}"
        short_description = "" if short_description is None else short_description
        description = "" if description is None else description
        # TODO: LLM augmenting synonyms ? (too expensive?)
        return cls(
            name=full_name,
            short_description=short_description,
            description=description,
            content_resources={
                "db_id": db_id,
                "tab_id": tab_id,
                "col_id": col_id,
                "enum": enum_val,
                "predicate": {"tab": tab_id, "col": col_id, "==": enum_val},
            },
            content_composers={
                "default": enum_info_composer,
                "info": enum_info_composer,
                "ac": enum_ac_searched_composer,
            },
            tags=ptags(DATABASE=db_id, TABLE=tab_id, COLUMN=col_id, ENUM=enum_val),
            synonyms={str(enum_val)} | (synonyms or set()),
        )

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    @property
    def tab_id(self) -> str:
        return self.get("tab_id", "")

    @property
    def col_id(self) -> str:
        return self.get("col_id", "")

    @property
    def enum(self) -> Any:
        return self.get("enum", None)

    def gen_syns(self, db_kl=None, tab_kl=None, col_kl=None, instructions: Optional[Union[str, List[str]]] = None, **kwargs) -> Set[str]:
        enum_val = str(self.enum)
        col_profile = col_kl.text(composer="detail", **kwargs) if col_kl else None
        db_profile = db_kl.text(composer="default") if db_kl else None
        tab_profile = tab_kl.text(composer="brief") if tab_kl else None
        descriptions_list = [
            f"Enum Value: {enum_val}",
            f"Schema Information of the Column that the Enum belongs to:\n{indent(col_profile)}" if col_profile else None,
            f"Schema Information of the Table that the Enum belongs to:\n{indent(tab_profile)}" if tab_profile else None,
            f"Schema Information of the Database that the Enum belongs to:\n{indent(db_profile)}" if db_profile else None,
        ]
        try:
            synonyms_list = autotask(
                prompt=RUBIK_KB.get_prompt("enum_gen_syns"),
                descriptions=descriptions_list,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if isinstance(synonyms_list, str):
                synonyms_list = [synonyms_list]
            if not isinstance(synonyms_list, list):
                raise AutoFuncError(f"Generated synonyms is not a list, but {type(synonyms_list)}: {synonyms_list}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate synonyms for enum {self.name}. {e}")
            synonyms_list = list()
        synonyms = set(syn.strip() for syn in synonyms_list if syn.strip())
        self.synonyms = self.synonyms.union(synonyms)
        return self.synonyms
