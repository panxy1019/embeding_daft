__all__ = [
    "extract_synonyms",
    "ExtractSynonymsToolSpec",
]

from ahvn.tool import ToolSpec
from ahvn.utils.exts.autotask import autotask
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.klop import KLOp

logger = get_logger(__name__)

from ..resources.rubik_kb import RUBIK_KB
from ..klbase import RubikSQLKLBase
from ..ukfs.pred_ukft import PredicateUKFT
from ..utils.pred_utils import pred_to_sql
from ahvn.ukf import ptags

from typing import List, Optional, Dict, Any


def extract_synonyms(
    kb: RubikSQLKLBase,
    question: str,
    sql: str,
) -> Dict[str, Any]:
    """\
    Extract synonyms from a natural language question and SQL query pair.

    This function analyzes the NL query and SQL to identify how users refer to
    database elements (tables, columns, enums, knowledge, predicates), then updates the
    knowledge base by adding these synonyms to existing knowledge objects or creating
    new knowledge objects for domain knowledge.

    Args:
        kb (RubikSQLKLBase): The knowledge base instance.
        question (str): Natural language question.
        sql (str): Ground truth SQL query.

    Returns:
        Dict[str, Any]: A dict with keys "output", "msg", "err".
            - output: Dict mapping synonym phrase -> UKF knowledge object that was updated.
            - msg: Optional informational message.
            - err: Optional error message string.

    Example:
        >>> result = extract_synonyms(kb, "Show me tall heroes", "SELECT * FROM superhero WHERE height_cm > 180")
        >>> if result["err"]:
        >>>     print(result["err"])
        >>> else:
        >>>     for phrase, kl in result["output"].items():
        >>>         print(f"'{phrase}' -> {kl.name}")
    """
    if not question or not question.strip():
        return {"output": None, "msg": None, "err": "`question` cannot be empty."}
    if not sql or not sql.strip():
        return {"output": None, "msg": None, "err": "`sql` cannot be empty."}

    # Extract synonyms using the autotask prompt
    try:
        extractor = autotask(
            prompt=RUBIK_KB.get_prompt("synonym_extraction"),
            llm_args={"preset": "chat"},
        )
        synonyms = extractor(question=question, sql=sql)
    except Exception as e:
        logger.error(f"Synonym extraction failed: {e}")
        return {"output": None, "msg": None, "err": f"Extraction failed: {str(e)}"}

    if not isinstance(synonyms, dict):
        return {"output": None, "msg": None, "err": f"Expected dict, got {type(synonyms)}"}

    phrase_to_kl = dict()  # Dict mapping phrase -> knowledge object

    # Process tables
    for synonym_phrase, ref in synonyms.get("tables", {}).items():
        tab_id = ref.get("tab_id")
        if not tab_id:
            continue

        # Search for existing table knowledge using facet engine
        tags = KLOp.AND([KLOp.NF(slot="TABLE", value=tab_id)])
        results = kb.search(engine="facet", type="db-table", tags=tags)
        if results:
            kl = results[0]["kl"]
            # Add synonym to existing knowledge
            if synonym_phrase not in kl.synonyms:
                kl.synonyms = kl.synonyms | {synonym_phrase}
                # Track phrase -> KL mapping
                phrase_to_kl[synonym_phrase] = kl

    # Process columns
    for synonym_phrase, ref in synonyms.get("columns", {}).items():
        tab_id = ref.get("tab_id")
        col_id = ref.get("col_id")
        if not tab_id or not col_id:
            continue

        # Search for existing column knowledge using facet engine
        tags = KLOp.AND(
            [
                KLOp.NF(slot="TABLE", value=tab_id),
                KLOp.NF(slot="COLUMN", value=col_id),
            ]
        )
        results = kb.search(engine="facet", type="db-column", tags=tags)
        if results:
            kl = results[0]["kl"]
            # Add synonym to existing knowledge
            if synonym_phrase not in kl.synonyms:
                kl.synonyms = kl.synonyms | {synonym_phrase}
                # Track phrase -> KL mapping
                phrase_to_kl[synonym_phrase] = kl

    # Process enums
    for synonym_phrase, ref in synonyms.get("enums", {}).items():
        tab_id = ref.get("tab_id")
        col_id = ref.get("col_id")
        enum_val = ref.get("enum_val")
        if not tab_id or not col_id or enum_val is None:
            continue

        # Search for existing enum knowledge using facet engine
        tags = KLOp.AND(
            [
                KLOp.NF(slot="TABLE", value=tab_id),
                KLOp.NF(slot="COLUMN", value=col_id),
                KLOp.NF(slot="ENUM", value=enum_val),
            ]
        )
        results = kb.search(engine="facet", type="db-enum", tags=tags)
        if results:
            kl = results[0]["kl"]
            # Add synonym to existing knowledge
            if synonym_phrase not in kl.synonyms:
                kl.synonyms = kl.synonyms | {synonym_phrase}
                # Track phrase -> KL mapping
                phrase_to_kl[synonym_phrase] = kl

    # Process knowledge
    for synonym_phrase, knowledge_data in synonyms.get("knowledge", {}).items():
        knowledge_text = knowledge_data.get("knowledge") if isinstance(knowledge_data, dict) else knowledge_data
        if not knowledge_text or not knowledge_text.strip():
            continue

        # Create a new knowledge object
        # Use KnowledgeUKFT as a simple container for domain knowledge
        from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT

        knowledge_kl = KnowledgeUKFT(
            name=knowledge_text.strip(),
            short_description=knowledge_text.strip(),
            content=knowledge_text.strip(),
            synonyms={synonym_phrase},
        )
        # Track phrase -> KL mapping
        phrase_to_kl[synonym_phrase] = knowledge_kl

    # Process predicates
    for synonym_phrase, pred_data in synonyms.get("predicates", {}).items():
        if not pred_data:
            continue

        # Handle both old format (direct predicate) and new format (tab_id + predicate)
        if "predicate" in pred_data:
            # New format: {"tab_id": "table", "predicate": {...}, "content": "..."}
            tab_id = pred_data.get("tab_id")
            predicate = pred_data.get("predicate")
            content = pred_data.get("content")  # Extract content field if present
        else:
            # Old format: direct predicate {"FIELD:table.column": {...}}
            predicate = pred_data
            tab_id = None
            content = None

        # Construct a PredicateUKFT from the predicate dict
        # The predicate must follow the standard format accepted by pred_to_sql
        # Content is stored as short_description in the knowledge object
        try:
            predicate_sql = pred_to_sql(predicate)
            pred_kl = PredicateUKFT.from_pred(db_id=kb.db_id, predicate=predicate, tab_id=tab_id, short_description=content)
        except Exception as e:
            logger.error(f"Failed to construct PredicateUKFT for synonym '{synonym_phrase}': {e}")
            predicate_sql = None

        if predicate_sql:
            from ahvn.utils.basic.hash_utils import md5hash, fmt_hash

            predicate_hash = fmt_hash(md5hash(predicate_sql))

            # Search for existing predicate with matching hash
            tags = KLOp.NF(slot="PREDICATE_HASH", value=predicate_hash)
            results = kb.search(engine="facet", type="db-predicate", tags=tags)

            if results:
                # Found existing predicate with same hash, merge synonyms
                existing_kl = results[0]["kl"]
                if synonym_phrase not in existing_kl.synonyms:
                    existing_kl.synonyms = existing_kl.synonyms | {synonym_phrase}
                    # Track phrase -> KL mapping
                    phrase_to_kl[synonym_phrase] = existing_kl
            else:
                # No collision found, use the constructed KL itself
                # Add synonym to the newly constructed KL
                if synonym_phrase not in pred_kl.synonyms:
                    pred_kl.synonyms = pred_kl.synonyms | {synonym_phrase}
                    # Track phrase -> KL mapping
                    phrase_to_kl[synonym_phrase] = pred_kl

    return {"output": phrase_to_kl, "msg": f"Updated {len(phrase_to_kl)} knowledge objects with new synonyms.", "err": None}


class ExtractSynonymsToolSpec(ToolSpec):
    @classmethod
    def from_kb(
        cls,
        kb: RubikSQLKLBase,
        name: str = "extract_synonyms",
    ):
        def wrapper(question: str, sql: str) -> str:
            """\
            Extract synonyms from a natural language question and SQL query pair, then update the knowledge base.

            This tool analyzes how users refer to database elements in their questions and
            automatically adds these as synonyms to existing knowledge objects (tables, columns, enums, knowledge, predicates).
            For domain knowledge, new knowledge objects are created when they don't already exist.

            Args:
                question (str): The natural language question asked by the user.
                sql (str): The ground truth SQL query that answers the question.

            Returns:
                str: A summary showing which synonym phrases were added to which knowledge objects.
            """
            result = extract_synonyms(kb=kb, question=question, sql=sql)

            parts = []
            if result["msg"]:
                parts.append(f"[INFO] {result['msg']}")
            if result["err"]:
                parts.append(f"[ERROR] {result['err']}")

            if result["output"]:
                parts.append("\nAdded synonyms:")
                for phrase, kl in result["output"].items():
                    parts.append(f"  - '{phrase}' -> {kl.name}")
            else:
                parts.append("No synonyms added.")

            return "\n".join(parts)

        toolspec = ToolSpec.from_function(func=wrapper, name=name, parse_docstring=True)
        return toolspec
