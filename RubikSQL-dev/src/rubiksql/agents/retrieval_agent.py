__all__ = [
    "RubikSQLRetrievalAgentSpec",
]

from typing import Any, Dict, List, Optional, Tuple, Generator

from ahvn.agent.base import BasePromptAgentSpec, AgentStreamChunk
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.tool.base import ToolSpec
from ahvn.llm import Messages, format_messages
from ahvn.cache import CacheEntry
from ahvn.ukf.base import BaseUKF
from ahvn.utils.basic.config_utils import HEAVEN_CM, dget, dset
from ahvn.utils.basic.serialize_utils import dumps_json, loads_json
from ahvn.utils.basic.misc_utils import unique

from rubiksql.utils.config_utils import RUBIK_CM


class RubikSQLRetrievalAgentSpec(BasePromptAgentSpec):
    """Agent spec for retrieval-only KL search."""

    def __init__(
        self,
        klbase,
        prompt: Optional[PromptUKFT] = None,
        llm_args: Optional[Dict[str, Any]] = None,
        max_steps: Optional[int] = None,
        tool_choice: str = "auto",
        **kwargs,
    ):
        self.klbase = klbase
        tools = self._build_tools()
        self.tools = tools
        super().__init__(
            prompt=prompt,
            tools=tools,
            llm_args=llm_args,
            max_steps=max_steps,
            **kwargs,
        )

    def _engine_condition_desc(self, engine_name: str) -> str:
        engine_cfg = (RUBIK_CM.get("klbase.engines", {}) or {}).get(engine_name, {}) or {}
        condition = engine_cfg.get("condition", {}) or {}
        include = condition.get("type_include")
        exclude = condition.get("type_exclude")
        # TODO
        return f"type_include={include}, type_exclude={exclude}"

    def _build_tools(self) -> List[ToolSpec]:
        tools: List[ToolSpec] = []
        for engine_name in self.klbase.engines.keys():
            condition_desc = self._engine_condition_desc(engine_name)

            def _make_search(name: str, cond_desc: str):
                # TODO , a description of the usage scenarios for each engine. 
                def search(
                    query: Optional[str] = None,
                    topk: Optional[int] = None,
                    fetchk: Optional[int] = None,
                    offset: Optional[int] = None,
                    orderby: Optional[List[str]] = None,
                    filters: Optional[Dict[str, Any]] = None,
                    mode: Optional[str] = None,
                ) -> str:
                    f"""Search KLs using engine {engine_name}.

                    The search scenarios applicable to this engine: ...
                    Types of KL can be retrieved: {condition_desc}.

                    Args:
                        query: Query string for semantic or string search.
                        topk: Max results to return.
                        fetchk: Candidate size for vector-like engines.
                        offset: Offset for scan-like engines.
                        orderby: Sort fields for engines that support ordering.
                        filters: Structured filters merged into engine kwargs.
                        mode: Optional engine search mode.

                    Returns:
                        str: JSON with engine metadata and results.
                    """.format(
                        engine_name=name,
                        cond_desc=cond_desc,
                    )
                    kwargs = dict(filters or {})
                    if query is not None:
                        kwargs["query"] = query
                    if topk is not None:
                        kwargs["topk"] = topk
                    if fetchk is not None:
                        kwargs["fetchk"] = fetchk
                    if offset is not None:
                        kwargs["offset"] = offset
                    if orderby is not None:
                        kwargs["orderby"] = orderby
                    results = self.klbase.search(engine=name, mode=mode, **kwargs)
                    payload = {
                        "engine": name,
                        "mode": mode,
                        "results": [
                            {
                                "id": r.get("id"),
                                "kl": (r.get("kl").to_dict() if isinstance(r.get("kl"), BaseUKF) else None),
                            }
                            for r in results
                        ],
                    }
                    return dumps_json(payload)

                search.__name__ = f"search_{name}"
                return search

            search_func = _make_search(engine_name, condition_desc)
            tools.append(ToolSpec.from_function(search_func, name=f"search_{engine_name}", parse_docstring=True))
        return tools

    def encode(self, query: str, hints: Optional[List[str]] = None, **kwargs) -> Tuple[Messages, Dict[str, Any]]:
        # TODO
        hints = hints or []
        lang = kwargs.pop("lang", None) or HEAVEN_CM.get("prompts.lang")
        instance = CacheEntry.from_args(
            func="kl_search",
            query=query,
            output=...,
            metadata={"hints": hints},
        )
        rendered = self.prompt.format(
            instance=instance,
            lang=lang,
            **kwargs,
        ).strip()
        return format_messages(rendered), None

    def update_state(self, state: Dict) -> Dict:
        state = super().update_state(state)
        dset(state, "inference[-1].tools", self.tools)
        return state

    def decode(self, messages: Messages, state: Dict) -> Any:
        # TODO
        return None
