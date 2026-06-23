"""\
RubikSQL Demo Script.

Run NL2SQL queries using the RubikSQL agent.

Usage:
    python demo.py -q "Your natural language question"
    python demo.py -q "Your question" --hints "Hint 1" "Hint 2"
    python demo.py -q "Your question" --verbose
"""

import argparse
from typing import List, Tuple

from ahvn.utils.basic.path_utils import pj
from ahvn.utils.basic.file_utils import exists_file
from ahvn.utils.basic.serialize_utils import load_json
from ahvn.ukf.ukf_utils import ptags

from rubiksql.db import RUBIK_DBM
from rubiksql.klbase import RubikSQLKLBase
from rubiksql.tools import RubikSQLToolkit
from rubiksql.agent import RubikSQLAgentSpec
from rubiksql.resources.rubik_kb import RUBIK_KB

# Suppress verbose logging by default
from ahvn.utils.basic.log_utils import set_log_level
set_log_level("WARNING", loggers=["ahvn", "ahvn.agent", "ahvn.agent.base", "ahvn.llm", "rubiksql", "sqlalchemy", "lance", "lancedb"])


def run_nl2sql(
    query: str,
    database: str,
    hints: List[str] = None,
    dialect: str = "sqlite",
    llm_preset: str = "chat",
    verbose: bool = False,
) -> Tuple[str, List[dict]]:
    """\
    Run an NL2SQL query using the RubikSQL agent.

    Args:
        query: Natural language question to convert to SQL.
        database: Database name (registered via `rubiksql db add`).
        hints: Optional list of hints to guide SQL generation.
        dialect: SQL dialect (default: sqlite).
        llm_preset: LLM preset name (default: chat).
        verbose: Whether to print verbose output.

    Returns:
        Generated SQL query string and query results.

    Raises:
        ValueError: If the knowledge base is not built for the database.
    """
    # Set log level based on verbose flag
    if verbose:
        set_log_level("DEBUG", loggers=["ahvn", "rubiksql"])
    else:
        set_log_level("WARNING", loggers=["ahvn", "ahvn.agent", "ahvn.agent.base", "ahvn.llm", "rubiksql"])

    hints = hints or []

    # Connect to database using RUBIK_DBM
    db = RUBIK_DBM.connect(database)

    # Load knowledge base (uses db_id only, database must be registered)
    klbase = RubikSQLKLBase(db_id=database)

    # Create toolkit
    toolkit = RubikSQLToolkit(klbase, db, db_id=database)

    # Get the nl2sql prompt from RUBIK_KB
    prompt = RUBIK_KB.get_prompt("nl2sql_with_tools", tags=ptags(DIALECT=dialect))

    # Create agent
    agent = RubikSQLAgentSpec(
        klbase=klbase,
        prompt=prompt,
        db_id=database,
        toolkit=toolkit,
        llm_args={"preset": llm_preset},
        dialect=dialect,
        max_steps=20,
    )

    # Run query
    sql = agent(query=query, hints=hints)

    # Execute and get result
    res = toolkit.get_tool("exec_sql")(sql=sql) if sql else {"result": None, "err": "No SQL generated"}

    db.close_conn()
    return sql, res


def main():
    parser = argparse.ArgumentParser(
        description="RubikSQL NL2SQL Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-q", "--query",
        type=str,
        required=True,
        help="Natural language question to convert to SQL",
    )
    parser.add_argument(
        "-db", "--database",
        type=str,
        required=True,
        help="Database name (registered via 'rubiksql db add')",
    )
    parser.add_argument(
        "--hints",
        type=str,
        nargs="*",
        default=[],
        help="Optional hints to guide SQL generation (can specify multiple)",
    )
    parser.add_argument(
        "--dialect",
        type=str,
        default="sqlite",
        help="SQL dialect (default: sqlite)",
    )
    parser.add_argument(
        "--llm-preset",
        type=str,
        default="chat",
        help="LLM preset name (default: chat)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    try:
        sql, res = run_nl2sql(
            query=args.query,
            database=args.database,
            hints=args.hints,
            dialect=args.dialect,
            llm_preset=args.llm_preset,
            verbose=args.verbose,
        )

        print("\n=== Generated SQL ===")
        print(sql)

        print("\n=== SQL Result ===")
        print(res)
    except ValueError as e:
        print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

