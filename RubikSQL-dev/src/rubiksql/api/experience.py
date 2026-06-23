"""\
Experience API for RubikSQL.

Provides programmatic access to add NL2SQL experiences to the knowledge base.
"""

__all__ = [
    "add_experience",
]

from typing import Optional, List, Dict, Any

from ahvn.utils.basic.log_utils import get_logger

from ..db import DB_MANAGER
from ..ukfs.exp_ukft import RubikSQLExpUKFT

logger = get_logger(__name__)


def add_experience(
    database: str,
    question: str,
    sql: str,
    context: Optional[Dict[str, Any]] = None,
    hints: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    format_sql: bool = True,
    comment_sql: bool = False,
    dialect: str = "sqlite",
) -> Dict[str, Any]:
    """\
    Add an NL2SQL experience to the knowledge base.

    Args:
        database: Database name (registered via 'rubiksql db add').
        question: Natural language question.
        sql: SQL query that answers the question.
        context: Query context (e.g., query_time, user_profile). Default: None.
        hints: Optional hints related to the query. Default: None.
        metadata: Additional metadata. Default: None.
        format_sql: Whether to format the SQL. Default: True.
        comment_sql: Whether to add LLM comments to SQL. Default: False.
        dialect: SQL dialect. Default: sqlite.

    Returns:
        Dict containing:
            - id: Experience ID string
            - name: Experience name
            - description: Experience description
            - type: Experience type

    Raises:
        ValueError: If database not found or knowledge base not built.
    """
    # Get database config
    db_config = DB_MANAGER.get_database(database)
    if not db_config:
        raise ValueError(f"Database '{database}' not found. Use 'rubiksql db list' to see available databases.")

    # Get database directory and klbase path
    from ahvn.utils.basic.path_utils import pj
    from ahvn.utils.basic.file_utils import touch_dir

    db_dir = DB_MANAGER._get_db_dir(database)
    klbase_path = pj(db_dir, "kb")
    touch_dir(klbase_path)

    # Get database connection and info
    db_connection = DB_MANAGER.connect(database)
    db_info = DB_MANAGER.generate_db_info(database)

    # Get klbase
    from ..klbase import RubikSQLKLBase

    klbase = RubikSQLKLBase(db_connection, db_info, klbase_path, db_name=database)

    # Create experience
    logger.info(f"Creating experience for question: {question[:50]}...")

    # Prepare metadata
    exp_metadata = {"db_id": database, "dialect": dialect, "source": "cli"}
    if metadata:
        exp_metadata.update(metadata)

    # Create experience using RubikSQLExpUKFT.from_nl2sql
    experience = RubikSQLExpUKFT.from_nl2sql(
        db_id=database,
        question=question,
        context=context,
        hints=hints,
        output_sql=sql,
        expected_sql=sql,  # For user-provided experiences, output == expected
        metadata=exp_metadata,
        format_sql=format_sql,
        comment_sql=comment_sql,
        verify_db=None,  # No verification needed for user-provided SQL
    ).signed(system=True)

    # Add to knowledge base
    logger.info(f"Adding experience to knowledge base: {experience.id_str}")
    klbase.upsert(experience)

    logger.info(f"Successfully added experience: {experience.name}")

    return {
        "id": experience.id_str,
        "name": experience.name,
        "description": experience.description,
        "short_description": experience.short_description,
        "type": experience.type,
    }
