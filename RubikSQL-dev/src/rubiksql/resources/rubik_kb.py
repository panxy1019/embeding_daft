__all__ = [
    "RUBIK_KB",
    "setup_rubik_kb",
]

from ahvn.cache import JsonCache
from ahvn.klstore.cache_store import CacheKLStore
from ahvn.klengine.scan_engine import ScanKLEngine
from ahvn.klbase.base import KLBase
from ahvn.ukf.ukf_utils import ptags
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.resources.ahvn_kb import HEAVEN_KB

from rubiksql.utils.config_utils import rpj, RUBIK_CM
from ahvn.utils.basic.serialize_utils import load_json
from ahvn.utils.basic.file_utils import list_files
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.db.base import table_display

logger = get_logger(__name__)


class RubikKLBase(KLBase):
    def __init__(self):
        super().__init__(name="rubiksql")
        self.add_storage(
            CacheKLStore(
                name="_prompts",
                cache=JsonCache(rpj("& ukfs/prompts")),
            )
        )
        self.add_engine(
            ScanKLEngine(
                name="prompts",
                storage=self.storages["_prompts"],
            )
        )

    def get_prompt(self, name: str, tags: set = None, **kwargs) -> PromptUKFT:
        results = self.search(engine="prompts", name=name, **kwargs)
        if tags:
            # Filter by tags (check if all required tags are present)
            results = [r for r in results if tags.issubset(r["kl"].tags)]
        if not results:
            raise ValueError(f"Prompt '{name}' not found in RUBIK_KB.")
        if len(results) > 1:
            raise ValueError(f"Multiple prompts named '{name}' found in RUBIK_KB. Please refine your search facets by adding `**kwargs`.")
        return results[0]["kl"]


RUBIK_KB = RubikKLBase()
SUPPORTED_DIALECTS = ["sqlite", "postgresql", "mysql", "duckdb", "mssql"]  # , "oracle"]


def setup_rubik_kb():
    logger.info("Re-generating RUBIK_KB...")
    RUBIK_KB.clear()

    db_gen_desc_prompt = (
        HEAVEN_KB.get_prompt("autotask")
        .clone(name="db_gen_desc")
        .bind(
            default_system="You are an expert database analyst. Generate a concise, practical description (1-2 sentences) for the database that helps SQL developers understand its purpose, key content, and avoid common usage mistakes.",
            instructions=[
                "Analyze the database schema information provided above",
                "Create a 1-2 sentence description that covers the database's main purpose and key entities",
                "If a description is already provided, make sure all information is accurately preserved",
                "Include important relationships or constraints that SQL developers should know",
                "Mention any potential pitfalls or common query patterns",
                "Provide only the description text without additional formatting or commentary",
            ],
        )
    )

    col_gen_desc_prompt = (
        HEAVEN_KB.get_prompt("autotask")
        .clone(name="col_gen_desc")
        .bind(
            default_system="You are an expert database analyst. Generate a concise, practical description (1-2 sentences) for the column that helps SQL developers understand its purpose, key content, and avoid common usage mistakes.",
            instructions=[
                "Analyze the column schema information provided above",
                "Create a 1-2 sentence description that covers the column's purpose and key content",
                "If a description is already provided, make sure all information is accurately preserved",
                "Include important constraints or data patterns that SQL developers should know",
                "Mention any potential pitfalls or common query patterns",
                "Provide only the description text without additional formatting or commentary",
            ],
        )
    )

    col_gen_syns_prompt = (
        HEAVEN_KB.get_prompt("autotask_repr")
        .clone(name="col_gen_syns")
        .bind(
            default_system="You are an expert database analyst. Generate a list of practical synonyms for the column that help SQL developers recognize and refer to it accurately.",
            instructions=[
                "The generated synonyms are used for string-based matching to identify the column in user queries",
                "Therefore, focus on common alternative names, abbreviations, and variations that SQL developers / Users / Domain experts might use",
                "Generated synonyms should be concise (typically 1-3 words, as synonyms that are too long are less likely to be matched as substrings in user queries)",
                "Generated synonyms will be lemmatized and lowercased before use, but DO consider different word forms like hyphens, underscores, and spaces",
                "Avoid overly generic terms that could refer to multiple columns",
            ],
        )
    )

    tab_gen_desc_prompt = (
        HEAVEN_KB.get_prompt("autotask")
        .clone(name="tab_gen_desc")
        .bind(
            default_system="You are an expert database analyst. Generate a concise, practical description (1-2 sentences) for the table that helps SQL developers understand its purpose, key content, and avoid common usage mistakes.",
            instructions=[
                "Analyze the table schema information provided above",
                "Create a 1-2 sentence description that covers the table's purpose and key content",
                "If a description is already provided, make sure all information is accurately preserved",
                "Include important relationships to other tables or constraints that SQL developers should know",
                "Mention any potential pitfalls or common query patterns",
                "Provide only the description text without additional formatting or commentary",
            ],
        )
    )

    tab_gen_syns_prompt = (
        HEAVEN_KB.get_prompt("autotask_repr")
        .clone(name="tab_gen_syns")
        .bind(
            default_system="You are an expert database analyst. Generate a list of practical synonyms for the table that help SQL developers recognize and refer to it accurately.",
            instructions=[
                "The generated synonyms are used for string-based matching to identify the table in user queries",
                "Therefore, focus on common alternative names, abbreviations, and variations that SQL developers / Users / Domain experts might use",
                "Generated synonyms should be concise (typically 1-3 words, as synonyms that are too long are less likely to be matched as substrings in user queries)",
                "Generated synonyms will be lemmatized and lowercased before use, but DO consider different word forms like hyphens, underscores, and spaces",
                "Avoid overly generic terms that could refer to multiple tables",
            ],
        )
    )

    enum_gen_syns_prompt = (
        HEAVEN_KB.get_prompt("autotask_repr")
        .clone(name="enum_gen_syns")
        .bind(
            default_system="You are an expert database analyst. Generate a list of practical synonyms for the enum value that help SQL developers recognize and refer to it accurately.",
            instructions=[
                "The generated synonyms are used for string-based matching to identify the enum value in user queries",
                "Therefore, focus on common alternative names, abbreviations, and variations that SQL developers / Users / Domain experts might use",
                "Generated synonyms should be concise (typically 1-3 words, as synonyms that are too long are less likely to be matched as substrings in user queries)",
                "Generated synonyms will be lemmatized and lowercased before use, but DO consider different word forms like hyphens, underscores, and spaces",
                "Avoid overly generic terms that could refer to multiple enum values",
            ],
        )
    )

    datetime_parser_examples_data = [load_json(example_file) for example_file in sorted(list_files(rpj("& examples/datetime_parser/"), ext=".json", abs=True))]
    datetime_parser_prompt = (
        HEAVEN_KB.get_prompt("autotask_repr")
        .clone(name="datetime_parser")
        .bind(
            descriptions=[
                "Given a list of strings sampled from a database column, identify the datetime format.",
                "If they are datetime strings of the same format, output the Python strptime format string that can be used to parse them.",
                "If they are not datetime strings or have mixed formats, output None.",
            ],
            default_examples=datetime_parser_examples_data,
        )
    )

    synonym_extraction_examples_data = [
        load_json(example_file) for example_file in sorted(list_files(rpj("& examples/synonym_extraction/"), ext=".json", abs=True))
    ]
    synonym_extraction_prompt = (
        HEAVEN_KB.get_prompt("autotask_json")
        .clone(name="synonym_extraction")
        .bind(
            input_schema={"question": {}, "sql": {"mode": "code", "args": {"language": "sql"}}},
            default_system="You are an expert database analyst and linguist. Extract synonyms from natural language queries and their corresponding SQL queries.",
            instructions=[
                "Analyze the natural language question and SQL query pair to identify synonym mappings.",
                "Extract synonyms for five types of database elements: tables, columns, enums, knowledge, and predicates.",
                "1. TABLE: entity nouns, pluralized names, or domain terms for the main subject (e.g., 'employees', 'superheroes', 'products'). Format: {\"tab_id\": \"table\"}.",
                "2. COLUMN: attribute names, property descriptors, or field references (e.g., 'salary', 'name', 'department', 'eye color'). Format: {\"tab_id\": \"table\", \"col_id\": \"column\"}.",
                '3. ENUM: specific values within a column\'s domain, only for single equality conditions (WHERE col = val), format: {"tab_id": "table", "col_id": "column", "enum_val": value}.',
                "4. KNOWLEDGE: domain knowledge or business rules explained in natural language (e.g., 'tall superheroes are those with height_cm >= 180', 'active customers are those who made a purchase in the last 30 days'). Format: {\"knowledge\": \"natural language explanation\"}.",
                '5. PREDICATE: complex conditions including IN, comparisons (>, <, >=, <=, !=), BETWEEN, LIKE, OR, AND. Predicates use NESTED FORMAT with tab_id separate: {"tab_id": "table", "predicate": {...}}. The nested predicate uses "FIELD:" prefix to the columns for simple parsing: {"FIELD:column": {"operator": value}}.',
                'For equality: {"tab_id": "t", "predicate": {"FIELD:col": {"==": value}}}.',
                'For comparisons: {"tab_id": "t", "predicate": {"FIELD:col": {">": val}}}, {"tab_id": "t", "predicate": {"FIELD:col": {"<": val}}}, etc.',
                'For IN: {"tab_id": "t", "predicate": {"FIELD:col": {"IN": [v1, v2, ...]}}}.',
                'For BETWEEN: {"tab_id": "t", "predicate": {"FIELD:col": {"BETWEEN": [min, max]}}}.',
                'For LIKE: {"tab_id": "t", "predicate": {"FIELD:col": {"LIKE": "pattern"}}}.',
                'For OR: {"tab_id": "t", "predicate": {"OR": [{"FIELD:col": {"==": v1}}, {"FIELD:col": {"==": v2}}]}.',
                'For AND: {"tab_id": "t", "predicate": {"AND": [{"FIELD:col1": {"op": val1}}, {"FIELD:col2": {"op": val2}}]}.',
                "Include only meaningful synonyms that reflect how users refer to database elements.",
                "Omit generic words like 'how many', 'show', 'list', 'find', 'all' unless they map to specific elements.",
                "All table, column, and enum values must match SQL exactly (case-sensitive).",
                'Output JSON structure: {"enums": {...}, "columns": {...}, "tables": {...}, "knowledge": {...}, "predicates": {...}}.',
            ],
            default_examples=synonym_extraction_examples_data,
        )
    )

    autoviz_deduct_examples_data = [load_json(example_file) for example_file in sorted(list_files(rpj("& examples/autoviz_deduct/"), ext=".json", abs=True))]
    for example in autoviz_deduct_examples_data:
        example["inputs"]["table_data"] = table_display(example["inputs"]["table_data"], max_rows=32, max_width=None)
    autoviz_deduct_prompt = (
        HEAVEN_KB.get_prompt("autotask_json")
        .clone(name="autoviz_deduct")
        .bind(input_schema={"table_data": {"mode": "code", "args": {"language": ""}}})
        .bind(
            default_system="You are an expert Data Visualization Analyst. Your task is to analyze a user's question and the resulting SQL table data to determine the most effective visualization.",
            instructions=[
                "Analyze the user's original question to understand their intent and what they want to see.",
                "Examine the SQL result schema and sample data to understand the data structure and content.",
                "Choose the visualization type based on the user's question and data characteristics:",
                "- Use \"table\" for detailed data viewing, when the user asks to 'show', 'list', 'display', or when uncertain.",
                "- Use \"bar\" for comparing categories or ranking, when the user asks about 'top', 'compare', 'rank', 'difference'.",
                "- Use \"line\" for trends over time or continuous data, when the user asks about 'trend', 'over time', 'change', 'growth'.",
                "- Use \"pie\" for showing proportions or percentages of a whole, when the user asks about 'percentage', 'share', 'proportion', 'breakdown'.",
                '- Use "kpi" for single important metrics as a bold letter value in a card, whenever the user expects a single aggregate value or a few specific values as answer (e.g., total, average, count, or some metric). Optionally, set "format" for formatting the KPI value `v` with units, currencies or suffices (e.g., `"format": "$ {v/1000000:.2f} M", or `"format": "{v*100:.2f}%"), or set a dictionary of `y_key_name:format_str` for multiple y_keys.',
                "- Use \"radar\" for multi-dimensional comparisons across several variables, when the user asks to 'compare' multiple attributes or dimensions, or there are clearly multiple entities with several numeric attributes each.",
                'Always prefer "table" as fallback if the visualization intent is unclear or ambiguous.',
                'In most cases, "table" is the safest choice when the schema is not obviously suited for other chart types.',
                'Only specify chart axes ("x_key", "y_keys", and optionally "x_rows") when the chosen visualization type clearly supports them. "x_key" contains the column name for x-axis, "y_keys" is a list of column names for y-axis values each for one line / type of bar / radar shape segment. "x_rows" is can be used to select a subset of rows (typically for KPI charts to select a single aggregation row).',
                'Always specify a meaningful "title" for the visualization based on the user\'s question.',
                'When asked to summarize (`summarize: True`), provide a brief textual summary (with markdown-style emphasis) of key insights from the data as the "summary" field in the output.',
            ],
            default_examples=autoviz_deduct_examples_data,
        )
    )
    comment_sql_prompts = list()
    nl2sql_with_tools_prompts = list()

    for dialect in SUPPORTED_DIALECTS:
        comment_sql_prompts.append(
            HEAVEN_KB.get_prompt("autotask")
            .clone(name="comment_sql", upd_tags=ptags(DIALECT=dialect))
            .bind(
                default_system=f"You are an expert SQL engineer. Add informative comments to the {dialect} SQL query to explain its purpose and logic.",
                default_instructions=[
                    f"Always use `/* comment */` style for comments in {dialect} SQL.",
                    "You should always: 1) Add a SQL header to embed all important metadata provided (e.g., the user's question, time, schema info, etc.), 2) Add a brief description, which explains the logical steps of the SQL query, and 3) Add short hints to remind about corner cases or    important details. 4) Make sure each sql clause is well-commented with inline comments to explain its function.",
                ],
            )
        )
        nl2sql_with_tools_prompts.append(
            HEAVEN_KB.get_prompt("autotask")
            .clone(name="nl2sql_with_tools", upd_tags=ptags(DIALECT=dialect))
            .bind(
                default_system=f"You are an expert SQL engineer. Given a natural language question and access to various database tools, generate a correct {dialect} SQL query to answer the question.",
                default_instructions=[
                    "Use the provided tools to gather necessary information about the database schema, validate or execute small SQL queries.",
                    "The examples, if provided, coulde be vital to generate correct SQLs. Pay close attention to how the SQL structures are formed, domain knowledge, as well as the information about tables, columns, enums used in the examples.",
                    "When the question is very similar to a given example, quickly imitate the example SQL structure and utilize knowledge from the example, to reduce the total number of tool calls and respond faster.",
                    "(Important!!!) When SQLs in the examples are sufficient to answer the question, directly adapt and modify the example SQLs to answer the question, and avoid all unnecessary tool calls.",
                    "The provided hints may help you generate accurate SQL queries. These hints are obtained from top-k relevant knowledge entries related to the user question. They are mostly reliable but may not be complete or may contain some irrelevant information. When hints disagree with examples, prioritize user inputs, then examples, then other hints. Use your best judgement to decide which parts are useful.",
                    "Balance quality and efficiency, for user experience, try to submit correct SQL as early as possible with fewer tool calls whenever possible.",
                    "When certain about the final SQL query, output the SQL as response in markdown code block format '```sql ... ```'.",
                    # "The SQL queries must contain comment headers as in the examples.",
                    # "`submit_sql` validates SQL queries by executing them. If the execution fails, you must continue refining your SQL until it passes validation.",
                    # f"The output SQL should be commented. Always use `/* comment */` style for comments in {dialect} SQL, imitate the style shown in the examples.",
                    "When the user query is an irrelevant task, politely decline to answer it and briefly introduce the database and suggested queries in natural language, then ends your response with `[END]` to force a termination.",
                    "Before you submit SQL or terminate, always double-check the user intent to ensure your response aligns with user needs (e.g., list records vs aggregation, counts, or distincts).",
                    # "When the user query is an analysis task, do not submit a SQL. Instead, explore the database via `exec_sql`, summarize and answer the question in natural language and end your response with `[END]` to force a termination.",
                ],
            )
        )

    RUBIK_KB.batch_upsert(
        [
            db_gen_desc_prompt,
            col_gen_desc_prompt,
            col_gen_syns_prompt,
            tab_gen_desc_prompt,
            tab_gen_syns_prompt,
            enum_gen_syns_prompt,
            datetime_parser_prompt,
            synonym_extraction_prompt,
            autoviz_deduct_prompt,
        ]
        + comment_sql_prompts
        + nl2sql_with_tools_prompts
    )


# Temporary trigger for initial setup
if (len(RUBIK_KB.storages["_prompts"]) == 0) or (RUBIK_CM.get("core.debug")):
    setup_rubik_kb()


if __name__ == "__main__":
    setup_rubik_kb()

    # Debug
    for r in RUBIK_KB.search(engine="prompts", name="autoviz_type"):
        print(f"Found prompt: {r['kl'].name}")
    exit(0)
