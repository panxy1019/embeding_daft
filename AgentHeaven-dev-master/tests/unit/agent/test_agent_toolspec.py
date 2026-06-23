from typing import Any, Dict

import pytest

from ahvn.agent.base import BaseAgentSpec, BasePromptAgentSpec
from ahvn.tool.base import ToolSpec
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.utils.prompt import PromptSpec, default_prompt_composer
from ahvn.utils.llm import Messages


class EchoAgent(BaseAgentSpec):
    def encode(self, **inputs):
        return []

    def decode(self, messages: Messages, state: Dict) -> Any:
        return None

    def run(self, question: str) -> str:
        """Echo the provided question."""
        return question

    def stream(self, **inputs):
        yield {"output": self.run(**inputs)}


def test_agent_first_class_tool_metadata_in_init():
    agent = EchoAgent(
        name="echo_agent",
        short_description="Echo questions.",
        description="A tiny subagent that echoes the incoming question.",
        sig="def echo_agent(question: str) -> str",
    )

    spec = agent.to_tool()
    assert spec.name == "echo_agent"
    assert spec.short_description == "Echo questions."
    assert spec.description == "A tiny subagent that echoes the incoming question."
    assert spec.input_schema["properties"]["question"]["type"] == "string"
    assert spec.input_schema["required"] == ["question"]
    assert spec.output_schema["properties"]["result"]["type"] == "string"


def test_agent_metadata_fields_remain_editable():
    agent = EchoAgent(name="echo_agent")
    agent.short_description = "Initial short description."
    agent.description = "Initial long description."
    agent.input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Prompt text.",
            }
        },
        "required": ["prompt"],
    }
    agent.output_schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Agent answer.",
            }
        },
        "required": ["answer"],
    }

    spec = agent.to_tool()
    assert spec.short_description == "Initial short description."
    assert spec.description == "Initial long description."
    assert spec.input_schema["properties"]["prompt"]["description"] == "Prompt text."
    assert spec.output_schema["properties"]["answer"]["description"] == "Agent answer."


def test_agent_to_tool_supports_sig_override():
    agent = EchoAgent(name="echo_agent")
    spec = agent.to_tool(sig="def delegate(query: str, retries: int = 0) -> str")

    assert spec.name == "delegate"
    assert spec.input_schema["properties"]["query"]["type"] == "string"
    assert spec.input_schema["properties"]["retries"]["default"] == 0
    assert spec.to_sig() == "delegate(query, retries=0)"


def test_agent_supports_nameless_sig_schema_with_explicit_name():
    agent = EchoAgent(name="echo_agent", sig="(question: str) -> str")
    spec = agent.to_tool()

    assert agent.name == "echo_agent"
    assert spec.name == "echo_agent"
    assert spec.input_schema["properties"]["question"]["type"] == "string"
    assert spec.output_schema["properties"]["result"]["type"] == "string"


def test_agent_infers_name_from_named_sig_when_missing():
    agent = EchoAgent(sig="def inferred_agent(question: str) -> str")
    spec = agent.to_tool()

    assert agent.name == "inferred_agent"
    assert spec.name == "inferred_agent"


def test_agent_requires_name_when_both_name_sources_missing():
    with pytest.raises(ValueError, match="Agent requires a name"):
        EchoAgent()

    with pytest.raises(ValueError, match="Agent requires a name"):
        EchoAgent(sig="(question: str) -> str")


def test_agent_keeps_instance_name_but_exports_sig_name():
    agent = EchoAgent(name="echo_agent", sig="def exported_delegate(question: str) -> str")
    spec = agent.to_tool()

    assert agent.name == "echo_agent"
    assert spec.name == "exported_delegate"
    assert spec.to_sig() == "exported_delegate(question)"


def test_base_prompt_agent_binds_skillspecs_only(monkeypatch):
    class _DummySkillToolSpec:
        state = {"loaded_tools": []}

    class _DummySkill:
        def text(self):
            return "<skill><name>dummy_skill</name></skill>"

    monkeypatch.setattr("ahvn.agent.base.SkillToolSpec.from_skills", lambda _skills: _DummySkillToolSpec())

    prompt_spec = PromptSpec.from_func(default_prompt_composer, id="agent_bind_prompt_test")
    prompt = PromptUKFT.from_spec(prompt_spec, name="agent_bind_prompt_test")

    tool = ToolSpec.from_func(lambda text: text, name="echo_tool", parse_docstring=False)
    skill = _DummySkill()

    agent = BasePromptAgentSpec(
        name="bind_agent",
        prompt=prompt,
        tools=[tool],
        skills=[skill],
        llm_args={"cache": False},
    )

    assert agent.prompt.get("binds.toolspecs") is None
    assert agent.prompt.get("binds.skillspecs") == [skill]
