__all__ = [
    "TaxonomyUKFT",
    "taxo_info_brief_composer",
    "taxo_ac_searched_composer",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.utils.db import Database
from ahvn.utils.basic.str_utils import value_repr
from ahvn.ukf import ptags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT

from ..utils.config_utils import rpj

from typing import Set, Optional, List, ClassVar


def taxo_info_brief_composer(kl, **kwargs):
    """\
    Compose a brief description of taxonomy information.

    Generates a text description of a database taxonomy, including its name,
    the table it belongs to, and the columns it includes.

    Recommended Knowledge Types:
        TaxonomyUKFT

    Args:
        kl (BaseUKF): Knowledge object containing taxonomy metadata.

    Returns:
        str: Formatted description of the taxonomy.
    """
    taxo_id = kl.get("taxo_id", "")
    tab_id = kl.get("tab_id", "")
    cols = kl.get("cols", [])
    description = kl.description
    short_description = kl.short_description

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="taxo_info_brief.jinja").render(
        taxo_id=taxo_id,
        tab_id=tab_id,
        cols=cols,
        description=description or short_description,
    )


def taxo_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for a taxonomy.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Taxonomy ..."

    Recommended Knowledge Types:
        TaxonomyUKFT

    Args:
        kl (BaseUKF): Knowledge object containing taxonomy metadata and search results.

    Returns:
        str: Formatted string describing the search match with hierarchy.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))
    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="taxo_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=taxo_info_brief_composer(kl, **kwargs),
    )


@register_ukft
class TaxonomyUKFT(KnowledgeUKFT):
    """\
    Database table taxonomies (column groupings).

    UKF Type: knowledge >> taxonomy
    Recommended Tags:
        - DATABASE
        - TABLE
        - TAXONOMY
        - COLUMN (for each column in the taxonomy)

    Recommended Relations:
        - TaxonomyUKFT -- has_column[idx] -> ColumnUKFT
        - ColumnUKFT -- in_taxonomy[idx] -> TaxonomyUKFT
        - TaxonomyUKFT -- in_table -> TableUKFT
        - TableUKFT -- has_taxonomy -> TaxonomyUKFT

    Recommended Components of `content_resources`:
        - db_id (str): Database identifier
        - tab_id (str): Table identifier
        - taxo_id (str): Taxonomy identifier (name)
        - cols (List[str]): List of column names in the taxonomy

    Recommended Composers:
        default: taxo_info_brief_composer - Brief taxonomy description
        brief: taxo_info_brief_composer - Brief taxonomy description
    """

    type_default: ClassVar[str] = "db-taxonomy"

    @classmethod
    def from_taxo(
        cls,
        db: Database,
        db_id: str,
        tab_id: str,
        taxo_id: str,
        cols: List[str],
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
    ) -> "TaxonomyUKFT":
        full_name = f"{value_repr(tab_id)}.{value_repr(taxo_id)}"
        short_description = "" if short_description is None else short_description
        description = "" if description is None else description
        return cls(
            name=full_name,
            short_description=short_description,
            description=description,
            content_resources={
                "db_id": db_id,
                "tab_id": tab_id,
                "taxo_id": taxo_id,
                "cols": cols,
            },
            content_composers={
                "default": taxo_info_brief_composer,
                "brief": taxo_info_brief_composer,
                "ac": taxo_ac_searched_composer,
            },
            tags=ptags(
                DATABASE=db_id,
                TABLE=tab_id,
                TAXONOMY=taxo_id,
                COLUMN=cols,
            ),
            synonyms={taxo_id} | (synonyms or set()),
        )

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    @property
    def tab_id(self) -> str:
        return self.get("tab_id", "")

    @property
    def taxo_id(self) -> str:
        return self.get("taxo_id", "")

    @property
    def cols(self) -> List[str]:
        return self.get("cols", [])
