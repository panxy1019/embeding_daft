"""
NL2SQL Extract System Encoder (Workflow: Seed + Expand).

Key design (per user spec):
- During _build_hints, we seed an initial knowledgespace using klbase.search(engine="ac"...).
- Each seeded item is written into knowledgespace as a labelled string of the form:
    "[E01] \"<hit>\" in query may refer to value <kl_name>."
    "[C01] \"<hit>\" in query may refer to column <kl_name>."
    "[T01] \"<hit>\" in query may refer to table <kl_name>."
    "[D01] \"<hit>\" in query may refer to database <kl_name>."
    "[K01] \"<hit>\" in query may refer to knowledge <kl_name>."
"""

__all__ = [
    "NL2SQLExtractEncoder",
]

from typing import List, Optional, TYPE_CHECKING

from ahvn.utils.basic import dget
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.cache import CacheEntry

if TYPE_CHECKING:
    from .tools import RubikSQLToolkit
    from .klbase import RubikSQLKLBase


class NL2SQLExtractEncoder:
    """
    System prompt encoder for Extract Agent.

    Responsibilities:
        1) Seed initial labelled knowledgespace (query <-> KB bridging hints)
        2) Render a structured system prompt that instructs the agent to:
            - optionally add more knowledge via tools
            - finally submit ordered labels via submit_kls
    """

    SYSTEM_PROMPT = (
        "You are an information extraction agent for NL2SQL. "
        "Your task is NOT to write SQL. "
        "Your task is to collect relevant database knowledge and submit an ordered list of knowledge labels."
    )

    INSTRUCTIONS = [
        "Initial labelled knowledge has been auto-seeded in the Hints section (e.g. [T01], [C01], [E01], [D01], [K01]).",
        "You may call tools (tab_info/col_info/fuzzy_enum/add_knowledge) to add more knowledge on top of the initial space.",
        "When information is sufficient, you MUST call `submit_kls(kls=[...])` with an ORDERED list of labels.",
        "Suggested ordering: D* -> T* -> C* -> E* -> K* (keep the list short and focused).",
        "Do NOT output SQL. Do NOT output final natural language answers. Only finish via `submit_kls`!",
        "you MUST call `submit_kls` tool before reaching the max steps!",
    ]

    def __init__(
        self,
        toolkit: "RubikSQLToolkit",
        klbase: "RubikSQLKLBase",
        seed_from_ac: bool = True,
        seed_topk: int = 12,
        reset_space_each_call: bool = True,
    ):
        """
        Args:
            toolkit: RubikSQLToolkit (should be the FULL toolkit, because we call db_info in the encoder).
            klbase: RubikSQLKLBase.
            seed_from_ac: Whether to seed initial knowledgespace from "ac" search results.
            seed_topk: Maximum number of seeded items to commit into space.
            reset_space_each_call: Whether to reset knowledgespace per query for deterministic labels.
        """
        self.toolkit = toolkit
        self.klbase = klbase
        self.seed_from_ac = seed_from_ac
        self.seed_topk = seed_topk
        self.reset_space_each_call = reset_space_each_call

        self.prompt = PromptUKFT.from_path(
            path="& prompts/system",
            default_entry="prompt.jinja",
        )

    # -----------------------------
    # knowledgespace helpers
    # -----------------------------

    def _reset_knowledgespace(self) -> None:
        """
        Reset space so labels are deterministic per query (T01/C01/... always start from 01).
        """
        ks = self.toolkit.knowledgespace
        for k in ["E", "C", "T", "D", "K"]:
            if k not in ks or ks[k] is None:
                ks[k] = []
            else:
                ks[k].clear()

    @staticmethod
    def _strip_label_prefix(line: str) -> str:
        """
        Convert "[E01] payload" -> "payload". Used for dedup by payload.
        """
        if isinstance(line, str) and line.startswith("[") and "] " in line:
            return line.split("] ", 1)[1]
        return str(line)

    def _append_labelled_payload(self, category: str, payload: str) -> str:
        """
        Append a labelled string into knowledgespace[category].
        """
        ks = self.toolkit.knowledgespace
        if category not in ks or ks[category] is None:
            ks[category] = []

        for existing in ks[category]:
            if self._strip_label_prefix(existing) == payload:
                return existing

        idx = len(ks[category]) + 1
        label = f"{category}{idx:02d}"
        line = f"[{label}] {payload}"
        ks[category].append(line)
        return line

    # -----------------------------
    # Seeding logic (core feature)
    # -----------------------------

    def _seed_space_from_ac(self, query: str) -> List[str]:
        """
        Seed initial knowledgespace from klbase.search(engine="ac"...).
        """
        if not self.seed_from_ac:
            return []

        seeded: List[str] = []

        for r in self.klbase.search(engine="ac", query=query, whole_word=False):
            kl = r.get("kl")
            if kl is None:
                continue

            kl_type = getattr(kl, "type", "") or ""
            kl_name = getattr(kl, "name", "") or ""

            search_strs = "/".join(dget(getattr(kl, "metadata", {}), "search.returns.strs") or [])
            hit = search_strs if search_strs else kl_name

            if kl_type == "db-enum":
                category = "E"
                payload = f'"{hit}" in query may refer to value {kl_name}.'
            elif kl_type == "db-column":
                category = "C"
                payload = f'"{hit}" in query may refer to column {kl_name}.'
            elif kl_type == "db-table":
                category = "T"
                payload = f'"{hit}" in query may refer to table {kl_name}.'
            elif kl_type == "db-database":
                category = "D"
                payload = f'"{hit}" in query may refer to database {kl_name}.'
            else:
                category = "K"
                payload = f'"{hit}" in query may refer to knowledge {kl_name}.'

            line = self._append_labelled_payload(category, payload)
            seeded.append(line)

            if len(seeded) >= self.seed_topk:
                break

        return seeded

    def _build_hints(self, query: str, hints: List[str]) -> List[str]:
        """
        Build hints list for prompt rendering.
        """
        seeded_lines = self._seed_space_from_ac(query)

        if not seeded_lines:
            seeded_lines = [
                self._append_labelled_payload(
                    "K",
                    "No AC hits for this query. Use tools (tab_info/col_info/fuzzy_enum/add_knowledge) to add knowledge, then submit labels via submit_kls.",
                )
            ]

        out: List[str] = []
        out.extend(seeded_lines)
        if hints:
            out.extend(hints)
        return out

    def __call__(self, query: str, hints: Optional[List[str]] = None, **kwargs) -> str:
        """
        Render the system prompt for Extract Agent, with initial knowledgespace seeded.
        """
        hints = hints or []

        if self.reset_space_each_call:
            self._reset_knowledgespace()

        db_info_tool = self.toolkit.get_tool("db_info")
        db_info = db_info_tool() if db_info_tool is not None else ""

        all_hints = self._build_hints(query, hints)

        instance = CacheEntry.from_args(
            query=query,
            output=...,
            metadata={"hints": all_hints},
        )

        return self.prompt.render(
            system=self.SYSTEM_PROMPT,
            descriptions=[db_info] if db_info else [],
            examples=[],
            instructions=self.INSTRUCTIONS,
            instance=instance,
            **kwargs,
        )
