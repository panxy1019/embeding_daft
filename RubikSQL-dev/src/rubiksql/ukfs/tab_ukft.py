__all__ = [
    "TableUKFT",
    "tab_info_brief_composer",
    "tab_ac_searched_composer",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.utils.db import Database
from ahvn.utils.basic.config_utils import HEAVEN_CM
from ahvn.utils.basic.str_utils import omission_list, value_repr, truncate, indent
from ahvn.utils.exts.autotask import autotask, AutoFuncError
from ahvn.ukf import ptags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT

from ..utils.config_utils import RUBIK_CM, rpj
from ..resources.rubik_kb import RUBIK_KB

from typing import Set, Optional, Union, List, ClassVar


def tab_info_brief_composer(kl, **kwargs):
    """\
    Compose a brief description of table information.

    Generates a text description of a database table, including its row count,
    column list, primary keys, and foreign keys. The level of detail is controlled
    by the mode parameter.

    Recommended Knowledge Types:
        TableUKFT

    Args:
        kl (BaseUKF): Knowledge object containing table metadata.

    Returns:
        str: Formatted description of the table.

    Example:
        >>> kl.content_resources = {
        ...     "db_id": "chinook",
        ...     "tab_id": "albums",
        ...     "# rows": 347,
        ...     "# cols": 3,
        ...     "cols": ["AlbumId", "Title", "ArtistId"],
        ...     "pks": ["AlbumId"],
        ...     "fks": {"ArtistId": {"table": "artists", "column": "ArtistId"}}
        ... }
        >>> tab_composer(kl, mode="brief")
        'Table "albums": 347 rows, 3 columns'
    """
    tab_id = kl.get("tab_id", "")
    short_description = kl.short_description
    description = kl.description

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="tab_info_brief.jinja").render(
        tab_id=tab_id,
        description=description or short_description,
        n_rows=kl.get("# rows", 0),
        n_cols=kl.get("# cols", 0),
        cols=kl.get("cols", []),
    )


def tab_info_detail_composer(kl, **kwargs):
    """\
    Compose a detailed description of table information.

    Generates a text description of a database table, including its row count,
    column list, primary keys, and foreign keys. The level of detail is controlled
    by the mode parameter.

    Recommended Knowledge Types:
        TableUKFT

    Args:
        kl (BaseUKF): Knowledge object containing table metadata.

    Returns:
        str: Formatted detailed description of the table.

    Example:
        >>> kl.content_resources = {
        ...     "db_id": "chinook",
        ...     "tab_id": "albums",
        ...     "description": "Contains album information.",
        ...     "# rows": 347,
        ...     "# cols": 3,
        ...     "cols": ["AlbumId", "Title", "ArtistId"],
        ...     "pks": ["AlbumId"],
        ...     "fks": {"ArtistId": {"table": "artists", "column": "ArtistId"}}
        ... }
        >>> tab_composer(kl, mode="detail")
        'Table "albums": 347 rows, 3 columns. Columns: AlbumId, Title, ArtistId. Primary Key(s): AlbumId. Foreign Keys: ArtistId -> artists(ArtistId). Description: Contains album information.'
    """
    tab_id = kl.get("tab_id", "")
    short_description = kl.short_description
    description = kl.description

    if kwargs.get("col_kls"):
        col_mappings = {col_kl.col_id: col_kl.text(composer="brief") for col_kl in kwargs["col_kls"]}
    else:
        col_mappings = dict()
    col_infos = [col_mappings.get(col, col) for col in kl.get("cols", [])]

    col_sample_top = HEAVEN_CM.get("ukfts.tab_ukft.detail.col_sample_top", 32)
    col_sample_bottom = HEAVEN_CM.get("ukfts.tab_ukft.detail.col_sample_bottom", 32)

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="tab_info_detail.jinja").render(
        tab_id=tab_id,
        description=description or short_description,
        n_rows=kl.get("# rows", 0),
        n_cols=kl.get("# cols", 0),
        pks=kl.get("pks", []),
        fks=kl.get("fks", []),
        col_infos=omission_list(col_infos, top=col_sample_top, bottom=col_sample_bottom),
    )


def tab_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for a table.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Table: ..."

    Recommended Knowledge Types:
        TableUKFT

    Args:
        kl (BaseUKF): Knowledge object containing table metadata and search results.

    Returns:
        str: Formatted string describing the search match.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))
    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="tab_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=tab_info_brief_composer(kl, **kwargs),
    )


@register_ukft
class TableUKFT(KnowledgeUKFT):
    """\
    Database tables.

    UKF Type: knowledge >> table
    Recommended Tags:
        - DATABASE
        - TABLE

    Recommended Relations:
        - TableUKFT -- in_database -> DatabaseUKFT
        - DatabaseUKFT -- has_table -> TableUKFT
        - TableUKFT -- has_column -> ColumnUKFT
        - ColumnUKFT -- in_table -> TableUKFT
        - TableUKFT -- has_taxonomy -> TaxonomyUKFT
        - TaxonomyUKFT -- in_table -> TableUKFT

    Recommended Components of `content_resources`:
        - db_id (str): Database identifier
        - tab_id (str): Table identifier
        - # rows (int): Total row count
        - # cols (int): Number of columns
        - cols (List[str]): List of column names
        - pks (List[str]): Primary key columns
        - fks (Dict): Foreign key relationships

    Recommended Composers:
        default: tab_composer (mode="detail") - Detailed table description with all columns and keys
        detail: tab_composer (mode="detail") - Detailed table description with all columns and keys
        brief: tab_composer (mode="brief") - Concise single-line summary
        auto: tab_auto_composer - Smart description with intelligent content selection
    """

    type_default: ClassVar[str] = "db-table"

    @classmethod
    def from_tab(
        cls,
        db_id: str,
        tab_id: str,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
    ) -> "TableUKFT":
        full_name = f"{value_repr(tab_id)}"
        short_description = "" if short_description is None else short_description
        description = "" if description is None else description

        from ..api import load_db, load_db_info

        db = load_db(db_id)
        db_info = load_db_info(db_id)

        # Get all columns for this table
        all_cols = db.tab_cols(tab_id)

        # Filter to only non-disabled columns
        if db_info and tab_id in db_info.tables:
            table = db_info.tables[tab_id]
            enabled_cols = [col_id for col_id in all_cols if col_id in table.columns and not table.columns[col_id].disabled]
        else:
            # If no db_info, use all columns
            enabled_cols = all_cols

        row_count = db.row_count(tab_id)

        result = cls(
            name=full_name,
            short_description=short_description,
            description=description,
            content_resources={
                "db_id": db_id,
                "tab_id": tab_id,
                "# rows": row_count,
                "# cols": len(enabled_cols),
                "pks": db.tab_pks(tab_id),
                "fks": db.tab_fks(tab_id),
                "cols": enabled_cols,
            },
            content_composers={
                "default": tab_info_brief_composer,
                "brief": tab_info_brief_composer,
                "detail": tab_info_detail_composer,
                "ac": tab_ac_searched_composer,
            },
            tags=ptags(DATABASE=db_id, TABLE=tab_id),
            synonyms={tab_id} | (synonyms or set()),
        )

        db.close()
        return result

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    @property
    def tab_id(self) -> str:
        return self.get("tab_id", "")

    def gen_desc(self, db_kl=None, col_kls: Optional[List[KnowledgeUKFT]] = None, instructions: Optional[Union[str, List[str]]] = None, **kwargs) -> str:
        tab_profile = self.text(composer="detail", col_kls=col_kls, **kwargs)
        db_profile = db_kl.text(composer="default") if db_kl else None
        descriptions = [
            f"Table Schema Information:\n{indent(tab_profile)}",
            f"Schema Information of the Database that the Table belongs to:\n{indent(db_profile)}" if db_profile else None,
        ]
        try:
            desc = autotask(
                prompt=RUBIK_KB.get_prompt("tab_gen_desc"),
                descriptions=descriptions,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if not isinstance(desc, str):
                raise AutoFuncError(f"Generated description is not a string, but {type(desc)}: {desc}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate description for table {self.name}. {e}")
            desc = self.description or self.short_description or ""
        self.description = truncate(desc.strip(), cutoff=KnowledgeUKFT.schema()["description"].max_length())
        return self.description

    def gen_syns(self, db_kl=None, instructions: Optional[Union[str, List[str]]] = None, **kwargs) -> Set[str]:
        db_profile = db_kl.text(composer="default") if db_kl else None
        tab_profile = self.text(composer="brief")
        descriptions = [
            f"Table Schema Information:\n{indent(tab_profile)}",
            f"Schema Information of the Database that the Table belongs to:\n{indent(db_profile)}" if db_profile else None,
        ]
        try:
            synonyms_list = autotask(
                prompt=RUBIK_KB.get_prompt("tab_gen_syns"),
                descriptions=descriptions,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if isinstance(synonyms_list, str):
                synonyms_list = [synonyms_list]
            if not isinstance(synonyms_list, list):
                raise AutoFuncError(f"Generated synonyms is not a list, but {type(synonyms_list)}: {synonyms_list}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate synonyms for table {self.name}. {e}")
            synonyms_list = list()
        synonyms = set(syn.strip() for syn in synonyms_list if syn.strip())
        self.synonyms = self.synonyms.union(synonyms)
        return self.synonyms
