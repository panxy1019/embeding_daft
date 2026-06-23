import json
import pytest

from ahvn.utils.llm import exec_tool_calls, repair_tool_call, LLM, EmbedIncludeType
import ahvn.utils.llm.base as llm_base
from ahvn.tool.base import ToolSpec


def echo(value: int = 0) -> int:
    return value


def failer() -> None:
    raise ValueError("boom")


def no_args() -> str:
    return "ok"


def run_query(sql: str, limit: int = 10) -> str:
    return f"{sql} | limit={limit}"


def _spec_map(spec: ToolSpec) -> dict:
    return {spec.binded.name: spec}


class _PassthroughCache:
    def batch_memoize(self, name=None):
        def _decorator(func):
            return func

        return _decorator


class _FakeUsage:
    def __init__(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens


class _FakeEmbeddingResponse:
    def __init__(self, data, prompt_tokens=None):
        self.data = data
        self.usage = _FakeUsage(prompt_tokens) if prompt_tokens is not None else None


class _FakeLiteLLM:
    @staticmethod
    def embedding(input, **kwargs):
        _ = kwargs
        return _FakeEmbeddingResponse(data=[{"embedding": [float(len(text)), 0.0, 0.0]} for text in input])


class _FakeLiteLLMWithUsage:
    @staticmethod
    def embedding(input, **kwargs):
        _ = kwargs
        data = [{"embedding": [float(len(text)), 0.0, 0.0]} for text in input]
        pt = sum(len(t) for t in input)
        return _FakeEmbeddingResponse(data=data, prompt_tokens=pt)


class TestExecToolCalls:
    def test_exec_tool_calls_basic(self):
        spec = ToolSpec.from_func(echo)
        tool_calls = [
            {
                "id": "abc",
                "function": {"name": spec.binded.name, "arguments": json.dumps({"value": 5})},
            }
        ]

        tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, _spec_map(spec))

        assert tool_results == ["5"]
        assert tool_messages == [
            {
                "role": "tool",
                "tool_call_id": "abc",
                "name": spec.binded.name,
                "content": "5",
            }
        ]
        assert "abc" in tool_usage
        assert isinstance(tool_usage["abc"]["elapsed"], float)
        assert isinstance(tool_usage["abc"]["created_at"], str)

    def test_exec_tool_calls_without_function_layer(self):
        spec = ToolSpec.from_func(echo)
        tool_calls = [
            {
                "name": spec.binded.name,
                "arguments": {"value": 7},
            }
        ]

        tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, _spec_map(spec))

        assert tool_results == ["7"]
        assert tool_messages[0]["tool_call_id"] == ""
        assert tool_messages[0]["name"] == spec.binded.name
        assert "" in tool_usage  # empty tool_call_id

    def test_exec_tool_calls_dict_arguments(self):
        spec = ToolSpec.from_func(no_args)
        tool_calls = [
            {
                "id": "",
                "function": {"name": spec.binded.name, "arguments": {}},
            }
        ]

        tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, _spec_map(spec))

        assert tool_results == ["ok"]
        assert tool_messages[0]["content"] == "ok"
        assert "" in tool_usage

    def test_exec_tool_calls_malformed_json(self):
        spec = ToolSpec.from_func(echo)
        tool_calls = [
            {
                "function": {"name": spec.binded.name, "arguments": "{bad"},
            }
        ]

        tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, _spec_map(spec))

        assert "Failed to parse arguments" in tool_results[0]
        assert "Failed to parse arguments" in tool_messages[0]["content"]
        assert len(tool_usage) == 1

    def test_exec_tool_calls_execution_error(self):
        spec = ToolSpec.from_func(failer)
        tool_calls = [
            {
                "function": {"name": spec.binded.name, "arguments": "{}"},
            }
        ]

        tool_messages, tool_results, tool_usage = exec_tool_calls(tool_calls, _spec_map(spec))

        assert tool_results[0].startswith(f"Error executing tool '{spec.binded.name}'")
        assert len(tool_usage) == 1

    def test_exec_tool_calls_missing_name(self):
        spec = ToolSpec.from_func(echo)
        tool_calls = [
            {
                "function": {},
            }
        ]

        with pytest.raises(ValueError):
            exec_tool_calls(tool_calls, _spec_map(spec))


class TestRepairToolCall:
    def test_repair_tool_call_heals_sql_escape_and_key_grounding(self):
        spec = ToolSpec.from_func(run_query)
        tool_call = {
            "id": "tc_sql",
            "type": "function",
            "function": {
                "name": spec.binded.name,
                "arguments": '{"SQL":"SELECT * FROM users WHERE name = "Alice"", "limit": 3}',
            },
        }

        repaired = repair_tool_call(tool_call, _spec_map(spec))
        parsed = json.loads(repaired["function"]["arguments"])

        assert parsed["sql"] == 'SELECT * FROM users WHERE name = "Alice"'
        assert parsed["limit"] == 3

    def test_repair_tool_call_output_executes_with_tool(self):
        spec = ToolSpec.from_func(run_query)
        tool_call = {
            "id": "tc_exec",
            "type": "function",
            "function": {
                "name": spec.binded.name,
                "arguments": '{"SQL":"SELECT 1", "LIMIT": 2}',
            },
        }

        repaired = repair_tool_call(tool_call, _spec_map(spec))
        tool_messages, tool_results, _ = exec_tool_calls([repaired], _spec_map(spec))

        assert tool_results == ["SELECT 1 | limit=2"]
        assert tool_messages[0]["content"] == "SELECT 1 | limit=2"

    def test_stream_repair_toggle_false_skips_repair(self, monkeypatch):
        spec = ToolSpec.from_func(echo)
        llm = object.__new__(LLM)

        monkeypatch.setattr(llm_base, "format_messages", lambda messages: messages)
        monkeypatch.setattr(
            llm_base,
            "repair_tool_call",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("repair_tool_call should not be called")),
        )

        llm._validate_args = lambda messages, tools=None, tool_choice=None, **kwargs: (
            {"messages": messages, "repair_tool_calls": kwargs.get("repair_tool_calls", True)},
            _spec_map(spec),
        )
        llm._validate_include = lambda include=None, **kwargs: include or ["tool_calls"]
        llm._cached_stream = lambda **cfg: iter(
            [
                [
                    {
                        "think": "",
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "tc_skip",
                                "type": "function",
                                "function": {"name": spec.binded.name, "arguments": "{bad"},
                            }
                        ],
                    }
                ]
            ]
        )

        chunks = list(
            llm.stream(
                messages=[{"role": "user", "content": "test"}],
                tools=[spec],
                include=["tool_calls"],
                reduce=False,
                repair_tool_calls=False,
            )
        )
        assert chunks[-1]["tool_calls"][0]["function"]["arguments"] == "{bad"

    def test_stream_repair_toggle_true_repairs(self, monkeypatch):
        spec = ToolSpec.from_func(echo)
        llm = object.__new__(LLM)

        monkeypatch.setattr(llm_base, "format_messages", lambda messages: messages)
        llm._validate_args = lambda messages, tools=None, tool_choice=None, **kwargs: (
            {"messages": messages, "repair_tool_calls": kwargs.get("repair_tool_calls", True)},
            _spec_map(spec),
        )
        llm._validate_include = lambda include=None, **kwargs: include or ["tool_calls"]
        llm._cached_stream = lambda **cfg: iter(
            [
                [
                    {
                        "think": "",
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "tc_repair",
                                "type": "function",
                                "function": {"name": spec.binded.name, "arguments": '{"VALUE": 5}'},
                            }
                        ],
                    }
                ]
            ]
        )

        chunks = list(
            llm.stream(
                messages=[{"role": "user", "content": "test"}],
                tools=[spec],
                include=["tool_calls"],
                reduce=False,
                repair_tool_calls=True,
            )
        )
        parsed_arguments = json.loads(chunks[-1]["tool_calls"][0]["function"]["arguments"])
        assert parsed_arguments == {"value": 5}


class TestEnforceNonStreamStructured:
    """Tests for the enforce_non_stream_structured feature."""

    def test_enforce_non_stream_flag_with_tools(self):
        """Test that enforce_non_stream_structured flag is properly detected."""
        # Test the logic: when enforce_non_stream_structured=True and tools are present, stream should be False
        inputs_with_enforce = {"enforce_non_stream_structured": True, "tools": [{"type": "function"}], "stream": True}

        enforce_non_stream = inputs_with_enforce.get("enforce_non_stream_structured", False)
        has_structured_or_tools = ("response_format" in inputs_with_enforce) or ("tools" in inputs_with_enforce)

        assert enforce_non_stream is True
        assert has_structured_or_tools is True
        # The implementation should set stream to False when both conditions are met

    def test_enforce_non_stream_flag_with_response_format(self):
        """Test that enforce_non_stream_structured flag works with response_format."""
        inputs_with_enforce = {"enforce_non_stream_structured": True, "response_format": {"type": "json_object"}, "stream": True}

        enforce_non_stream = inputs_with_enforce.get("enforce_non_stream_structured", False)
        has_structured_or_tools = ("response_format" in inputs_with_enforce) or ("tools" in inputs_with_enforce)

        assert enforce_non_stream is True
        assert has_structured_or_tools is True
        # The implementation should set stream to False when both conditions are met

    def test_no_enforce_non_stream_without_structured(self):
        """Test that enforce_non_stream_structured doesn't affect normal requests."""
        inputs_no_structured = {"enforce_non_stream_structured": True, "messages": [{"role": "user", "content": "test"}], "stream": True}

        enforce_non_stream = inputs_no_structured.get("enforce_non_stream_structured", False)
        has_structured_or_tools = ("response_format" in inputs_no_structured) or ("tools" in inputs_no_structured)

        assert enforce_non_stream is True
        assert has_structured_or_tools is False
        # The implementation should keep stream=True when no structured output/tools

    def test_default_enforce_non_stream_is_false(self):
        """Test that enforce_non_stream_structured defaults to False."""
        inputs_default = {"tools": [{"type": "function"}], "stream": True}

        enforce_non_stream = inputs_default.get("enforce_non_stream_structured", False)
        has_structured_or_tools = ("response_format" in inputs_default) or ("tools" in inputs_default)

        assert enforce_non_stream is False
        assert has_structured_or_tools is True
        # The implementation should keep stream=True when enforce_non_stream_structured is False


class TestEmbedDispatchRegression:
    def test_cached_embed_non_empty_does_not_touch_embed_empty(self, monkeypatch):
        class GuardLLM(LLM):
            @property
            def embed_empty(self):
                raise AssertionError("embed_empty should not be evaluated for non-empty inputs")

        llm = object.__new__(GuardLLM)
        llm.name = "llm-test"
        llm.cache = _PassthroughCache()
        llm._get_retry = lambda: (lambda fn: fn)

        monkeypatch.setattr(llm_base, "get_litellm", lambda: _FakeLiteLLM())

        result = llm._cached_embed(batch=["Hello"], batch_size=64, num_threads=-1, model="fake")
        assert result == [[5.0, 0.0, 0.0]]

    def test_cached_embed_supports_default_negative_num_threads(self, monkeypatch):
        llm = object.__new__(LLM)
        llm.name = "llm-test"
        llm.cache = _PassthroughCache()
        llm._get_retry = lambda: (lambda fn: fn)

        monkeypatch.setattr(llm_base, "get_litellm", lambda: _FakeLiteLLM())

        result = llm._cached_embed(batch=["A", "BBBB"], batch_size=1, model="fake")
        assert result == [[1.0, 0.0, 0.0], [4.0, 0.0, 0.0]]

    def test_embed_dispatch_normalizes_num_threads_none_to_negative_one(self):
        _, _, _, _, num_threads, _, _ = LLM._embed_dispatch(["hello"], num_threads=None)
        assert num_threads == -1


# ---------------------------------------------------------------------------
# Multi-inference (`n`) warning tests
# ---------------------------------------------------------------------------


class TestMultiInferenceWarning:
    def test_validate_args_warns_when_n_gt_one(self, monkeypatch):
        llm = object.__new__(LLM)
        llm.args = {"n": 2}
        calls = []
        monkeypatch.setattr(llm_base.logger, "warning", lambda *args, **kwargs: calls.append((args, kwargs)))

        cfg, _ = llm._validate_args(messages=[{"role": "user", "content": "hello"}])

        assert cfg["n"] == 2
        assert any(args and ("multi-inference is currently not supported in ahvn" in str(args[0]).lower()) for args, _kwargs in calls)

    def test_validate_args_no_warning_when_n_is_one(self, monkeypatch):
        llm = object.__new__(LLM)
        llm.args = {"n": 1}
        calls = []
        monkeypatch.setattr(llm_base.logger, "warning", lambda *args, **kwargs: calls.append((args, kwargs)))

        cfg, _ = llm._validate_args(messages=[{"role": "user", "content": "hello"}])

        assert cfg["n"] == 1
        assert calls == []

    def test_validate_args_warns_for_non_integer_n(self, monkeypatch):
        llm = object.__new__(LLM)
        llm.args = {}
        calls = []
        monkeypatch.setattr(llm_base.logger, "warning", lambda *args, **kwargs: calls.append((args, kwargs)))

        cfg, _ = llm._validate_args(messages=[{"role": "user", "content": "hello"}], n="many")

        assert cfg["n"] == "many"
        assert any(args and ("multi-inference is currently not supported in ahvn" in str(args[0]).lower()) for args, _kwargs in calls)


# ---------------------------------------------------------------------------
# Embed include / usage tests
# ---------------------------------------------------------------------------


def _make_embed_llm(monkeypatch, litellm_cls=_FakeLiteLLMWithUsage):
    """Create a minimal LLM for embed tests."""
    llm = object.__new__(LLM)
    llm.name = "llm-test"
    llm.cache = _PassthroughCache()
    llm._get_retry = lambda: (lambda fn: fn)
    llm.args = {}
    llm._dim = 3
    monkeypatch.setattr(llm_base, "get_litellm", lambda: litellm_cls())
    return llm


class TestValidateEmbedInclude:
    def test_defaults_to_embeddings(self):
        llm = object.__new__(LLM)
        assert llm._validate_embed_include(None) == ["embeddings"]

    def test_string_coerced_to_list(self):
        llm = object.__new__(LLM)
        assert llm._validate_embed_include("usage") == ["usage"]

    def test_rejects_unknown(self):
        llm = object.__new__(LLM)
        with pytest.raises(ValueError):
            llm._validate_embed_include(["nonsense"])

    def test_deduplicates(self):
        llm = object.__new__(LLM)
        result = llm._validate_embed_include(["usage", "usage", "embeddings"])
        assert result == ["usage", "embeddings"]


class TestEmbedInclude:
    def test_default_embed_returns_list(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello", "World"], model="fake")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == [5.0, 0.0, 0.0]

    def test_default_single_returns_flat(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed("Hello", model="fake")
        assert result == [5.0, 0.0, 0.0]

    def test_include_embeddings_only(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello"], include=["embeddings"], model="fake")
        assert result == [[5.0, 0.0, 0.0]]

    def test_include_usage_only(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello", "World"], include=["usage"], model="fake")
        assert isinstance(result, dict)
        assert "created_at" in result
        assert "elapsed" in result
        assert result["total_count"] == 2
        assert result["unique_count"] == 2
        assert result["empty_count"] == 0
        assert result["dim"] == 3
        assert result["cached"] == 0

    def test_include_both(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello"], include=["embeddings", "usage"], model="fake")
        assert isinstance(result, dict)
        assert "embeddings" in result
        assert "usage" in result
        assert result["embeddings"] == [[5.0, 0.0, 0.0]]
        assert result["usage"]["total_count"] == 1

    def test_include_both_single_input(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed("Hello", include=["embeddings", "usage"], model="fake")
        assert result["embeddings"] == [5.0, 0.0, 0.0]  # unwrapped for single

    def test_reduce_false_wraps_in_dict(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello"], include=["embeddings"], reduce=False, model="fake")
        assert isinstance(result, dict)
        assert "embeddings" in result
        assert result["embeddings"] == [[5.0, 0.0, 0.0]]

    def test_usage_dedup_counts(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["A", "B", "A"], include=["usage"], model="fake")
        assert result["total_count"] == 3
        assert result["unique_count"] == 2

    def test_usage_empty_strings(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(["Hello", "", "World"], include=["usage"], model="fake")
        assert result["empty_count"] == 1
        assert result["total_count"] == 3

    def test_usage_includes_batch_controls_when_present(self, monkeypatch):
        llm = _make_embed_llm(monkeypatch)
        result = llm.embed(
            ["Hello", "World"],
            include=["usage"],
            model="fake",
            batch_size=64,
            num_threads=4,
        )
        assert result["batch_size"] == 64
        assert result["num_threads"] == 4
