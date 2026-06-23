from copy import deepcopy

import pytest

from ahvn.agent.base import BaseAgentSpec
from ahvn.utils.basic.config_utils import dget, dset
from ahvn.utils.llm import Messages


class DeterministicUsageAgent(BaseAgentSpec):
    def __init__(self, usage_plan):
        super().__init__(name="deterministic_usage_agent", llm_args={"cache": False}, max_steps=8)
        self.usage_plan = deepcopy(usage_plan)
        self.inference_calls = 0

    def encode(self, **inputs):
        return [{"role": "user", "content": inputs.get("query", "hello")}], {"target_steps": int(inputs.get("steps", len(self.usage_plan)))}

    def inference(self, messages: Messages, **kwargs):
        self.inference_calls += 1
        step = self.inference_calls
        yield {
            "text": f"assistant step {step}",
            "delta_messages": [{"role": "assistant", "content": f"assistant step {step}"}],
        }
        usage = self.usage_plan[step - 1] if step - 1 < len(self.usage_plan) else {}
        yield {"usage": deepcopy(usage)}
        return

    def process(self, messages: Messages, delta_messages: Messages, state):
        if dget(state, "metadata.step", 0) >= dget(state, "target_steps", 1):
            dset(state, "metadata.is_finished", True)
            dset(state, "metadata.finish_reason", "completed")
        return delta_messages, state

    def decode(self, messages: Messages, state):
        return {"messages": len(messages), "finish_reason": dget(state, "metadata.finish_reason")}


def test_base_agent_usage_event_aggregates_step_usage():
    usage_plan = [
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_elapsed": 0.1,
            "completion_elapsed": 0.2,
            "inference_elapsed": 0.3,
            "elapsed": 0.3,
        },
        {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "prompt_elapsed": 0.2,
            "completion_elapsed": 0.4,
            "inference_elapsed": 0.6,
            "tool_elapsed": 0.1,
            "elapsed": 0.7,
        },
    ]
    agent = DeterministicUsageAgent(usage_plan=usage_plan)
    chunks = list(agent.stream(steps=2))

    usage_events = [chunk for chunk in chunks if "usage" in chunk]
    assert len(usage_events) == 1
    usage_event = usage_events[0]
    usage = usage_event["usage"]

    assert usage["elapsed"] >= 0.0
    assert usage["created_at"]
    assert "agent_elapsed" not in usage
    assert "agent_loop_elapsed" not in usage
    assert "agent_completed_at" not in usage

    assert usage["prompt_tokens"] == 30
    assert usage["completion_tokens"] == 15
    assert usage["total_tokens"] == 45
    assert usage["prompt_elapsed"] == pytest.approx(0.3, abs=1e-8)
    assert usage["completion_elapsed"] == pytest.approx(0.6, abs=1e-8)
    assert usage["inference_elapsed"] == pytest.approx(0.9, abs=1e-8)
    assert usage["tool_elapsed"] == pytest.approx(0.1, abs=1e-8)

    assert set(usage["step_usage"].keys()) == {"1", "2"}
    assert usage["step_usage"]["1"]["created_at"]
    assert usage["step_usage"]["2"]["created_at"]
    assert usage["step_usage"]["1"]["elapsed"] >= 0.0
    assert usage["step_usage"]["2"]["elapsed"] >= 0.0
    assert usage["step_usage"]["1"]["usage"]["total_tokens"] == 15
    assert usage["step_usage"]["2"]["usage"]["total_tokens"] == 30

    final_state = usage_event["state"]
    assert dget(final_state, "metadata.usage.total_tokens") == 45
    assert dget(final_state, "metadata.usage.step_usage.1.usage.total_tokens") == 15
    assert dget(final_state, "metadata.agent_elapsed") is None
    assert dget(final_state, "metadata.agent_loop_elapsed") is None
    assert dget(final_state, "metadata.agent_created_at") is None
    assert dget(final_state, "metadata.agent_completed_at") is None

    usage_idx = next(i for i, chunk in enumerate(chunks) if "usage" in chunk)
    output_idx = next(i for i, chunk in enumerate(chunks) if "output" in chunk)
    assert usage_idx < output_idx
