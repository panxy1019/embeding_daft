# Phase0 Single Node Validation Report

## 结论

Phase0 单节点验证已在 serve00 的复制目录完成，未启动 Ray、RayCluster、Daft 分发或任何并行构建任务。

验证目录：`/home/admin/testpanxy/ray_job_test/rubiksql/embeding_daft-phase0`

核心结果：

- 状态：成功
- Parquet：`s3://rubikbench/rubikbench_parquet/RubikBench/PROFIT_AND_LOSS/data_0.parquet`
- Database：`RubikBench`
- Table：`PROFIT_AND_LOSS`
- Daft Schema 列数：67
- 采样行数：5000
- UKFT 总数：79
- DatabaseUKFT：1
- TableUKFT：1
- ColumnUKFT：67
- EnumUKFT：10
- Embedding 数量：10
- Embedding 维度：768
- SQLite：已生成
- LanceDB：已生成，`vec_enums` 行数 10

## 输出文件

- `phase0_output/ukfts.jsonl`
- `phase0_output/sqlite/main.db`
- `phase0_output/lance/vec_enums.lance/`
- `phase0_output/logs/single_node_build.log`
- `phase0_output/phase0_summary.json`
- `phase0_output/PHASE0_SINGLE_NODE_VALIDATION_REPORT.md`

## 代理与依赖

已验证 serve00 通过反向代理 `127.0.0.1:17894` 可访问 Google：`https://www.google.com/generate_204` 返回 HTTP 204。

安装/修复依赖时显式使用：

```bash
HTTP_PROXY=http://127.0.0.1:17894
HTTPS_PROXY=http://127.0.0.1:17894
NO_PROXY=127.0.0.1,localhost,10.42.0.29
```

本阶段在 `ray-submit` 环境中补齐/确认的关键包包括：

- `daft==0.7.15`
- `lancedb==0.30.0`
- `llama-index-core==0.14.23`
- `litellm==1.81.6`
- `sqlalchemy==2.0.51`
- `loguru==0.7.3`
- `termcolor==3.3.0`
- `pyahocorasick==2.3.0`
- `pandas`
- `openai`
- `omegaconf`
- `diskcache`
- `prettytable`
- `tenacity`
- `dill`
- `cloudpickle`

`llama-index-vector-stores-lancedb` 在 conda channel 中不可用，但当前环境已能导入 `llama_index.vector_stores.lancedb`，LanceDB 写入验证通过。

`litellm==1.81.6` 初始安装存在包内部不一致，导致 `ARIZE_PHOENIX` 和 Vertex helper 导入错误；已通过代理执行 `pip install --force-reinstall --no-deps litellm==1.81.6`，只重装 litellm 本体，未升级其它依赖。

## Embedding 路径验证

当前 `LLM(preset="embedder")` 配置来自 RubikSQL 的 `ahvn_config.yaml`，实际为：

- provider：`ollama`
- model：`embeddinggemma`
- 本地服务：`http://localhost:11434`
- 已检测模型：`embeddinggemma:latest`
- 模型能力：embedding
- 输出维度：768

原配置中的 Ollama `api_base` 为 `http://localhost:11434/v1`。在当前 litellm Ollama embedding 路径下，这会拼成 `http://localhost:11434/v1/api/embed` 并返回 404。Phase0 脚本中仅对本次构建使用 `api_base=http://localhost:11434` 运行时覆盖，不修改集群配置。

## 实现说明

新增核心文件：

```text
docs/rubiksql-lake-pipeline/src/rubiksql_lake/single_node_build.py
```

核心入口：

```python
build_table(config: BuildTableConfig) -> dict
```

该函数可作为后续 Ray Worker 的复用核心：Ray Worker 只需构造 `BuildTableConfig`，传入分片对应的 Parquet、db/table、输出目录和采样/枚举参数即可。

Phase0 流程：

1. Daft + `IOConfig(S3Config)` 从 MinIO S3 读取 Parquet。
2. 读取 Schema，采样 5000 行数据。
3. 生成 DatabaseUKFT / TableUKFT / ColumnUKFT / EnumUKFT 字典。
4. import RubikSQL UKFT 类并通过 AgentHeaven `BaseUKF.from_dict()` 反序列化为真实 UKFT 对象。
5. 写出 `ukfts.jsonl`。
6. 用 AgentHeaven `DatabaseKLStore` 写 SQLite。
7. 用 `VectorKLStore` + `VectorKLEngine` + `LLM(preset="embedder")` 写 LanceDB。
8. 用实际 LanceDB row count 校验向量落库成功。

## 副本内兼容修补

所有源码修补均发生在复制目录 `embeding_daft-phase0`，未修改原始 `embeding_daft-main`，未修改集群、Ray、NPU 配置。

修补点：

- `RubikSQL-dev/src/rubiksql/utils/config_utils.py`
  - 适配新版 AgentHeaven `ConfigManager(package/distribution/scope)` 接口。
  - 延后 `RUBIK_CM.setup()`，避免 `rpj()` 定义前被调用。
  - `rpj()` 改用当前 `cm.pj()` API。
- `AgentHeaven-dev-master/src/ahvn/utils/basic/config_utils.py`
  - 增加旧名兼容 `HEAVEN_CM = CM_AHVN`。
  - 增加 `set_cwd()`。
  - `get/set` 兼容旧代码中的 `level=` 参数。
- `AgentHeaven-dev-master/src/ahvn/utils/db/__init__.py`
  - `SQLErrorResponse` 兼容映射到当前 `SQLResponse`。
- `AgentHeaven-dev-master/src/ahvn/llm.py`
  - 新增旧路径兼容 re-export：`from .utils.llm import *`。
- `RubikSQL-dev/src/rubiksql/__init__.py` 与 `RubikSQL-dev/src/rubiksql/ukfs/__init__.py`
  - Phase0 仅导入 UKFT 构建所需核心类，避免 nl2sql agent/toolkit 旧接口阻断验证。

## 验证命令

正式运行命令：

```bash
PYTHONPATH="$PWD/docs/rubiksql-lake-pipeline/src:$PWD/RubikSQL-dev/src:$PWD/AgentHeaven-dev-master/src" \
MINIO_SECRET_ACCESS_KEY='<minio-secret>' \
HTTP_PROXY=http://127.0.0.1:17894 \
HTTPS_PROXY=http://127.0.0.1:17894 \
NO_PROXY=127.0.0.1,localhost,10.42.0.29 \
conda run -n ray-submit python -m rubiksql_lake.single_node_build --output-dir ./phase0_output
```

## 限制与后续

Phase0 目标是验证端到端链路，不是全量构建。当前默认采样 5000 行，并限制最多 8 个 enum 列、每列最多 20 个 enum 候选。后续接入 Ray Job Submission 后，可以复用 `build_table()` 并把采样/枚举参数调大或改为全量 profiling。

本阶段没有启动 Ray，没有查询 Ray 状态，没有修改 RayCluster YAML，没有修改 NPU 配置。
