# RubikSQL Phase1 部署方案设计报告

> 日期：2026-06-26
> 范围：Ray Job Submission 模式下的 Phase1 部署设计、embedding 执行路径追踪、源码部署方案、Kubernetes/RayCluster 代理方案
> 状态：设计报告，不包含代码修改、不包含依赖安装、不包含 Ray 集群连接、不启动 Pipeline、不生成 UKFT

## 1. 执行模式修正

后续 Phase1 不应按 serve00 本地 Driver 模式设计。正确边界如下：

```text
serve00
  |
  | ray job submit / JobSubmissionClient
  v
Ray Head: Job Supervisor 启动 Python Driver
  |
  | Ray task / actor / Ray Data / Daft Ray runner
  v
Ray Head + Ray Workers 执行实际计算
```

因此：

- serve00 只是提交入口，不参与 Daft profiling、UKFT 构建、embedding 计算。
- Python Driver 运行在 Ray Head 内部。
- Worker 函数中的 `VectorKLEngine`、`LLM.embed()`、LanceDB 写入等都发生在 Ray Head / Worker Pod 的进程里。
- 后续排查 `localhost`、环境变量、代理、模型服务时，必须以 Head / Worker Pod 的网络命名空间为准，而不是 serve00。
- 如果 Phase1 入口代码在 Job 中调用 `ray.init(address="auto")`，它是在 Ray Head 内运行，语义不同于从 serve00 本地进程直连集群。

参考提交方式：

- `vllm/submit.py` 使用 `JobSubmissionClient("http://<ray-head-dashboard>:8265")` 提交 job。
- `runtime_env={"working_dir": "./"}` 会把提交目录打包给 Ray Job。
- `vllm/test_vll5.py` 在集群内运行 Driver，使用 Ray Data `map_batches` 创建 embedding actor，并通过 `resources={"NPU": 1}` 绑定 NPU。

## 2. Embedding 完整执行路径

### 2.1 RubikSQL Phase1 worker 入口

Phase1 示例 worker 位于：

```text
docs/rubiksql-lake-pipeline/src/rubiksql_lake/worker.py
```

当前 worker 的 embedding 初始化逻辑为：

```python
vector_storage = VectorKLStore(
    provider="lancedb",
    uri=str(vec_db_path),
    table_name="vec_enums",
    encoder=None,
    embedder="embedder",
)

vec_engine = VectorKLEngine(
    storage=vector_storage,
    inplace=False,
    provider="lancedb",
    uri=str(vec_db_path),
    encoder=[
        "lambda kl: str(kl.enum).strip().lower()",
        "lambda q: str(q).strip().lower()",
    ],
    embedder="embedder",
    condition=lambda kl: getattr(kl, "type", None) == "db-enum",
)

kb.batch_upsert(ukft_objects, batch_size=256)
```

关键点：

- `kb.batch_upsert()` 会同步触发 engine 的 `batch_upsert()`。
- `VectorKLEngine(inplace=False)` 会把 UKFT 转为向量库节点并写入 LanceDB。
- `condition` 限定只有 `db-enum` 类型进入 vec-enums embedding。

### 2.2 AgentHeaven VectorKLEngine 到 VectorDatabase

路径：

```text
AgentHeaven-dev-master/src/ahvn/klengine/vector_engine.py
```

核心调用链：

```text
VectorKLEngine._batch_upsert()
  -> VectorKLStore._batch_upsert()
  -> VectorKLEngine._batch_convert()
  -> self.vdb.batch_k_encode_embed(non_dummy_kls)
```

`VectorKLEngine.__init__()` 在 `inplace=False` 时创建：

```python
self.vdb = VectorDatabase(
    collection=collection,
    provider=provider,
    encoder=encoder,
    embedder=embedder,
    **connection_args,
)
```

这里的 `provider="lancedb"` 是向量库 backend，不是 LLM provider。真正的 LLM provider 来自 `embedder="embedder"`。

### 2.3 VectorDatabase 解析 embedder

路径：

```text
AgentHeaven-dev-master/src/ahvn/utils/vdb/base.py
AgentHeaven-dev-master/src/ahvn/utils/vdb/vdb_utils.py
```

`VectorDatabase.__init__()` 调用：

```python
parse_encoder_embedder(encoder=encoder, embedder=embedder)
```

`parse_encoder_embedder()` 的关键逻辑：

```python
if embedder is None:
    embedder = "embedder"
if isinstance(embedder, str):
    embedder = LLM(preset=embedder)
...
if isinstance(k_embedder, LLM):
    k_dim = k_embedder.dim
    k_embedder = k_embedder.embed
```

因此：

```text
embedder="embedder"
  -> LLM(preset="embedder")
  -> LLM.embed()
```

注意：`LLM.dim` 会用 `"<TEST>"` 触发一次 embedding 以探测维度。因此只要创建 `VectorKLEngine`，就可能在正式 upsert 前先请求一次 embedding 服务。

### 2.4 LLM 配置来源

AgentHeaven LLM 配置解析路径：

```text
AgentHeaven-dev-master/src/ahvn/utils/llm/base.py
AgentHeaven-dev-master/src/ahvn/utils/llm/spec.py
AgentHeaven-dev-master/src/ahvn/utils/basic/config_utils.py
```

`LLM.__init__()`：

```python
self.spec = LLM_CONFIG_ENGINE.resolve(
    {"preset": preset, "model": model, "provider": provider, **kwargs}
)
self.config = LLM_CONFIG_ENGINE.materialize(self.spec, mode="spec")
self.args = LLM_CONFIG_ENGINE.materialize(self.spec, mode="litellm")
```

`LLM_CONFIG_ENGINE.resolve()` 从 `CM_AHVN.get("llm", ...)` 读取：

- `llm.presets`
- `llm.providers`
- `llm.models`
- `llm.default_args`
- model alias / provider identifier

AgentHeaven 默认配置位于：

```text
AgentHeaven-dev-master/src/ahvn/resources/configs/default_config.yaml
```

RubikSQL 会在自己的 setup 中覆盖 AgentHeaven 配置：

```text
RubikSQL-dev/src/rubiksql/utils/config_utils.py
RubikSQL-dev/src/rubiksql/resources/configs/ahvn_config.yaml
```

RubikSQL 的 `ahvn_config.yaml` 当前写明：

```yaml
llm:
  providers:
    _OVERWRITE_: true
    ollama:
      backend: ollama
    lmstudio:
      backend: lm_studio
      api_base: http://localhost:1234/v1
    vllm:
      backend: hosted_vllm
      api_base: <VLLM_API_BASE>
  presets:
    _OVERWRITE_: true
    embedder:
      provider: ollama
      model: embeddinggemma
```

结论：

1. `LLM(preset="embedder")` 的配置来自 AgentHeaven `CM_AHVN` 配置系统。
2. 在 RubikSQL 环境下，`CM_AHVN` 会被 RubikSQL 的 `src/rubiksql/resources/configs/ahvn_config.yaml` 覆盖。
3. 当前 `embedder` preset 对应 `provider: ollama`、`model: embeddinggemma`。

### 2.5 LLM.embed 到 LiteLLM

路径：

```text
AgentHeaven-dev-master/src/ahvn/utils/llm/base.py
AgentHeaven-dev-master/src/ahvn/utils/llm/llm_utils.py
```

`LLM.embed()`：

```text
LLM.embed(inputs)
  -> cfg = deepcopy(self.args) | kwargs
  -> NetworkProxy(http_proxy=..., https_proxy=..., no_proxy=...)
  -> self._cached_embed(...) 或 self._instrumented_embed(...)
```

`_cached_embed()` / `_instrumented_embed()`：

```text
_embed_dispatch()
  - 去空字符串
  - batch 内去重
  - 按 batch_size 切分
  - 从 kwargs 中移除 num_threads，避免传给 provider

litellm = get_litellm()
litellm.embedding(input=sub_batch, **kwargs)
```

异步版本调用：

```python
await litellm.aembedding(input=sub_batch, **kwargs)
```

`get_litellm()` 懒加载 `litellm`，并设置：

- `litellm.drop_params = True`
- `litellm.ssl_verify = False`
- `litellm.disable_end_user_cost_tracking = True`

结论：AgentHeaven 自身不直接发 HTTP 请求；真正的 HTTP 请求由 LiteLLM provider adapter 发起。

### 2.6 LiteLLM 到 Ollama HTTP 请求

当前 materialize 后的模型会按 LiteLLM 格式变为：

```text
model = "ollama/embeddinggemma"
```

LiteLLM 的 Ollama embedding handler 会构造 payload：

```python
data = {"model": model, "input": prompts}
```

并发起 HTTP 请求：

```python
response = litellm.module_level_client.post(url=api_base, json=data)
```

如果 `api_base` 不以 `/api/embed` 结尾，则追加：

```text
/api/embed
```

Ollama 官方 API 的 embedding endpoint 是：

```text
POST /api/embed
```

官方示例使用：

```text
http://localhost:11434/api/embed
```

当前 RubikSQL 覆盖配置中，`ollama` provider 没有显式 `api_base`，因此按 LiteLLM/Ollama 默认行为，应请求：

```text
http://localhost:11434/api/embed
```

如果后续显式设置 `OLLAMA_API_BASE` 或 provider `api_base`，则会改为：

```text
${OLLAMA_API_BASE}/api/embed
```

### 2.7 八个问题的直接回答

1. `LLM(preset="embedder")` 的配置来自哪里？

   来自 AgentHeaven 的 `CM_AHVN` 配置系统，RubikSQL 启动时通过 `RubikSQL-dev/src/rubiksql/resources/configs/ahvn_config.yaml` 覆盖 AgentHeaven 默认配置。

2. `embedder` 对应的 provider 是什么？

   当前 RubikSQL 配置中是：

   ```text
   provider = ollama
   ```

3. 最终请求发送给哪个服务？

   当前配置下发送给 Ollama，而不是 vLLM，也不是 MindIE。

4. HTTP Endpoint 是哪里？

   当前有效设计应为：

   ```text
   POST http://localhost:11434/api/embed
   ```

   其中 `localhost` 是执行 `LLM.embed()` 的 Ray Head / Worker Pod 内部视角。

5. 默认监听哪个端口？

   Ollama 默认端口是：

   ```text
   11434
   ```

6. Worker 是访问本机服务还是远程服务？

   当前默认 endpoint 是 `localhost`，所以 Worker 会访问本机 / 本 Pod 网络命名空间内的 Ollama 服务。如果 Worker Pod 内没有 Ollama，或者没有同 Pod sidecar 暴露 `11434`，请求会失败。它不会自动访问 serve00，也不会自动访问某个中心 embedding 服务。

7. embedding 模型名称是什么？

   当前 RubikSQL 配置为：

   ```text
   embeddinggemma
   ```

   经 LiteLLM materialize 后是：

   ```text
   ollama/embeddinggemma
   ```

8. 是否支持使用 Ascend 910B？

   当前 AgentHeaven/RubikSQL 默认 embedding 路径本身只是 HTTP client，不直接管理 NPU。是否使用 Ascend 910B 取决于 `localhost:11434` 后面的 Ollama 服务是否能使用 Ascend。按当前代码和配置，未看到 Ollama + Ascend 910B 的显式适配。

   当前仓库中已有 `vllm/test_vll5.py` 展示了另一条可用思路：Ray Worker 上用 vLLM embedding actor，每个 actor 申请 `resources={"NPU": 1}`，并配置 Ascend CANN 动态库。这条路径能让 embedding 计算显式落到 NPU，但它尚未接入 AgentHeaven 的 `VectorKLEngine` 默认链路。

9. 当前代码是否具备切换到其它推理后端的能力？

   具备基础能力，但还不是开箱即用的 Phase1 NPU embedding 方案。

   已具备：

   - `LLM_CONFIG_ENGINE` 可通过 config 切换 `provider/model/backend/api_base`。
   - RubikSQL 配置里已有 `vllm` provider：

     ```yaml
     vllm:
       backend: hosted_vllm
       api_base: <VLLM_API_BASE>
     ```

   - AgentHeaven README 中也说明可把 `embedder` 切换到 OpenAI 等 provider。

   仍需补齐：

   - 如果使用 vLLM，需要启动 OpenAI-compatible embedding server，并将 `llm.presets.embedder.provider=vllm`、`llm.providers.vllm.api_base=http://.../v1`。
   - 如果使用 MindIE，需要确认 MindIE 是否暴露 OpenAI-compatible `/v1/embeddings`。若是，可通过 OpenAI-compatible provider 接入；若不是，需要新增 LiteLLM provider 或在 AgentHeaven 增加自定义 embedder adapter。
   - 如果希望沿用 `test_vll5.py` 的 in-process vLLM actor 模式，需要改造 Phase1 embedding 管线，让 Daft/Ray embedding stage 直接调用 vLLM actor，而不是通过 `VectorKLEngine -> LLM.embed()` 同步 HTTP client。

## 3. Ray Job Submission 下的 Phase1 入口建议

推荐把 Phase1 拆成两层：

```text
serve00 submit wrapper
  - 只负责 JobSubmissionClient.submit_job()
  - 传入 entrypoint、runtime_env、环境变量

Ray Head 内部 phase1 driver
  - 加载 manifest
  - 设置 Daft/Ray runner
  - 做 S3/MinIO IOConfig
  - 提交 Ray task / actor
  - 汇总结果
```

提交脚本参考结构：

```python
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://<ray-head-dashboard>:8265")

job_id = client.submit_job(
    entrypoint=(
        "python -m rubiksql_lake.cli build "
        "-m /mnt/rubiksql/lake_manifest_s3.yaml"
    ),
    runtime_env={
        "env_vars": {
            "PYTHONPATH": (
                "/mnt/rubiksql/embeding_daft-main/docs/rubiksql-lake-pipeline/src:"
                "/mnt/rubiksql/embeding_daft-main/RubikSQL-dev/src:"
                "/mnt/rubiksql/embeding_daft-main/AgentHeaven-dev-master/src"
            ),
            "AWS_ACCESS_KEY_ID": "<MINIO_ACCESS_KEY>",
            "AWS_SECRET_ACCESS_KEY": "<MINIO_SECRET_ACCESS_KEY>",
            "AWS_ENDPOINT_URL": "http://10.42.0.29:9000",
            "NO_PROXY": "127.0.0.1,localhost,.svc,.cluster.local,10.0.0.0/8,10.42.0.0/16,10.42.0.29",
        }
    },
)
```

说明：

- 如果使用共享源码挂载，不建议用 `runtime_env.working_dir` 打包整个 RubikSQL/AgentHeaven 大仓库；会增加提交时间和 Ray runtime_env 解包风险。
- `runtime_env.working_dir` 适合提交一个很小的 wrapper 目录。
- 真正源码推荐由 RayCluster YAML Volume 挂载，Head/Worker 看到一致路径。

## 4. RubikSQL / AgentHeaven 部署方式分析

### 4.1 方案一：所有节点分别 `pip install -e ...`

方式：

```bash
pip install -e AgentHeaven-dev-master
pip install -e RubikSQL-dev
pip install -e docs/rubiksql-lake-pipeline
```

在 Kubernetes RayCluster 中，这通常意味着：

- 在 Head Pod 安装一次。
- 每个 Worker Pod 安装一次。
- Pod 重启后如果不是 baked image，需要重新安装。

优点：

- Python 包元数据完整，console script 如 `rubiksql-lake` 可直接使用。
- `importlib.metadata`、entry points、依赖解析更符合标准 Python 包行为。
- 对本地开发和调试最直观。

缺点：

- 多节点一致性难保障。某个 Worker 安装失败或版本落后，会出现 import 行为不一致。
- Pod 是易失的；如果不是写入镜像层，每次重启都要重复安装。
- `pip install -e` 对共享源码路径有依赖，若各节点路径不同，editable link 容易失效。
- 升级时需要逐节点或逐 Pod 重装，Ray Worker 滚动过程中可能出现新旧代码混跑。
- 如果在 Worker 启动脚本里临时 pip install，会显著拉长扩缩容和故障恢复时间。

后续升级：

- 手工节点安装：不推荐，升级成本高。
- 容器镜像内安装：一致性好，但每次源码变更都要重新 build/push image。
- 启动时安装：灵活但慢，且依赖外网/代理稳定。

适用场景：

- 小规模调试。
- 固定节点、非 K8s 的长期虚拟机环境。
- 希望完整使用 CLI entrypoint 且不频繁升级源码。

### 4.2 方案二：共享存储挂载源码，直接 import

方式：

```text
/home/admin/testpanxy/ray_job_test/rubiksql
  -> 挂载到 Ray Head / Worker Pod 的相同路径
  -> 设置 PYTHONPATH
```

RayCluster YAML 中声明 Volume，并让 Head / Worker 都挂载，例如：

```yaml
env:
  - name: PYTHONPATH
    value: >-
      /mnt/rubiksql/embeding_daft-main/docs/rubiksql-lake-pipeline/src:
      /mnt/rubiksql/embeding_daft-main/RubikSQL-dev/src:
      /mnt/rubiksql/embeding_daft-main/AgentHeaven-dev-master/src
```

优点：

- Head 和 Worker 看到同一份源码，路径一致。
- 源码升级简单：更新共享目录即可。
- 不需要每个 Pod 重复 `pip install -e`。
- 与 Ray Job Submission 模式匹配：Job 只提交入口，源码由集群挂载提供。
- 适合当前 Phase1 仍在快速迭代、调试的阶段。

缺点：

- Python package metadata / console script 不一定可用，需要用 `python -m ...` 或显式入口脚本。
- 依赖包仍需在镜像或环境中预装；共享源码不能替代 `daft/ray/pyarrow/lancedb/litellm/torch-npu/vllm` 等依赖。
- 如果 job 正在运行时共享源码被修改，可能出现同一 job 内新旧代码混杂。
- NFS/共享存储性能和可用性会影响 import、资源文件读取、配置文件读取。
- 如果多个用户同时修改共享源码，风险较高。

后续升级：

- 推荐使用版本化发布目录，而不是直接覆盖同一路径：

  ```text
  /mnt/rubiksql/releases/20260626-001/
  /mnt/rubiksql/releases/20260626-002/
  /mnt/rubiksql/current -> releases/20260626-002
  ```

- Ray Job 提交时固定 `RUBIKSQL_RELEASE=/mnt/rubiksql/releases/<id>`，不要让运行中 job 跟随 `current` 漂移。
- 需要变更时，先准备新目录，验证后提交新 job。

### 4.3 推荐方案

推荐采用“共享源码 + 固定运行镜像”的混合方案：

1. 构建或选定一套 Ray Head/Worker 基础镜像，预装重依赖：

   ```text
   ray 版本与集群一致
   daft
   pyarrow
   pandas
   pydantic
   lancedb
   llama-index-vector-stores-lancedb
   litellm
   sqlalchemy
   click
   jinja2
   pyyaml
   torch-npu / CANN / vLLM Ascend 或 MindIE client 依赖
   ```

2. 通过共享 Volume 挂载源码：

   ```text
   /mnt/rubiksql/releases/<release-id>/
   ```

3. 在 RayCluster Head/Worker 环境中设置：

   ```text
   PYTHONPATH=
     <release>/embeding_daft-main/docs/rubiksql-lake-pipeline/src:
     <release>/embeding_daft-main/RubikSQL-dev/src:
     <release>/embeding_daft-main/AgentHeaven-dev-master/src
   ```

4. Job Submission 只提交很小的 submit wrapper，不打包整个源码树。

5. 如必须使用 CLI entrypoint，可在镜像 build 阶段对同一路径结构执行：

   ```bash
   pip install --no-deps -e AgentHeaven-dev-master
   pip install --no-deps -e RubikSQL-dev
   pip install --no-deps -e docs/rubiksql-lake-pipeline
   ```

   但 Phase1 初期更建议使用 `PYTHONPATH + python -m`，降低重装成本。

## 5. 代理方案设计

### 5.1 需求边界

Head / Worker 需要外网的场景：

- pip 安装依赖。
- 下载 HuggingFace 模型。
- 访问 GitHub。
- 访问外部 LLM API。
- 拉取容器镜像或模型权重。

不应走外网代理的场景：

- Ray GCS / Dashboard / Worker 内部通信。
- Kubernetes service DNS。
- MinIO / S3 内网 endpoint。
- vLLM / MindIE / Ollama 内网服务。
- LanceDB / 共享存储 / NFS。

### 5.2 是否可以统一使用本地代理

可以，但不建议作为长期生产依赖。

适合：

- Phase1 调试期临时下载依赖、模型。
- GitHub / HuggingFace 访问受限时的短期通道。

不适合：

- 长时间 Phase1 大规模构建。
- Worker 扩缩容频繁、模型权重大量下载。
- 多用户共享集群的稳定依赖。

原因：

- 本地电脑断网、休眠、代理重启都会影响集群。
- SSH reverse tunnel 是单点。
- 大模型下载流量会压在本地代理链路上。
- Kubernetes Pod 不能直接访问 serve00 的 `127.0.0.1`。

更稳妥的长期方案：

- 依赖通过镜像预装。
- 模型通过集群内共享模型缓存、MinIO、PVC 或 HuggingFace mirror 预热。
- 外网代理只作为 fallback。

### 5.3 RayCluster YAML 注入代理环境变量

可以在 RayCluster Head 和 Worker 容器中统一注入：

```yaml
env:
  - name: HTTP_PROXY
    value: "http://proxy-gateway.ray-system.svc.cluster.local:17893"
  - name: HTTPS_PROXY
    value: "http://proxy-gateway.ray-system.svc.cluster.local:17893"
  - name: ALL_PROXY
    value: "http://proxy-gateway.ray-system.svc.cluster.local:17893"
  - name: NO_PROXY
    value: >-
      127.0.0.1,localhost,
      .svc,.cluster.local,
      10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,
      10.42.0.0/16,
      10.42.0.29,
      minio,minio.default.svc,minio.default.svc.cluster.local,
      raycluster-kuberay-head-svc,
      ollama,vllm,mindie
```

注意：

- `runtime_env.env_vars` 只影响 Ray Job 的 Python worker 进程，不一定影响容器启动阶段的 pip、模型预热、镜像初始化。
- 如果要让 pip / git / huggingface 在容器启动脚本里也走代理，必须在 RayCluster Pod spec 的容器 env 注入。
- `NO_PROXY` 必须包含 MinIO endpoint，否则 S3 访问可能绕到外网代理，性能和稳定性都会变差。

### 5.4 Pod 如何访问本地代理

Pod 不能访问用户本机的 `127.0.0.1:7890`，也不能访问 serve00 上仅绑定 loopback 的 `127.0.0.1:17891`。需要一个 Kubernetes 可达的代理入口。

推荐拓扑：

```text
User laptop
  127.0.0.1:7890
      ^
      | SSH reverse tunnel
      |
serve00
  127.0.0.1:17891
      ^
      | proxy relay, binds 10.42.0.1:17893
      |
Kubernetes Service / Endpoint
  proxy-gateway:17893
      ^
      |
Ray Head / Worker Pods
```

实现方式 A：SSH reverse tunnel + serve00 relay

1. 用户本地到 serve00 建立反向隧道：

   ```bash
   ssh -N \
     -R 127.0.0.1:17891:127.0.0.1:7890 \
     -o ServerAliveInterval=30 \
     -o ServerAliveCountMax=3 \
     -o ExitOnForwardFailure=yes \
     admin@110.120.0.3
   ```

2. 在 serve00 上启动一个仅面向集群内网的 relay，将 `10.42.0.1:17893` 转到 `127.0.0.1:17891`。

   可选工具：`socat`、`gost`、`tinyproxy`、`nginx stream`。

   设计要求：

   - 只绑定 serve00 的 Kubernetes 内网 IP，例如 `10.42.0.1`。
   - 不绑定公网网卡。
   - 设置访问控制，只允许 Pod CIDR。

3. 在 Kubernetes 中创建 Service + Endpoints：

   ```yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: proxy-gateway
     namespace: ray-system
   spec:
     ports:
       - name: http-proxy
         port: 17893
         targetPort: 17893
   ---
   apiVersion: v1
   kind: Endpoints
   metadata:
     name: proxy-gateway
     namespace: ray-system
   subsets:
     - addresses:
         - ip: 10.42.0.1
       ports:
         - name: http-proxy
           port: 17893
   ```

4. RayCluster Head/Worker 注入：

   ```text
   HTTP_PROXY=http://proxy-gateway.ray-system.svc.cluster.local:17893
   HTTPS_PROXY=http://proxy-gateway.ray-system.svc.cluster.local:17893
   ALL_PROXY=http://proxy-gateway.ray-system.svc.cluster.local:17893
   ```

实现方式 B：允许 SSH 反向隧道直接绑定内网地址

如果 sshd 开启 `GatewayPorts clientspecified`，可以直接：

```bash
ssh -N \
  -R 10.42.0.1:17891:127.0.0.1:7890 \
  admin@110.120.0.3
```

然后 Pod 访问 `http://10.42.0.1:17891`。

不推荐默认采用该方式，因为它依赖 sshd 配置，并且更容易误暴露端口。若使用，必须确认只绑定内网 IP，不绑定 `0.0.0.0` 公网。

实现方式 C：集群内独立代理

在 Kubernetes 内部署 egress proxy，例如 Squid / Tinyproxy / gost，并让它直接访问外网或企业代理。

这是长期最推荐的生产形态：

- 不依赖用户本地电脑。
- 可以做审计、限流、访问控制。
- 可用 Kubernetes Secret 管理认证。
- 更适合多用户和大规模模型下载。

### 5.5 推荐代理方案

Phase1 调试期推荐：

1. 使用本地代理 + SSH reverse tunnel。
2. 在 serve00 上通过 relay 暴露一个仅集群内网可达的 HTTP proxy endpoint。
3. RayCluster Head/Worker 统一注入 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY`。
4. HuggingFace、pip、git 使用代理；Ray、MinIO、K8s service、内部模型服务走 `NO_PROXY`。

Phase1 稳定期推荐：

1. 构建包含依赖的 Ray 镜像。
2. 模型权重预热到共享 PVC / MinIO / 节点本地缓存。
3. 使用集群内 egress proxy 或企业代理。
4. 本地 SSH 代理仅保留为临时应急通道。

## 6. Embedding 后端切换设计

### 6.1 当前默认：Ollama

优点：

- 已被 AgentHeaven/RubikSQL 配置为默认路径。
- AgentHeaven `LLM.embed()` 已经支持 batch、cache、并发切分。
- 对 `VectorKLEngine` 无需改代码。

问题：

- 默认 `localhost:11434` 要求每个执行 embedding 的 Head/Worker Pod 本地都有 Ollama。
- 当前未看到 Ollama 使用 Ascend 910B 的明确配置。
- 每个 Worker 8 张 NPU 无法自动被当前 Ollama HTTP client 使用。

适合：

- 小规模功能验证。
- 不追求 910B 利用率的 smoke test。

### 6.2 vLLM OpenAI-compatible server

RubikSQL 配置已有：

```yaml
vllm:
  backend: hosted_vllm
  api_base: <VLLM_API_BASE>
```

切换思路：

```yaml
llm:
  presets:
    embedder:
      provider: vllm
      model: <served-embedding-model-name>
  providers:
    vllm:
      backend: hosted_vllm
      api_base: http://<vllm-service>:8000/v1
```

要求：

- vLLM 服务必须暴露 OpenAI-compatible embeddings API。
- `api_base` 必须是 Head/Worker Pod 可访问的 Kubernetes Service 地址。
- 如果每个 Worker 8 张 NPU，推荐每个 Worker Pod 内或每个节点上启动 vLLM embedding 服务，或者按节点维度部署 vLLM service，让 Ray task 就近访问。

优点：

- 与当前 AgentHeaven `LLM.embed()` HTTP 模式兼容。
- 后端可独立扩缩容。
- 比 in-process vLLM actor 更少侵入 RubikSQL/AgentHeaven。

风险：

- 需要确认 vLLM Ascend 版本、模型、embedding API、CANN 环境全部可用。
- 如果所有 Worker 都访问一个中心 vLLM 服务，可能形成瓶颈。

### 6.3 vLLM in-process actor

`vllm/test_vll5.py` 展示了这个方向：

```python
dataset.map_batches(
    VLLMEmbeddingPredictor,
    concurrency=24,
    batch_size=512,
    resources={"NPU": 1},
)
```

优点：

- NPU 绑定明确。
- 可以每张 NPU 一个 actor。
- 吞吐更容易打满。

问题：

- 当前 AgentHeaven `VectorKLEngine` 仍是同步 `LLM.embed()` HTTP/client 形态。
- 若要使用 in-process actor，需要改 Phase1 embedding 设计：先用 Ray/vLLM 生成 embedding，再写入 LanceDB，或扩展 `VectorKLEngine.batch_upsert_precomputed()`。

适合：

- 大规模 enum embedding 构建。
- 追求 910B 利用率。

### 6.4 MindIE

当前配置未看到 `mindie` provider。

可选接入方式：

1. 如果 MindIE 暴露 OpenAI-compatible `/v1/embeddings`：

   - 使用 LiteLLM 的 OpenAI-compatible 路径。
   - 配置一个 provider 指向 MindIE `api_base`。
   - `model` 写 MindIE server 中注册的 embedding model 名称。

2. 如果 MindIE API 不兼容 OpenAI：

   - 新增 LiteLLM provider adapter；或
   - 在 AgentHeaven 中支持自定义 callable embedder；或
   - 在 Phase1 管线中旁路 `LLM.embed()`，直接由 Ray actor 调 MindIE SDK/API。

建议：

- 如果目标是最快进入 Phase1 构建，优先走 OpenAI-compatible HTTP server 形态。
- 如果目标是最高吞吐，后续再做 MindIE/vLLM actor 化和预计算 embedding 写入。

## 7. Phase1 执行前检查清单

不要在集群未恢复时执行以下检查；这里只列设计清单。

集群恢复后再确认：

- Ray Head Dashboard 地址，供 `JobSubmissionClient` 使用。
- Head / Worker 镜像中的 Ray 版本与集群一致。
- Head / Worker 均能 import：

  ```text
  ahvn
  rubiksql
  rubiksql_lake
  daft
  pyarrow
  lancedb
  litellm
  ```

- Head / Worker 均能访问 MinIO：

  ```text
  http://10.42.0.29:9000
  ```

- S3 manifest path 已改为：

  ```text
  s3://rubikbench/rubikbench_parquet/RubikBench/<table_id>/*.parquet
  ```

- 若使用 Ollama：

  ```text
  curl http://localhost:11434/api/embed
  ```

  必须在执行 embedding 的 Pod 内成立。

- 若使用 vLLM / MindIE：

  ```text
  curl http://<service>:<port>/v1/embeddings
  ```

  必须在 Head / Worker Pod 内成立。

- `NO_PROXY` 包含：

  ```text
  localhost,127.0.0.1,.svc,.cluster.local,10.42.0.0/16,10.42.0.29
  ```

- 输出目录使用共享存储或对象存储，且 Head/Worker 都可写。

## 8. 推荐落地路线

第一步：固定运行方式

- 用 Ray Job Submission 提交 Phase1。
- serve00 仅保留 submit 脚本。
- Phase1 driver 在 Ray Head 内运行。

第二步：固定源码部署

- 使用共享 Volume 挂载版本化 release 目录。
- Head/Worker 统一 `PYTHONPATH`。
- 镜像预装重依赖。

第三步：先跑最小闭环

- 使用 S3/MinIO manifest。
- 暂时使用当前 Ollama embedder 或 mock/small embedding 服务验证 UKFT JSONL、LanceDB shard、merge 流程。
- 不追求吞吐。

第四步：切换 NPU embedding

- 优先验证 vLLM OpenAI-compatible embedding service。
- 将 `embedder` provider 切到 `vllm`，保持 `VectorKLEngine` 代码不变。
- 如果中心 vLLM 服务成为瓶颈，再推进 per-worker / per-NPU actor 化 embedding。

第五步：稳定化

- 使用镜像 + 模型缓存，减少外网依赖。
- 本地 SSH 代理仅作为临时 fallback。
- 将每次 Phase1 run 的 manifest、release id、Ray job id、embedding backend、模型名、输出路径写入 run metadata。

## 9. 关键风险

- 当前默认 `localhost:11434` 在 Ray Job 模式下不是 serve00，而是 Head/Worker Pod 自己。
- 当前默认 Ollama 路径没有显式使用 Ascend 910B。
- `LLM.dim` 可能在 engine 初始化时提前发起一次 embedding 请求。
- 如果共享源码路径在 job 运行中被修改，可能出现不可复现结果。
- `runtime_env.working_dir` 不适合长期打包整个大仓库。
- 代理若未配置 `NO_PROXY`，Ray/MinIO/内部模型服务可能被错误转发到外网代理。
- RayCluster Head/Worker 的 Ray 版本必须与提交/运行环境匹配。

## 10. 参考来源

- 本仓库源码：
  - `docs/rubiksql-lake-pipeline/src/rubiksql_lake/worker.py`
  - `AgentHeaven-dev-master/src/ahvn/klengine/vector_engine.py`
  - `AgentHeaven-dev-master/src/ahvn/utils/vdb/base.py`
  - `AgentHeaven-dev-master/src/ahvn/utils/vdb/vdb_utils.py`
  - `AgentHeaven-dev-master/src/ahvn/utils/llm/base.py`
  - `AgentHeaven-dev-master/src/ahvn/utils/llm/spec.py`
  - `RubikSQL-dev/src/rubiksql/resources/configs/ahvn_config.yaml`
  - `vllm/test_vll5.py`
  - `vllm/submit.py`
- LiteLLM Ollama embedding handler：
  - https://raw.githubusercontent.com/BerriAI/litellm/main/litellm/llms/ollama/completion/handler.py
- Ollama API：
  - https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md
