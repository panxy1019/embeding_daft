from ahvn.utils.llm import LLM
from ahvn.utils.basic.config_utils import CM_AHVN

# ===== Basic Scoping =====
with CM_AHVN.scoped("demo"):
    CM_AHVN.set("llm.presets.embedder.model", "text-embedding-nomic-embed-text-v1.5")

print(CM_AHVN.get("llm.presets.embedder"))
# {'provider': 'lmstudio', 'model': 'embeddinggemma'}
llm = LLM(preset="embedder")
print(llm.config)  # Correct gemma model

with CM_AHVN.scoped("demo"):
    print(CM_AHVN.get("llm.presets.embedder"))
    # {'provider': 'lmstudio', 'model': 'text-embedding-nomic-embed-text-v1.5'}
    llm2 = LLM(preset="embedder")
    print(llm2.config)  # Correct nomic model


# ===== Class Scoping =====
@CM_AHVN.scoped("demo")
class MyLLM(LLM):
    def hello(self):
        print(">", CM_AHVN.get("llm.presets.embedder"))  # Correct nomic model
        print("> Current scope:", CM_AHVN.scope)  # Correct: ahvn.demo
        print("> Current config:", self.config)  # Incorrect: still gemma (self.config is created in __init__)   # Fixed

        print("> Hello!")
        return self.embed("Hello!")  # Incorrectly using the gemma model for embedding               # Fixed


my_llm = MyLLM(preset="embedder")
print(len(my_llm.hello()))

# ===== Nested Scoping =====
# Usually it is not recommended to have duplicate scope names
with CM_AHVN.scoped("a"):
    with CM_AHVN.scoped("a"):
        with CM_AHVN.scoped("b"):
            with CM_AHVN.scoped("c"):
                with CM_AHVN.scoped("a"):
                    with CM_AHVN.scoped("c"):
                        print("Current scope:", CM_AHVN.scope)  # Should be "ahvn.c.a.b"
                    print("Current scope:", CM_AHVN.scope)  # Should be "ahvn.a.c.b"


# ===== Function & Nested Scoping =====
@CM_AHVN.scoped("demo2")
def hello_embedding():
    with CM_AHVN.scoped("demo3"):
        llm = MyLLM(preset="embedder")
        print(len(llm.hello()))


hello_embedding()  # ahvn.demo.demo3.demo2

# ===== Dynamic Scoping =====
COUNTER = 1


def dynamic_scope():
    return f"foo{COUNTER}"


with CM_AHVN.scoped(dynamic_scope):
    print("Current scope:", CM_AHVN.scope)  # Should be "ahvn.foo1"

COUNTER += 1

with CM_AHVN.scoped(dynamic_scope):
    print("Current scope:", CM_AHVN.scope)  # Should be "ahvn.foo2"
