# AgentHeaven Embedding 过程专项解析

本文专门解析 AgentHeaven 中 embedding 的完整流程：从配置解析、encoder/embedder 标准化、LLM 批量向量化，到向量库写入和查询检索。重点关注本地源码里的真实调用链，而不是只描述概念。

## 1. 总体结论

AgentHeaven 的 embedding 主线可以概括为：

```text
UKF 知识对象
  -> k_encoder 生成入库文本 key
  -> k_embedder 生成 embedding
  -> VdbUKFAdapter 生成 LlamaIndex TextNode
  -> VectorStore.add 写入向量库

用户 query
  -> q_encoder 生成查询文本 qkey
  -> q_embedder 生成 query embedding
  -> VectorStoreQuery + MetadataFilters
  -> VectorStore.query
  -> 返回 id / score / kl
```

真正做 embedding 的底层通常是 `LLM.embed()`，它通过 LiteLLM 调用具体 provider。向量库写入和检索通过 LlamaIndex VectorStore 适配 LanceDB、Chroma、Milvus、PGVector 等后端。

## 2. 配置层

### 2.1 LLM preset

配置文件：

- `src/ahvn/resources/configs/default_config.yaml`

默认 embedding preset：

```yaml
llm:
  presets:
    embedder:
      desc: "Embedding preset for general-purpose embeddings. Balanced between quality and speed."
      provider: ollama
      model: embeddinggemma
    embedder-pro:
      desc: "Embedding preset for high-quality embeddings."
      provider: ollama
      model: qwen3-embedding:8b
    embedder-tiny:
      desc: "Tiny embedding preset for lightweight embedding tasks and testing. Fast but less accurate."
      provider: ollama
      model: all-minilm:33m
```

相关模型配置里带有 `_dim` 和默认批处理参数：

| 模型别名 | provider 标识 | 维度 | 默认批处理 |
| --- | --- | --- | --- |
| `all-minilm:33m` | `ollama: all-minilm:33m` | 384 | `batch_size: 256`, `num_threads: -1` |
| `embeddinggemma` | `ollama: embeddinggemma` | 768 | `batch_size: 256`, `num_threads: -1` |
| `qwen3-embedding:8b` | `ollama: qwen3-embedding:8b` | 4096 | `batch_size: 256`, `num_threads: -1` |

`LLM.__init__()` 会通过 `LLM_CONFIG_ENGINE.resolve()` 把 `preset/model/provider` 解析为 `LLMSpec`，再 materialize 成 LiteLLM 可用参数。

### 2.2 VDB 配置

同一个配置文件中有向量库配置：

```yaml
vdb:
  default_provider: lancedb
  default_embedder: embedder
  providers:
    simple:
      backend: simple
    lancedb:
      backend: lancedb
      uri: "./.ahvn/lancedb/"
      collection: "default"
      refine_factor: 10
    chromalite:
      backend: chroma
      mode: "ephemeral"
      collection: "default"
    chroma:
      backend: chroma
      mode: "persistent"
      path: "./.ahvn/chromadb/"
      collection: "default"
    milvuslite:
      backend: milvus
      uri: "./.ahvn/milvus.db"
      collection: "default"
    milvus:
      backend: milvus
      uri: "http://localhost:19530"
      db_name: "default"
      collection: "default"
    pgvector:
      backend: pgvector
      dialect: postgresql
      host: "localhost"
      port: 5432
      username: "${whoami}"
      collection: "default"
```

`VectorDatabase.__init__()` 会调用 `resolve_vdb_config()` 合并默认参数、provider 参数和用户传入 kwargs。

## 3. 三个关键对象

### 3.1 VectorDatabase

核心文件：

- `src/ahvn/utils/vdb/base.py`

`VectorDatabase` 是 embedding 过程的核心中转站。它持有：

- `k_encoder`: 知识对象入库时的编码函数。
- `q_encoder`: 查询文本检索时的编码函数。
- `k_embedder`: 入库文本的 embedding 函数。
- `q_embedder`: 查询文本的 embedding 函数。
- `k_dim/q_dim`: embedding 维度。
- `vdb`: 具体 LlamaIndex VectorStore 实例。

它暴露的方法分两组：

| 方法 | 作用 |
| --- | --- |
| `k_encode(kl)` | UKF -> 入库文本 |
| `k_embed(encoded_kl)` | 入库文本 -> embedding |
| `k_encode_embed(kl)` | UKF -> 入库文本 + embedding |
| `batch_k_encode_embed(kls)` | 批量 UKF -> 批量文本 + embedding |
| `q_encode(query)` | query -> 查询文本 |
| `q_embed(encoded_query)` | 查询文本 -> query embedding |
| `q_encode_embed(query)` | query -> 查询文本 + embedding |
| `batch_q_encode_embed(queries)` | 批量 query -> 批量文本 + embedding |

### 3.2 VdbUKFAdapter

核心文件：

- `src/ahvn/adapter/vdb.py`

`VdbUKFAdapter` 把 UKF 和 embedding 包成 LlamaIndex `TextNode`：

```python
data[self.key_field] = kl.name if key is None else key
vector = VDB_FIELD_TYPES["vector"].from_ukf(embedding, backend=self.backend)
return TextNode(text=data[self.key_field], embedding=vector, metadata=data, id_=data["id"])
```

重要字段：

| 字段 | 含义 |
| --- | --- |
| `id` | UKF id 的字符串化 hash，用作 node id |
| `_key` | encoder 输出的入库文本，也是 `TextNode.text` |
| `_vec` | embedding 字段名，adapter 上为 `embedding_field` |
| `metadata` | UKF 字段转换后的 metadata |

因此，如果要外部预计算 embedding，最稳的写入方式不是直接猜向量库 schema，而是继续调用：

```python
engine.adapter.from_ukf(kl=kl, key=key, embedding=embedding)
```

### 3.3 VectorKLEngine / VectorKLStore

核心文件：

- `src/ahvn/klengine/vector_engine.py`
- `src/ahvn/klstore/vdb_store.py`

两者都使用 `VectorDatabase` 和 `VdbUKFAdapter`：

- `VectorKLStore` 是“把 UKF 存在向量库里”的 store。
- `VectorKLEngine` 是“给已有 storage 建向量索引并搜索”的 engine。

它们都有 `_batch_convert()`，关键逻辑都是：

```text
BaseUKF 列表
  -> vdb.batch_k_encode_embed(kls)
  -> adapter.from_ukf(kl, key, embedding)
  -> TextNode 列表
```

差异是：

- `VectorKLStore` 直接把自己当作 storage。
- `VectorKLEngine` 可以 `inplace=True` 复用 `VectorKLStore` 的底层 vdb，也可以 `inplace=False` 建独立索引。

## 4. encoder/embedder 标准化

核心文件：

- `src/ahvn/utils/vdb/vdb_utils.py`

函数：

```python
parse_encoder_embedder(encoder=None, embedder=None)
```

它做四件事。

第一，标准化 encoder。

如果 `encoder` 是单个 callable，则同时作为 `k_encoder` 和 `q_encoder`。如果是 tuple，则拆成 `(k_encoder, q_encoder)`。

默认值：

```python
def default_k_encoder(kl):
    return kl.text()

def default_q_encoder(query):
    return str(query).strip()
```

第二，标准化 embedder。

如果 `embedder is None`，默认使用 `"embedder"` preset。如果是字符串，例如 `"embedder"`，会创建：

```python
LLM(preset="embedder")
```

如果是 tuple，则拆成 `(k_embedder, q_embedder)`。

第三，确定维度。

如果 embedder 是 `LLM`，直接读取 `LLM.dim`。如果是普通函数，则调用：

```python
len(k_embedder("<TEST>"))
```

第四，返回统一结构：

```text
((k_encoder, q_encoder), (k_embedder, q_embedder), k_dim, q_dim)
```

这个函数是整个 embedding 链路的入口之一。只要调用方给 `VectorDatabase` 传入 encoder/embedder，都会被它归一化。

## 5. LLM.embed 的内部流程

核心文件：

- `src/ahvn/utils/llm/base.py`

公开入口：

```python
LLM.embed(inputs, include=None, verbose=False, reduce=True, **kwargs)
LLM.aembed(inputs, include=None, verbose=False, reduce=True, **kwargs)
```

### 5.1 输入标准化

`embed()` 允许传单个字符串或字符串列表：

```python
if isinstance(inputs, str):
    inputs = [inputs]
    single = True
else:
    single = False
```

返回时，如果是单个字符串且只请求 embeddings，就返回一条向量；如果传入 list，则返回向量列表。

### 5.2 配置合并和代理

`embed()` 会把实例级配置与调用级 kwargs 合并：

```python
cfg = deepcopy(self.args) | deepcopy(kwargs)
```

然后用 `NetworkProxy` 临时设置：

- `http_proxy`
- `https_proxy`
- `no_proxy`

### 5.3 _embed_dispatch: 去空、去重、切 batch

`LLM._embed_dispatch(batch, **kwargs)` 是批量 embedding 的预处理核心。它会：

1. 找出空字符串位置。
2. 如果全是空字符串，直接返回特殊状态。
3. 对非空文本做 in-batch 去重。
4. 根据 `batch_size` 切分 sub-batches。
5. 从 kwargs 中弹出 `batch_size` 和 `num_threads`，避免传给 provider。

返回值包含：

```text
empty_set
dedup_map
unique_batch
sub_batches
num_threads
batch_len
kwargs
```

这说明 AgentHeaven 原生已经不是逐条 embedding，而是有批量、去重和并发能力。

### 5.4 _cached_embed: 缓存和 LiteLLM 调用

`_cached_embed()` 内部定义 `vanilla_embed()`，再用：

```python
@self.cache.batch_memoize(name=self.name)
```

做批量缓存。

真实 provider 调用在这里：

```python
litellm.embedding(input=sb, **kwargs)
```

当有多个 sub-batch 时，会用 `Parallelized` 并发执行：

```python
with Parallelized(func=_embed_one, args=args_list, num_threads=num_threads) as ptasks:
    ...
```

最后再按原始输入顺序重建结果：

```python
return [
    self.embed_empty if i in empty_set else unique_embeddings[dedup_map[i]]
    for i in range(batch_len)
]
```

### 5.5 空字符串 embedding

`LLM.embed_empty` 返回固定向量：

```python
[1.0] + [0.0] * (self.dim - 1)
```

这能避免空文本直接调用 provider，同时保持维度一致。

### 5.6 usage 统计

如果调用：

```python
llm.embed(inputs, include=["embeddings", "usage"], reduce=False)
```

会走 `_instrumented_embed()`，返回 usage metadata：

| 字段 | 含义 |
| --- | --- |
| `created_at` | 调用时间 |
| `elapsed` | embedding 总耗时 |
| `total_count` | 原始输入数量 |
| `empty_count` | 空文本数量 |
| `unique_count` | 去重后的非空文本数量 |
| `dim` | 向量维度 |
| `cached` | 缓存命中数量 |
| `batch_size` | 如果传入则记录 |
| `num_threads` | 如果传入则记录 |

这对性能分析非常有用。

## 6. 入库 embedding 流程

### 6.1 VectorKLStore 写入流程

`VectorKLStore._batch_convert()`：

```python
keys_embeddings = self.vdb.batch_k_encode_embed(kls)
for kl, (key, embedding) in zip(kls, keys_embeddings):
    nodes.append(self.adapter.from_ukf(kl=kl, key=key, embedding=embedding))
```

`VectorKLStore._batch_upsert()`：

```text
1. 按 kl.id 去重
2. 把 kl.id 转成 vector store node id
3. delete_nodes(ukf_ids)
4. add(_batch_convert(kls))
5. 更新 progress
```

这说明 upsert 是“先删再加”，不是原地更新。

### 6.2 VectorKLEngine 写入流程

`VectorKLEngine._batch_convert()` 会额外处理 `DummyUKFT`。初始化 vector collection 时，代码会插入 dummy node 再删除，以确保 schema 初始化。

非 dummy 的核心逻辑：

```python
non_dummy_kls = [kl for kl in kls_list if not isinstance(kl, DummyUKFT)]
non_dummy_key_embeddings = self.vdb.batch_k_encode_embed(non_dummy_kls)
non_dummy_mapping = dict(zip([kl.id for kl in non_dummy_kls], non_dummy_key_embeddings))
```

然后逐条生成 `TextNode`。

这一点有两个实际含义：

1. 自定义 `k_encoder` 不会被 dummy 节点破坏。
2. 外部预计算 embedding 时也要处理 dummy/init schema 问题，最好复用现有 `_init()` 或 adapter。

### 6.3 TextNode 的结构

最终写入向量库的是 LlamaIndex `TextNode`：

```text
TextNode
  text: data["_key"]
  embedding: embedding vector
  metadata:
    id
    expiration_timestamp
    name
    content
    tags
    synonyms
    ...
    _key
  id_: data["id"]
```

实际 metadata 取决于 adapter 的 `include/exclude`。

## 7. 查询 embedding 流程

核心方法：

- `VectorKLEngine._search_vector()`

流程：

```text
1. 校验过滤字段是否在 adapter.fields 中。
2. 校验 include 返回字段是否合法。
3. 用 VectorCompiler.compile() 构造 metadata_filters。
4. 如果 query 不为空，调用 self.vdb.q_encode_embed(query)。
5. 构造 VectorStoreQuery(query_embedding, similarity_top_k, filters)。
6. 调用 self.vdb.vdb.query(query_stmt)。
7. 将 nodes/similarities 转成结果列表。
8. 如果 include 包含 kl 且 adapter recoverable，则 adapter.to_ukf(entity=node)。
```

默认返回字段：

```python
{"id", "kl", "score"}
```

可选 include：

- `id`
- `kl`
- `score`
- `filter`
- `vsq`
- `key`
- `embedding`
- `qkey`
- `qembedding`

注意：代码里 `key` 和 `qkey` 当前都返回 query key，`embedding` 和 `qembedding` 当前都返回 query embedding，更像调试字段，而不是返回命中节点自身 embedding。

## 8. metadata filter 流程

核心文件：

- `src/ahvn/utils/vdb/compiler.py`

`VectorCompiler` 把 KLOp JSON IR 编译成 LlamaIndex metadata filters。

支持逻辑：

- `AND`
- `OR`
- `NOT`
- `FIELD:<field>`
- `==`
- `!=`
- `<`
- `<=`
- `>`
- `>=`
- `LIKE`
- `ILIKE`
- `IN`

例如：

```python
VectorCompiler.compile(priority=5)
```

会转成对应字段的 LlamaIndex filter。

这说明 AgentHeaven 的向量检索不是纯相似度检索，也能叠加结构化字段过滤。

## 9. 向量库后端连接流程

`VectorDatabase.connect()` 根据 backend 创建具体后端。

| backend | 实现 |
| --- | --- |
| `simple` | `llama_index.core.vector_stores.SimpleVectorStore` |
| `lancedb` | `llama_index.vector_stores.lancedb.LanceDBVectorStore` |
| `chroma` | `chromadb` client + `ChromaVectorStore` |
| `milvus` | `pymilvus` + `MilvusVectorStore` |
| `pgvector` | SQLAlchemy engine + `PGVectorStore` |

不同后端 collection 参数名不同，源码用映射处理：

```python
VDB_BACKEND_COLLECTION_MAPPING = {
    "simple": None,
    "lancedb": "table_name",
    "chroma": None,
    "milvus": "collection_name",
    "pgvector": "database",
}
```

因此不要假设每个 backend 的 collection 参数都叫 `collection`。

## 10. 批量性能已经具备的能力

AgentHeaven 当前 embedding 流程已经支持：

- 批量输入。
- in-batch 去重。
- 空字符串固定向量。
- `batch_size` 切分。
- `num_threads` 并发 sub-batch。
- tenacity 重试。
- `batch_memoize` 缓存。
- usage 统计。
- sync/async 两套接口。

所以后续如果接入 Daft，Daft 的价值不是“第一次让系统支持 batch embedding”，而是进一步提供：

- 更大的数据流水线调度。
- 更清晰的 candidate/staging 表。
- Ray 分布式执行。
- 断点续跑。
- 跨任务 cache/metrics。
- embedding 和写入阶段解耦。

## 11. 测试如何模拟 embedding

核心文件：

- `tests/fixtures/mock_embedder.py`
- `tests/unit/vdb/test_vdb.py`
- `tests/unit/klengine/test_vector_klengine.py`

`mock_embedder()` 可以输入单个字符串或字符串列表，返回稳定的随机向量。测试中常用：

```python
store_params = {
    "encoder": (encoder_fn, query_encoder_fn),
    "embedder": embedder_fn,
    "provider": provider,
}
```

这说明项目已经支持：

- 自定义 k/q encoder。
- 自定义 embedder 函数。
- 不依赖真实 LLM provider 的向量库测试。
- 多 provider 测试，包括 lancedb/chroma/milvus/simple/pgvector 等。

后续写 Daft 或预计算 embedding 测试时，也应复用 mock embedder，避免测试环境依赖 Ollama/OpenAI。

## 12. 常见坑

### 12.1 embedding 维度必须与后端一致

Milvus/PGVector 等后端需要提前知道维度。`VectorDatabase` 会用 `k_dim` 初始化部分后端。如果换模型或换 embedder，维度可能变化，旧 collection 不能直接复用。

### 12.2 不要混用不同 embedding 空间

同一个 vector index 中不要混用：

- 不同模型。
- 同模型但不同 provider 实现。
- 不同 encoder 文本。
- 不同归一化策略。

否则相似度没有可靠语义。

### 12.3 `fetchk` 当前没有真正下推

`VectorKLEngine._search_vector()` 接收 `fetchk`，但代码里构造 `VectorStoreQuery` 时使用的是 `similarity_top_k=topk`。注释里也有 TODO。也就是说当前 `fetchk` 更像预留参数。

### 12.4 `simple` 后端不适合完整持久化场景

源码注释指出 LlamaIndex `SimpleVectorStore` 默认不持久化完整 `TextNode`，会影响 get all nodes 或 delete by node id 等行为。生产场景更应使用 LanceDB/Chroma/Milvus/PGVector。

### 12.5 include/exclude 会影响能否恢复 KL

如果 adapter 没有包含完整 UKF 字段，`adapter.to_ukf()` 会失败。此时 search 想返回 `kl`，需要 attached storage 中能按 id 找回原始 KL。

### 12.6 直接写 LanceDB schema 风险高

AgentHeaven 当前通过 LlamaIndex `TextNode` 和 `LanceDBVectorStore` 管理写入结构。如果外部直接写 LanceDB 表，必须完全兼容：

- node id。
- text 字段。
- embedding 字段。
- metadata 字段。
- LlamaIndex 对字段名和类型的期望。

第一版改造更建议复用 `VdbUKFAdapter.from_ukf()` 和 `VectorStore.add()`。

## 13. 外部预计算 embedding 的安全写入方式

如果后续要用 Daft 或其他系统预计算 embedding，建议保持这条边界：

```text
外部系统负责:
  kl_id
  key text
  embedding vector

AgentHeaven 负责:
  根据 kl_id 回查 BaseUKF
  adapter.from_ukf()
  delete_nodes + add(nodes)
  flush
```

伪代码：

```python
def write_precomputed(engine, rows):
    nodes = []
    node_ids = []

    for row in rows:
        kl = engine.storage.get(row["kl_id"], default=None)
        if kl is None:
            continue

        node_id = engine.adapter.parse_id(kl.id)
        node = engine.adapter.from_ukf(
            kl=kl,
            key=row["key"],
            embedding=row["embedding"],
        )
        node_ids.append(node_id)
        nodes.append(node)

    if node_ids:
        engine.vdb.vdb.delete_nodes(node_ids)
    if nodes:
        engine.vdb.vdb.add(nodes)
    engine.vdb.flush()
```

这基本复用 `VectorKLStore._batch_upsert()` 的写入语义，只是 embedding 来源变成外部系统。

## 14. 推荐观测指标

无论继续用 AgentHeaven 原生 embedding，还是改造成 Daft pipeline，都建议记录：

| 指标 | 说明 |
| --- | --- |
| `total_count` | 原始待 embedding 文本数量 |
| `empty_count` | 空文本数量 |
| `unique_count` | 去重后文本数量 |
| `cached` | 缓存命中数量 |
| `dim` | 向量维度 |
| `batch_size` | 子批大小 |
| `num_threads` | 并发线程数 |
| `embedding_elapsed` | embedding 阶段耗时 |
| `write_elapsed` | vector store 写入耗时 |
| `query_elapsed` | 查询耗时 |
| `provider`/`model` | embedding 空间标识 |
| `encoder_hash` | encoder 版本或文本生成规则标识 |

其中 `provider/model/encoder_hash` 对 cache key 和索引版本非常关键。

## 15. 面向 Daft 改造的建议边界

最稳的改造边界是：

```text
保持:
  BaseUKF
  VectorKLEngine.search()
  VdbUKFAdapter.from_ukf()
  VectorDatabase 后端连接

替换:
  batch_k_encode_embed 的批量调度方式
```

第一阶段不要直接改查询侧，也不要直接改 SQL Agent。先把 build-time embedding 独立成离线任务：

```text
storage 中已有 KL
  -> 提取 candidate
  -> 生成 key
  -> Daft 批量 embedding
  -> 预计算结果写回 vector store
  -> 原 search API 不变
```

这样最容易验证“新旧索引检索结果是否一致”。

## 16. 推荐阅读顺序

1. `src/ahvn/utils/vdb/vdb_utils.py`
2. `src/ahvn/utils/vdb/base.py`
3. `src/ahvn/utils/llm/base.py`
4. `src/ahvn/adapter/vdb.py`
5. `src/ahvn/klstore/vdb_store.py`
6. `src/ahvn/klengine/vector_engine.py`
7. `src/ahvn/utils/vdb/compiler.py`
8. `tests/fixtures/mock_embedder.py`
9. `tests/unit/vdb/test_vdb.py`
10. `tests/unit/klengine/test_vector_klengine.py`

## 17. 源码参考

- [utils/vdb/vdb_utils.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/vdb/vdb_utils.py)
- [utils/vdb/base.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/vdb/base.py)
- [utils/vdb/compiler.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/vdb/compiler.py)
- [utils/vdb/types.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/vdb/types.py)
- [utils/llm/base.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/llm/base.py)
- [utils/llm/spec.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/utils/llm/spec.py)
- [adapter/vdb.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/adapter/vdb.py)
- [klstore/vdb_store.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/klstore/vdb_store.py)
- [klengine/vector_engine.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/klengine/vector_engine.py)
- [resources/configs/default_config.yaml](../../AgentHeaven-dev-master/AgentHeaven-dev-master/src/ahvn/resources/configs/default_config.yaml)
- [tests/fixtures/mock_embedder.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/tests/fixtures/mock_embedder.py)
- [tests/unit/vdb/test_vdb.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/tests/unit/vdb/test_vdb.py)
- [tests/unit/klengine/test_vector_klengine.py](../../AgentHeaven-dev-master/AgentHeaven-dev-master/tests/unit/klengine/test_vector_klengine.py)
