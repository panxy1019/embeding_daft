"""
Translation Demo — showcases TranslationDict with the current API.

Demonstrates:
  1. DB-backed persistent translation (TranslationStore + TranslationDict)
  2. Multi-language support (zh, ja)
  3. {placeholder}-pattern matching (template substitution)
  4. Dynamic edit: set / delete / overwrite
  5. Persistence across instances (reload from DB)
  6. Query helpers: languages(), keys(), search_keys(), missing_keys()
  7. Elicit modes: "none", "human" (interactive), "llm" (placeholder)
  8. Serialization round-trip (to_dict / from_dict with store)
  9. Integration with fast_prompt_section via td.tr()
"""

import tempfile
import os
from ahvn.utils.prompt.translate import TranslationDict, TranslationStore

SEPARATOR = "=" * 60

_tmp_dir = tempfile.mkdtemp()
_db_path = os.path.join(_tmp_dir, "demo_translations.db")
store = TranslationStore(provider="sqlite", database=_db_path)
print(f"Using temporary DB: {_db_path}\n")

# ------------------------------------------------------------------ #
#  1. Create & populate
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("1. Creating a DB-backed TranslationDict (namespace='fibonacci')")
print(SEPARATOR)

td = TranslationDict(namespace="fibonacci", main_lang="en", store=store)

td.set_many(
    {
        "zh": {
            "Task Descriptions": "任务描述",
            "Task Descriptions 2": "任务描述 2",
            "Examples": "示例",
            "Instructions": "指令",
            "New Instance": "新实例",
            "You are a helpful assistant for calculating Fibonacci numbers.": "你是一个帮助计算斐波那契数的助手。",
            "The Fibonacci sequence is defined as follows: F(0) = 0, F(1) = 1, and F(n) = F(n-1) + F(n-2) for n > 1.": "斐波那契数列定义如下：F(0) = 0，F(1) = 1，且对于 n > 1，F(n) = F(n-1) + F(n-2)。",
            "The task is to calculate the Fibonacci number for a given input n.": "任务是计算给定输入 n 的斐波那契数。",
            "Please calculate the Fibonacci number for the following input.": "请计算以下输入的斐波那契数。",
            "Calculate F({n}).": "计算 F({n})。",
            "The result of F({n}) is {result}.": "F({n}) 的结果是 {result}。",
        },
        "ja": {
            "Task Descriptions": "タスクの説明",
            "Examples": "例",
            "Instructions": "手順",
            "You are a helpful assistant for calculating Fibonacci numbers.": "あなたはフィボナッチ数を計算するための便利なアシスタントです。",
            "Calculate F({n}).": "F({n})を計算してください。",
        },
    }
)

print(td)
print(f"  DB entry count: {store.entry_count('fibonacci')}")
print(f"  Languages: {store.list_languages('fibonacci')}")
print()

# ------------------------------------------------------------------ #
#  2. Exact-match translations
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("2. Exact-match translations")
print(SEPARATOR)

tr_zh = td.tr("zh")
tr_ja = td.tr("ja")
tr_en = td.tr("en")

for text in [
    "Task Descriptions",
    "Instructions",
    "You are a helpful assistant for calculating Fibonacci numbers.",
]:
    print(f"  EN: {text}")
    print(f"  ZH: {tr_zh(text)}")
    print(f"  JA: {tr_ja(text)}")
    print(f"  EN→EN: {tr_en(text)}")
    print()

# ------------------------------------------------------------------ #
#  3. {placeholder}-pattern matching
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("3. {placeholder}-pattern matching")
print(SEPARATOR)

for n in [5, 10, 100]:
    source = f"Calculate F({n})."
    print(f"  EN: {source}")
    print(f"  ZH: {tr_zh(source)}")
    print(f"  JA: {tr_ja(source)}")
    print()

source = "The result of F(10) is 55."
print(f"  EN: {source}")
print(f"  ZH: {tr_zh(source)}")
print()

# tr before vs after .format()
template = "Calculate F({n})."
print(f"  Template: {template}")
translated_template = tr_zh(template)
print(f"  tr(template) → {translated_template}")
print(f"  .format(n=42) → {translated_template.format(n=42)}")
formatted = template.format(n=42)
print(f"  Formatted: {formatted}")
print(f"  tr(formatted) → {tr_zh(formatted)}")
print()

# ------------------------------------------------------------------ #
#  4. Dynamic edit: set / delete / overwrite
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("4. Dynamic edit (set / delete / overwrite)")
print(SEPARATOR)

td.set("Goodbye", "zh", "再见")
print(f"  set 'Goodbye' → '{td.lookup('Goodbye', 'zh')}'")

td.set("Goodbye", "zh", "拜拜")
print(f"  overwrite 'Goodbye' → '{td.lookup('Goodbye', 'zh')}'")

td.delete("Goodbye", "zh")
print(f"  delete 'Goodbye' → lookup returns: {td.lookup('Goodbye', 'zh')}")
print()

# ------------------------------------------------------------------ #
#  5. Persistence: new instance reads from same DB
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("5. Persistence across instances")
print(SEPARATOR)

td2 = TranslationDict(namespace="fibonacci", main_lang="en", store=store)
print(f"  New instance lookup 'Task Descriptions': {td2.lookup('Task Descriptions', 'zh')}")
print(f"  New instance pattern 'Calculate F(7).': {td2.lookup('Calculate F(7).', 'zh')}")
print(f"  Entries match: {td2.to_dict()['translations'] == td.to_dict()['translations']}")
print()

# ------------------------------------------------------------------ #
#  6. Query helpers
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("6. Query helpers")
print(SEPARATOR)

print(f"  Languages: {td.languages()}")
print(f"  All keys count: {len(td.keys())}")
print(f"  Prefix search 'Task': {td.search_keys('Task')}")
missing = td.missing_keys("ja", ref_lang="zh")
print(f"  Keys in zh but missing in ja ({len(missing)}):")
for k in missing[:5]:
    print(f"    - {k[:60]}{'...' if len(k) > 60 else ''}")
print()

# ------------------------------------------------------------------ #
#  7. Elicit modes
# ------------------------------------------------------------------ #
print(SEPARATOR)
print('7. Elicit modes: "none", "human", "llm"')
print(SEPARATOR)

print(f'  elicit="none": {tr_zh("unknown text")}')
print('  elicit="human": (uncomment to try interactively)')
# tr_human = td.tr("zh", elicit="human")
# result = tr_human("A brand new sentence.")

tr_llm = td.tr("zh", elicit="llm")
try:
    tr_llm("Some text.")
except NotImplementedError as e:
    print(f'  elicit="llm": NotImplementedError — {e}')
print()

# ------------------------------------------------------------------ #
#  8. Serialization: to_dict / from_dict
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("8. Serialization round-trip (to_dict → from_dict)")
print(SEPARATOR)

data = td.to_dict()
print(f"  namespace: {data['namespace']}")
print(f"  languages: {list(data['translations'].keys())}")

data["namespace"] = "fibonacci_copy"
td_copy = TranslationDict.from_dict(data, store=store, replace=True)
assert td_copy.lookup("Task Descriptions", "zh") == "任务描述"
assert td_copy.lookup("Calculate F(99).", "zh") == "计算 F(99)。"
print(f"  Copied to '{td_copy.namespace}' — DB entry count: {store.entry_count('fibonacci_copy')}")
print(f"  Namespaces in DB: {[p['id'] for p in store.list_namespaces()]}")
print()

# ------------------------------------------------------------------ #
#  9. Integration with fast_prompt_section
# ------------------------------------------------------------------ #
print(SEPARATOR)
print("9. Integration with fast_prompt_section")
print(SEPARATOR)

from ahvn.utils.basic.rnd_utils import StableRNG
from ahvn.utils.prompt import fast_prompt_section
from ahvn.cache import InMemCache

cache = InMemCache()


@cache.memoize()
def fibonacci(n):
    return n if n <= 1 else fibonacci(n - 1) + fibonacci(n - 2)


fibonacci(10)
exps = [entry for entry in cache]
sampled = [exps[i] for i in StableRNG(42).hash_sample(list(range(len(exps))), 3)]

prompt = fast_prompt_section(
    system="You are a helpful assistant for calculating Fibonacci numbers.",
    descriptions={
        "Task Descriptions": "The Fibonacci sequence is defined as follows: F(0) = 0, F(1) = 1, and F(n) = F(n-1) + F(n-2) for n > 1.",
        "Task Descriptions 2": "The task is to calculate the Fibonacci number for a given input n.",
    },
    examples=sampled,
    instructions="Please calculate the Fibonacci number for the following input.",
    instance={"inputs": {"n": 15}},
    tr=td.tr("zh"),
)
print(prompt[0].get("content"))
print()
print(SEPARATOR)
print("Demo complete.")
