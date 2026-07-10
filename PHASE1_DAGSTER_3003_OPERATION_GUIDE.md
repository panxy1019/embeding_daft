# Phase1 Dagster 3003 操作与可视化说明

本文档说明当前 RubikSQL Phase1 如何通过 Dagster UI 3003 提交 Ray Job、如何观察 Head/Worker 构建过程、如何在 UI 中修改任务参数，以及每个 Dagster op/asset 代表什么。

## 1. 当前目标

Phase1 的目标是：

1. 在 serve00 上通过 Dagster UI 提交任务。
2. Dagster 使用 Ray Job Submission 把 Python driver 提交到 Ray Head。
3. Ray Head 从 MinIO/S3 数据湖读取 Parquet schema 和表清单。
4. Ray 选择 worker，并在 worker 上执行 RubikSQL 表构建。
5. worker 侧按表生成 UKFT JSONL、SQLite、LanceDB，并上传回 MinIO。
6. Dagster 展示配置、提交、Ray 执行、表级构建、MinIO 校验和耗时报告。

当前不改变 RayCluster 配置，不绑定 NPU，也不把 serve00 当作计算 Driver。serve00 只是 Dagster UI 和 Ray Job Submission 的入口。

## 2. 启动和状态检查

进入 Phase1 目录：

```bash
cd /home/admin/testpanxy/ray_job_test/rubiksql/phase1
```

启动 Dagster UI 3003：

```bash
bash scripts/start_phase1_build_ui_3003.sh
bash scripts/start_phase1_build_daemon_3003.sh
```

检查状态：

```bash
bash scripts/phase1_build_ui_status_3003.sh
```

浏览器打开：

```text
http://127.0.0.1:3003/jobs
```

如果本地浏览器打不开，通常是 SSH 端口转发或浏览器访问路径问题。3003 是 serve00 上的 Dagster webserver 端口。

停止 3003：

```bash
bash scripts/stop_phase1_build_ui_3003.sh
```

## 3. UI 中如何提交 RubikSQL 构建

在 Dagster UI 3003 中：

1. 打开 `Jobs`。
2. 选择 `phase1_rubiksql_build_job`。
3. 打开 `Launchpad`。
4. 修改 YAML 配置。
5. 点击 `Launch Run`。

最常用配置入口是：

```yaml
ops:
  prepare_phase1_run:
    config:
      table_selection: db
      only_db_id: RubikBench
      only_table_id: ""
      max_tables: 2
      sample_rows: 32
      max_columns: 4
      max_enum_columns: 1
      max_enum_values: 3
      embedding_mode: worker_cpu_vllm
      worker_cpu_vllm_count: 1
      worker_cpu_vllm_first_port: 18100
      worker_cpu_vllm_executable: /tmp/rubiksql_vllm_cpu_venv/bin/python
      worker_cpu_vllm_module: vllm.entrypoints.openai.api_server
      worker_cpu_vllm_model_path: intfloat/e5-small-v2
```

### 表选择参数

`table_selection` 支持：

- `single_table`: 只构建 `only_db_id + only_table_id` 指定的一张表。
- `db`: 构建 `only_db_id` 下的表，并用 `max_tables` 限制数量。
- `full_lake`: 扫描整个数据湖，并用 `max_tables` 限制数量。

`max_tables` 规则：

- `max_tables: 1` 表示最多构建 1 张表。
- `max_tables: 10` 表示最多构建 10 张表。
- `max_tables: 0` 表示不限制数量，谨慎使用。

示例：从 `RubikBench` 中构建 5 张表：

```yaml
ops:
  prepare_phase1_run:
    config:
      table_selection: db
      only_db_id: RubikBench
      only_table_id: ""
      max_tables: 5
      sample_rows: 64
      max_columns: 8
      max_enum_columns: 2
      max_enum_values: 5
      embedding_mode: worker_cpu_vllm
```

示例：全数据湖最多构建 20 张表：

```yaml
ops:
  prepare_phase1_run:
    config:
      table_selection: full_lake
      max_tables: 20
      sample_rows: 64
      max_columns: 8
      max_enum_columns: 2
      max_enum_values: 5
      embedding_mode: worker_cpu_vllm
```

## 4. 当前 Dagster Job 拓扑

`phase1_rubiksql_build_job` 当前由以下步骤组成：

```text
prepare_phase1_run
  -> inventory_lake_tables
  -> build_lake_table[每张表一个动态步骤]
  -> collect_full_lake_report
  -> verify_minio_outputs
  -> collect_timing_report
```

这些步骤在 Dagster Run 页面中可以看到耗时条。多张表时，`build_lake_table[...]` 会按表展开，类似：

```text
build_lake_table[t_0001_RubikBench_PROFIT_AND_LOSS]
build_lake_table[t_0002_RubikBench_BUDGET_AND_FORECAST_ACCESSORY]
build_lake_table[t_0003_RubikBench_...]
```

### 重要说明

当前版本保留 Ray Job Submission，但把提交粒度调整为表级：

```text
Dagster on serve00
  -> build_lake_table[某张表]
  -> Ray Job Submission
  -> Ray Head table driver
  -> Ray worker table task
```

因此，每个 `build_lake_table[...]` 的耗时就是这张表真实的 Ray 提交、Head schema probe、worker 构建、embedding、SQLite/LanceDB 写入和 MinIO 上传耗时。`collect_full_lake_report` 只负责在所有表完成后合并全局报告。

## 5. 每个步骤代表什么

`prepare_phase1_run`

- 读取 Launchpad 配置。
- 生成本次 run_id。
- 写入生成后的配置文件到 `configs/generated_3003/<run_id>.yaml`。
- 扫描数据湖，确认本次会构建多少张表。
- materialize `phase1_lake_inventory_asset`。

`inventory_lake_tables`

- 将 `prepare_phase1_run` 中选出的表清单展开为 Dagster dynamic outputs。
- 每张表对应一个后续的 `build_lake_table[...]` step。

`build_lake_table[...]`

- 使用 `ray.job_submission.JobSubmissionClient` 连接 Ray dashboard。
- 为当前表生成表级配置：

```text
configs/generated_3003/table_jobs/<table_job_id>.yaml
```

- 提交表级 entrypoint：

```text
python -m ray_driver.phase1_table_driver --config configs/generated_3003/table_jobs/<table_job_id>.yaml
```

- 轮询这个表级 Ray job。
- Ray Head 读取当前表的 Parquet schema。
- Ray Head 选择 worker。
- worker 构建 DatabaseUKFT/TableUKFT/ColumnUKFT/EnumUKFT。
- worker 调用 embedding endpoint。
- worker 写出 UKFT JSONL、SQLite、LanceDB，并上传到 MinIO。
- Dagster step metadata 展示 worker IP、worker hostname、worker pid、embedding endpoint、UKFT 数和输出 S3 URI。
- 表级 Ray 日志写入：

```text
/home/admin/testpanxy/ray_job_test/rubiksql/phase1/logs/<ray_job_id>.log
```

`collect_full_lake_report`

- 收集所有 `build_lake_table[...]` 返回的表级结果。
- 合并全局 driver manifests：
  - `schema_probes.jsonl`
  - `table_summaries.jsonl`
  - `selected_tables.jsonl`
- 生成全局 timing report 和 `PHASE1_BUILD_REPORT.md`。
- 上传到：

```text
s3://rubiksql-build-runs/phase1/ray-demo/<run_id>/driver/
```
- 展示该表：
  - DB/Table 名称
  - worker IP
  - worker hostname
  - worker pid
  - Ray node id
  - Parquet URI
  - UKFT 数量
  - Embedding 数量
  - LanceDB 行数
  - S3 输出 URI
  - 最慢步骤
- materialize 表级 asset：

```text
phase1_worker_table_build/<db_id>/<table_id>
```

`collect_worker_build_report`

- 汇总所有表的构建状态。
- 统计成功/失败表数量、UKFT 数、embedding 数。
- materialize `phase1_worker_build_asset`。

`verify_minio_outputs`

- 检查 MinIO 写回结果。
- 至少要求存在：
  - `driver/manifests/phase1_config.json`
  - `driver/manifests/schema_probes.jsonl`
  - `driver/manifests/table_summaries.jsonl`
  - `driver/reports/PHASE1_BUILD_REPORT.md`
  - `driver/timing/full_lake_summary.json`
  - `tables/*/ukfts.jsonl`
  - `tables/*/sqlite/*`
  - `tables/*/lance/*`
- materialize `phase1_minio_outputs_asset`。

`collect_timing_report`

- 读取完整耗时报告：

```text
s3://rubiksql-build-runs/phase1/ray-demo/<run_id>/driver/timing/full_lake_summary.json
```

- 展示总耗时、最慢步骤、最慢表。
- materialize `phase1_timing_report_asset`。

## 6. Asset 与 Job 的关系

3003 中主要操作入口是：

```text
Jobs -> phase1_rubiksql_build_job -> Launchpad -> Launch Run
```

这个 job 会在运行过程中写出 asset materialization 事件。Catalog 中可以看到这些资产：

```text
phase1_lake_inventory_asset
phase1_head_schema_asset
phase1_worker_build_asset
phase1_minio_outputs_asset
phase1_timing_report_asset
phase1_worker_table_build/<db>/<table>
```

这里的 asset 表示“本次构建过程产生或验证过的数据产品/报告”。实际调度仍以 `phase1_rubiksql_build_job` 为主。

## 7. 如何确认任务跑在 worker 上

在 Dagster run 页面打开某个 `build_lake_table[...]`，查看 Metadata：

```text
worker_ip
worker_hostname
worker_pid
worker_node_id
embedding_endpoint
```

如果使用 worker CPU vLLM，通常能看到：

```text
embedding_endpoint: http://<worker_ip>:<worker_cpu_vllm_port>/v1
```

例如：

```text
http://10.42.5.22:18142/v1
```

## 8. worker CPU vLLM 配置

当前默认使用 worker CPU vLLM：

```yaml
embedding_mode: worker_cpu_vllm
worker_cpu_vllm_executable: /tmp/rubiksql_vllm_cpu_venv/bin/python
worker_cpu_vllm_module: vllm.entrypoints.openai.api_server
worker_cpu_vllm_model_path: intfloat/e5-small-v2
worker_cpu_vllm_first_port: 18100
worker_cpu_vllm_count: 1
```

当 `worker_cpu_vllm_count` 大于 1 时，会从 `worker_cpu_vllm_first_port` 开始连续分配端口：

```text
18100, 18101, 18102, ...
```

构建表时会轮询使用这些 endpoint。

CPU vLLM 环境会显式设置：

```text
VLLM_TARGET_DEVICE=cpu
CUDA_VISIBLE_DEVICES=
ASCEND_RT_VISIBLE_DEVICES=
```

这可以避免占用当前训练中的 NPU。

## 9. 输出位置

每次运行的 MinIO 根目录：

```text
s3://rubiksql-build-runs/phase1/ray-demo/<run_id>/
```

常用路径：

```text
driver/manifests/phase1_config.json
driver/manifests/selected_tables.jsonl
driver/manifests/schema_probes.jsonl
driver/manifests/table_summaries.jsonl
driver/reports/PHASE1_BUILD_REPORT.md
driver/timing/full_lake_summary.json
driver/timing/slow_steps.md
tables/<db>/<table>/<table_run_id>/ukfts.jsonl
tables/<db>/<table>/<table_run_id>/sqlite/
tables/<db>/<table>/<table_run_id>/lance/
```

本地 Ray job 日志：

```text
/home/admin/testpanxy/ray_job_test/rubiksql/phase1/logs/<ray_job_id>.log
```

## 10. 常见问题

### 为什么以前看起来只有一个大步骤？

以前 `submit_ray_job_to_head` 同时负责提交 Ray job、等待 Ray job 完成、解析 Ray 日志。因此 Dagster 只能看到一个长步骤。

现在已经改成表级动态步骤：

```text
inventory_lake_tables
build_lake_table[每张表]
collect_full_lake_report
```

### 这是不是 asset 构建？

是。当前是 Dagster job 直接调度每张表的 Ray Job，并在每张表完成后 materialize 表级 asset：

```text
Dagster dynamic table step
  -> table-level Ray Job Submission
  -> Ray Head table driver
  -> Ray worker RubikSQL build
  -> table asset materialization
```

这样既保留 Ray Job Submission，又能在 Dagster Gantt 图里看到每张表的真实构建耗时。

### 为什么每张表会对应一个 Ray job？

这是为了让 Dagster 的动态 step 和真实构建边界一致。每个 `build_lake_table[...]` step 内部会提交一个表级 Ray job，并等待这一张表完成。

### 如何减少测试规模？

在 Launchpad 中调小：

```yaml
max_tables: 1
sample_rows: 16
max_columns: 2
max_enum_columns: 1
max_enum_values: 3
```

### 如何扩大到更多表？

在 Launchpad 中调大：

```yaml
table_selection: db
only_db_id: RubikBench
max_tables: 10
```

或者：

```yaml
table_selection: full_lake
max_tables: 20
```

确认稳定后再使用：

```yaml
max_tables: 0
```

## 11. 推荐使用流程

1. 先启动 3003 webserver 和 daemon。
2. 打开 `Jobs -> phase1_rubiksql_build_job -> Launchpad`。
3. 先用小参数测试：

```yaml
max_tables: 1
sample_rows: 16
max_columns: 2
```

4. 确认 Run 成功。
5. 打开 Run 页面查看 Gantt：

```text
inventory_lake_tables
build_lake_table[...]
collect_full_lake_report
verify_minio_outputs
collect_timing_report
```

6. 打开表级 step metadata，确认 worker IP、endpoint 和输出 S3 URI。
7. 打开 Catalog 查看 asset materialization。
8. 再逐步扩大 `max_tables`、`sample_rows`、`max_columns`。
