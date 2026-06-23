__all__ = [
    "BaseAgentSpec",
    "BasePromptAgentSpec",
    "AgentStreamChunk",
]

from ..utils.basic.config_utils import CM_AHVN
from ..utils.basic.log_utils import get_logger
from ..utils.basic.func_utils import (
    can_infer_sig,
    code2func,
    desc2short,
    func2meta,
    funcwrap,
    merge_meta,
    norm_schema,
    sig2func,
    sig2name,
    sig2meta,
    synthesize_def,
)
import inspect

logger = get_logger(__name__)

import time
from datetime import datetime, timezone

from ..utils.llm import Messages, LLM, gather_stream, format_messages, round_elapsed
from ..utils.basic.parser_utils import parse_md
from ..ukf.templates.basic.prompt import PromptUKFT
from ..ukf.templates.basic.tool import ToolType
from ..ukf.templates.basic.skill import SkillType
from ..tool.base import ToolSpec
from ..tool.skill import SkillToolSpec
from ..utils.prompt import PM_AHVN, PromptSpec
from ..utils.basic.config_utils import dget, dset

from typing import Dict, Any, List, Optional, Iterable, Generator, Tuple, Union

from abc import ABC, abstractmethod
from copy import deepcopy

# Type alias for stream chunks
AgentStreamChunk = Dict[str, Any]


class BaseAgentSpec(ABC):
    """\
    LLM Agents in AHVN can be used as end-to-end functions `func(**inputs) -> output`, or more generally as a producer of a stream of events `func(**inputs) -> Generator[AgentStreamChunk]` for more fine-grained control and observability.

    Specifically, the main LLM Agent run contains three stages:
    - Encode: `agent.encode(**inputs) -> (messages, state)` encodes the arbitrary inputs into the initial messages stack, and a initial state dict (optional and arbitrary, only the `metadata` field is reserved for internal use, other fields can be used freely for custom logic and data passing between steps).
    - Step: `agent.step(messages, state) -> (messages, state)` runs a single step of the agent, which will be continuously called until the agent finishes or reaches the maximum step limit.
    - Decode: `agent.decode(messages, state) -> output` decodes the final messages and state into the agent's arbitrary output.

    And each `step` consists of three phases, maintaining the same messages and state paradigm:
    - Inference: invoke the LLM to generate responses, this is often used as the `delta_messages` to be processed in future phases. `delta_messages` contains ALL new messages generated from a single LLM invocation, including assistant messages and tool messages. This step is built-in and often does not need to be customized, but can be overridden if needed (e.g., for non-LLM-powered agents).
    - Process(delta_messages, state) -> (delta_messages, state): process the `delta_messages` and update the agent state accordingly, while updating the `delta_messages` (e.g., this is where user proxies or customized tool handling often happens). The processed `delta_messages` and updated state will be passed to the next `compact` phase.
    - Compact(messages, delta_messages, state) -> (messages, state): compact the original messages with the new `delta_messages` after processing, and further update the state if needed. This phase is often used to maintain a concise messages stack for better efficiency, and not required if the agent does not need modifying past messages.
    Specifically, the `state.metadata` field is reserved for internal use, it automatically tracks the current step number (`state.metadata.step`), maximum step limit (`state.metadata.max_steps`). Here are two aspects that recommended to pay attention to for better control of the agent run:
    - `state.metadata.is_finished/state.metadata.finish_reason`: the agent will automatically finish when reaching the maximum step limit, but you can also set `state.metadata.is_finished = True` in the `process` (or `compact`) phase to finish the agent early, and set `state.metadata.finish_reason` for better observability.
    - `state.inference[-1]`: the `inference` field allow running the agent with different LLM args on each step. `inference` is a list that automatically appends an empty dict for each step, and you can set any LLM args for the next step by updating `state.inference[-1]` in the `process` (or `compact`) phase. For example, you can set `state.inference[-1].temperature = 0` to make the next step deterministic. `tools` are automatically carried over by default, but you can also update `state.inference[-1].tools` to change the tools for the next step.

    In stream mode, the agent will yield a series of `AgentStreamChunk` (dictionaries) during the run, which have hard-coded types and semantics, and carries almost all the information during the agent run for maximum observability and flexibility. This is often useful for constructing a chat UI, or for more complex agent orchestration and control. For the detailed semantics of the stream events, please refer to the `stream` method docstring.

    Usage format:
    - Per-step usage is stored in `state.inference[-1].usage` and should be treated as the single source of timing/token metrics for that step.
      Each step usage entry has the shape:
        - `created_at`: step start timestamp (UTC, ISO-8601).
        - `elapsed`: end-to-end elapsed for the whole step (inference + process + compact), in seconds.
        - `usage`: nested LLM usage for that step (same format as `LLM.include=["usage"]`).
    - Final run usage is emitted as a `usage` stream event and stored at `state.metadata.usage`.
      It contains:
        - `created_at`: agent loop start timestamp (UTC, ISO-8601).
        - `elapsed`: agent end-to-end loop elapsed, in seconds.
        - flat aggregate fields: `prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_elapsed`,
          `completion_elapsed`, `inference_elapsed`, `tool_elapsed`.
        - `step_usage`: map `{ "<step_idx>": {"created_at": str, "elapsed": float, "usage": dict} }`.
    - No `completed_at` timestamp is recorded for usage; use `created_at + elapsed`.

    In conclusion, the agent centers around (messages, state) maintainance with a simple pipeline: encode -> step(inference, process, compact) -> decode.
    To customize the agent for a new scenario, it is often recommended to first design the state, then implement `encode` and `decode` to fit the designed state, and the agent is already runnable. Overriding `process` and `compact` is optional for more fine-grained control, and often not needed for simple scenarios. To mock the Agent for testing or non-LLM use cases, you can also override the `inference` method to yield custom `delta_messages` and `usage` without calling the LLM.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable[Any]] = None,
        sig: Optional[str] = None,
        max_steps: Optional[int] = None,
        llm_args: Optional[Dict] = None,
    ):
        super().__init__()
        self.sig_spec = sig
        self._sig_name = sig2name(sig) if sig else None
        self.name = name or self._sig_name
        if not self.name:
            raise ValueError("Agent requires a name. Provide `name` or a named signature like `def my_agent(...) -> ...`.")
        self.max_steps = CM_AHVN.get("agent.max_steps", 20) if max_steps is None else max_steps
        self.llm = LLM(**(llm_args or dict()))
        self._short_description = short_description
        self._description = description
        self._input_schema = norm_schema(input_schema)
        self._output_schema = deepcopy(output_schema)
        self._examples = deepcopy(examples)

    @property
    def description(self) -> str:
        return self.resolved_tool_meta().get("description", "")

    @description.setter
    def description(self, value: str):
        self._description = value or ""

    @property
    def short_description(self) -> str:
        return self.resolved_tool_meta().get("short_description", "")

    @short_description.setter
    def short_description(self, value: str):
        self._short_description = value or ""

    @property
    def input_schema(self) -> Optional[Dict[str, Any]]:
        return self.resolved_tool_meta().get("input_schema")

    @input_schema.setter
    def input_schema(self, value: Optional[Dict[str, Any]]):
        self._input_schema = norm_schema(value)

    @property
    def output_schema(self) -> Optional[Dict[str, Any]]:
        return self.resolved_tool_meta().get("output_schema")

    @output_schema.setter
    def output_schema(self, value: Optional[Dict[str, Any]]):
        self._output_schema = deepcopy(value)

    @property
    def examples(self):
        return self.resolved_tool_meta().get("examples")

    @examples.setter
    def examples(self, value):
        self._examples = value

    def _infer_class_doc_meta(self) -> Dict[str, Any]:
        docstring = inspect.getdoc(type(self)) or ""
        if not docstring:
            return {}
        description = docstring.strip()
        return {
            "description": description,
            "short_description": desc2short(description),
        }

    def _infer_run_meta(self) -> Dict[str, Any]:
        try:
            return func2meta(self.run, include_docstring=True)
        except Exception:
            return {}

    def _infer_sig_meta(self) -> Dict[str, Any]:
        if not self.sig_spec:
            return {}
        try:
            return sig2meta(self.sig_spec, name=self.name)
        except Exception:
            return {}

    def resolved_tool_meta(self) -> Dict[str, Any]:
        resolved: Dict[str, Any] = merge_meta(
            {"name": self.name},
            self._infer_class_doc_meta(),
            self._infer_run_meta(),
            self._infer_sig_meta(),
            {
                "name": self.name,
                "short_description": self._short_description,
                "description": self._description,
                "input_schema": self._input_schema,
                "output_schema": self._output_schema,
                "examples": self._examples,
            },
        )
        resolved["name"] = resolved.get("name") or self.name
        resolved["input_schema"] = norm_schema(resolved.get("input_schema"))
        if not resolved.get("short_description") and resolved.get("description"):
            resolved["short_description"] = desc2short(resolved["description"])
        return resolved

    @staticmethod
    def _wrap_tool_result(result: Any, output_schema: Optional[Dict[str, Any]]) -> Any:
        if output_schema is None or isinstance(result, dict):
            return result
        properties = output_schema.get("properties", {}) if isinstance(output_schema, dict) else {}
        if len(properties) == 1:
            return {next(iter(properties)): result}
        if "result" in properties:
            return {"result": result}
        return result

    def to_tool(
        self,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable[Any]] = None,
        sig: Optional[str] = None,
        parse_docstring: bool = False,
    ) -> ToolSpec:
        resolved = self.resolved_tool_meta()
        resolved_sig = sig if sig is not None else self.sig_spec
        sig_name = sig2name(resolved_sig) if resolved_sig else None

        if resolved_sig:
            fallback_sig_name = name or sig_name or resolved.get("name") or self.name
            try:
                resolved = merge_meta(resolved, sig2meta(resolved_sig, name=fallback_sig_name))
            except Exception:
                pass

        resolved = merge_meta(
            resolved,
            {
                "short_description": short_description,
                "description": description,
                "input_schema": deepcopy(input_schema),
                "output_schema": deepcopy(output_schema),
                "examples": deepcopy(examples),
            },
        )
        resolved["name"] = name or sig_name or resolved.get("name") or self.name
        resolved["input_schema"] = norm_schema(resolved.get("input_schema"))
        if not resolved.get("short_description") and resolved.get("description"):
            resolved["short_description"] = desc2short(resolved["description"])

        sig_func = None
        if resolved_sig:
            try:
                sig_func = sig2func(resolved_sig, name=resolved["name"])
            except Exception:
                sig_func = None

        if sig_func is None and can_infer_sig(self.run):
            sig_func = self.run
        if sig_func is None:
            sig_func = code2func(
                synthesize_def(
                    name=resolved["name"],
                    input_schema=resolved.get("input_schema"),
                    output_schema=resolved.get("output_schema"),
                    docstring=resolved.get("description") or resolved.get("short_description"),
                    code="pass",
                ),
                func_name=resolved["name"],
            )

        def _tool_exec(**tool_inputs):
            result = self.run(**tool_inputs)
            return self._wrap_tool_result(result, resolved.get("output_schema"))

        tool_callable = funcwrap(exec_func=_tool_exec, sig_func=sig_func)
        return ToolSpec.from_func(
            tool_callable,
            name=resolved["name"],
            short_description=resolved.get("short_description"),
            description=resolved.get("description"),
            input_schema=resolved.get("input_schema"),
            output_schema=resolved.get("output_schema"),
            examples=resolved.get("examples"),
            parse_docstring=parse_docstring,
        )

    def to_sig(self, **kwargs) -> str:
        return self.to_tool().to_sig(**kwargs)

    def to_jsonschema(self, **kwargs) -> Dict[str, Any]:
        return self.to_tool().to_jsonschema(**kwargs)

    def to_mcp(self):
        return self.to_tool().to_mcp()

    def to_fastmcp(self):
        return self.to_tool().to_fastmcp()

    def to_ukf(self, transport: Optional[Dict[str, Any]] = None, **updates):
        return self.to_tool().to_ukf(transport=transport, **updates)

    def run(self, **inputs) -> Any:
        for chunk in reversed(list(self.stream(**inputs))):
            if "output" in chunk:
                return chunk["output"]
            if "err_msg" in chunk:
                logger.error(chunk["err_msg"])
                raise chunk["err"]

    @abstractmethod
    def encode(self, **inputs) -> Union[Messages, Tuple[Messages, Dict]]:
        """
        Encode inputs into LLM messages and an optional initial state (A default state is provided if not returned).

        Args:
            **inputs: Arbitrary input arguments.

        Returns:
            Messages or (Messages, state): The encoded messages and optional initial state.
                It is recommended to return (Messages, state) for better flexibility.
        """
        pass

    @abstractmethod
    def decode(self, messages: Messages, state: Dict) -> Any:
        """
        Decode LLM messages and final state into the agent's output.

        Args:
            messages: Final conversation messages.
            state: Final agent state.

        Returns:
            Decoded output.
        """
        pass

    # @abstractmethod
    def process(self, messages: Messages, delta_messages: Messages, state: Dict) -> Tuple[Messages, Dict]:
        """
        Process the agent state and process the delta messages from the LLM.

        `process` never changes the original messages, but can modify the state and delta_messages as needed.
        To modify the original messages, use `compact` instead, which is executed after `process`.

        Default behavior: return (delta_messages, state)

        Args:
            messages: Current conversation messages.
            delta_messages: Delta messages from the LLM.
            state: Current agent state.

        Returns:
            Updated delta messages and state.
        """
        return (delta_messages, state)

    # @abstractmethod
    def compact(self, messages: Messages, delta_messages: Messages, state: Dict) -> Tuple[Messages, Dict]:
        """
        Compact the original messages based on the delta messages from the LLM.

        `compact` is executed after `process`, and modify the entire messages stack.
        Here the "delta_messages" and "state" are returned from `process`.

        Default behavior: return tuple([msg for msg in messages + delta_messages], state)

        Args:
            messages: Current conversation messages.
            delta_messages: Delta messages from the LLM.
            state: Current agent state.

        Returns:
            Updated messages and state.
        """
        return ([msg for msg in messages + delta_messages], state)

    def inference(self, messages: Messages, **kwargs) -> Generator[AgentStreamChunk, None, Any]:
        for chunk in self.llm.stream(messages=messages, **kwargs, include=["text", "think", "tool_calls", "delta_messages", "usage"], reduce=False):
            yield chunk
        return

    def update_state(self, state: Dict) -> Dict:
        state = state if state is not None else dict()
        dset(state, "metadata.step", dget(state, "metadata.step", 0) + 1)
        dset(state, "metadata.max_steps", self.max_steps)
        dset(state, "metadata.is_finished", False)
        dset(state, "metadata.finish_reason", None)
        if not isinstance(dget(state, "inference"), list):
            dset(state, "inference", list())
        previous_tools = dget(state, "inference[-1].tools", list()) if dget(state, "inference") else list()
        dset(state, "inference[-]", dict())  # append an empty dict for the current step
        dset(state, "inference[-1].tools", previous_tools)  # carry over previous tools by default
        # dset(state, "inference[-1].skills", list())   # skills must be used in prompt `skillspecs`, as there is no standard skills argument during LLM call
        return state

    @staticmethod
    def _usage_number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _round_usage_number(cls, value: Any, ndigits: int = 4) -> Optional[float]:
        if ndigits != 4:
            return round_elapsed(value, ndigits=ndigits)
        return round_elapsed(value)

    @classmethod
    def aggregate_usage(
        cls,
        state: Optional[Dict],
        created_at: Optional[str] = None,
        elapsed: Optional[float] = None,
    ) -> Dict[str, Any]:
        """\
        Aggregate per-step usage from ``state.inference`` into final run usage.

        Returned shape:
            {
                "created_at": str,
                "elapsed": float,
                "prompt_tokens": int,
                "completion_tokens": int,
                "total_tokens": int,
                "prompt_elapsed": float,
                "completion_elapsed": float,
                "inference_elapsed": float,
                "tool_elapsed": float,
                "step_usage": {
                    "<step_idx>": {
                        "created_at": str,
                        "elapsed": float,
                        "usage": dict,
                    },
                },
            }

        Notes:
            - Uses only start-time + elapsed representation (no completed-at timestamp).
            - Each step usage entry is expected under ``state.inference[i].usage`` with
              shape ``{"created_at", "elapsed", "usage"}``.
        """
        state = state or dict()
        inference_steps = dget(state, "inference", list()) or list()

        llm_numeric_fields = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_elapsed",
            "completion_elapsed",
            "inference_elapsed",
            "tool_elapsed",
        )
        totals_float = {k: 0.0 for k in llm_numeric_fields}
        step_usage = dict()

        for step_idx, step_data in enumerate(inference_steps, start=1):
            if not isinstance(step_data, dict):
                continue
            step_entry = deepcopy(step_data.get("usage") or dict())
            if not isinstance(step_entry, dict):
                step_entry = dict()
            step_created_at = step_entry.get("created_at")
            step_elapsed = cls._round_usage_number(step_entry.get("elapsed"))
            nested_usage = deepcopy(step_entry.get("usage") or dict())
            if not isinstance(nested_usage, dict):
                nested_usage = dict()

            for key in llm_numeric_fields:
                usage_value = cls._usage_number(nested_usage.get(key))
                if usage_value is not None:
                    totals_float[key] += usage_value
            step_usage[str(step_idx)] = (
                {"created_at": step_created_at, "elapsed": step_elapsed, "usage": nested_usage}
                if (step_created_at is not None or step_elapsed is not None or nested_usage)
                else {"usage": nested_usage}
            )

        usage = {
            "created_at": created_at,
            "elapsed": cls._round_usage_number(elapsed),
            "prompt_tokens": int(round(totals_float["prompt_tokens"])),
            "completion_tokens": int(round(totals_float["completion_tokens"])),
            "total_tokens": int(round(totals_float["total_tokens"])),
            "prompt_elapsed": round_elapsed(totals_float["prompt_elapsed"]),
            "completion_elapsed": round_elapsed(totals_float["completion_elapsed"]),
            "inference_elapsed": round_elapsed(totals_float["inference_elapsed"]),
            "tool_elapsed": round_elapsed(totals_float["tool_elapsed"]),
            "step_usage": step_usage,
        }
        return {k: v for k, v in usage.items() if v is not None}

    def step(self, messages: Messages, state: Dict) -> Generator[AgentStreamChunk, None, Any]:
        """Run a single inference step of the agent."""
        state = self.update_state(state)
        step = dget(state, "metadata.step", 1)
        step_created_at = datetime.now(timezone.utc).isoformat()
        step_t0 = time.perf_counter()
        yield {"start": step} | {"step_idx": step}

        try:
            chunks = list()
            for chunk in self.inference(messages, **dget(state, "inference[-1]", dict())):
                yield {"chunk": {k: v for k, v in chunk.items() if k != "delta_messages"}} | {"step_idx": step}
                chunks.append(chunk)
        except Exception as e:
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "error")
            yield {"err_msg": f"Error during agent step `{step}.inference`.", "state": state, "err": e} | {"step_idx": step}
            return

        try:
            gathered = gather_stream(chunks, reduce=False)
        except Exception as e:
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "error")
            yield {"err_msg": f"Error during agent step `{step}.gather_stream`.", "state": state, "err": e, "end": step} | {"step_idx": step}
            return

        # Store usage from LLM inference in state
        step_llm_usage = deepcopy(gathered.get("usage")) if isinstance(gathered, dict) else dict()
        if not isinstance(step_llm_usage, dict):
            step_llm_usage = dict()

        try:
            proc_delta_messages, state = self.process(messages, gathered.get("delta_messages", list()), state=state)
            yield {"delta_messages": proc_delta_messages, "state": state} | {"step_idx": step}
        except Exception as e:
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "error")
            yield {"err_msg": f"Error during agent step `{step}.process`.", "state": state, "err": e, "end": step} | {"step_idx": step}
            return

        try:
            compact_messages, state = self.compact(messages, proc_delta_messages, state=state)
            yield {"messages": compact_messages, "state": state} | {"step_idx": step}
        except Exception as e:
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "error")
            yield {"err_msg": f"Error during agent step `{step}.compact`.", "state": state, "err": e, "end": step} | {"step_idx": step}
            return

        # Record step timing
        dset(
            state,
            "inference[-1].usage",
            {
                "created_at": step_created_at,
                "elapsed": round_elapsed(time.perf_counter() - step_t0),
                "usage": step_llm_usage,
            },
        )

        yield {"end": step} | {"step_idx": step}
        return

    def stream(self, **inputs) -> Generator[AgentStreamChunk, None, Any]:
        """
        Run the agent in streaming mode.

        Args:
            **inputs: Arbitrary input arguments for encoding.

        Yields:
            AgentStreamChunk:
                The stream while produce the following events:
                    - start/end: The start and end of each step.
                    - chunk: Each chunk from the LLM during a step (includes usage on the final LLM chunk).
                    - delta_messages: Appended messages after a step (processed).
                    - messages: Updated messages stack after a step (processed + compacted), initial, or at the end.
                    - usage: Final aggregated usage for the agent run. This is the canonical place for run-level timing/tokens.
                        usage event shape:
                        {
                            "created_at": str,
                            "elapsed": float,
                            "prompt_tokens": int,
                            "completion_tokens": int,
                            "total_tokens": int,
                            "prompt_elapsed": float,
                            "completion_elapsed": float,
                            "inference_elapsed": float,
                            "tool_elapsed": float,
                            "step_usage": {
                                "<step_idx>": {
                                    "created_at": str,
                                    "elapsed": float,
                                    "usage": ...
                                }
                            }
                        }
                    - state: Updated agent state after each process, compact, initial, or at the end.
                        state.inference[-1].usage contains per-step usage (LLM + step timing).
                        state.metadata.usage contains run-level aggregated usage.
                    - output: Final decoded output after completion.
                    - err/err_msg: If any error occurs during processing.
                Each event contains `step_idx` to quickly identify which step it belongs to (allowing out-of-order events for better UI rendering).
                Step starts at 1. The initial encoding before any step is considered as step 0 (with step_idx=0), the decode and final output has step_idx=None.
        """
        agent_created_at = datetime.now(timezone.utc).isoformat()
        agent_t0 = time.perf_counter()

        try:
            encoded = self.encode(**inputs)
            messages, state = encoded if isinstance(encoded, tuple) else (encoded, None)
        except Exception as e:
            yield {"err_msg": "Error during agent `encode`.", "err": e, "messages": list(), "state": None} | {"step_idx": 0}
            return
        state = state or dict()
        yield {"messages": messages, "state": state} | {"step_idx": 0}

        # If `encode()` already decided to finish early, skip `step()` entirely.
        # (Some agents return `messages` empty/None with `state.metadata.is_finished=True`.)
        if not dget(state, "metadata.is_finished", False):
            for _ in range(self.max_steps):
                for chunk in self.step(messages, state):
                    yield chunk
                    if "messages" in chunk:
                        messages = chunk.get("messages")
                    if "state" in chunk:
                        state = chunk.get("state")
                if dget(state, "metadata.is_finished", True):
                    break
            else:
                dset(state, "metadata.is_finished", True)
                dset(state, "metadata.finish_reason", "max_steps_reached")

        agent_elapsed = round_elapsed(time.perf_counter() - agent_t0)

        usage = self.aggregate_usage(
            state=state,
            created_at=agent_created_at,
            elapsed=agent_elapsed,
        )
        dset(state, "metadata.usage", usage)
        yield {"usage": usage, "state": state} | {"step_idx": None}

        try:
            output = self.decode(messages, state=state)
            yield {"output": output, "messages": messages, "state": state} | {"step_idx": None}
        except Exception as e:
            yield {"err_msg": "Error during agent `decode`.", "messages": messages, "state": state, "err": e} | {"step_idx": None}
        return

    def __call__(self, **inputs) -> Any:
        return self.run(**inputs)

    def replay(self, chunks: Iterable[AgentStreamChunk], step: Optional[int] = None) -> Tuple[Messages, Dict]:
        """
        Replay a stream of AgentStreamChunk to reconstruct the final messages and state after step `step`.

        Args:
            chunks: An iterable of AgentStreamChunk.
            step: Optional step number to replay up to (inclusive). If None, replay all steps.

        Returns:
            A tuple of (Messages, state) representing the final messages and state after replaying the stream.
        """
        messages, state = list(), None
        for chunk in chunks:
            if "messages" in chunk:
                messages = chunk["messages"]
            if "state" in chunk:
                state = chunk["state"]
            if "err_msg" in chunk:
                return messages, state
            if ("end" in chunk) and (step is not None) and (int(chunk["end"]) >= int(step)):
                return messages, state
        return messages, state


class BasePromptAgentSpec(BaseAgentSpec):
    def __init__(
        self,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        examples: Optional[Iterable[Any]] = None,
        sig: Optional[str] = None,
        prompt: Optional[PromptUKFT] = None,
        tools: Optional[List[ToolType]] = None,
        skills: Optional[List[SkillType]] = None,
        skills_tools: Optional[Dict[str, ToolType]] = None,
        llm_args: Optional[Dict] = None,
        max_steps: Optional[int] = None,
        **prompt_kwargs,
    ):
        super().__init__(
            name=name,
            short_description=short_description,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            examples=examples,
            sig=sig,
            max_steps=max_steps,
            llm_args=llm_args,
        )
        if prompt is None:
            default_spec = PM_AHVN.get("default_prompt")
            if not isinstance(default_spec, PromptSpec):
                from ..utils.prompt.prompt_spec import setup_system_prompts

                setup_system_prompts(force=False)
                default_spec = PM_AHVN.get("default_prompt")
            if not isinstance(default_spec, PromptSpec):
                raise ValueError("Failed to load 'default_prompt' from PM_AHVN.")
            prompt = PromptUKFT.from_spec(default_spec, name="default_prompt")
        self.prompt = prompt.clone()
        self.tools = tools or list()
        self.skills = skills or list()
        self.skills_tools = skills_tools or dict()  # skill name -> ToolSpec, used for quick lookup of tools provided by skills
        self.prompt.bind(**prompt_kwargs)
        binds = self.prompt.get("binds") or {}
        # ToolSpecs should be used in prompt only when in NL Tool Call mode, otherwise use it as LLM args `tools`
        # if "toolspecs" not in binds:
        #     self.prompt.bind(toolspecs=self.tools)
        if "skillspecs" not in binds:
            self.prompt.bind(skillspecs=self.skills)

        # Convert skills to SkillToolSpec
        self.skill_tool_spec = None
        if self.skills:
            self.skill_tool_spec = SkillToolSpec.from_skills(self.skills)

    @property
    def dynamic_tools(self) -> List[ToolSpec]:
        if not self.skill_tool_spec:
            return list()
        return [self.skills_tools[name] for name in self.skill_tool_spec.state.get("loaded_tools", list()) if name in self.skills_tools]

    def encode(self, **inputs) -> Union[Messages, Tuple[Messages, Dict]]:
        lang = inputs.pop("lang", None) or CM_AHVN.get("prompts.lang")

        rendered = self.prompt.format(lang=lang, instance={"inputs": inputs})
        return format_messages(rendered)

    def process(self, messages: Messages, delta_messages: Messages, state: Dict) -> Tuple[Messages, Dict]:
        for msg in delta_messages:
            if msg.get("role") == "assistant" and msg.get("content") and ("<output>" in msg["content"]):
                parsed_output = parse_md(msg["content"], recurse=True).get("output.text", None)
                if parsed_output is not None:
                    try:
                        output = eval(parsed_output)
                    except Exception:
                        output = parsed_output
                    dset(state, "output", output)
                    dset(state, "metadata.finish_reason", "completed")
                    dset(state, "metadata.is_finished", True)
                    break
        return delta_messages, state

    def decode(self, messages: Messages, state: Dict) -> Any:
        if dget(state, "metadata.is_finished", False):
            return dget(state, "output", None)
        return None

    def update_state(self, state: Dict) -> Dict:
        state = super().update_state(state)
        # Include skill_tool_spec (the Skill loader tool) + user tools + dynamically loaded tools
        skill_tools = [self.skill_tool_spec] if self.skill_tool_spec else []
        dset(state, "inference[-1].tools", skill_tools + self.tools + self.dynamic_tools)
        return state
