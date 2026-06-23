__all__ = [
    "LLM",
    "LLMResponse",
    "LLMIncludeType",
    "EmbedIncludeType",
    "exec_tool_calls",
    "repair_tool_call",
    "gather_assistant_message",
    "gather_stream",
]

from ..basic.config_utils import CM_AHVN
from ..basic.misc_utils import unique
from ..basic.parallel_utils import Parallelized
from ..basic.request_utils import NetworkProxy
from ..basic.debug_utils import error_str, raise_mismatch
from ..basic.serialize_utils import loads_json, dumps_json, heal_json
from ..basic.log_utils import get_logger, encrypt_display

from .spec import LLM_CONFIG_ENGINE
from .llm_utils import get_litellm, get_litellm_retryable_exceptions
from .llm_utils import Messages, format_messages, round_elapsed

from ...cache.base import BaseCache
from ...cache.no_cache import NoCache
from ...cache.disk_cache import DiskCache
from ...tool.base import ToolSpec

logger = get_logger(__name__)

import inspect
import json
import os
import time
from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Generator, AsyncGenerator, Any, Dict, List, Optional, Union, Iterable, Literal
from dataclasses import dataclass, field
from copy import deepcopy


def _normalize_tool_call_delta(tool_call) -> Dict[str, Any]:
    """\
    Normalize a tool call delta object to a dict format.
    Handles both dict and litellm ChoiceDeltaToolCall objects.
    """
    if isinstance(tool_call, dict):
        return tool_call
    # Handle litellm ChoiceDeltaToolCall objects
    result = {
        "index": getattr(tool_call, "index", None),
        "id": getattr(tool_call, "id", None),
        "type": getattr(tool_call, "type", "function"),
    }
    func = getattr(tool_call, "function", None)
    if func:
        result["function"] = {
            "name": getattr(func, "name", None),
            "arguments": getattr(func, "arguments", "") or "",
        }
    return result


def _merge_tool_call_deltas(accumulated: List[Dict[str, Any]], deltas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """\
    Merge incremental tool call deltas into accumulated tool calls by index.
    """
    for delta in deltas:
        raw_idx = delta.get("index", None)
        idx = raw_idx if raw_idx is not None else (len(accumulated) - bool(delta.get("function", {}).get("name") is None))
        # Extend the list if necessary
        while idx >= len(accumulated):
            accumulated.append({"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
        # Merge delta into accumulated
        if delta.get("id"):
            accumulated[idx]["id"] = delta["id"]
        if delta.get("type"):
            accumulated[idx]["type"] = delta["type"]
        if "function" in delta:
            func_delta = delta["function"]
            if func_delta.get("name"):
                accumulated[idx]["function"]["name"] = func_delta["name"]
            if func_delta.get("arguments"):
                accumulated[idx]["function"]["arguments"] = (accumulated[idx]["function"].get("arguments") or "") + func_delta["arguments"]
    return accumulated


def _normalize_tools(tools: Optional[List[Union[Dict, "ToolSpec"]]]) -> tuple:
    """\
    Normalize a list of tools to (jsonschema_list, toolspec_dict).

    Args:
        tools: List of tools, each can be a ToolSpec or a jsonschema dict.

    Returns:
        tuple: (jsonschema_list for LLM API, toolspec_dict mapping name->ToolSpec for execution)
    """
    if not tools:
        return [], {}
    jsonschema_list = []
    toolspec_dict = {}
    for tool in tools:
        if isinstance(tool, ToolSpec):
            jsonschema_list.append(tool.to_jsonschema())
            toolspec_dict[tool.binded.name] = tool
        elif isinstance(tool, dict):
            jsonschema_list.append(tool)
            # Extract name from jsonschema format
            name = tool.get("function", {}).get("name") or tool.get("name")
            if name:
                toolspec_dict[name] = None  # No ToolSpec available for execution
        else:
            raise TypeError(f"Tool must be ToolSpec or dict, got {type(tool)}")
    return jsonschema_list, toolspec_dict


def exec_tool_calls(tool_calls: List[Dict], toolspec_dict: Dict[str, Optional["ToolSpec"]]) -> tuple:
    """\
    Execute tool calls and return standardized tool messages/results with timing.

    Compatibility:
    - Accepts tool calls with or without a ``function`` layer (e.g., ``{"name": "foo", "arguments": "{}"}``).
    - Missing or empty ``id`` defaults to an empty string.
    - ``arguments`` may be a dict or a JSON string; non-dict inputs are parsed via ``json.loads`` with graceful errors.

    Args:
        tool_calls: List of tool call dicts from LLM responses (raw or parsed).
        toolspec_dict: Mapping from tool name to ``ToolSpec`` (or None if not available).

    Returns:
        tuple: (tool_messages, tool_results, tool_usage)
            - tool_messages: List of tool message dicts in OpenAI format for conversation continuation.
            - tool_results: List of result content strings (just the returned values).
            - tool_usage: Dict mapping ``tool_call_id`` to ``{"elapsed": float, "created_at": str}``.

    Raises:
        ValueError: If a tool name is missing or the ToolSpec is unavailable.
    """

    tool_messages = []
    tool_results = []
    tool_usage = {}
    for tc in tool_calls:
        tc = tc or dict()
        func_info = tc.get("function") if isinstance(tc.get("function"), dict) else dict()
        name = (func_info.get("name") or tc.get("name") or "").strip()
        tool_call_id = tc.get("id") or ""
        args_raw = func_info.get("arguments") if func_info else tc.get("arguments", "{}")

        if not name:
            raise ValueError("Tool call missing function name.")

        toolspec = toolspec_dict.get(name)
        if toolspec is None:
            raise ValueError(f"Cannot execute tool '{name}': no ToolSpec available. " "tool_messages/tool_results requires all tools to be ToolSpec instances.")

        parse_error = None
        if isinstance(args_raw, dict):
            args = args_raw
        else:
            raw_str = args_raw if args_raw is not None else "{}"
            try:
                args = json.loads(raw_str)
            except Exception as exc:
                parse_error = f"Failed to parse arguments '{raw_str}' for tool '{name}': {exc}."
                args = dict()

        tool_created_at = datetime.now(timezone.utc).isoformat()
        tool_t0 = time.perf_counter()
        if parse_error:
            content = parse_error
        else:
            try:
                result = toolspec(**args)
                content = result
            except Exception as exc:
                content = f"Error executing tool '{name}': {exc}."
        tool_elapsed_val = round_elapsed(time.perf_counter() - tool_t0)

        content_str = str(content)
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": content_str,
            }
        )
        tool_results.append(content_str)
        tool_usage[tool_call_id] = {"elapsed": tool_elapsed_val, "created_at": tool_created_at}

    return tool_messages, tool_results, tool_usage


def repair_tool_call(tool_call: Dict[str, Any], toolspec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """\
    Repair a tool call by filling in missing fields using the ToolSpec.

    Args:
        tool_call: The tool call dict to repair.
        toolspec_dict: Mapping from tool name to ToolSpec.

    Returns:
        The repaired tool call dict.
    """
    tool_name = tool_call.get("function", {}).get("name")
    if tool_name not in toolspec_dict:
        tool_name = raise_mismatch(supported=list(toolspec_dict.keys()), got=tool_name, mode="match", thres=0.01)
    if tool_name not in toolspec_dict:
        raise ValueError(f"Cannot repair tool call for unknown tool '{tool_name}'.")
    toolspec = toolspec_dict.get(tool_name)
    arguments = tool_call.get("function", {}).get("arguments", "{}")

    schema = None
    if toolspec is not None:
        schema = {key: None for key in list(toolspec.params)}

        def _schema_is_string(schema: Any) -> bool:
            if not isinstance(schema, dict):
                return False
            schema_type = schema.get("type")
            if schema_type == "string":
                return True
            if isinstance(schema_type, list) and ("string" in schema_type):
                return True
            for union_key in ("anyOf", "oneOf", "allOf"):
                variants = schema.get(union_key)
                if isinstance(variants, list) and any(_schema_is_string(item) for item in variants):
                    return True
            return False

        properties = (toolspec.input_schema or {}).get("properties", {})
        for key, key_schema in properties.items():
            if key in schema and _schema_is_string(key_schema):
                schema[key] = "string"

    try:
        healed_arguments = heal_json(
            arguments,
            schema=schema,
            drop_extras=False,
        )
        repaired_arguments = dumps_json(healed_arguments, indent=None)
    except Exception:
        repaired_arguments = arguments if isinstance(arguments, str) else dumps_json(arguments, indent=None)

    repaired_tool_call = deepcopy(tool_call)
    repaired_tool_call["function"]["name"] = tool_name
    repaired_tool_call["function"]["arguments"] = repaired_arguments
    return repaired_tool_call


@dataclass
class _LLMChunk:
    """\
    A response object that holds various formats of LLM output.
    """

    chunks: List[Dict[str, Any]] = field(default_factory=list)
    think: str = field(default="")
    text: str = field(default="")
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    content: str = field(default="")
    delta_think: str = field(default="")
    delta_text: str = field(default="")
    delta_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    delta_content: str = field(default="")
    think_begin_token: str = field(default="<think>\n")
    think_end_token: str = field(default="\n</think>\n")
    _thinking: Optional[bool] = None

    def __getitem__(self, key: str) -> Any:
        """\
        Get item by key, allowing for dict-like access.
    """
        return getattr(self, key, None)

    def __add__(self, other: Union["_LLMChunk", Dict]) -> "_LLMChunk":
        """\
        Combine two _LLMChunk objects.
    """
        self.delta_think = ""
        self.delta_text = ""
        self.delta_tool_calls = list()
        self.delta_content = ""
        for chunk in other.chunks if isinstance(other, _LLMChunk) else [other]:
            self.chunks.append(chunk)
            delta_think = chunk.get("think", "")
            delta_text = chunk.get("text", "")
            raw_tool_calls = chunk.get("tool_calls", list())
            # Normalize and merge tool_call deltas
            delta_tool_calls = [_normalize_tool_call_delta(tc) for tc in raw_tool_calls]
            _merge_tool_call_deltas(self.tool_calls, delta_tool_calls)
            delta_content = ""
            if delta_think:
                if self._thinking is None:
                    self._thinking = True
                    delta_content += self.think_begin_token
                delta_content += delta_think
            if delta_text:
                if self._thinking is True:
                    self._thinking = False
                    delta_content += self.think_end_token
                delta_content += delta_text
            self.delta_think += delta_think
            self.delta_text += delta_text
            self.delta_tool_calls = delta_tool_calls  # Store normalized deltas
            self.delta_content += delta_content
        self.think += self.delta_think
        self.text += self.delta_text
        # Note: tool_calls are merged incrementally, not appended
        self.content += self.delta_content
        return self

    def to_message(self) -> Dict[str, Any]:
        """\
        Convert the response to a message format.
    """
        return {
            "role": "assistant",
            "content": self.text,
        } | ({"tool_calls": self.tool_calls} if self.tool_calls else {})

    def to_message_delta(self) -> Dict[str, Any]:
        """\
        Convert the response to a message delta format.
    """
        return {
            "role": "assistant",
            "content": self.delta_text,
        } | ({"tool_calls": self.delta_tool_calls} if self.delta_tool_calls else {})

    def to_dict(self) -> Dict[str, Any]:
        """\
        Convert the response to a dictionary format.
    """
        return {
            "text": self.text,
            "think": self.think,
            "tool_calls": self.tool_calls,
            "content": self.content,
            "message": self.to_message(),
        }

    def to_dict_delta(self) -> Dict[str, Any]:
        """\
        Convert the response to a delta format.
    """
        return {
            "text": self.delta_text,
            "think": self.delta_think,
            "tool_calls": self.delta_tool_calls,
            "content": self.delta_content,
            "message": self.to_message_delta(),
        }


LLMIncludeType = Literal[
    "text", "think", "tool_calls", "content", "message", "structured", "tool_messages", "tool_results", "delta_messages", "messages", "usage"
]
_LLM_INCLUDES = ["text", "think", "tool_calls", "content", "message", "structured", "tool_messages", "tool_results", "delta_messages", "messages", "usage"]
_LLM_TEXT_INCLUDES = ["text", "think", "content"]
_LLM_LIST_INCLUDES = ["tool_calls", "tool_messages", "tool_results", "delta_messages", "messages"]
_LLM_STREAM_INCLUDES = ["text", "think", "content", "message"]
_LLM_META_INCLUDES = ["usage"]

EmbedIncludeType = Literal["embeddings", "usage"]
_EMBED_INCLUDES = ["embeddings", "usage"]


def _llm_response_formatting(
    delta: Dict[str, Any], include: List[LLMIncludeType], messages: List[Dict[str, Any]] = None, reduce: bool = True
) -> Union[Dict[str, Any], str, List]:
    """\
    Format the LLM response delta according to include fields and reduce settings.

    Args:
        delta: The response delta dict containing fields like text, think, tool_calls, tool_messages, tool_results, etc.
        include: Fields to include in the output.
        messages: Optional messages list for "messages" field construction.
        reduce: If True and len(include)==1, return the single value instead of dict.

    Returns:
        Formatted response - either a dict, single value, or list depending on include and reduce.
    """
    messages = messages or list()
    formatted_delta = {}
    for k in include:
        if k == "messages":
            if delta.get("messages") is not None:
                formatted_delta[k] = delta["messages"]
            else:
                formatted_delta[k] = (
                    deepcopy(messages)
                    + ([delta["gathered_message"]] if delta.get("gathered_message") else list())
                    + (delta["tool_messages"] if delta.get("tool_messages") else list())
                )
        elif k == "delta_messages":
            if delta.get("delta_messages") is not None:
                formatted_delta[k] = delta["delta_messages"]
            else:
                formatted_delta[k] = ([delta["gathered_message"]] if delta.get("gathered_message") else list()) + (
                    delta["tool_messages"] if delta.get("tool_messages") else list()
                )
        elif k == "structured":
            if not delta.get(k):
                formatted_delta[k] = dict()
            try:
                formatted_delta[k] = loads_json(delta.get(k))
            except (json.JSONDecodeError, TypeError):
                formatted_delta[k] = dict()
        elif k in _LLM_META_INCLUDES:
            # Meta fields (e.g. usage): None means "not yet available" (streaming); omit from output
            val = delta.get(k)
            if val is None:
                continue
            formatted_delta[k] = val
        else:
            # Default empty values for list fields
            default = {} if k in ("structured",) else [] if k in ("tool_calls", "tool_messages", "tool_results") else ""
            formatted_delta[k] = delta.get(k, default)
    # Suppress empty text-type fields in a meta-carrying delta (e.g. final usage chunk)
    if any(k in formatted_delta for k in _LLM_META_INCLUDES):
        for k in _LLM_TEXT_INCLUDES:
            if k in formatted_delta and formatted_delta[k] == "":
                del formatted_delta[k]
    return formatted_delta if (not reduce or len(formatted_delta) != 1) else next(iter(formatted_delta.values()))


def gather_assistant_message(message_chunks: List[Dict]):
    """\
    Gather assistant message_chunks (returned by `_LLMChunk.to_message()`) from a list of message dictionaries.

    Args:
        message_chunks (List[Dict]): A list of message dictionaries to gather.

    Returns:
        Dict[str, Any]: A dictionary containing the gathered assistant message.
    """
    gathered = {"role": "assistant", "content": "", "tool_calls": list()}
    for message_chunk in message_chunks:
        gathered["content"] += message_chunk.get("content", "")
        gathered["tool_calls"].extend(message_chunk.get("tool_calls", list()))
    if not gathered.get("tool_calls"):
        del gathered["tool_calls"]
    return gathered


LLMResponse = Union[str, Dict[str, Any], List[Union[str, Dict[str, Any]]]]


def gather_stream(stream: Iterable[Dict[str, Any]], include: Optional[List[LLMIncludeType]] = None, reduce: bool = True) -> LLMResponse:
    """\
    Gather an iterable of `LLM.stream` responses into a single consolidated `LLM.oracle` response.
    To use `gather_stream`, the stream must uses `reduce=False` to return a dictionary per delta.

    Args:
        stream (Iterable[LLMResponse]): An iterable of LLM responses from `LLM.stream`.
        include (List[LLMIncludeType] | None): Fields to include in the final output.
            If None, includes all fields found in the stream.
            This can usually be omitted if the stream was generated with the desired `include` fields.
            However, when the streaming fails (empty), this ensures the final output has the expected structure.
        reduce (bool): Whether to reduce the final output if only one field is included.

    Returns:
        LLMResponse: The consolidated LLM response.
    """
    response = dict()
    if include is not None:
        for key in include:
            if key in _LLM_TEXT_INCLUDES:
                response[key] = ""
            elif key in _LLM_LIST_INCLUDES:
                response[key] = list()
            elif key == "structured":
                response[key] = dict()
            elif key == "message":
                response[key] = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": list(),
                }
            elif key in _LLM_META_INCLUDES:
                pass  # Meta fields: take last non-empty value, no initialization needed
            else:
                raise_mismatch(supported=_LLM_INCLUDES, got=key, name="include key for `gather_stream`", thres=1.0)
    for delta in stream:
        if delta is None:
            continue
        for key, value in delta.items():
            if (include is not None) and (key not in include):
                continue
            if key in _LLM_TEXT_INCLUDES:
                response[key] = response.get(key, "") + (value or "")
            elif key in _LLM_LIST_INCLUDES:
                response[key] = response.get(key, list()) + (value or list())
            elif key == "structured":
                response[key] = response.get(key, dict()) | (value or dict())
            elif key in _LLM_META_INCLUDES:
                # Meta fields: take the last non-empty value
                if value:
                    response[key] = value
            elif key == "message":
                response[key] = response.setdefault(
                    key,
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": list(),
                    },
                )
                response[key]["content"] += value.get("content") or ""
                response[key]["tool_calls"].extend(value.get("tool_calls") or list())
    if ("message" in response) and ("tool_calls" in response["message"]) and (not response["message"].get("tool_calls")):
        del response["message"]["tool_calls"]
    response = response if (include is None) else {k: response.get(k) for k in unique(include)}
    return response if (not reduce or len(response) != 1) else next(iter(response.values()))


class LLM(object):
    """\
    High-level chat LLM client with retry, caching, proxy, and streaming support.

    This class wraps a litellm-compatible chat API and provides two access modes:
    - stream: incremental (delta) results as they arrive
    - oracle: full (final) result collected from the stream

    Key features:
    - Retry: automatic retries via tenacity on retryable exceptions.
    - Caching: memoizes successful results keyed by all request inputs and a user-defined `name`. Excluded keys can be configured via `cache_exclude`.
    - Streaming-first: always uses `stream=True` under the hood for stability; `oracle` aggregates the stream.
    - Proxies: optional `http_proxy` and `https_proxy` support per-request.
    - Flexible messages: accepts multiple message formats and normalizes them.
    - Output shaping: `include` and `reduce` control what is returned and whether to flatten lists.

    Parameters:
        preset (str | None): Named preset from configuration.
        model (str | None): Model identifier (e.g., "gpt-4o"). Overrides preset when provided.
        provider (str | None): Provider name used by the underlying client.
        cache (Union[bool, str, BaseCache] | None): Cache implementation. Defaults to True.
            If True, uses DiskCache with the default cache directory ("core.cache_path").
            If a string is provided, it is treated as the path for DiskCache.
            If None/False, uses NoCache (no caching).
        cache_exclude (list[str] | None): Keys to exclude from cache key construction.
        name (str | None): Logical name for this LLM instance. Used to namespace the cache. Defaults to "llm".
        **kwargs: Additional provider/client config (e.g., temperature, top_p, n, tools, tool_choice, http_proxy, https_proxy, and any litellm client options).
            These act as defaults and can be overridden per call.

    Notes:
        - Caching: Only successful executions are cached. The cache key includes the normalized messages,
            the full effective configuration, and `name`, minus any keys listed in `cache_exclude`.
        - Set `name` differently for semantically distinct use-cases to avoid cache collisions.

    Usage format (`include=["usage"]`):
        `usage` is emitted only once, on the final delta of `stream` / `astream`, and carried into `oracle`.
        It contains token counters and timing metrics for the full LLM invocation:

        {
            "created_at": str,                 # start timestamp (UTC, ISO-8601)
            "elapsed": float                   # total elapsed for this call (inference + optional tool execution)
            "prompt_tokens": int,              # provider-reported
            "completion_tokens": int,          # provider-reported
            "total_tokens": int,               # provider-reported
            "prompt_elapsed": float,           # start -> first content chunk
            "completion_elapsed": float,       # first content chunk -> end of inference
            "inference_elapsed": float,        # prompt_elapsed + completion_elapsed
            "tool_elapsed": float,             # optional, only when tool execution happens here
            "tool_usage": {                    # optional, keyed by tool_call_id
                "<tool_call_id>": {
                    "created_at": str,         # tool call start timestamp (UTC, ISO-8601)
                    "elapsed": float           # tool execution elapsed seconds
                }
            }
        }

        Time representation uses start-time + elapsed duration. No `completed_at` is recorded.
        Typical invariants:
        - `prompt_elapsed + completion_elapsed ~= inference_elapsed`
        - If tools execute in this call path: `inference_elapsed + tool_elapsed ~= elapsed`
        - (notice that tool_elapsed may not be sum of tool_usage elapsed if tools are executed asynchronously)
        - Otherwise: `inference_elapsed ~= elapsed`
    """

    def __init__(
        self,
        preset: str = None,
        model: str = None,
        provider: str = None,
        cache: Union[bool, str, "BaseCache"] = True,
        cache_exclude: Optional[List[str]] = None,
        name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__()
        self.name = name or "llm"
        self.spec = LLM_CONFIG_ENGINE.resolve({"preset": preset, "model": model, "provider": provider, **kwargs})
        self.config = LLM_CONFIG_ENGINE.materialize(self.spec, mode="spec")
        self.args = LLM_CONFIG_ENGINE.materialize(self.spec, mode="litellm")
        if (cache is None) or (cache is False) or CM_AHVN.get("llm.cache_disabled", False):
            self.cache = NoCache()
        elif cache is True:
            self.cache = DiskCache(path=CM_AHVN.pj(CM_AHVN.get("core.cache_path"), "llm_default"))
        elif isinstance(cache, str):
            self.cache = DiskCache(path=CM_AHVN.pj(cache))
        else:
            self.cache = cache
        _cache_exclude = set(CM_AHVN.get("llm.cache_exclude_keys", list())) if cache_exclude is None else set(cache_exclude)
        self.cache.add_exclude(_cache_exclude)
        self._dim = self.args.pop("_dim", None)

    def _get_retry(self):
        retry_config = CM_AHVN.get("llm.retry", dict())
        max_attempts = retry_config.get("max_attempts", 3)
        wait_multiplier = retry_config.get("multiplier", 1)
        wait_max = retry_config.get("max", 60)
        reraise = retry_config.get("reraise", True)
        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=wait_multiplier, max=wait_max),
            retry=retry_if_exception_type(tuple(get_litellm_retryable_exceptions())),
            reraise=reraise,
        )

    def _cached_stream(self, enforce_non_stream_structured: bool = False, _meta: Optional[Dict] = None, **inputs) -> Generator[Any, None, None]:
        _is_miss = [False]

        @self._get_retry()
        def vanilla_stream(**inputs) -> Generator[Any, None, None]:
            _is_miss[0] = True
            litellm = get_litellm()
            for chunk in litellm.completion(**inputs):
                # Handle usage-only chunk (final chunk when include_usage=True may have empty choices)
                usage = getattr(chunk, "usage", None)
                if not chunk.choices:
                    if usage:
                        yield [
                            {"usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}}
                        ]
                        continue
                    raise ValueError("Empty response from LLM API")
                choice = chunk.choices[0]
                yield [
                    {
                        "think": getattr(choice.delta, "reasoning_content", None) or "",
                        "text": getattr(choice.delta, "content", None) or "",
                        "tool_calls": getattr(choice.delta, "tool_calls", None) or list(),
                    }
                ]
                # Also yield usage if present on a chunk with choices (some providers attach it to the last content chunk)
                if usage and usage.total_tokens:
                    yield [{"usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}}]
            return

        @self._get_retry()
        def vanilla_non_stream(**inputs) -> Generator[Any, None, None]:
            """Non-streaming completion that yields a single chunk for compatibility."""
            _is_miss[0] = True
            litellm = get_litellm()
            response = litellm.completion(**inputs)
            if not getattr(response, "choices", None):
                raise ValueError("Empty response from LLM API")
            # Yield single chunk with full response
            choice = response.choices[0]
            yield [
                {
                    "think": getattr(choice.message, "reasoning_content", None) or "",
                    "text": getattr(choice.message, "content", None) or "",
                    "tool_calls": getattr(choice.message, "tool_calls", None) or list(),
                }
            ]
            # Yield usage if available
            usage = getattr(response, "usage", None)
            if usage:
                yield [{"usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}}]
            return

        @self.cache.memoize(name=self.name)
        def cached_vanilla_stream(**inputs) -> Generator[Any, None, None]:
            yield from vanilla_stream(**inputs)

        @self.cache.memoize(name=self.name)
        def cached_vanilla_non_stream(**inputs) -> Generator[Any, None, None]:
            yield from vanilla_non_stream(**inputs)

        # Check if we should enforce non-streaming for structured output/tool use
        has_structured_or_tools = ("response_format" in inputs) or ("tools" in inputs)

        if enforce_non_stream_structured and has_structured_or_tools:
            # Override stream to False for structured output/tool use
            non_stream_inputs = {**inputs, "stream": False}
            yield from cached_vanilla_non_stream(**non_stream_inputs)
        else:
            yield from cached_vanilla_stream(**inputs)

        if _meta is not None:
            _meta["cached"] = not _is_miss[0]
        return

    async def _cached_astream(self, enforce_non_stream_structured: bool = False, _meta: Optional[Dict] = None, **inputs) -> AsyncGenerator[Any, None]:
        _is_miss = [False]

        @self._get_retry()
        async def vanilla_astream(**inputs) -> AsyncGenerator[Any, None]:
            _is_miss[0] = True
            litellm = get_litellm()
            stream_resp = await litellm.acompletion(**inputs)

            try:
                if hasattr(stream_resp, "__aiter__"):
                    async for chunk in stream_resp:
                        usage = getattr(chunk, "usage", None)
                        if not chunk.choices:
                            if usage:
                                yield [
                                    {
                                        "usage": {
                                            "prompt_tokens": usage.prompt_tokens,
                                            "completion_tokens": usage.completion_tokens,
                                            "total_tokens": usage.total_tokens,
                                        }
                                    }
                                ]
                                continue
                            raise ValueError("Empty response from LLM API")
                        choice = chunk.choices[0]
                        yield [
                            {
                                "think": getattr(choice.delta, "reasoning_content", None) or "",
                                "text": getattr(choice.delta, "content", None) or "",
                                "tool_calls": getattr(choice.delta, "tool_calls", None) or list(),
                            }
                        ]
                        if usage and usage.total_tokens:
                            yield [
                                {
                                    "usage": {
                                        "prompt_tokens": usage.prompt_tokens,
                                        "completion_tokens": usage.completion_tokens,
                                        "total_tokens": usage.total_tokens,
                                    }
                                }
                            ]
                elif hasattr(stream_resp, "__iter__"):
                    for chunk in stream_resp:
                        usage = getattr(chunk, "usage", None)
                        if not chunk.choices:
                            if usage:
                                yield [
                                    {
                                        "usage": {
                                            "prompt_tokens": usage.prompt_tokens,
                                            "completion_tokens": usage.completion_tokens,
                                            "total_tokens": usage.total_tokens,
                                        }
                                    }
                                ]
                                continue
                            raise ValueError("Empty response from LLM API")
                        choice = chunk.choices[0]
                        yield [
                            {
                                "think": getattr(choice.delta, "reasoning_content", None) or "",
                                "text": getattr(choice.delta, "content", None) or "",
                                "tool_calls": getattr(choice.delta, "tool_calls", None) or list(),
                            }
                        ]
                        if usage and usage.total_tokens:
                            yield [
                                {
                                    "usage": {
                                        "prompt_tokens": usage.prompt_tokens,
                                        "completion_tokens": usage.completion_tokens,
                                        "total_tokens": usage.total_tokens,
                                    }
                                }
                            ]
                else:
                    raise TypeError(f"Unsupported async streaming response type: {type(stream_resp)}")
            finally:
                closer = getattr(stream_resp, "aclose", None)
                if callable(closer):
                    maybe = closer()
                    if inspect.isawaitable(maybe):
                        await maybe
                else:
                    closer = getattr(stream_resp, "close", None)
                    if callable(closer):
                        closer()
            return

        @self._get_retry()
        async def vanilla_anon_stream(**inputs) -> AsyncGenerator[Any, None]:
            """Non-streaming async completion that yields a single chunk for compatibility."""
            _is_miss[0] = True
            litellm = get_litellm()
            response = await litellm.acompletion(**inputs)
            if not getattr(response, "choices", None):
                raise ValueError("Empty response from LLM API")
            # Yield single chunk with full response
            choice = response.choices[0]
            yield [
                {
                    "think": getattr(choice.message, "reasoning_content", None) or "",
                    "text": getattr(choice.message, "content", None) or "",
                    "tool_calls": getattr(choice.message, "tool_calls", None) or list(),
                }
            ]
            # Yield usage if available
            usage = getattr(response, "usage", None)
            if usage:
                yield [{"usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}}]
            return

        @self.cache.memoize(name=self.name)
        async def cached_vanilla_astream(**inputs) -> AsyncGenerator[Any, None]:
            async for chunk in vanilla_astream(**inputs):
                yield chunk
            return

        @self.cache.memoize(name=self.name)
        async def cached_vanilla_anon_stream(**inputs) -> AsyncGenerator[Any, None]:
            async for chunk in vanilla_anon_stream(**inputs):
                yield chunk
            return

        # Check if we should enforce non-streaming for structured output/tool use
        has_structured_or_tools = ("response_format" in inputs) or ("tools" in inputs)

        if enforce_non_stream_structured and has_structured_or_tools:
            # Override stream to False for structured output/tool use
            non_stream_inputs = {**inputs, "stream": False}
            async for chunk in cached_vanilla_anon_stream(**non_stream_inputs):
                yield chunk
        else:
            async for chunk in cached_vanilla_astream(**inputs):
                yield chunk

        if _meta is not None:
            _meta["cached"] = not _is_miss[0]
        return

    @staticmethod
    def _embed_dispatch(batch: List[str], **kwargs):
        """\
        Shared pre-processing for ``_cached_embed`` / ``_cached_aembed``.

        Splits *batch* into empty / non-empty indices, deduplicates identical
        strings (in-batch caching), computes sub-batches according to
        ``batch_size``, and pops ``num_threads`` from *kwargs* so they are not
        forwarded to the provider.

        Returns:
            tuple: (empty_set, dedup_map, unique_batch, sub_batches, num_threads, batch_len, kwargs)
                *empty_set*   – set of indices whose original text was empty.
                *dedup_map*   – ``{original_index: index_in_unique_batch}`` for
                                non-empty entries.
                *unique_batch* – deduplicated list of non-empty strings.
                *sub_batches* – list of slices of *unique_batch* (``None`` when
                                the entire batch is empty).
                *num_threads* – popped from kwargs.
                *batch_len*   – ``len(batch)``.
                *kwargs*      – cleaned copy (``batch_size`` / ``num_threads`` removed).
        """
        batch_len = len(batch)
        empty_set = {i for i, text in enumerate(batch) if not text}
        if len(empty_set) == batch_len:
            return empty_set, {}, [], None, -1, batch_len, kwargs

        # Deduplicate: map each non-empty index → position in unique_batch
        seen: Dict[str, int] = {}  # text → index in unique_batch
        unique_batch: List[str] = []
        dedup_map: Dict[int, int] = {}  # original_index → unique_batch_index
        for i, text in enumerate(batch):
            if i in empty_set:
                continue
            if text not in seen:
                seen[text] = len(unique_batch)
                unique_batch.append(text)
            dedup_map[i] = seen[text]

        batch_size = kwargs.pop("batch_size", len(unique_batch))
        if batch_size is None:
            batch_size = len(unique_batch)
        try:
            batch_size = int(batch_size)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"batch_size must be an integer, got {batch_size!r}.") from exc
        if batch_size <= 0:
            batch_size = len(unique_batch)

        num_threads = kwargs.pop("num_threads", -1)
        if num_threads is None:
            num_threads = -1
        else:
            try:
                num_threads = int(num_threads)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"num_threads must be an integer, got {num_threads!r}.") from exc

        sub_batches = [unique_batch[start : start + batch_size] for start in range(0, len(unique_batch), batch_size)]
        return empty_set, dedup_map, unique_batch, sub_batches, num_threads, batch_len, kwargs

    def _cached_embed(self, batch: List[str], **kwargs) -> List[List[float]]:
        @self._get_retry()
        def vanilla_embed(batch: List[str], **kwargs) -> List[List[float]]:
            empty_set, dedup_map, _, sub_batches, num_threads, batch_len, kwargs = LLM._embed_dispatch(batch, **kwargs)
            if sub_batches is None:
                return [self.embed_empty for _ in batch]
            litellm = get_litellm()
            if len(sub_batches) > 1:
                args_list = [{"batch_index": idx, "input": sb, **kwargs} for idx, sb in enumerate(sub_batches)]

                def _embed_one(batch_index: int, **kw):
                    return batch_index, litellm.embedding(**kw).data

                indexed_results = []
                with Parallelized(func=_embed_one, args=args_list, num_threads=num_threads) as ptasks:
                    for _, result, error in ptasks:
                        if error is not None:
                            raise error
                        indexed_results.append(result)
                indexed_results.sort(key=lambda item: item[0])
                all_embeddings = []
                for _, embeddings in indexed_results:
                    all_embeddings.extend(embeddings)
            else:
                all_embeddings = []
                for sb in sub_batches:
                    all_embeddings.extend(litellm.embedding(input=sb, **kwargs).data)
            unique_embeddings = [e["embedding"] for e in all_embeddings]
            return [self.embed_empty if i in empty_set else unique_embeddings[dedup_map[i]] for i in range(batch_len)]

        @self.cache.batch_memoize(name=self.name)
        def cached_vanilla_embed(batch: List[str], **kwargs) -> List[List[float]]:
            return vanilla_embed(batch, **kwargs)

        return cached_vanilla_embed(batch, **kwargs)

    async def _cached_aembed(self, batch: List[str], **kwargs) -> List[List[float]]:
        @self._get_retry()
        async def vanilla_aembed(batch: List[str], **kwargs) -> List[List[float]]:
            empty_set, dedup_map, _, sub_batches, num_threads, batch_len, kwargs = LLM._embed_dispatch(batch, **kwargs)
            if sub_batches is None:
                return [self.embed_empty for _ in batch]
            litellm = get_litellm()
            if len(sub_batches) > 1:
                args_list = [{"batch_index": idx, "input": sb, **kwargs} for idx, sb in enumerate(sub_batches)]

                async def _aembed_one(batch_index: int, **kw):
                    return batch_index, (await litellm.aembedding(**kw)).data

                indexed_results = []
                async with Parallelized(func=_aembed_one, args=args_list, num_threads=num_threads) as ptasks:
                    async for _, result, error in ptasks:
                        if error is not None:
                            raise error
                        indexed_results.append(result)
                indexed_results.sort(key=lambda item: item[0])
                all_embeddings = []
                for _, embeddings in indexed_results:
                    all_embeddings.extend(embeddings)
            else:
                all_embeddings = []
                for sb in sub_batches:
                    embeddings_resp = await litellm.aembedding(input=sb, **kwargs)
                    all_embeddings.extend(embeddings_resp.data)
            unique_embeddings = [e["embedding"] for e in all_embeddings]
            return [self.embed_empty if i in empty_set else unique_embeddings[dedup_map[i]] for i in range(batch_len)]

        @self.cache.batch_memoize(name=self.name)
        async def cached_vanilla_aembed(batch: List[str], **kwargs) -> List[List[float]]:
            return await vanilla_aembed(batch, **kwargs)

        return await cached_vanilla_aembed(batch, **kwargs)

    def _instrumented_embed(self, batch: List[str], **kwargs) -> tuple:
        """\
        Embed with full usage tracking.  Returns ``(embeddings, usage_meta)``.

        Uses the same ``batch_memoize`` caching as ``_cached_embed`` but captures
        cache-hit counts and provider token usage via mutable side-channels.
        """
        _uncached_count = [0]

        @self._get_retry()
        def vanilla_embed(batch: List[str], **kwargs) -> List[List[float]]:
            _uncached_count[0] = len(batch)
            empty_set, dedup_map, _, sub_batches, num_threads, batch_len, kwargs = LLM._embed_dispatch(batch, **kwargs)
            if sub_batches is None:
                return [self.embed_empty for _ in batch]
            litellm = get_litellm()
            if len(sub_batches) > 1:
                args_list = [{"batch_index": idx, "input": sb, **kwargs} for idx, sb in enumerate(sub_batches)]

                def _embed_one(batch_index: int, **kw):
                    resp = litellm.embedding(**kw)
                    return batch_index, resp.data

                indexed_results = []
                with Parallelized(func=_embed_one, args=args_list, num_threads=num_threads) as ptasks:
                    for _, result, error in ptasks:
                        if error is not None:
                            raise error
                        indexed_results.append(result)
                indexed_results.sort(key=lambda item: item[0])
                all_embeddings = []
                for _, embeddings in indexed_results:
                    all_embeddings.extend(embeddings)
            else:
                all_embeddings = []
                for sb in sub_batches:
                    resp = litellm.embedding(input=sb, **kwargs)
                    all_embeddings.extend(resp.data)
            unique_embeddings = [e["embedding"] for e in all_embeddings]
            return [self.embed_empty if i in empty_set else unique_embeddings[dedup_map[i]] for i in range(batch_len)]

        @self.cache.batch_memoize(name=self.name)
        def cached_vanilla_embed(batch: List[str], **kwargs) -> List[List[float]]:
            return vanilla_embed(batch, **kwargs)

        # Dispatch stats (copy to avoid popping from kwargs)
        dispatch_kwargs = deepcopy(kwargs)
        empty_set, dedup_map, unique_batch, sub_batches, _, batch_len, _ = LLM._embed_dispatch(batch, **dispatch_kwargs)

        import time as _time

        t0 = _time.perf_counter()
        embeddings = cached_vanilla_embed(batch, **kwargs)
        elapsed = _time.perf_counter() - t0

        cached_count = len(batch) - _uncached_count[0]
        dim = len(embeddings[0]) if embeddings and embeddings[0] else 0
        usage_meta = {
            "created_at": int(_time.time()),
            "elapsed": round(elapsed, 4),
            "total_count": batch_len,
            "empty_count": len(empty_set),
            "unique_count": len(unique_batch) if unique_batch else 0,
            "dim": dim,
            "cached": cached_count,
        }
        if "batch_size" in kwargs:
            usage_meta["batch_size"] = kwargs.get("batch_size")
        if "num_threads" in kwargs:
            usage_meta["num_threads"] = kwargs.get("num_threads")
        return embeddings, usage_meta

    async def _instrumented_aembed(self, batch: List[str], **kwargs) -> tuple:
        """\
        Async embed with full usage tracking.  Returns ``(embeddings, usage_meta)``.
        """
        _uncached_count = [0]

        @self._get_retry()
        async def vanilla_aembed(batch: List[str], **kwargs) -> List[List[float]]:
            _uncached_count[0] = len(batch)
            empty_set, dedup_map, _, sub_batches, num_threads, batch_len, kwargs = LLM._embed_dispatch(batch, **kwargs)
            if sub_batches is None:
                return [self.embed_empty for _ in batch]
            litellm = get_litellm()
            if len(sub_batches) > 1:
                args_list = [{"batch_index": idx, "input": sb, **kwargs} for idx, sb in enumerate(sub_batches)]

                async def _aembed_one(batch_index: int, **kw):
                    resp = await litellm.aembedding(**kw)
                    return batch_index, resp.data

                indexed_results = []
                async with Parallelized(func=_aembed_one, args=args_list, num_threads=num_threads) as ptasks:
                    async for _, result, error in ptasks:
                        if error is not None:
                            raise error
                        indexed_results.append(result)
                indexed_results.sort(key=lambda item: item[0])
                all_embeddings = []
                for _, embeddings in indexed_results:
                    all_embeddings.extend(embeddings)
            else:
                all_embeddings = []
                for sb in sub_batches:
                    resp = await litellm.aembedding(input=sb, **kwargs)
                    all_embeddings.extend(resp.data)
            unique_embeddings = [e["embedding"] for e in all_embeddings]
            return [self.embed_empty if i in empty_set else unique_embeddings[dedup_map[i]] for i in range(batch_len)]

        @self.cache.batch_memoize(name=self.name)
        async def cached_vanilla_aembed(batch: List[str], **kwargs) -> List[List[float]]:
            return await vanilla_aembed(batch, **kwargs)

        dispatch_kwargs = deepcopy(kwargs)
        empty_set, dedup_map, unique_batch, sub_batches, _, batch_len, _ = LLM._embed_dispatch(batch, **dispatch_kwargs)

        import time as _time

        t0 = _time.perf_counter()
        embeddings = await cached_vanilla_aembed(batch, **kwargs)
        elapsed = _time.perf_counter() - t0

        cached_count = len(batch) - _uncached_count[0]
        dim = len(embeddings[0]) if embeddings and embeddings[0] else 0
        usage_meta = {
            "created_at": int(_time.time()),
            "elapsed": round(elapsed, 4),
            "total_count": batch_len,
            "empty_count": len(empty_set),
            "unique_count": len(unique_batch) if unique_batch else 0,
            "dim": dim,
            "cached": cached_count,
        }
        if "batch_size" in kwargs:
            usage_meta["batch_size"] = kwargs.get("batch_size")
        if "num_threads" in kwargs:
            usage_meta["num_threads"] = kwargs.get("num_threads")
        return embeddings, usage_meta

    def _validate_include(
        self,
        include: Optional[List[LLMIncludeType]] = None,
        stream: bool = True,
        has_tools: bool = False,
        has_structured: bool = False,
        toolspec_dict: Optional[Dict[str, Optional["ToolSpec"]]] = None,
    ) -> List[str]:
        """\
        Validate and normalize include fields.

        Args:
            include: Fields to include, or None for defaults.
            stream: Whether this is a streaming request.
            has_tools: Whether tools are provided.
            has_structured: Whether structured output is expected.
            toolspec_dict: Dict mapping tool names to ToolSpec (for tool_messages/tool_results validation).

        Returns:
            Validated and normalized list of include fields.
        """
        # Smart defaults based on whether tools are present
        if include is None:
            include = ["think", "text", "tool_calls"] if has_tools else ["text"]
        if isinstance(include, str):
            include = [include]
        if has_structured and ("structured" not in include):
            include.append("structured")
        include = unique(include)
        if not len(include):
            raise ValueError("Include list must not be empty.")
        for item in include:
            raise_mismatch(supported=_LLM_INCLUDES, got=item, name="include key", thres=1.0)
        # Validate tool_messages/tool_results: requires all tools to be ToolSpec
        needs_execution = ("tool_messages" in include) or ("tool_results" in include) or ("delta_messages" in include) or ("messages" in include)
        if needs_execution and toolspec_dict:
            for name, spec in toolspec_dict.items():
                if spec is None:
                    raise ValueError(
                        f"tool_messages/tool_results/messages/delta_messages requires all tools to be ToolSpec instances, "
                        f"but tool '{name}' is a raw jsonschema dict."
                    )
        # Validate structured output: requires `response_format` in config
        if ("structured" in include) and (not has_structured):
            raise ValueError("Including 'structured' output requires a 'response_format' to be specified in the LLM config.")
        return include

    def _validate_embed_include(
        self,
        include: Optional[List["EmbedIncludeType"]] = None,
    ) -> List[str]:
        """\
        Validate and normalize include fields for embed calls.

        Args:
            include: Fields to include, or None for defaults.

        Returns:
            Validated and normalized list of include fields.
        """
        if include is None:
            include = ["embeddings"]
        if isinstance(include, str):
            include = [include]
        include = unique(include)
        if not len(include):
            raise ValueError("Include list must not be empty.")
        for item in include:
            raise_mismatch(supported=_EMBED_INCLUDES, got=item, name="embed include key", thres=1.0)
        return include

    def _validate_args(
        self,
        messages: Messages,
        tools: Optional[List[Union[Dict, "ToolSpec"]]] = None,
        tool_choice: Optional[str] = None,
        **kwargs,
    ) -> tuple:
        """\
        Validate and prepare args for LLM call.

        Args:
            messages: The messages to send.
            tools: Optional list of tools (ToolSpec or jsonschema dicts).
            tool_choice: Optional tool choice setting.
            **kwargs: Additional args overrides.

        Returns:
            tuple: (config_dict, toolspec_dict)
        """
        jsonschema_list, toolspec_dict = _normalize_tools(tools)

        cfg = deepcopy(self.args) | deepcopy(kwargs) | {"messages": messages} | {"stream": True}

        # Multi-inference (`n` > 1) is currently unsupported in AHVN.
        # We keep current behavior (consume first choice) but warn explicitly.
        raw_n = cfg.get("n", None)
        if raw_n is not None:
            try:
                normalized_n = int(raw_n)
            except (TypeError, ValueError):
                normalized_n = None
            if (normalized_n is not None and normalized_n > 1) or (normalized_n is None):
                logger.warning(
                    "Detected LLM parameter `n=%r`, but multi-inference is currently not supported in AHVN. "
                    "Only the first choice will be used. Multi-inference support is planned for a future release.",
                    raw_n,
                )

        # Add tools to config if present
        if jsonschema_list:
            cfg["tools"] = jsonschema_list
            # Default tool_choice: "auto" if tools present and not specified
            if tool_choice is None:
                tool_choice = "auto"
            cfg["tool_choice"] = tool_choice
        elif tool_choice is not None:
            cfg["tool_choice"] = tool_choice

        return cfg, toolspec_dict

    def stream(
        self,
        messages: Messages,
        tools: Optional[List[Union[Dict, "ToolSpec"]]] = None,
        tool_choice: Optional[str] = None,
        include: Optional[List[LLMIncludeType]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> Generator[LLMResponse, None, None]:
        """\
        Stream LLM responses (deltas) for the given messages.

        Args:
            messages: Conversation content, normalized by ``format_messages``.
            tools: Optional list of tools, each can be a ToolSpec or jsonschema dict.
            tool_choice: Tool choice setting. Defaults to "auto" if tools present.
            include: Fields to include in each streamed delta.
                If it contains `"usage"`, usage appears only on the final delta.
            verbose: If True, logs the resolved request config.
            reduce: If True and len(include) == 1, returns a single value instead of a dict.
            **kwargs: Per-call overrides for LLM config.

        Yields:
            LLMResponse: Incremental response deltas.
                Usage schema is documented in the class docstring under "Usage format".
        """
        formatted_messages = format_messages(messages)
        cfg, toolspec_dict = self._validate_args(messages=formatted_messages, tools=tools, tool_choice=tool_choice, **kwargs)
        has_tools, has_structured = bool(tools), bool("response_format" in cfg)
        include = self._validate_include(include=include, stream=True, has_tools=has_tools, has_structured=has_structured, toolspec_dict=toolspec_dict)
        has_messages = bool(("delta_messages" in include) or ("messages" in include))
        has_meta = bool(set(include) & set(_LLM_META_INCLUDES))

        # Inject stream_options for usage when requested
        if "usage" in include:
            cfg["stream_options"] = {**(cfg.get("stream_options") or {}), "include_usage": True}

        repair_tool_calls = bool(cfg.pop("repair_tool_calls", True))
        enforce_non_stream_structured = bool(cfg.pop("enforce_non_stream_structured", False))

        # Record timing for usage meta
        created_at = datetime.now(timezone.utc).isoformat() if has_meta else ""
        t0 = time.perf_counter() if has_meta else None

        with NetworkProxy(
            http_proxy=cfg.pop("http_proxy", None),
            https_proxy=cfg.pop("https_proxy", None),
            no_proxy=cfg.pop("no_proxy", None),
        ):
            if verbose:
                logger.info(f"HTTP  Proxy: {os.environ.get('HTTP_PROXY')}")
                logger.info(f"HTTPS Proxy: {os.environ.get('HTTPS_PROXY')}")
                logger.info(f"Request: {encrypt_display(cfg)}")

            response = _LLMChunk()
            usage_data = {}
            _stream_meta = {} if has_meta else None
            first_chunk_t = None
            for chunk in self._cached_stream(enforce_non_stream_structured=enforce_non_stream_structured, _meta=_stream_meta, **cfg):
                # Capture usage chunk (separate from content chunks)
                if "usage" in chunk[0]:
                    usage_data = chunk[0]["usage"]
                    continue
                response += chunk[0]
                delta_dict = response.to_dict_delta()

                # When tools present, don't yield tool_calls incrementally
                if has_tools:
                    delta_dict["tool_calls"] = list()
                # When structured output is requested, don't yield partial output
                if has_structured:
                    delta_dict["structured"] = ""
                # When messages requested, use empty list to bypass messages deepcopy
                if has_messages:
                    delta_dict["messages"] = list()
                    delta_dict["delta_messages"] = list()
                # Meta fields are only emitted in the final delta
                if has_meta:
                    for mk in _LLM_META_INCLUDES:
                        delta_dict[mk] = None

                if (not delta_dict.get("text")) and (not delta_dict.get("think")) and (not delta_dict.get("content")):
                    continue  # Skip empty deltas
                # Record first content chunk time (prefill complete)
                if first_chunk_t is None and t0 is not None:
                    first_chunk_t = time.perf_counter()
                yield _llm_response_formatting(delta=delta_dict, include=include, messages=list(), reduce=reduce)

            # Yield final tool_calls/tool_messages/tool_results/structured/delta_messages/messages/meta after stream ends
            if t0 is not None:
                inference_end_t = time.perf_counter()
                prompt_elapsed = round_elapsed(first_chunk_t - t0) if first_chunk_t else round_elapsed(inference_end_t - t0)
                completion_elapsed = round_elapsed(inference_end_t - first_chunk_t) if first_chunk_t else 0.0
                inference_elapsed = round_elapsed((prompt_elapsed or 0.0) + (completion_elapsed or 0.0))
                usage_data["prompt_elapsed"] = prompt_elapsed
                usage_data["completion_elapsed"] = completion_elapsed
                usage_data["inference_elapsed"] = inference_elapsed
            if created_at:
                usage_data["created_at"] = created_at
            if _stream_meta is not None:
                usage_data["cached"] = _stream_meta.get("cached", False)

            if repair_tool_calls:
                tool_calls = [repair_tool_call(tool_call, toolspec_dict) for tool_call in response.tool_calls]
            else:
                tool_calls = response.tool_calls
            final_delta = {
                "think": "",
                "text": "",
                "tool_calls": tool_calls if tool_calls else list(),
                "content": "",
                "message": {"role": "assistant", "content": ""} | ({"tool_calls": tool_calls} if tool_calls else {}),
                "gathered_message": response.to_message(),
                "usage": usage_data,
            }
            # Execute tools if tool_messages or tool_results requested
            if response.tool_calls and (("tool_messages" in include) or ("tool_results" in include) or has_messages):
                tool_exec_t0 = time.perf_counter()
                tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, toolspec_dict)
                final_delta["tool_messages"] = tool_messages
                final_delta["tool_results"] = tool_results
                if tool_usage:
                    usage_data["tool_usage"] = tool_usage
                    usage_data["tool_elapsed"] = round_elapsed(time.perf_counter() - tool_exec_t0)
            # Compute total elapsed (inference + tools)
            if t0 is not None:
                usage_data["elapsed"] = round_elapsed(time.perf_counter() - t0)
            if has_structured:
                final_delta["structured"] = response.text
            if has_messages:
                final_delta["messages"] = None  # Explicitly set to None to trigger construction in formatting
                final_delta["delta_messages"] = None  # Explicitly set to None to trigger construction in formatting
            yield _llm_response_formatting(delta=final_delta, include=include, messages=formatted_messages, reduce=reduce)
            return

    async def astream(
        self,
        messages: Messages,
        tools: Optional[List[Union[Dict, "ToolSpec"]]] = None,
        tool_choice: Optional[str] = None,
        include: Optional[List[LLMIncludeType]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> AsyncGenerator[LLMResponse, None]:
        """\
        Asynchronously stream LLM responses (deltas) for the given messages.

        Mirrors :meth:`stream` but returns an async generator suitable for async workflows.
        """
        formatted_messages = format_messages(messages)
        cfg, toolspec_dict = self._validate_args(messages=formatted_messages, tools=tools, tool_choice=tool_choice, **kwargs)
        has_tools, has_structured = bool(tools), bool("response_format" in cfg)
        include = self._validate_include(include=include, stream=True, has_tools=has_tools, has_structured=has_structured, toolspec_dict=toolspec_dict)
        has_messages = bool(("delta_messages" in include) or ("messages" in include))
        has_meta = bool(set(include) & set(_LLM_META_INCLUDES))

        # Inject stream_options for usage when requested
        if "usage" in include:
            cfg["stream_options"] = {**(cfg.get("stream_options") or {}), "include_usage": True}

        repair_tool_calls = bool(cfg.pop("repair_tool_calls", True))
        enforce_non_stream_structured = bool(cfg.pop("enforce_non_stream_structured", False))

        # Record timing for usage meta
        created_at = datetime.now(timezone.utc).isoformat() if has_meta else ""
        t0 = time.perf_counter() if has_meta else None

        with NetworkProxy(
            http_proxy=cfg.pop("http_proxy", None),
            https_proxy=cfg.pop("https_proxy", None),
            no_proxy=cfg.pop("no_proxy", None),
        ):
            if verbose:
                logger.info(f"HTTP  Proxy: {os.environ.get('HTTP_PROXY')}")
                logger.info(f"HTTPS Proxy: {os.environ.get('HTTPS_PROXY')}")
                logger.info(f"Request: {encrypt_display(cfg)}")

            response = _LLMChunk()
            usage_data = {}
            _stream_meta = {} if has_meta else None
            first_chunk_t = None
            async for chunk in self._cached_astream(enforce_non_stream_structured=enforce_non_stream_structured, _meta=_stream_meta, **cfg):
                # Capture usage chunk (separate from content chunks)
                if "usage" in chunk[0]:
                    usage_data = chunk[0]["usage"]
                    continue
                response += chunk[0]
                delta_dict = response.to_dict_delta()

                # When tools present, don't yield tool_calls incrementally
                if has_tools:
                    delta_dict["tool_calls"] = list()
                # When structured output is requested, don't yield partial output
                if has_structured:
                    delta_dict["structured"] = ""
                # When messages requested, use empty list to bypass messages deepcopy
                if has_messages:
                    delta_dict["messages"] = list()
                    delta_dict["delta_messages"] = list()
                # Meta fields are only emitted in the final delta
                if has_meta:
                    for mk in _LLM_META_INCLUDES:
                        delta_dict[mk] = None

                if (not delta_dict.get("text")) and (not delta_dict.get("think")) and (not delta_dict.get("content")):
                    continue  # Skip empty deltas
                # Record first content chunk time (prefill complete)
                if first_chunk_t is None and t0 is not None:
                    first_chunk_t = time.perf_counter()
                yield _llm_response_formatting(delta=delta_dict, include=include, messages=list(), reduce=reduce)

            # Yield final tool_calls/tool_messages/tool_results/structured/delta_messages/messages/meta after stream ends
            if t0 is not None:
                inference_end_t = time.perf_counter()
                prompt_elapsed = round_elapsed(first_chunk_t - t0) if first_chunk_t else round_elapsed(inference_end_t - t0)
                completion_elapsed = round_elapsed(inference_end_t - first_chunk_t) if first_chunk_t else 0.0
                inference_elapsed = round_elapsed((prompt_elapsed or 0.0) + (completion_elapsed or 0.0))
                usage_data["prompt_elapsed"] = prompt_elapsed
                usage_data["completion_elapsed"] = completion_elapsed
                usage_data["inference_elapsed"] = inference_elapsed
            if created_at:
                usage_data["created_at"] = created_at
            if _stream_meta is not None:
                usage_data["cached"] = _stream_meta.get("cached", False)

            if repair_tool_calls:
                tool_calls = [repair_tool_call(tool_call, toolspec_dict) for tool_call in response.tool_calls]
            else:
                tool_calls = response.tool_calls
            final_delta = {
                "think": "",
                "text": "",
                "tool_calls": tool_calls if tool_calls else list(),
                "content": "",
                "message": {"role": "assistant", "content": ""} | ({"tool_calls": tool_calls} if tool_calls else {}),
                "gathered_message": response.to_message(),
                "usage": usage_data,
            }
            # Execute tools if tool_messages or tool_results requested
            if response.tool_calls and (("tool_messages" in include) or ("tool_results" in include) or has_messages):
                tool_exec_t0 = time.perf_counter()
                tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, toolspec_dict)
                final_delta["tool_messages"] = tool_messages
                final_delta["tool_results"] = tool_results
                if tool_usage:
                    usage_data["tool_usage"] = tool_usage
                    usage_data["tool_elapsed"] = round_elapsed(time.perf_counter() - tool_exec_t0)
            # Compute total elapsed (inference + tools)
            if t0 is not None:
                usage_data["elapsed"] = round_elapsed(time.perf_counter() - t0)
            if has_structured:
                final_delta["structured"] = response.text
            if has_messages:
                final_delta["messages"] = None  # Explicitly set to None to trigger construction in formatting
                final_delta["delta_messages"] = None  # Explicitly set to None to trigger construction in formatting
            yield _llm_response_formatting(delta=final_delta, include=include, messages=formatted_messages, reduce=reduce)
            return

    def oracle(
        self,
        messages: Messages,
        tools: Optional[List[Union[Dict, "ToolSpec"]]] = None,
        tool_choice: Optional[str] = None,
        include: Optional[List[LLMIncludeType]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> LLMResponse:
        """\
        Get the final LLM response for the given messages (aggregated from a stream).

        Args:
            messages: Conversation content, normalized by ``format_messages``.
            tools: Optional list of tools, each can be a ToolSpec or jsonschema dict.
            tool_choice: Tool choice setting. Defaults to "auto" if tools present.
            include: Fields to include in the final result.
            verbose: If True, logs the resolved request config.
            reduce: If True and len(include) == 1, returns a single value instead of a dict.
            **kwargs: Per-call overrides for LLM config.

        Returns:
            LLMResponse: The consolidated response.
        """
        stream = list()
        for chunk in self.stream(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            include=include,
            verbose=verbose,
            reduce=False,  # Must use reduce=False for gather_stream
            **kwargs,
        ):
            stream.append(chunk)
        return gather_stream(stream, include=include, reduce=reduce)

    async def aoracle(
        self,
        messages: Messages,
        tools: Optional[List[Union[Dict, "ToolSpec"]]] = None,
        tool_choice: Optional[str] = None,
        include: Optional[List[LLMIncludeType]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> LLMResponse:
        """\
        Asynchronously retrieve the final LLM response (aggregated from the async stream).
        """
        stream = list()
        async for chunk in self.astream(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            include=include,
            verbose=verbose,
            reduce=False,  # Must use reduce=False for gather_stream
            **kwargs,
        ):
            stream.append(chunk)
        return gather_stream(stream, include=include, reduce=reduce)

    def embed(
        self,
        inputs: Union[str, List[str]],
        include: Optional[List["EmbedIncludeType"]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> Union[List[List[float]], Dict[str, Any]]:
        """\
        Get embeddings for the given inputs.

        Args:
            inputs: A single string or a list of strings to embed.
            include: Fields to include (``"embeddings"``, ``"usage"``).
                     Defaults to ``["embeddings"]`` for backward compatibility.
            verbose: If True, logs the resolved request config.
            reduce: If True and only one include field, unwrap the dict and return
                    the value directly.
            **kwargs: Additional parameters for the embedding request.

        Returns:
            Embeddings list when *include* is default, or a dict of requested fields.
        """
        include = self._validate_embed_include(include)
        if isinstance(inputs, str):
            inputs = [inputs]
            single = True
        else:
            single = False
        cfg = deepcopy(self.args) | deepcopy(kwargs)
        with NetworkProxy(
            http_proxy=cfg.pop("http_proxy", None),
            https_proxy=cfg.pop("https_proxy", None),
            no_proxy=cfg.pop("no_proxy", None),
        ):
            if verbose:
                logger.info(f"HTTP  Proxy: {os.environ.get('HTTP_PROXY')}")
                logger.info(f"HTTPS Proxy: {os.environ.get('HTTPS_PROXY')}")
                logger.info(f"Request Args: {encrypt_display(cfg)}\nInputs:\n" + "\n".join(f"- {input}" for input in inputs))
            has_usage = "usage" in include
            if has_usage:
                embeddings, usage_meta = self._instrumented_embed(batch=inputs, **cfg)
            else:
                embeddings = self._cached_embed(batch=inputs, **cfg)
            result = {}
            if "embeddings" in include:
                result["embeddings"] = embeddings[0] if single else embeddings
            if has_usage:
                result["usage"] = usage_meta
            if reduce and len(result) == 1:
                return next(iter(result.values()))
            return result

    async def aembed(
        self,
        inputs: Union[str, List[str]],
        include: Optional[List["EmbedIncludeType"]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> Union[List[List[float]], Dict[str, Any]]:
        """\
        Get embeddings for the given inputs asynchronously.
        """
        include = self._validate_embed_include(include)
        if isinstance(inputs, str):
            inputs = [inputs]
            single = True
        else:
            single = False
        cfg = deepcopy(self.args) | deepcopy(kwargs)
        with NetworkProxy(
            http_proxy=cfg.pop("http_proxy", None),
            https_proxy=cfg.pop("https_proxy", None),
            no_proxy=cfg.pop("no_proxy", None),
        ):
            if verbose:
                logger.info(f"HTTP  Proxy: {os.environ.get('HTTP_PROXY')}")
                logger.info(f"HTTPS Proxy: {os.environ.get('HTTPS_PROXY')}")
                logger.info(f"Request Args: {encrypt_display(cfg)}\nInputs:\n" + "\n".join(f"- {input}" for input in inputs))
            has_usage = "usage" in include
            if has_usage:
                embeddings, usage_meta = await self._instrumented_aembed(batch=inputs, **cfg)
            else:
                embeddings = await self._cached_aembed(batch=inputs, **cfg)
            result = {}
            if "embeddings" in include:
                result["embeddings"] = embeddings[0] if single else embeddings
            if has_usage:
                result["usage"] = usage_meta
            if reduce and len(result) == 1:
                return next(iter(result.values()))
            return result

    def tooluse(
        self,
        messages: Messages,
        tools: List[Union[Dict, "ToolSpec"]],
        tool_choice: str = "required",
        include: Optional[Union[str, List[str]]] = None,
        verbose: bool = False,
        reduce: bool = True,
        **kwargs,
    ) -> List[Dict]:
        """\
        Execute tool calls with the LLM.

        This is a convenience method that forces the LLM to use tools and returns the
        executed tool messages. It sets tool_choice="required" and returns tool_messages by default.

        Args:
            messages: Conversation content.
            tools: List of tools (ToolSpec instances required for execution).
            tool_choice: Tool choice setting. Defaults to "required".
            include: Fields to include in the result. Defaults to ["tool_messages"].
            verbose: If True, logs the resolved request config.
            reduce: If True, simplifies the output when possible.
            **kwargs: Per-call overrides for LLM config.

        Returns:
            List[Dict]: List of tool result messages in OpenAI format.
        """
        return self.oracle(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            include=["tool_messages"] if include is None else include,
            verbose=verbose,
            reduce=reduce,
            **kwargs,
        )

    async def atooluse(
        self,
        messages: Messages,
        tools: List[Union[Dict, "ToolSpec"]],
        verbose: bool = False,
        **kwargs,
    ) -> List[Dict]:
        """\
        Asynchronously execute tool calls with the LLM.
        """
        return await self.aoracle(
            messages=messages,
            tools=tools,
            tool_choice="required",
            include=["tool_messages"],
            verbose=verbose,
            reduce=True,
            **kwargs,
        )

    @property
    def dim(self):
        """\
        Get the dimensionality of the embeddings produced by this LLM.
        This is determined by making a test embedding call (i.e., "<TEST>").

        Returns:
            int: The dimensionality of the embeddings.
        """
        if self._dim is not None:
            return self._dim
        try:
            test_embed = self.embed("<TEST>", verbose=False)
            if test_embed and isinstance(test_embed, list):
                self._dim = len(test_embed)
                return self._dim
            raise ValueError(f"Unexpected embedding format. This LLM may not support embeddings (got: {test_embed})")
        except Exception as e:
            raise ValueError(f"Failed to determine embedding. This LLM may not support embeddings (got error: {error_str(e)})")

    @property
    def embed_empty(self) -> List[float]:
        """\
        Get a fixed embedding vector for empty strings.

        Returns:
            List[float]: The embedding vector for an empty string.
        """
        return [1.0] + [0.0] * (self.dim - 1)
