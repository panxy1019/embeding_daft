"""Tests for REVIEW.md bug fixes on the agent-mcp branch."""

import json
import pytest
from copy import deepcopy

from ahvn.utils.llm.base import (
    _merge_tool_call_deltas,
    _normalize_tool_call_delta,
    _llm_response_formatting,
    _LLMChunk,
)

# ---------------------------------------------------------------------------
# P0 #1: _merge_tool_call_deltas — index 0 treated as falsy
# ---------------------------------------------------------------------------


class TestMergeToolCallDeltasIndexZero:
    """Regression: index=0 was treated as falsy by `or`, merging into wrong slot."""

    def test_index_zero_merges_to_first_slot(self):
        accumulated = []
        deltas = [
            {"index": 0, "id": "tc_0", "function": {"name": "foo", "arguments": '{"a":1}'}},
        ]
        result = _merge_tool_call_deltas(accumulated, deltas)
        assert len(result) == 1
        assert result[0]["id"] == "tc_0"
        assert result[0]["function"]["name"] == "foo"

    def test_index_zero_continuation_appends_arguments(self):
        accumulated = [
            {"id": "tc_0", "type": "function", "function": {"name": "foo", "arguments": '{"a":'}},
        ]
        deltas = [
            {"index": 0, "function": {"arguments": "1}"}},
        ]
        result = _merge_tool_call_deltas(accumulated, deltas)
        assert result[0]["function"]["arguments"] == '{"a":1}'

    def test_two_tools_index_0_and_1(self):
        accumulated = []
        deltas = [
            {"index": 0, "id": "tc_0", "function": {"name": "foo", "arguments": "{}"}},
            {"index": 1, "id": "tc_1", "function": {"name": "bar", "arguments": "{}"}},
        ]
        result = _merge_tool_call_deltas(accumulated, deltas)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "foo"
        assert result[1]["function"]["name"] == "bar"

    def test_index_zero_argument_continuation_does_not_go_to_last(self):
        """Core regression: index=0 continuation chunk must go to slot 0, not the last slot."""
        accumulated = [
            {"id": "tc_0", "type": "function", "function": {"name": "foo", "arguments": '{"x":'}},
            {"id": "tc_1", "type": "function", "function": {"name": "bar", "arguments": "{}"}},
        ]
        deltas = [
            {"index": 0, "function": {"arguments": "42}"}},
        ]
        result = _merge_tool_call_deltas(accumulated, deltas)
        assert result[0]["function"]["arguments"] == '{"x":42}'
        assert result[1]["function"]["arguments"] == "{}"


# ---------------------------------------------------------------------------
# P0 #2: _llm_response_formatting — messages include crashes (dict + list)
# ---------------------------------------------------------------------------


class TestResponseFormattingMessagesInclude:
    """Regression: gathered_message is a dict, not a list; concatenation crashed."""

    def test_messages_include_with_gathered_message_dict(self):
        delta = {
            "messages": None,
            "gathered_message": {"role": "assistant", "content": "hello"},
            "tool_messages": [{"role": "tool", "tool_call_id": "t1", "content": "ok"}],
        }
        msgs = [{"role": "user", "content": "hi"}]
        result = _llm_response_formatting(delta=delta, include=["messages"], messages=msgs, reduce=True)
        # Should be: [user_msg, assistant_msg, tool_msg]
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "tool"

    def test_delta_messages_include_with_gathered_message_dict(self):
        delta = {
            "delta_messages": None,
            "gathered_message": {"role": "assistant", "content": "hello"},
            "tool_messages": [],
        }
        result = _llm_response_formatting(delta=delta, include=["delta_messages"], messages=[], reduce=True)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"

    def test_messages_include_no_gathered_message(self):
        delta = {
            "messages": None,
            "gathered_message": None,
            "tool_messages": [],
        }
        msgs = [{"role": "user", "content": "hi"}]
        result = _llm_response_formatting(delta=delta, include=["messages"], messages=msgs, reduce=True)
        assert isinstance(result, list)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# P1 #3: _LLMChunk.__getitem__ — getattr(..., default=...) invalid keyword
# ---------------------------------------------------------------------------


class TestLLMChunkGetItem:
    """Regression: getattr() does not accept 'default' as a keyword argument."""

    def test_getitem_existing_field(self):
        chunk = _LLMChunk()
        chunk.text = "hello"
        assert chunk["text"] == "hello"

    def test_getitem_missing_field_returns_none(self):
        chunk = _LLMChunk()
        assert chunk["nonexistent"] is None

    def test_getitem_tool_calls_default(self):
        chunk = _LLMChunk()
        assert chunk["tool_calls"] == []


# ---------------------------------------------------------------------------
# P2: XML skill descriptor escaping
# ---------------------------------------------------------------------------


class TestSkillXMLEscape:
    """Ensure XML special chars in skill names/descriptions are escaped."""

    def test_skill_desc_composer_escapes_special_chars(self):
        from ahvn.ukf.templates.basic.skill import desc_composer

        class FakeSkillUKFT:
            name = 'test<>&"skill'
            description = "A <bold> skill with & special chars"

            def get(self, key, default=None):
                if key == "tools":
                    return []
                return default

        result = desc_composer(FakeSkillUKFT())
        assert "&lt;" in result
        assert "&amp;" in result
        assert "<bold>" not in result  # should be escaped


# ---------------------------------------------------------------------------
# P2: load_skill duplicated branches — directory vs not-found
# ---------------------------------------------------------------------------


class TestLoadSkillDirectoryDetection:
    """Regression: duplicated `if file_content is None` made directory detection unreachable."""

    def test_directory_path_returns_directory_error(self):
        from ahvn.utils.basic.skill_utils import load_skill

        # A directory entry has None as its value
        data = {
            "subdir": None,
            "subdir/file.md": "base64encodedcontent",
        }
        result = load_skill("test-skill", path="subdir", data=data)
        assert "[ERROR]" in result
        assert "directory" in result.lower()

    def test_missing_path_returns_not_found(self):
        from ahvn.utils.basic.skill_utils import load_skill

        data = {"file.md": "dGVzdA=="}
        result = load_skill("test-skill", path="nonexistent.md", data=data)
        assert "[ERROR]" in result
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# P3: PromptStore unused imports removed
# ---------------------------------------------------------------------------


class TestPromptStoreImports:
    """Ensure the unused imports were actually removed and the module still loads."""

    def test_prompt_store_importable(self):
        from ahvn.utils.prompt.prompt_store import PromptStore  # noqa: F401

        assert PromptStore is not None
