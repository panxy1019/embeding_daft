__all__ = [
    "PredicateUKFT",
    "pred_info_composer",
    "pred_ac_searched_composer",
]

from ahvn.ukf import ptags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.utils.basic.hash_utils import md5hash, fmt_hash
from ahvn.utils.basic.str_utils import value_repr

from ..utils.config_utils import rpj
from ..utils.pred_utils import pred_to_sql

from typing import ClassVar, Dict, Any, Set, Optional


def pred_info_composer(kl, **kwargs):
    """\
    Compose a SQL string representation of the predicate.

    Converts the predicate JSON structure to a SQL predicate string
    using the pred_to_sql utility function.

    If a tab_id is available in content_resources, formats the output as:
    "{predicate_sql} in table '{tab_id}'" for better context.

    If a description is available, appends it to provide additional context
    about computed fields or domain-specific logic.

    Recommended Knowledge Types:
        PredicateUKFT

    Args:
        kl (BaseUKF): Knowledge object containing predicate metadata.

    Returns:
        str: SQL predicate string with optional table context and description.
    """
    predicate_sql = f"`{kl.sql}`"

    # Add table context if available
    tab_id = kl.get("tab_id")
    if tab_id and predicate_sql:
        predicate_sql = f"{predicate_sql} (Table: '{tab_id}')"

    # Append description if it exists (e.g., explanations of computed fields)
    short_description = kl.short_description if hasattr(kl, "short_description") else None
    if short_description:
        predicate_sql = f"{predicate_sql} {short_description}"

    return predicate_sql


def pred_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for a predicate.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Predicate: ..."

    Recommended Knowledge Types:
        PredicateUKFT

    Args:
        kl (BaseUKF): Knowledge object containing predicate metadata and search results.

    Returns:
        str: Formatted string describing the search match.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))

    # Build the predicate knowledge
    predicate_sql = f"`{kl.sql}`"

    # Add table context if available
    tab_id = kl.tab_id
    if tab_id and predicate_sql:
        predicate_sql = f"{predicate_sql} (Table: '{tab_id}')"

    # Append description if it exists (e.g., explanations of computed fields)
    short_description = kl.short_description if hasattr(kl, "short_description") else None
    if short_description:
        predicate_sql = f"{predicate_sql} {short_description}"

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="pred_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=predicate_sql,
    )


@register_ukft
class PredicateUKFT(KnowledgeUKFT):
    """\
    Database SQL query predicates.

    UKF Type: knowledge >> predicate
    Recommended Tags:
        - DATABASE
        - TABLE
        - PREDICATE_HASH

    Recommended Relations:
        - PredicateUKFT -- in_table -> TableUKFT
        - PredicateUKFT -- in_database -> DatabaseUKFT
        - TableUKFT -- has_predicate -> PredicateUKFT
        - DatabaseUKFT -- has_predicate -> PredicateUKFT

    Recommended Components of `content_resources`:
        - predicate (Dict): JSON predicate structure following the protocol:
            - {"FIELD:tab.col": {"==": value}} for equality
            - {"FIELD:tab.col": {"IN": [v1, v2, ...]}} for IN clause
            - {"AND": [...]} for AND of multiple predicates
            - {"OR": [...]} for OR of multiple predicates
            - {"NOT": {...}} for negation
            - ...
            See pred_utils.pred_to_sql for the complete protocol.

    Recommended Composers:
        Any
    """

    type_default: ClassVar[str] = "db-predicate"

    @classmethod
    def from_pred(
        cls,
        db_id: str,
        predicate: Dict[str, Any],
        tab_id: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
    ) -> "PredicateUKFT":
        """\
        Create a PredicateUKFT from a predicate dictionary.

        Constructs a PredicateUKFT instance from a JSON predicate structure
        following the protocol defined in pred_utils.pred_to_sql.

        Args:
            db_id: Database identifier for context (stored separately, not in predicate).
                This is used for display purposes in composers but does not affect the predicate hash.
            predicate: A dictionary representing the predicate in JSON format.
                The predicate must follow the standard format accepted by pred_to_sql:
                - {"FIELD:tab.col": {"==": value}} for equality
                - {"FIELD:tab.col": {"IN": [v1, v2, ...]}} for IN clause
                - {"AND": [...]} for AND of multiple predicates
                - {"OR": [...]} for OR of multiple predicates
                - {"NOT": {...}} for negation
                See pred_utils.pred_to_sql for the complete protocol.
            tab_id: Optional table identifier for context (stored separately, not in predicate).
                This is used for display purposes in composers but does not affect the predicate hash.
            short_description: Optional short description
            description: Optional detailed description
            synonyms: Optional set of synonyms

        Returns:
            PredicateUKFT: A new PredicateUKFT instance

        Examples:
            >>> PredicateUKFT.from_pred({"FIELD:users.status": {"==": "active"}})
            >>> PredicateUKFT.from_pred({"FIELD:state": {"IN": ["NY", "CA"]}}, tab_id="customers")
            >>> PredicateUKFT.from_pred({"AND": [
            ...     {"FIELD:users.age": {">=": 18}},
            ...     {"FIELD:users.status": {"==": "active"}}
            ... ]})
        """
        # Generate name from predicate
        predicate_sql = pred_to_sql(predicate)
        full_name = predicate_sql if predicate_sql else "predicate"

        short_description = "" if short_description is None else short_description
        description = "" if description is None else description

        # Build content_resources
        content_resources = {
            "db_id": db_id,
            "predicate": predicate,
            "sql": predicate_sql,
        }
        if tab_id is not None:
            content_resources["tab_id"] = tab_id

        # Build tags (TABLE tag only if tab_id provided, not used for hash)
        tags_dict = {
            "DATABASE": db_id,
            "PREDICATE_HASH": fmt_hash(md5hash(predicate_sql)),
        }

        # Add TABLE tag for facet search if tab_id provided
        if tab_id is not None:
            tags_dict["TABLE"] = tab_id

        return cls(
            name=full_name,
            short_description=short_description,
            description=description,
            content_resources=content_resources,
            content_composers={
                "default": pred_info_composer,
                "info": pred_info_composer,
                "ac": pred_ac_searched_composer,
            },
            tags=ptags(**tags_dict),
            synonyms=synonyms or set(),
        )

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    @property
    def tab_id(self) -> str:
        return self.get("tab_id", "")

    @property
    def predicate(self) -> Dict[str, Any]:
        return self.get("predicate", {})

    @property
    def sql(self) -> str:
        return self.get("sql", "")
