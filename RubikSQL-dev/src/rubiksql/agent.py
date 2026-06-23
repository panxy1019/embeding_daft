"""\
RubikSQL Agent Spec.

This module provides the RubikSQLAgentSpec class for executing NL2SQL tasks,
inheriting from BasePromptAgentSpec with custom encode/decode/process methods.
"""

__all__ = [
    "RubikSQLAgentSpec",
]

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from ahvn.agent.base import BasePromptAgentSpec
from ahvn.cache import CacheEntry
from ahvn.llm import Messages, format_messages
from ahvn.tool.builtins import SkillToolSpec
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.utils.basic.config_utils import HEAVEN_CM, dget, dset
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.parser_utils import parse_md
from ahvn.utils.db.db_utils import prettify_sql

from .ukfs.exp_ukft import RubikSQLExpUKFT

if TYPE_CHECKING:
    from .klbase import RubikSQLKLBase
    from .tools import RubikSQLToolkit

logger = get_logger(__name__)


class RubikSQLAgentSpec(BasePromptAgentSpec):
    """\
    Agent spec for RubikSQL NL2SQL tasks.

    Inherits from BasePromptAgentSpec (base2) and adds:
    - Custom encode method that builds hints from the KLBase and returns (Messages, state)
    - Custom process method that checks for SQL completion and handles errors
    - Custom decode method that returns transpiled SQL from state
    """

    def __init__(
        self,
        prompt: PromptUKFT,
        klbase: "RubikSQLKLBase",
        db_id: str,
        toolkit: "RubikSQLToolkit",
        llm_args: Optional[Dict] = None,
        max_steps: Optional[int] = None,
        dialect: str = "sqlite",
        **kwargs,
    ):
        """\
        Initialize the RubikSQLAgentSpec.

        Args:
            toolkit: RubikSQL toolkit containing available tools.
            klbase: Knowledge base for hint retrieval.
            prompt: PromptUKFT for system prompt generation.
            db_id: Database identifier.
            dialect: SQL dialect (default: sqlite).
            llm_args: LLM configuration arguments.
            max_steps: Maximum number of interaction steps.
            **kwargs: Additional arguments passed to parent.
        """
        tools = toolkit.get_tools(["col_info", "fuzzy_enum", "exec_sql"])
        
        # Build tool_map for skill tool resolution
        tool_map = {name: tool for name, tool in toolkit.tools.items()}
        
        super().__init__(
            prompt=prompt,
            tools=tools,
            tool_map=tool_map,
            llm_args=llm_args,
            max_steps=max_steps,
            **kwargs,
        )
        self.toolkit = toolkit
        self.klbase = klbase
        self.db_id = db_id
        self.dialect = dialect
        self.end_signal = "[END]"
        self.err_signal = "[ERROR]"

    def _build_hints_examples(
        self,
        results: List[Dict[str, Any]],
        hints: List[str],
        topk: int = 3,
        thres: float = 0.3,
    ) -> Tuple[List[str], List[RubikSQLExpUKFT]]:
        """\
        Build hints and examples from query-relevant knowledge entries and user-provided hints.

        Args:
            results: Search results from KLBase.
            hints: User-provided additional hints.
            topk: Number of examples to return. Default is 3.
            thres: Similarity threshold for example retrieval. Default is 0.3. TODO need tobe configured?

        Returns:
            Tuple of (hint strings, example entries).
        """
        kl_hints = list()
        examples = list()

        def _hint_text(kl, composer: str) -> str:
            text = kl.text(composer="default")
            return text # engine-name -> composer

        for r in results:
            if r["kl"].type == "nl2sql-query": # examples
                if r["kl"].is_inactive:
                    continue
                score = r["kl"].metadata.get("search", dict()).get("returns", dict()).get("score", 0.0)
                if score < thres:
                    continue
                examples.append(r["kl"])
                continue
            composer = "ac" if r.get("engine_name", "default") == "ac" else "default"
            kl_hints.append(_hint_text(r["kl"], composer=composer))

        hints_out = kl_hints + ["USER: " + h for h in hints]
        examples_out = [exp.to_cache_entry(output=...) for exp in examples[:topk]]
        return hints_out, examples_out

    def encode(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        schema: Optional[Any] = None,
        hints: Optional[List[str]] = None,
        retrieval_mode: Optional[str] = None,
        **kwargs,
    ) -> Messages:
        """\
        Encode inputs into messages and initial state for the agent.

        Uses RubikSQLExpUKFT structure for instance encoding with:
        - question: The user's query
        - context: Query context (user profile, time, etc.)
        - schema: Expected result schema
        - hints: Built from KLBase search + user hints

        Args:
            query: Natural language question.
            context: Optional query context dict.
            schema: Optional expected result schema.
            hints: Optional list of user-provided hints.
            **kwargs: Additional template variables.

        Returns:
            Tuple of (Messages, state): Formatted messages and initial agent state.
        """
        hints = hints or []
        context = context or {}

        retrieval_mode = retrieval_mode or self.klbase.get_retrieval_mode()
        results = self.klbase.search_kl(query=query, mode=retrieval_mode)

        # Build hints/examples from one search
        all_hints, examples = self._build_hints_examples(
            results=results,
            hints=hints,
            topk=HEAVEN_CM.get("prompts.nl2sql.example_topk", 3),
        )

        # Get database info for description
        db_info = self.toolkit.get_tool("db_info")()

        # Search skills from knowledge base
        skills = [r["kl"] for r in self.klbase.search(engine="skills", query=query, topk=1)]

        # Track loaded tool names from skills
        loaded_tool_names: Set[str] = set()
        skill_toolspec = SkillToolSpec.from_skills(skills, loaded_tool_names=loaded_tool_names)
        if skill_toolspec:
            self.tools.append(skill_toolspec)

        # Build instance kwargs
        instance_kwargs: Dict[str, Any] = {}
        if context:
            instance_kwargs["context"] = context
        if schema:
            instance_kwargs["schema"] = schema

        # Create instance using CacheEntry structure (compatible with RubikSQLExpUKFT)
        instance = CacheEntry.from_args(
            func="nl2sql",
            query=query,
            output=...,
            metadata={"hints": all_hints, "db_id": self.db_id},
            **instance_kwargs,
        )

        # Render the prompt with instance, using configured language
        lang = kwargs.pop("lang", None) or HEAVEN_CM.get("prompts.lang")

        rendered = self.prompt.format(
            descriptions=[db_info],
            skillspecs=skills,
            examples=examples,
            instance=instance,
            output_schema={"mode": "code", "args": {"language": "sql"}},
            lang=lang,
            **kwargs,
        ).strip()

        # Initialize state with loaded tool names and query
        state = {
            "loaded_tool_names": loaded_tool_names,
            "loaded_skills": [skill.name for skill in skills],
        }

        return format_messages(rendered), state

    def user_proxy(self, message: str, **kwargs) -> Dict[str, Any]:
        """\
        Create a user proxy message to inject into the conversation.

        This method acts as a user proxy, generating messages that guide
        the agent when errors occur or additional information is needed.

        Args:
            message: The message content to send as user proxy.
            **kwargs: Additional fields to include in the message dict.

        Returns:
            A message dict with role "user" and the specified content.
        """
        return {"role": "user", "content": message, **kwargs}

    def process(
        self,
        messages: Messages,
        delta_messages: Messages,
        state: Dict[str, Any],
    ) -> Tuple[Messages, Dict[str, Any]]:
        """\
        Process delta messages and state after each LLM inference step.

        This method:
        1. Loads tools from skills that were referenced
        2. Checks if the task is complete (SQL found or [END] signal)
        3. Adds user proxy messages if SQL execution failed

        Args:
            messages: Current conversation messages.
            delta_messages: Delta messages from the LLM.
            state: Current agent state.

        Returns:
            Tuple of (processed delta_messages, updated state).
        """
        self._load_skill_tools(state)

        if not delta_messages:
            dset(state, "metadata.is_finished", False)
            return delta_messages, state

        return self._process_assistant_messages(delta_messages, state)

    def _load_skill_tools(self, state: Dict[str, Any]) -> None:
        """Load tools from skills that were referenced in state."""
        loaded_tool_names = dget(state, "loaded_tool_names", set())
        existing_tool_names = {t.name for t in self.tools}

        for tool_name in loaded_tool_names:
            if tool_name in existing_tool_names:
                continue
            if tool_name not in self.tool_map:
                logger.warning(f"Tool '{tool_name}' not found in tool_map, cannot load from skill")
                continue
            self.tools.append(self.tool_map[tool_name])
            logger.info(f"Loaded tool '{tool_name}' from skill")

    def _process_assistant_messages(
        self,
        delta_messages: Messages,
        state: Dict[str, Any],
    ) -> Tuple[Messages, Dict[str, Any]]:
        """Process assistant messages to check for completion or errors."""
        for message in reversed(delta_messages):
            if message.get("role") != "assistant":
                continue

            content = message.get("content", "")

            # Check for [END] signal
            if self.end_signal in content:
                logger.info("No submit tool call found, but [END] signal detected in assistant message.")
                dset(state, "metadata.is_finished", True)
                dset(state, "metadata.finish_reason", "end_signal")
                dset(state, "metadata.final_sql", None)
                return delta_messages, state

            # Check for SQL code block
            if "```" in content:
                result = self._try_validate_sql(content, delta_messages, state)
                if result is not None:
                    return result

        return delta_messages, state

    def _try_validate_sql(
        self,
        content: str,
        delta_messages: Messages,
        state: Dict[str, Any],
    ) -> Optional[Tuple[Messages, Dict[str, Any]]]:
        """Try to extract and validate SQL from message content."""
        try:
            parsed = parse_md(content)
            sql = parsed.get("sql", parsed.get("markdown", None))
            if sql is None:
                return None

            # Validate SQL by executing submit_sql tool
            tool_result = self.tool_map.get("submit_sql")(sql=sql)

            if self.err_signal in tool_result:
                # SQL has error, add user proxy message
                logger.warning("SQL execution failed, prompting for correction")
                proxy_msg = self.user_proxy(
                    f"The SQL you submitted resulted in an error:\n```\n{tool_result}\n```\nPlease correct it and try again."
                )
                updated_messages = list(delta_messages) + [proxy_msg]
                dset(state, "inference[-1].sql_error", tool_result)
                return updated_messages, state

            # SQL is valid, task complete
            logger.info("Task completed: Valid SQL parsed from assistant message")
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "parsed_sql")
            dset(state, "metadata.final_sql", sql)
            return delta_messages, state

        except Exception as e:
            logger.debug(f"Failed to parse SQL from message: {e}")
            return None

    def decode(self, messages: Messages, state: Dict[str, Any]) -> Optional[str]:
        """\
        Decode the agent's output, returning transpiled SQL.

        Args:
            messages: Final messages from the agent run.
            state: Final agent state.

        Returns:
            Transpiled SQL string, or None if no valid SQL was found.
        """
        sql = dget(state, "metadata.final_sql", None)
        if sql is None:
            logger.warning("No SQL found in final state")
            return None

        # Transpile the SQL to standard format
        try:
            return prettify_sql(
                query=str(sql).strip(),
                dialect=self.dialect,
                comments=True,
            ).strip()
        except Exception as e:
            logger.warning(f"Failed to transpile SQL: {e}")
            return str(sql).strip() if sql else None