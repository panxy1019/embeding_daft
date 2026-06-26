# Phase0 Single Node Validation 技术报告

## 1. 执行结论

Phase0 Single Node Validation 已在 serve00 上完成端到端验证。验证过程中只使用复制出来的项目目录，不启动 Ray、不启动 RayCluster、不提交 Ray Job、不执行 Daft 分发、不进行并行 UKFT 构建，也未修改集群配置、NPU 配置或其它共享服务配置。

本次验证证明：RubikSQL lake 构建链路可以在单节点进程内完成从 MinIO Parquet 读取、Schema 获取、列级 profiling、DatabaseUKFT / TableUKFT / ColumnUKFT / EnumUKFT 构建、AgentHeaven Embedding、SQLite 写入与 LanceDB 写入的完整闭环。

核心结果如下：

| 项目 | 结果 |
| --- | --- |
| 验证状态 | 成功 |
| 执行节点 | serve00 |
| Conda 环境 | ray-submit |
| 项目副本 | `/home/admin/testpanxy/ray_job_test/rubiksql/embeding_daft-phase0` |
| Parquet 输入 | `s3://rubikbench/rubikbench_parquet/RubikBench/PROFIT_AND_LOSS/data_0.parquet` |
| Database | `RubikBench` |
| Table | `PROFIT_AND_LOSS` |
| Daft Schema 列数 | 67 |
| 采样行数 | 5000 |
| UKFT 总数 | 79 |
| DatabaseUKFT | 1 |
| TableUKFT | 1 |
| ColumnUKFT | 67 |
| EnumUKFT | 10 |
| Embedding 数量 | 10 |
| Embedding 维度 | 768 |
| SQLite | 已生成 |
| LanceDB | 已生成，`vec_enums` 行数 10 |

## 2. 验证边界

Phase0 的目标是验证单节点独立跑通 RubikSQL 构建链路，为后续 Ray Job Submission 模式下的 Worker 复用做准备。

本阶段明确没有执行以下操作：

- 没有启动 Ray Head 或 Ray Worker。
- 没有执行 `ray.init(address="auto")`。
- 没有执行 `ray job submit`。
- 没有查询或修改 Ray 集群状态。
- 没有修改 RayCluster YAML。
- 没有修改 Kubernetes、NPU、Ascend 910B、驱动或 runtime 配置。
- 没有生成全量 UKFT。
- 没有执行并行分发构建。

本阶段允许并完成的操作：

- 在复制目录 `embeding_daft-phase0` 中添加 Phase0 单节点验证代码。
- 在 `ray-submit` conda 环境中补齐运行所需 Python 依赖。
- 通过 serve00 上的代理访问外网完成依赖检查和安装。
- 通过 MinIO S3 endpoint 读取一个指定 Parquet 文件。
- 调用本机 Ollama embedding 服务完成向量化。

## 3. 代码交付内容

本次提交到 GitHub `phase0/` 目录的主要内容如下：

```text
phase0/
  PHASE0_SINGLE_NODE_VALIDATION_TECHNICAL_REPORT_CN.md
  README.md
  requirements_phase0.txt
  scripts/
    run_phase0_single_node.sh
  src/
    rubiksql_lake/
      single_node_build.py
      ukft_builder.py
      worker.py
      merge.py
      pipeline.py
      profiling.py
      spec.py
      cli.py
      __init__.py
  vendor_overrides/
    RubikSQL-dev/...
    AgentHeaven-dev-master/...
  patches/
    phase0_compat.patch
  artifacts/
    phase0_summary.json
    ukfts.jsonl
```

其中最关键的新代码是：

```text
src/rubiksql_lake/single_node_build.py
```

核心可复用入口是：

```python
build_table(config: BuildTableConfig) -> dict
```

这个函数被设计成后续 Ray Worker 可以直接调用的形式。未来接入 Ray Job Submission 后，Driver 运行在 Ray Head，Worker 只需要基于分片任务构造 `BuildTableConfig`，传入对应的 `parquet_uri`、`db_id`、`table_id`、输出目录和采样/枚举参数即可复用 Phase0 里的核心构建逻辑。

## 4. 单节点构建流程

Phase0 的执行链路如下：

```text
single_node_build.py
  -> build_table(BuildTableConfig)
    -> Daft read_parquet + IOConfig(S3Config)
    -> 获取 schema 与 sample dataframe
    -> 列级 profiling
    -> build_database_ukft()
    -> build_table_ukft()
    -> build_column_ukft()
    -> build_enum_ukft()
    -> AgentHeaven BaseUKF.from_dict()
    -> DatabaseKLStore 写 SQLite
    -> VectorKLStore / VectorKLEngine 调用 LLM(preset="embedder")
    -> 写 LanceDB
    -> 校验 LanceDB row count
    -> 写 phase0_summary.json 与报告
```

### 4.1 S3 Parquet 读取

Phase0 保留 Daft 读取路径，使用 `daft.read_parquet()` 和 `IOConfig(S3Config)` 从 MinIO 读取 Parquet，而不是改成 pandas 或 pyarrow 的临时读取方式。

关键配置：

| 参数 | 值 |
| --- | --- |
| S3 endpoint | `http://10.42.0.29:9000` |
| access key | `admin` |
| secret key | 通过 `MINIO_SECRET_ACCESS_KEY` 环境变量注入 |
| region | `us-east-1` |
| virtual addressing | disabled |

这样可以保持后续 Ray Worker 中的数据读取方式与 Phase0 一致。

### 4.2 Schema 与列级统计

Phase0 从 Daft DataFrame 获取 schema 和列名，然后通过 `limit(sample_rows).to_pandas()` 收集样本。当前验证使用 `sample_rows=5000`。

列级 profiling 输出以下信息：

- column id
- pandas dtype
- total rows
- distinct count
- null count
- top enum values
- bottom enum values

这些统计会被用于构建 ColumnUKFT 和 EnumUKFT。

### 4.3 UKFT 构建

Phase0 保留 RubikSQL lake pipeline 的 UKFT builder 语义，构建以下对象：

- DatabaseUKFT：数据库级元信息。
- TableUKFT：表级元信息、列列表、行数。
- ColumnUKFT：列级类型、distinct/null/top/bottom enum 信息。
- EnumUKFT：枚举值级信息，用于向量化与检索。

验证结果：

| UKFT 类型 | 数量 |
| --- | --- |
| DatabaseUKFT | 1 |
| TableUKFT | 1 |
| ColumnUKFT | 67 |
| EnumUKFT | 10 |
| 总数 | 79 |

当前为了控制 Phase0 验证时间，默认限制最多 8 个 enum 列、每列最多 20 个 enum 候选。实际本次构建产生 10 个 EnumUKFT。

### 4.4 SQLite 写入

Phase0 使用 AgentHeaven 的 `DatabaseKLStore` 写出 SQLite，输出路径为：

```text
phase0_output/sqlite/main.db
```

验证结果：SQLite 文件生成成功。

### 4.5 LanceDB 写入与 Embedding

Phase0 使用 AgentHeaven / RubikSQL 的向量链路写出 LanceDB：

```text
VectorKLEngine
  -> VectorDatabase.batch_k_encode_embed()
  -> LLM(preset="embedder")
  -> provider HTTP request
  -> LanceDB table vec_enums
```

输出路径为：

```text
phase0_output/lance/
```

验证结果：LanceDB 生成成功，`vec_enums` 实际 row count 为 10，与本次 embedding 数量一致。

## 5. Embedding 实际路径

本阶段继续向下追踪了 embedding 调用链，重点验证真正发起 HTTP 请求的位置和服务。

结论如下：

| 问题 | 结论 |
| --- | --- |
| `LLM(preset="embedder")` 配置来源 | RubikSQL / AgentHeaven 配置中的 `ahvn_config.yaml` |
| embedder provider | `ollama` |
| embedding 模型 | `embeddinggemma`，实际检测到 `embeddinggemma:latest` |
| HTTP 服务 | Ollama |
| 默认 endpoint | `http://localhost:11434` |
| Worker 访问方式 | 当前 Phase0 为本机访问，即 serve00 访问 localhost Ollama |
| 输出维度 | 768 |
| 是否使用 Ascend 910B | 本次 Phase0 未验证 Ascend 910B；Ollama 本机服务是否使用 NPU 取决于该服务自身部署方式 |
| 是否可切换后端 | LLM 抽象具备 provider/config 切换入口，但 Phase0 当前验证的是 Ollama；切换 MindIE/vLLM 需要补充对应 provider endpoint 与兼容性验证 |

需要特别记录的是：原配置中的 Ollama `api_base` 为：

```text
http://localhost:11434/v1
```

在当前 `litellm` Ollama embedding 路径下，这会拼接成：

```text
http://localhost:11434/v1/api/embed
```

该地址返回 404。Phase0 脚本没有修改全局配置，而是在本次构建时对 `LLM(preset="embedder")` 使用运行时覆盖：

```text
api_base=http://localhost:11434
```

最终请求落到 Ollama embedding API，验证成功。

## 6. 代理与依赖

serve00 已验证可以通过 SSH Reverse Proxy 使用本地代理访问外网。验证命令访问 `https://www.google.com/generate_204` 返回 HTTP 204。

本阶段依赖安装与网络访问均显式使用以下代理变量：

```bash
HTTP_PROXY=http://127.0.0.1:17894
HTTPS_PROXY=http://127.0.0.1:17894
NO_PROXY=127.0.0.1,localhost,10.42.0.29
```

注意：提交到 GitHub 的脚本不会写死任何密钥。MinIO secret 需要通过环境变量注入：

```bash
export MINIO_SECRET_ACCESS_KEY='<minio-secret>'
```

关键依赖如下：

| 包 | 验证版本 |
| --- | --- |
| daft | 0.7.15 |
| lancedb | 0.30.0 |
| llama-index-core | 0.14.23 |
| litellm | 1.81.6 |
| sqlalchemy | 2.0.51 |
| loguru | 0.7.3 |
| termcolor | 3.3.0 |
| pyahocorasick | 2.3.0 |

补充说明：`llama-index-vector-stores-lancedb` 在测试时未能从 conda channel 获取，但当前环境中 `llama_index.vector_stores.lancedb` 可导入，且 LanceDB 写入和 row count 校验均已通过。

`litellm==1.81.6` 初始安装后出现包内部不一致，表现为 `ARIZE_PHOENIX` 和 Vertex helper 导入错误。已通过代理重装 litellm 本体解决，未修改集群配置。

## 7. 副本内兼容修补

为跑通 Phase0，在复制目录内做了兼容修补。这些修补没有应用到原始 `embeding_daft-main`，也没有修改集群配置。

修补内容已经同时提交为：

```text
phase0/vendor_overrides/
phase0/patches/phase0_compat.patch
```

主要修补点：

| 文件 | 修补目的 |
| --- | --- |
| `RubikSQL-dev/src/rubiksql/utils/config_utils.py` | 适配新版 AgentHeaven `ConfigManager(package/distribution/scope)` 接口，延后 setup，改用 `cm.pj()` |
| `AgentHeaven-dev-master/src/ahvn/utils/basic/config_utils.py` | 增加 `HEAVEN_CM` 旧名兼容、`set_cwd()`、兼容旧代码中的 `level=` 参数 |
| `AgentHeaven-dev-master/src/ahvn/utils/db/__init__.py` | 将 `SQLErrorResponse` 兼容映射到当前 `SQLResponse` |
| `AgentHeaven-dev-master/src/ahvn/llm.py` | 新增旧路径 re-export：`from .utils.llm import *` |
| `RubikSQL-dev/src/rubiksql/__init__.py` | Phase0 只导入 UKFT 构建所需核心模块，避免旧 nl2sql agent/toolkit 接口阻断验证 |
| `RubikSQL-dev/src/rubiksql/ukfs/__init__.py` | Phase0 只导入 db/table/column/enum UKFT 核心类 |

这些修补本质上是版本适配，不涉及集群运行参数。

## 8. 运行方式

在已经准备好 `ray-submit` 环境、代理、MinIO 访问和本地 Ollama embedding 服务后，可执行：

```bash
cd /path/to/embeding_daft-phase0
export MINIO_SECRET_ACCESS_KEY='<minio-secret>'
export HTTP_PROXY=http://127.0.0.1:17894
export HTTPS_PROXY=http://127.0.0.1:17894
export NO_PROXY=127.0.0.1,localhost,10.42.0.29

PYTHONPATH="$PWD/docs/rubiksql-lake-pipeline/src:$PWD/RubikSQL-dev/src:$PWD/AgentHeaven-dev-master/src" \
conda run -n ray-submit python -m rubiksql_lake.single_node_build --output-dir ./phase0_output
```

GitHub `phase0/scripts/run_phase0_single_node.sh` 也提供了封装入口：

```bash
cd /path/to/repo/phase0
export MINIO_SECRET_ACCESS_KEY='<minio-secret>'
./scripts/run_phase0_single_node.sh --output-dir ./phase0_output
```

## 9. 输出文件

本次验证输出：

```text
phase0_output/
  ukfts.jsonl
  sqlite/
    main.db
  lance/
    vec_enums.lance/
  logs/
    single_node_build.log
  phase0_summary.json
  PHASE0_SINGLE_NODE_VALIDATION_REPORT.md
```

提交到 GitHub 的 `artifacts/` 只包含轻量验证产物：

- `phase0_summary.json`
- `ukfts.jsonl`

未提交 SQLite、LanceDB 和日志目录，避免把运行时数据库文件和机器本地路径日志作为长期源码资产提交。

## 10. 对后续 Ray Job Submission 的意义

后续正式 Phase1 不再把 serve00 当作 Driver，而是通过 Ray Job Submission 提交任务：

```text
serve00
  -> ray job submit
  -> Ray Head 上运行 Python Driver
  -> Head / Workers 执行 Daft、RubikSQL、AgentHeaven
```

Phase0 的 `build_table()` 可以作为 Worker 内部构建函数复用。后续 Ray 接入建议：

1. Ray Job Driver 只负责读取 manifest / 分片计划并提交 Ray tasks。
2. 每个 Ray Worker 调用 `build_table(BuildTableConfig)`。
3. `BuildTableConfig` 中的 `parquet_uri`、`db_id`、`table_id`、输出目录由分片任务传入。
4. Worker 环境统一注入 `PYTHONPATH`、MinIO secret、proxy、embedding backend endpoint。
5. 输出目录按 task id / db / table 分区，最后再由 merge 阶段合并 SQLite、LanceDB 和 jsonl。

这样可以避免维护单节点代码和分布式 Worker 代码两套逻辑。

## 11. 风险与后续事项

当前 Phase0 已验证单表单文件闭环，但仍有以下后续事项：

- Ascend 910B 是否参与 embedding 取决于后端推理服务部署，本次未修改或验证 NPU runtime。
- 当前验证使用 Ollama；如后续切换 MindIE 或 vLLM，需要重新验证 provider、API path、输入输出格式和 embedding 维度。
- 当前枚举生成是 Phase0 控制规模后的样本验证，不代表全量 profiling 策略。
- 后续 Ray Worker 中需要确认每个 Pod 能访问 MinIO endpoint 和 embedding endpoint。
- 后续如果 embedding 服务部署在每个 Worker 本机，则 endpoint 可保持 localhost；如果集中部署，则必须改为 service DNS 或固定 service endpoint。
- 依赖安装建议通过镜像或共享 conda 环境固化，避免每个 Worker 启动时临时联网安装。

## 12. 最终结论

Phase0 Single Node Validation 已经达到预期目标：在不启动 Ray、不修改集群配置、不影响其他用户的前提下，验证了 RubikSQL 从 MinIO Parquet 到 UKFT、SQLite、LanceDB 和 Embedding 的单节点完整链路。

Phase0 产出的 `build_table()` 具备后续迁移到 Ray Worker 的接口形态，建议作为 Phase1 分布式构建任务的核心复用函数。


## 13. MinIO 上传更新

Phase0 已从“只写本地目录”升级为“本地构建缓存 + MinIO 数据湖上传”。LanceDB 和 SQLite 仍先写入本地 `output_dir`，因为 LanceDB/SQLite 的写入语义以本地目录/文件为核心；构建完成后，脚本会递归上传整个输出目录到：

```text
s3://rubiksql-build-runs/phase0/<db_id>/<table_id>/<run_id>/
```

新增参数：

- `--s3-output-root`：MinIO/S3 输出根路径，默认 `s3://rubiksql-build-runs/phase0`。
- `--run-id`：构建 run id；不传时自动生成 UTC 时间戳。
- `--skip-s3-upload`：只保留本地输出，不上传 MinIO。

本次修复后验证通过的输出路径：

```text
s3://rubiksql-build-runs/phase0/RubikBench/PROFIT_AND_LOSS/phase0_minio_upload_validation_fixed_20260626T081207Z/
```

验证结果：MinIO prefix 下可列到 17 个对象，总大小 1,487,898 bytes；其中包含 `ukfts.jsonl`、`phase0_summary.json`、SQLite 文件、LanceDB data/manifest/transaction 文件和日志。

同时修复了 `lancedb==0.30.0` 下 `db.list_tables()` 返回 `ListTablesResponse` 导致 row count 误判的问题。修复后 `vec_enums` row count 为 10，与 embedding 数量一致。

## 14. RubikSQL 使用方式

RubikSQL 的使用分为两个阶段：

1. 构建知识库：从数据源生成 DatabaseUKFT、TableUKFT、ColumnUKFT、EnumUKFT，并写入 SQLite 与 LanceDB。
2. 使用知识库：RubikSQL Retrieval/Agent 在 NL2SQL 时检索这些知识，辅助生成 SQL。

Phase0 当前完成的是第 1 阶段，并把产物上传到 MinIO。第 2 阶段还需要补一个“从 MinIO build run 注册/导入 RubikSQL KB”的步骤，才能直接通过 `rubiksql search` 或 `rubiksql ask` 使用。

完整说明见：`phase0/RUBIKSQL_USAGE_CN.md`。
