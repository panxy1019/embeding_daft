# RubikSQL 使用说明（Phase0 视角）

## 1. 先明确 RubikSQL 是什么

RubikSQL 不是单纯的 Parquet 读取工具。它更像一个 NL2SQL 的知识增强层：先把数据库、表、列、枚举值等信息构建成 UKFT 知识对象，再把这些知识写入结构化存储和向量存储，后续 NL2SQL Agent 在回答自然语言问题时检索这些知识，生成更准确的 SQL。

在当前项目里可以把链路理解为：

```text
数据源（Parquet / DB）
  -> 构建 UKFT 知识
  -> SQLite 保存结构化知识
  -> LanceDB 保存 enum 等向量知识
  -> RubikSQL Retrieval / Agent 使用这些知识做 NL2SQL
```

Phase0 当前完成的是前半段：从 MinIO Parquet 构建 UKFT、SQLite、LanceDB，并上传构建产物到 MinIO 数据湖。

## 2. Phase0 产物在哪里

默认输出到 MinIO：

```text
s3://rubiksql-build-runs/phase0/<db_id>/<table_id>/<run_id>/
```

本次验证通过的 run 是：

```text
s3://rubiksql-build-runs/phase0/RubikBench/PROFIT_AND_LOSS/phase0_minio_upload_validation_fixed_20260626T081207Z/
```

里面包含：

```text
ukfts.jsonl
phase0_summary.json
PHASE0_SINGLE_NODE_VALIDATION_REPORT.md
sqlite/main.db
sqlite/main.db-wal
sqlite/main.db-shm
lance/vec_enums.lance/...
logs/single_node_build.log
```

注意：LanceDB 是目录型数据集，不是单文件；SQLite 在 WAL 模式下会同时出现 `main.db`、`main.db-wal`、`main.db-shm`，恢复或复制时应保持这几个文件同一版本。

## 3. 如何运行 Phase0 构建

在 serve00 的 Phase0 复制目录中：

```bash
cd /home/admin/testpanxy/ray_job_test/rubiksql/embeding_daft-phase0
export MINIO_SECRET_ACCESS_KEY='<minio-secret>'
export HTTP_PROXY=http://127.0.0.1:17894
export HTTPS_PROXY=http://127.0.0.1:17894
export NO_PROXY=127.0.0.1,localhost,10.42.0.29

PYTHONPATH="$PWD/docs/rubiksql-lake-pipeline/src:$PWD/RubikSQL-dev/src:$PWD/AgentHeaven-dev-master/src" \
conda run -n ray-submit python -m rubiksql_lake.single_node_build \
  --output-dir ./phase0_output \
  --s3-output-root s3://rubiksql-build-runs/phase0
```

如果只想本地验证、不上传 MinIO：

```bash
PYTHONPATH="$PWD/docs/rubiksql-lake-pipeline/src:$PWD/RubikSQL-dev/src:$PWD/AgentHeaven-dev-master/src" \
conda run -n ray-submit python -m rubiksql_lake.single_node_build \
  --output-dir ./phase0_output \
  --skip-s3-upload
```

后续 Ray Worker 可以直接复用：

```python
from rubiksql_lake.single_node_build import BuildTableConfig, build_table

summary = build_table(
    BuildTableConfig(
        parquet_uri="s3://rubikbench/rubikbench_parquet/RubikBench/PROFIT_AND_LOSS/data_0.parquet",
        db_id="RubikBench",
        table_id="PROFIT_AND_LOSS",
        output_dir="./phase0_output",
        s3_output_root="s3://rubiksql-build-runs/phase0",
    )
)
```

## 4. 怎么“使用”这些构建产物

当前 Phase0 产物的直接用途是验证和交付知识库构建结果：

- `ukfts.jsonl`：可读、可审计的 UKFT 知识对象列表。
- `sqlite/`：结构化知识库，主要保存 database/table/column/enum 等对象。
- `lance/`：向量知识库，当前主要保存 enum embedding，用于相似检索。
- `phase0_summary.json`：构建摘要，包括 schema 列数、UKFT 数量、embedding 数量、LanceDB row count、MinIO 上传状态。

如果要把它接到 RubikSQL 的在线 NL2SQL 能力，需要再完成“导入/挂载知识库到 RubikSQL KLBase”的步骤。也就是说，Phase0 现在已经把知识建好了，但还没有把这些产物注册成 RubikSQL CLI 的一个正式 database KB。

## 5. RubikSQL CLI 的目标使用方式

完整 RubikSQL 通常按下面方式使用：

```bash
rubiksql setup
rubiksql db add ...
rubiksql build -n <db_id>
rubiksql search -n <db_id> -t <table_id> -c <column_id>
rubiksql ask -n <db_id> "自然语言问题"
```

各命令含义：

- `rubiksql setup`：初始化 RubikSQL 全局配置。
- `rubiksql db add`：注册一个真实数据库连接，例如 SQLite、DuckDB、PostgreSQL、MySQL 等。
- `rubiksql build`：从注册数据库构建 RubikSQL 知识库。
- `rubiksql search`：检索 database/table/column/enum 知识。
- `rubiksql ask`：调用 Agent，把自然语言问题转换为 SQL 并执行或返回结果。

但是当前 Phase0 为了专注 Parquet 数据湖构建，绕过了 RubikSQL 原本面向“已注册数据库连接”的 build CLI，并直接从 MinIO Parquet 生成 UKFT。因此 Phase0 产物还需要后续补一个导入步骤，才能被 `rubiksql search` / `rubiksql ask` 当作正式 KB 使用。

## 6. 后续建议

建议 Phase1 增加一个 `import_build_run` 或 `register_build_run` 步骤：

```text
s3://rubiksql-build-runs/phase0/<db>/<table>/<run_id>/
  -> 下载或挂载 sqlite/ 与 lance/
  -> 注册到 RubikSQLKLBase / AgentHeaven KLBase
  -> rubiksql search 可查
  -> rubiksql ask 可用
```

这样后续流程会变成：

```text
Ray Job 构建 UKFT
  -> 产物上传 MinIO
  -> 注册/导入 RubikSQL KB
  -> NL2SQL Agent 使用 KB
```

当前最稳的下一步不是直接启动 Ray，而是先实现一个单机的 `load_phase0_kb.py` 或 `register_build_run.py`，验证从 MinIO 下载 Phase0 产物后，能否用 RubikSQL/AgentHeaven 的 KLBase 正常检索 `vec_enums`。
