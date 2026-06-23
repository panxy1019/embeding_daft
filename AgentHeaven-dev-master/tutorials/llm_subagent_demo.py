from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)


def add(a: int, b: int) -> int:
    """Add two integers."""
    logger.success(f"[TOOL] add({a}, {b}) = {a + b}")
    return a + b


def sub(a: int, b: int) -> int:
    """Subtract two integers."""
    logger.success(f"[TOOL] sub({a}, {b}) = {a - b}")
    return a - b


def mul(a: int, b: int) -> int:
    """Multiply two integers."""
    logger.success(f"[TOOL] mul({a}, {b}) = {a * b}")
    return a * b


def sqr(a: int) -> int:
    """Square an integer."""
    logger.success(f"[TOOL] sqr({a}) = {a * a}")
    return a * a


def exp(a: int, b: int, mod: int = None) -> int:
    """Exponentiate a to the power of b. When mod is provided, compute (a ** b) % mod."""
    result = a**b
    if mod is not None:
        result %= mod
    logger.success(f"[TOOL] exp({a}, {b}, mod={mod}) = {result}")
    return result


def mod(a: int, b: int) -> int:
    """Compute a modulo b."""
    logger.success(f"[TOOL] mod({a}, {b}) = {a % b}")
    return a % b


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number (0-indexed)."""
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    logger.success(f"[TOOL] fibonacci({n}) = {b}")
    return b


from ahvn.tool import Toolkit, ToolSpec


class FibonacciToolkit(Toolkit):
    def __init__(self, name: str = "fibonacci"):
        super().__init__(
            name=name,
            description="A toolkit for computing Fibonacci numbers",
            tools={
                "fibonacci": ToolSpec.from_func(fibonacci),
                "mod": ToolSpec.from_func(mod),
            },
        )


class CalculatorToolkit(Toolkit):
    def __init__(self, name: str = "calculator"):
        super().__init__(
            name=name,
            description="A toolkit for basic arithmetic operations on integers only.",
            tools={
                "add": ToolSpec.from_func(add),
                "sub": ToolSpec.from_func(sub),
                "mul": ToolSpec.from_func(mul),
                "sqr": ToolSpec.from_func(sqr),
                "exp": ToolSpec.from_func(exp),
                "mod": ToolSpec.from_func(mod),
            },
        )


from ahvn.agent import BasePromptAgentSpec


def build_fibonacci_specialist() -> BasePromptAgentSpec:
    return BasePromptAgentSpec(
        name="fibonacci_specialist",
        short_description="Compute Fibonacci numbers (optionally with modulo).",
        description="A subagent that specializes in computing Fibonacci numbers and applying modulo to them.",
        sig="(question: str) -> int",
        system="You are a fibonacci computation agent.",
        descriptions=[
            "Use the fibonacci tool for direct Fibonacci computations.",
            "If explicitly asked for a modulo operation, use the mod tool to process the result of fibonacci with the given modulus.",
            "Do not perform any arithmetic operations yourself.",
        ],
        instructions=[
            "Use tools for every numeric result.",
            "When done, return only the integer value in <output></output>.",
        ],
        tools=FibonacciToolkit().to_tool_list(),
        llm_args={"preset": "chat-local", "temperature": 0},
    )


def build_arithmetic_specialist() -> BasePromptAgentSpec:
    return BasePromptAgentSpec(
        name="arithmetic_specialist",
        short_description="Compute integer arithmetic expressions.",
        description="A subagent that specializes in basic arithmetic operations on integers.",
        sig="(question: str) -> int",
        system="You are a calculator agent.",
        descriptions=[
            "Use the calculator tool for all arithmetic computations.",
            "Do not perform any arithmetic operations yourself.",
        ],
        instructions=[
            "Prefer exp(a, b, mod=m) for large exponent modulo computations.",
            "For (x - 1) mod m, use sub then mod.",
            "When done, return only the integer value in <output></output>.",
        ],
        tools=CalculatorToolkit().to_tool_list(),
        llm_args={"preset": "chat-local", "temperature": 0},
    )


def build_coordinator() -> BasePromptAgentSpec:
    return build_demo()[0]


def build_demo() -> tuple[BasePromptAgentSpec, BasePromptAgentSpec, BasePromptAgentSpec]:
    fibonacci_agent = build_fibonacci_specialist()
    arithmetic_agent = build_arithmetic_specialist()

    fibonacci_specialist = fibonacci_agent.to_tool()
    arithmetic_specialist = arithmetic_agent.to_tool()
    coordinator = BasePromptAgentSpec(
        name="math_solver",
        system="You are a math agent that coordinates subagents to solve math problems.",
        descriptions=[
            "Currently you have two subagents: a fibonacci specialist and an arithmetic specialist.",
            "The fibonacci specialist can compute Fibonacci numbers and allow apply modulo to them.",
            "The arithmetic specialist can perform basic arithmetic operations on integers including add, sub, mul, sqr, exp, and mod.",
        ],
        instructions=[
            "Always delegate computations to the appropriate subagent. Do not perform any calculations yourself.",
            "For length-n binary strings with no consecutive 1s, compute fibonacci(n+2).",
            "For this problem, first ask fibonacci_specialist for count = fibonacci(12).",
            "Then ask arithmetic_specialist to compute mod(sub(exp(2, count, 10000), 1), 10000).",
            "Return only the final integer in <output></output>.",
        ],
        tools=[
            fibonacci_specialist,
            arithmetic_specialist,
        ],
        llm_args={"preset": "chat-local", "temperature": 0},
    )
    return coordinator, fibonacci_agent, arithmetic_agent


def run_demo() -> None:
    coordinator, _, _ = build_demo()
    question = (
        "We have a series of disks or radius small to large, labelled with length 10 binary strings (a total 2^10 of them)."
        " If we discard all disks whose label has two consecutive 1s, and use the remaining disks to build a hanoi tower."
        " How many steps are required to finish the hanoi tower? Output the final answer modulo 10000."
    )
    expected = "415"  # 2 ^ fib(10 + 2) - 1 mod 10000

    # Direct call
    # answer = coordinator(question=question)

    # Streaming
    from ahvn.utils.basic.color_utils import color_grey
    from ahvn.utils.basic.serialize_utils import dumps_json
    from ahvn.utils.llm import format_tool_call

    for chunk in coordinator.stream(question=question):
        if "chunk" in chunk:
            if "think" in chunk["chunk"]:
                print(color_grey(chunk["chunk"]["think"]), end="", flush=True)
            if "text" in chunk["chunk"]:
                print(chunk["chunk"]["text"], end="", flush=True)
            if "tool_calls" in chunk["chunk"]:
                for tool_call in chunk["chunk"]["tool_calls"]:
                    print(f"\n<tool>{format_tool_call(tool_call)}</tool>")
                print()
        if "delta_messages" in chunk:
            for msg in chunk["delta_messages"]:
                if msg["role"] == "tool":
                    print(f"\n<tool_result>{msg['content']}</tool_result>")
        if "output" in chunk:
            answer = chunk["output"]
            print(f"<output>{answer}</output>")
            print()
        if "usage" in chunk:
            print("Usage info:")
            print(dumps_json(chunk["usage"]))
            print()

    print("Question:", question)
    print("Answer:", str(answer))
    print("Expected:", str(expected))
    if answer is None:
        assert False, f"Expected {expected}, got None"
    else:
        assert int(answer) == int(expected), f"Expected {expected}, got {answer}"


if __name__ == "__main__":
    run_demo()
