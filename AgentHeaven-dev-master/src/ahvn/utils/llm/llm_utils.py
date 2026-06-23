__all__ = [
    "get_litellm",
    "get_litellm_retryable_exceptions",
    "Message",
    "Messages",
    "normalize_tool_call",
    "parse_tool_args",
    "format_tool_call",
    "format_tool_calls",
    "format_messages",
    "round_elapsed",
]

from ..basic.log_utils import get_logger

logger = get_logger(__name__)
from ..basic.config_utils import CM_AHVN
from ..deps import deps

import os

_core = CM_AHVN.get("core", dict())
_debug = _core.get("debug", False)
_litellm_debug = bool(CM_AHVN.get("llm.litellm_debug", False) and _debug)

# Set up LiteLLM environment variables
_http_proxy = _core.get("http_proxy", None)
_https_proxy = _core.get("https_proxy", None)
if _http_proxy:
    os.environ["HTTP_PROXY"] = _http_proxy
if _https_proxy:
    os.environ["HTTPS_PROXY"] = _https_proxy
if not _litellm_debug:
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["DISABLE_SCHEMA_UPDATE"] = "True"
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    os.environ["LITELLM_LOG"] = "ERROR"

_litellm = None


def get_litellm():
    """Lazy load litellm with configuration."""
    global _litellm
    if _litellm is None:
        _litellm = deps.load("litellm")

        _litellm.drop_params = True
        # NOTE: SSL verification is globally disabled for compatibility with proxy setups
        # and internal endpoints. A per-call ssl_verify override should be added in the future.
        _litellm.ssl_verify = False
        _litellm.disable_end_user_cost_tracking = True
        if not _litellm_debug:
            _litellm._logging._disable_debugging()
            _litellm.suppress_debug_info = True
            _litellm.set_verbose = False
        else:
            _litellm._turn_on_debug()
    return _litellm


def get_litellm_retryable_exceptions():
    """Get retryable exceptions from litellm."""
    litellm = get_litellm()
    return [
        litellm.Timeout,
        litellm.RateLimitError,
        litellm.ServiceUnavailableError,
        litellm.APIConnectionError,
        litellm.InternalServerError,
        litellm.APIError,
    ]


from typing import Dict, Any, Union, List
from copy import deepcopy
from ..basic.serialize_utils import dumps_json, loads_json

Message = Union[str, Dict[str, Any], Any]  # litellm.Message is Any when lazy loaded
Messages = Union[Message, List[Message]]
_DEFAULT_ARGUMENTS = object()


def normalize_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one tool call to OpenAI function-call shape.

    This accepts both common representations:
    - ``{"function": {"name": "...", "arguments": ...}, "id": "...", ...}``
    - ``{"name": "...", "arguments": ...}``

    Args:
        tool_call: Raw tool-call payload from LLM output or parser output.

    Returns:
        Dict[str, Any]: A normalized dict with ``id``, ``type``, and ``function`` keys.

    Raises:
        TypeError: If ``tool_call`` is not a dict.
    """
    if not isinstance(tool_call, dict):
        raise TypeError(f"tool_call must be dict, got {type(tool_call)}")

    raw_function = tool_call.get("function", dict())
    if isinstance(raw_function, dict):
        name = raw_function.get("name", None) or tool_call.get("name", "") or ""
        arguments = raw_function.get("arguments", tool_call.get("arguments", "{}"))
    else:
        name = tool_call.get("name", "") or ""
        arguments = tool_call.get("arguments", "{}")

    if arguments is None:
        arguments = "{}"

    normalized = {
        "id": tool_call.get("id", "") or "",
        "type": tool_call.get("type", "function") or "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }
    if "index" in tool_call:
        normalized["index"] = tool_call["index"]
    return normalized


def parse_tool_args(arguments: Any, default: Any = _DEFAULT_ARGUMENTS) -> Any:
    """Parse a tool-call ``arguments`` payload into a Python object.

    Behavior:
    - Dict/list inputs are returned unchanged.
    - JSON strings are decoded with ``loads_json``.
    - Empty strings and ``None`` become ``{}`` by default.
    - Parse failures return ``default`` (or ``{}`` if not provided).

    Args:
        arguments: Value from ``tool_call["function"]["arguments"]``.
        default: Value returned when parsing fails. If omitted, defaults to ``{}``.

    Returns:
        Any: Parsed arguments object.
    """
    if default is _DEFAULT_ARGUMENTS:
        default = {}
    if arguments is None:
        return default
    if isinstance(arguments, (dict, list)):
        return arguments
    if not isinstance(arguments, str):
        return arguments
    stripped = arguments.strip()
    if not stripped:
        return default
    try:
        return loads_json(stripped)
    except Exception:
        return default


def format_tool_call(tool_call: Dict[str, Any], parse_arguments: bool = True, sort_keys: bool = False, ensure_ascii: bool = False) -> str:
    """Format one tool call as a readable function statement.

    Example output:
    - ``add(a=1, b=2)``
    - ``search(query="llm")``
    - ``unknown_tool({"broken_json":)``

    Args:
        tool_call: Tool-call dict in raw or normalized form.
        parse_arguments: If True, attempts JSON parsing before formatting.
        sort_keys: Whether to sort keys when serializing argument values.
        ensure_ascii: Whether to escape non-ASCII characters in serialized values.

    Returns:
        str: Function-like statement for logging or display.
    """
    normalized = normalize_tool_call(tool_call)
    function = normalized.get("function", dict())
    name = function.get("name", "") or "<unknown_tool>"
    raw_arguments = function.get("arguments", "{}")
    if parse_arguments:
        arguments = parse_tool_args(raw_arguments, default=raw_arguments)
    else:
        arguments = raw_arguments

    if isinstance(arguments, dict):
        if not arguments:
            return f"{name}()"
        kwargs = ", ".join(f"{k}={dumps_json(v, sort_keys=sort_keys, indent=None, ensure_ascii=ensure_ascii)}" for k, v in arguments.items())
        return f"{name}({kwargs})"

    if isinstance(arguments, str):
        arg_text = arguments.strip()
        if arg_text in ("", "{}"):
            return f"{name}()"
        return f"{name}({arg_text})"

    return f"{name}({dumps_json(arguments, sort_keys=sort_keys, indent=None, ensure_ascii=ensure_ascii)})"


def format_tool_calls(tool_calls: List[Dict[str, Any]], parse_arguments: bool = True, sort_keys: bool = False, ensure_ascii: bool = False) -> List[str]:
    """Format a list of tool calls into function-like statements.

    Args:
        tool_calls: List of tool-call dictionaries.
        parse_arguments: Passed through to ``format_tool_call``.
        sort_keys: Passed through to ``format_tool_call``.
        ensure_ascii: Passed through to ``format_tool_call``.

    Returns:
        List[str]: Formatted statements in input order.
    """
    return [
        format_tool_call(
            tool_call,
            parse_arguments=parse_arguments,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
        for tool_call in (tool_calls or list())
    ]


def format_messages(messages: Messages) -> List[Dict]:
    """\
    Unify messages for LLM in diverse formats to OpenAI message format.

    1. If messages is a single string, it is treated as a single user message.
    2. If messages is a list, each item is processed as follows:

        - If the item is a litellm.Message object, it is converted to dict using its json() method.
        - If the item is a string, it is treated as a user message.
        - If the item is a dict, it is used as is, but must contain a "role" field.
        - If the item is of any other type, a TypeError is raised.

    3. If a message dict contains "tool_calls", its "function.arguments" field is converted to a JSON string if it is not already a string.

    Args:
        messages: List of messages that can be either dict or Message objects

    Returns:
        List[dict]: List of formatted messages in OpenAI format

    Raises:
        ValueError: If messages are invalid or missing required fields
        TypeError: If an unsupported message type is encountered
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    formatted_messages = []
    litellm = get_litellm()
    for message in messages:
        if isinstance(message, str):
            formatted_messages.append({"role": "user", "content": message})
            continue
        if isinstance(message, litellm.Message):
            message = message.json()
        if isinstance(message, dict):
            if "role" not in message:
                logger.error("Message dict must contain 'role' field")
                raise ValueError("Message dict must contain 'role' field")
            if message.get("tool_calls"):
                copied_message = deepcopy(message)
                for i, tool_call in enumerate(copied_message["tool_calls"]):
                    if not isinstance(tool_call["function"]["arguments"], str):
                        tool_call["function"]["arguments"] = dumps_json(tool_call["function"]["arguments"], indent=None)
                formatted_messages.append(copied_message)
            else:
                formatted_messages.append(deepcopy(message))
            continue
        raise TypeError(f"Unsupported message type: {type(message)}")
    return formatted_messages


def round_elapsed(value: Any, ndigits: int = None) -> Union[float, None]:
    """\
    Round elapsed time values using global LLM usage precision config.

    Args:
        value: Numeric elapsed value in seconds.
        ndigits: Optional precision override. If None, uses ``llm.usage.round_elapsed``.

    Returns:
        Rounded float, or None when value is not numeric.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if ndigits is None:
        try:
            ndigits = int(CM_AHVN.get("llm.usage.round_elapsed", 4))
        except Exception:
            ndigits = 4
    return round(number, ndigits)
