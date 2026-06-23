"""\
NL2SQL API for RubikSQL.

Provides programmatic access to the NL2SQL functionality,
wrapping the CLI logic into a reusable Python function.
"""

__all__ = [
    "ask",
]

import warnings
from typing import Optional, List, Dict, Any

from ahvn.utils.basic.color_utils import color_success, color_error, color_warning, color_grey
from ahvn.utils.basic.path_utils import pj
from ahvn.utils.basic.file_utils import exists_file
from ahvn.utils.basic.log_utils import set_log_level
from ahvn.utils.basic.serialize_utils import load_json
from ahvn.utils.db.db_utils import prettify_sql
from ahvn.ukf.ukf_utils import ptags

from ..db import DB_MANAGER
from ..klbase import RubikSQLKLBase
from ..tools import RubikSQLToolkit
from ..agent import RubikSQLAgentSpec
from ..resources.rubik_kb import RUBIK_KB
from ..utils.config_utils import RUBIK_CM


def ask(
    database: str,
    query: str,
    agent: Optional[str] = None,
    hints: Optional[List[str]] = None,
    dialect: str = "sqlite",
    llm_preset: Optional[str] = None,
    query_time: Optional[int] = None,
    currency: Optional[str] = None,
    caliber: Optional[str] = None,
    department: Optional[str] = None,
    region: Optional[str] = None,
    preference: Optional[str] = None,
    execute: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """\
    Ask a natural language question and generate SQL.

    This function wraps the RubikSQL agent to convert natural language
    questions into SQL queries.

    NOTE: You must first build the knowledge base using 'rubiksql kb build'.

    Args:
        database: Database name (registered via 'rubiksql db add').
        query: Natural language question to ask.
        agent: Agent preset to use (flash, auto, heavy). Default: from config.
        hints: Hints to guide SQL generation.
        dialect: SQL dialect (default: sqlite).
        llm_preset: LLM preset name (default: from agent config).
        query_time: Query time in YYYYMM format (default: None).
        currency: Currency type (USD/EUR/CNY) (default: None).
        caliber: Caliber type (A/B) (default: None).
        department: Department name (arbitrary string) (default: None).
        region: Region name (arbitrary string) (default: None).
        preference: User preference (arbitrary string) (default: None).
        execute: Whether to execute the generated SQL. Default: False.
        verbose: Display intermediate agent steps. Default: False.

    Returns:
        Dict containing:
            - sql: The generated SQL (prettified with comments).
            - result: Execution result if execute=True, else None.
            - error: Error message if any, else None.

    Raises:
        ValueError: If database not found or knowledge base not built.
    """
    hints = hints or []
    query = query.strip()

    # Resolve agent configuration
    available_agents = list(RUBIK_CM.get("agents").keys())
    available_agents.remove("default_agent")

    if agent is None:
        agent = RUBIK_CM.get("agents.default_agent")
    if agent not in available_agents:
        raise ValueError(f"Invalid agent '{agent}'. Must be one of: {', '.join(available_agents)}")

    agent_config = RUBIK_CM.get(f"agents.{agent}")
    effective_llm_preset = llm_preset if llm_preset is not None else agent_config.get("llm_args", {}).get("preset")
    max_steps = agent_config.get("max_steps", 20)

    # Suppress verbose output unless verbose flag is set
    if not verbose:
        warnings.filterwarnings("ignore")
        set_log_level("CRITICAL", loggers=["ahvn", "ahvn.agent", "ahvn.agent.base", "ahvn.llm", "rubiksql", "sqlalchemy", "lance", "lancedb"])

    # Check database exists
    if not DB_MANAGER.database_exists(database):
        raise ValueError(f"Database '{database}' not found. Use 'rubiksql db list' to see available databases.")

    # Get database directory and KB path
    db_dir = DB_MANAGER._get_db_dir(database)
    kb_path = pj(db_dir, "kb")
    build_file = pj(kb_path, "build.json")

    # Check if KB is built
    if not exists_file(build_file):
        raise ValueError(f"Knowledge base not built for database '{database}'. Run 'rubiksql kb build -db {database}' first.")

    build_state = load_json(build_file)
    if build_state.get("status") != "completed":
        raise ValueError(f"Knowledge base build incomplete for database '{database}'. Run 'rubiksql kb build -db {database}' to complete the build.")

    db = None
    try:
        # Connect and setup
        db = DB_MANAGER.connect(database)
        db_info = DB_MANAGER.generate_db_info(database)

        # Load knowledge base (no build, just load)
        klbase = RubikSQLKLBase(db, db_info, kb_path, db_name=database)

        # Create toolkit
        toolkit = RubikSQLToolkit(klbase, db, db_id=database)

        # Get the nl2sql prompt from RUBIK_KB
        prompt = RUBIK_KB.get_prompt("nl2sql_with_tools", tags=ptags(DIALECT=dialect))

        # Create agent
        agent_spec = RubikSQLAgentSpec(
            klbase=klbase,
            prompt=prompt,
            db_id=database,
            toolkit=toolkit,
            llm_args={"preset": effective_llm_preset},
            dialect=dialect,
            max_steps=max_steps,
        )

        # Build context
        context = {}
        if query_time is not None:
            context["query_time"] = query_time
        else:
            import datetime

            now = datetime.datetime.now()
            context["query_time"] = now.year * 100 + now.month

        if currency is not None:
            if currency not in ["USD", "EUR", "CNY"]:
                raise ValueError(f"Invalid currency '{currency}'. Must be one of: USD, EUR, CNY")
            context["currency"] = currency

        if caliber is not None:
            if caliber not in ["A", "B"]:
                raise ValueError(f"Invalid caliber '{caliber}'. Must be one of: A, B")
            context["caliber"] = caliber

        if department is not None:
            context["department"] = department

        if region is not None:
            context["region"] = region

        if preference is not None:
            context["preference"] = preference

        # Run query (non-streaming for API usage)
        sql = agent_spec(query=query, context=context, hints=list(hints))

        if sql is None:
            return {
                "sql": None,
                "result": None,
                "error": "Failed to generate SQL.",
            }

        # Prettify the SQL with comments
        sql = prettify_sql(sql, dialect=dialect, comments=True)

        result = None
        error = None

        # Execute if requested
        if execute:
            exec_result = toolkit.get_tool("exec_sql")(sql=sql)
            if "[ERROR]" in exec_result:
                error = exec_result
            else:
                result = exec_result

        return {
            "sql": sql,
            "result": result,
            "error": error,
        }

    except KeyboardInterrupt:
        raise KeyboardInterrupt("Cancelled by user.")
    except Exception:
        if verbose:
            import traceback

            traceback.print_exc()
        raise
    finally:
        if db is not None:
            db.close_conn()
