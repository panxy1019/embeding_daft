__all__ = [
    "DatabaseUKFT",
    "db_info_composer",
    "db_ac_searched_composer",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.utils.basic.str_utils import truncate, indent
from ahvn.utils.db import Database
from ahvn.utils.exts.autotask import autotask, AutoFuncError
from ahvn.ukf import ptags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT

from ..utils.config_utils import RUBIK_CM, rpj
from ..resources.rubik_kb import RUBIK_KB

from typing import Set, Optional, Union, List, ClassVar


def db_info_composer(kl, **kwargs):
    """\
    Compose a description of database information with configurable detail level.

    Generates a text description of a database, including its table count and
    table list. The level of detail is controlled by the mode parameter.

    Recommended Knowledge Types:
        DatabaseUKFT

    Args:
        kl (BaseUKF): Knowledge object containing database metadata.
        tab_kls (Optional[List[TableUKFT]]): List of TableUKFT knowledge objects.
            Used to generate table information if tab_infos is not provided.
            Note: Either tab_infos or tab_kls must be provided.
    Returns:
        str: Formatted description of the database.

    Example:
        >>> kl.content_resources = {
        ...     "db_id": "chinook",
        ...     "# tabs": 13,
        ...     "tabs": ["albums", "artists", "customers", "employees"]
        ... }
        >>> db_composer(kl)
        'Database "chinook": 13 tables'
    """
    db_id = kl.get("db_id", "")
    short_description = kl.short_description
    description = kl.description

    if kwargs.get("tab_kls"):
        tab_mappings = {tab_kl.tab_id: tab_kl.text(composer="brief") for tab_kl in kwargs["tab_kls"]}
    else:
        tab_mappings = dict()
    tab_infos = [tab_mappings.get(tab, tab) for tab in kl.get("tabs", [])]

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="db_info.jinja").render(
        db_id=db_id,
        description=description or short_description,
        tab_infos=tab_infos,
    )


def db_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for a database.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Database: ..."

    Recommended Knowledge Types:
        DatabaseUKFT

    Args:
        kl (BaseUKF): Knowledge object containing database metadata and search results.

    Returns:
        str: Formatted string describing the search match.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))
    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="db_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=db_info_composer(kl, **kwargs),
    )


@register_ukft
class DatabaseUKFT(KnowledgeUKFT):
    """\
    Databases.

    UKF Type: knowledge >> database
    Recommended Tags:
        - DATABASE

    Recommended Relations:
        - DatabaseUKFT -- has_table -> TableUKFT
        - TableUKFT -- in_database -> DatabaseUKFT

    Recommended Components of `content_resources`:
        - db_id (str): Database identifier
        - # tabs (int): Number of tables
        - tabs (List[str]): List of table names

    Recommended Composers:
        default: db_composer (mode="detail") - Detailed database description with all tables
        detail: db_composer (mode="detail") - Detailed database description with all tables
        brief: db_composer (mode="brief") - Concise single-line summary
        auto: db_auto_composer - Smart description with intelligent table organization
    """

    type_default: ClassVar[str] = "db-database"

    @classmethod
    def from_db(
        cls,
        db_id: str,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
    ) -> "DatabaseUKFT":
        full_name = db_id
        short_description = "" if short_description is None else short_description
        description = "" if description is None else description

        from ..api import load_db, load_db_info

        db = load_db(db_id)
        db_info = load_db_info(db_id)

        # Get all tables
        all_tabs = db.db_tabs()

        # Filter to only non-disabled tables
        if db_info:
            enabled_tabs = [tab_id for tab_id in all_tabs if tab_id in db_info.tables and not db_info.tables[tab_id].disabled]
        else:
            # If no db_info, use all tables
            enabled_tabs = all_tabs

        result = cls(
            name=full_name,
            short_description=short_description,
            description=description,
            content_resources={
                "db_id": db_id,
                "tabs": enabled_tabs,
                "# tabs": len(enabled_tabs),
            },
            content_composers={
                "default": db_info_composer,
                "info": db_info_composer,
                "ac": db_ac_searched_composer,
            },
            tags=ptags(DATABASE=db_id),
            synonyms={db_id} | (synonyms or set()),
        )

        db.close()
        return result

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    def gen_desc(self, tab_kls: Optional[List[KnowledgeUKFT]] = None, instructions: Optional[Union[str, List[str]]] = None, **kwargs) -> str:
        db_profile = self.text(tab_kls=tab_kls, **kwargs)
        descriptions = [f"Database Schema Information:\n{indent(db_profile)}"]
        try:
            desc = autotask(
                prompt=RUBIK_KB.get_prompt("db_gen_desc"),
                descriptions=descriptions,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if not isinstance(desc, str):
                raise AutoFuncError(f"Generated description is not a string, but {type(desc)}: {desc}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate description for database {self.name}. {e}")
            desc = self.description or self.short_description or ""
        self.description = truncate(desc.strip(), cutoff=KnowledgeUKFT.schema()["description"].max_length())
        return self.description
