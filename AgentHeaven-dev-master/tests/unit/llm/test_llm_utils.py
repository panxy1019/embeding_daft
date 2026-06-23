from ahvn.utils.llm import (
    normalize_tool_call,
    parse_tool_args,
    format_tool_call,
    format_tool_calls,
)


def test_normalize_tool_call_with_function_layer():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "add",
            "arguments": '{"a": 1, "b": 2}',
        },
    }
    normalized = normalize_tool_call(tool_call)
    assert normalized["id"] == "call_1"
    assert normalized["type"] == "function"
    assert normalized["function"]["name"] == "add"
    assert normalized["function"]["arguments"] == '{"a": 1, "b": 2}'


def test_normalize_tool_call_without_function_layer():
    tool_call = {
        "name": "sub",
        "arguments": {"a": 3, "b": 1},
    }
    normalized = normalize_tool_call(tool_call)
    assert normalized["id"] == ""
    assert normalized["type"] == "function"
    assert normalized["function"] == {"name": "sub", "arguments": {"a": 3, "b": 1}}


def test_parse_tool_args_json_string():
    parsed = parse_tool_args('{"query":"ahvn","top_k":3}')
    assert parsed == {"query": "ahvn", "top_k": 3}


def test_parse_tool_args_invalid_returns_default():
    parsed = parse_tool_args("{bad", default={"fallback": True})
    assert parsed == {"fallback": True}


def test_format_tool_call_with_dict_arguments():
    tool_call = {
        "function": {
            "name": "add",
            "arguments": {"a": 1, "b": 2},
        }
    }
    assert format_tool_call(tool_call) == "add(a=1, b=2)"


def test_format_tool_call_with_json_arguments():
    tool_call = {
        "function": {
            "name": "search",
            "arguments": '{"query": "llm", "limit": 5}',
        }
    }
    assert format_tool_call(tool_call) == 'search(query="llm", limit=5)'


def test_format_tool_call_with_empty_arguments():
    tool_call = {
        "function": {
            "name": "ping",
            "arguments": "{}",
        }
    }
    assert format_tool_call(tool_call) == "ping()"


def test_format_tool_call_with_invalid_json_keeps_raw():
    tool_call = {
        "function": {
            "name": "broken",
            "arguments": "{bad",
        }
    }
    assert format_tool_call(tool_call) == "broken({bad)"


def test_format_tool_calls_batch():
    tool_calls = [
        {"function": {"name": "add", "arguments": '{"a":1,"b":2}'}},
        {"name": "ping", "arguments": "{}"},
    ]
    assert format_tool_calls(tool_calls) == ["add(a=1, b=2)", "ping()"]
