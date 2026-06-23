from ahvn.utils.llm import LLM


def main() -> None:
    llm = LLM(preset="chat-local", model="qwen/qwen3.5-35b-a3b", cache=False)

    # Test 1: Streaming with reduce=True (default text only)
    print("========== Test 1: Basic stream ==========")
    for chunk in llm.stream(messages="Say hello in one sentence."):
        print(chunk, end="", flush=True)
    print()

    # Test 2: Streaming with usage (reduce=False to see all fields)
    print("\n========== Test 2: Usage include ==========")
    chunks = list(
        llm.stream(
            messages="Say hello in one sentence.",
            include=["text", "usage"],
            reduce=False,
        )
    )
    for chunk in chunks:
        print(chunk)
    # Verify usage is present on the final chunk and timing invariants hold
    usage = chunks[-1].get("usage", {})
    print(f"\nUsage summary: {usage}")
    assert "prompt_elapsed" in usage, "Missing prompt_elapsed"
    assert "completion_elapsed" in usage, "Missing completion_elapsed"
    assert "inference_elapsed" in usage, "Missing inference_elapsed"
    assert "elapsed" in usage, "Missing elapsed"
    assert abs(usage["prompt_elapsed"] + usage["completion_elapsed"] - usage["inference_elapsed"]) < 0.001, (
        f"prompt_elapsed + completion_elapsed != inference_elapsed: "
        f"{usage['prompt_elapsed']} + {usage['completion_elapsed']} != {usage['inference_elapsed']}"
    )
    # Without tools: elapsed ≈ inference_elapsed
    assert (
        abs(usage["elapsed"] - usage["inference_elapsed"]) < 0.01
    ), f"elapsed != inference_elapsed (no tools): {usage['elapsed']} != {usage['inference_elapsed']}"
    print("  [PASS] prompt_elapsed + completion_elapsed == inference_elapsed")
    print("  [PASS] elapsed == inference_elapsed (no tools)")

    # Test 3: Tool call response with detailed timing and usage
    print("\n========== Test 3: Tool call with usage ==========")
    from math_toolkit_demo import math_toolkit

    chunks = list(
        llm.stream(
            messages="Calculate fibonacci(8) * fibonacci(16) + fibonacci(12).",
            tools=[math_toolkit.get_tool(name) for name in math_toolkit.list_tools()],
            include=["text", "delta_messages", "usage"],
            reduce=False,
        )
    )
    for chunk in chunks:
        print(chunk)
    # Verify tool usage and timing invariants
    usage = chunks[-1].get("usage", {})
    print(f"\nUsage summary: {usage}")
    if "tool_usage" in usage:  # When not providing tool_results, tool_messages, nor delta_messages, tool_usage may be missing as tools are not executed
        assert "tool_usage" in usage, "Missing tool_usage"
        assert "tool_elapsed" in usage, "Missing tool_elapsed"
        assert "inference_elapsed" in usage, "Missing inference_elapsed"
        assert "elapsed" in usage, "Missing elapsed"
        assert abs(usage["prompt_elapsed"] + usage["completion_elapsed"] - usage["inference_elapsed"]) < 0.001, (
            f"prompt_elapsed + completion_elapsed != inference_elapsed: "
            f"{usage['prompt_elapsed']} + {usage['completion_elapsed']} != {usage['inference_elapsed']}"
        )
        assert abs(usage["tool_elapsed"] + usage["inference_elapsed"] - usage["elapsed"]) < 0.01, (
            f"tool_elapsed + inference_elapsed != elapsed: " f"{usage['tool_elapsed']} + {usage['inference_elapsed']} != {usage['elapsed']}"
        )
        print("  [PASS] prompt_elapsed + completion_elapsed == inference_elapsed")
        print("  [PASS] tool_elapsed + inference_elapsed ≈ elapsed")
        print(f"  tool_usage keys (tool_call_ids): {list(usage['tool_usage'].keys())}")
        for tc_id, tc_usage in usage["tool_usage"].items():
            print(f"    {tc_id}: elapsed={tc_usage['elapsed']}s, created_at={tc_usage['created_at']}")
    else:
        assert "inference_elapsed" in usage, "Missing inference_elapsed"
        assert "elapsed" in usage, "Missing elapsed"
        assert abs(usage["prompt_elapsed"] + usage["completion_elapsed"] - usage["inference_elapsed"]) < 0.001, (
            f"prompt_elapsed + completion_elapsed != inference_elapsed: "
            f"{usage['prompt_elapsed']} + {usage['completion_elapsed']} != {usage['inference_elapsed']}"
        )
        assert (
            abs(usage["elapsed"] - usage["inference_elapsed"]) < 0.01
        ), f"elapsed != inference_elapsed (tools not executed): {usage['elapsed']} != {usage['inference_elapsed']}"
        print("  [PASS] prompt_elapsed + completion_elapsed == inference_elapsed")
        print("  [SKIP] tool_elapsed and tool_usage not present, likely because tools were not executed (e.g. due to missing tool_results or tool_messages)")


if __name__ == "__main__":
    main()
