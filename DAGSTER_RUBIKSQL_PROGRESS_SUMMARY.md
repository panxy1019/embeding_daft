# Dagster 到 RubikSQL 构建进展总结

更新时间：2026-07-08  
目录：`/home/admin/testpanxy/ray_job_test/rubiksql/dagster`

## 1. 当前状态

Phase0.5 已经完成从 Dagster 到 RubikSQL 单机全数据湖构建的闭环验证：

- 使用 Dagster 管理 RubikSQL 构建任务。
- 使用 Dagster Asset UI 展示数据湖到构建产物的资产链路。
- 使用 vLLM CPU endpoint 替代 Ollama 执行 embedding。
- 使用 Daft 从 MinIO/S3 数据湖读取 Parquet。
- 使用 RubikSQL / AgentHeaven 构建 UKFT、SQLite、LanceDB。
- 构建产物上传回 MinIO 数据湖。

没有启动 Ray/RayCluster，没有修改集群配置，也没有修改 NPU 配置。

当前关键服务：

```text
vLLM embedding endpoint: http://127.0.0.1:18000/v1
Dagster job UI:          http://127.0.0.1:3000
Dagster asset UI:        http://127.0.0.1:3001/asset-groups
Dagster daemon:          running
```

本地访问 Asset UI：

```bash
ssh -L 3001:127.0.0.1:3001 admin@110.120.0.3
```

然后打开：

```text
http://127.0.0.1:3001/asset-groups
```

## 2. Dagster 目录结构

当前 Dagster 执行入口位于：

```text
/home/admin/testpanxy/ray_job_test/rubiksql/dagster
```

核心文件：

```text
rubiksql_assets.py          # Dagster asset 定义
workspace_assets.yaml       # Asset UI workspace
materialize_assets.sh       # 多表/全湖 asset 构建入口
materialize_table.sh        # 单表 asset 构建入口
start_asset_ui.sh           # 启动 3001 Asset UI
start_dagster_ui.sh         # 启动 3000 Job UI
start_vllm_cpu.sh           # 启动 vLLM CPU embedding endpoint
check_assets.sh             # 检查 asset definitions
check_services.sh           # 检查 Dagster/vLLM 服务
env.sh                      # 公共环境变量
runs/                       # 本地构建产物
logs/                       # Dagster 和构建日志
configs/                    # 每次 run 的 Dagster config
```

## 3. Asset UI 中的资产图

当前 Asset Graph 为：

```text
s3_rubikbench_parquet
  -> rubiksql_lake_inventory
  -> rubiksql_table_builds
  -> rubiksql_build_report
```

资产组：

```text
rubiksql_phase0_5
```

### s3_rubikbench_parquet

这是外部 Source Asset，表示 MinIO/S3 数据湖：

```text
s3://rubikbench/rubikbench_parquet
```

当前数据湖 inventory：

```text
Parquet objects: 160
Tables:          20
Source size:     11.156 GiB
```

### rubiksql_lake_inventory

作用：

1. 连接 MinIO/S3 数据湖。
2. 扫描 `s3://rubikbench/rubikbench_parquet`。
3. 按 `db_id/table_id` 聚合 Parquet 文件。
4. 生成待构建表清单。
5. 写出 inventory 文件。

本次全湖 run：

```text
selected tables: 20
total tables in lake: 20
inventory time: 0.284s
```

输出示例：

```text
runs/<RUN_ID>/asset_inventory/lake_tables.jsonl
runs/<RUN_ID>/asset_inventory/run_config.json
```

### rubiksql_table_builds

作用：

对 inventory 选中的每张表顺序执行 RubikSQL 构建。当前实现是一个聚合 asset，内部保留每张表的明细 summary。

每张表的执行步骤：

1. 使用 Daft 从 S3 读取 Parquet：

   ```text
   daft.read_parquet("s3://rubikbench/rubikbench_parquet/<DB>/<TABLE>/*.parquet")
   ```

2. 推断 schema。
3. 采样数据，默认：

   ```text
   sample_rows = 5000
   ```

4. 对列做 profile 和 enum 候选识别。
5. 构建 UKFT dict：

   ```text
   DatabaseUKFT
   TableUKFT
   ColumnUKFT
   EnumUKFT
   ```

6. 写出：

   ```text
   ukfts.jsonl
   ```

7. 通过 AgentHeaven / RubikSQL registry 反序列化 UKFT 对象。
8. 写入 SQLite KL store。
9. 写入 LanceDB vector KL store。
10. 通过 vLLM OpenAI-compatible endpoint 做 embedding：

    ```text
    api_base = http://127.0.0.1:18000/v1
    model    = text-embedding-ada-002
    root     = intfloat/e5-small-v2
    ```

11. 上传该表构建产物到 MinIO。

每张表输出目录：

```text
runs/<RUN_ID>/tables/<DB>/<TABLE>/
```

典型产物：

```text
ukfts.jsonl
phase0_summary.json
PHASE0_5_VLLM_VALIDATION_REPORT.md
sqlite/main.db
lance/
logs/
```

### rubiksql_build_report

作用：

1. 汇总所有表的构建 summary。
2. 统计成功/失败数量。
3. 统计慢步骤和慢表。
4. 写出 timing 报告。
5. 上传报告到 MinIO。

输出：

```text
runs/<RUN_ID>/timing/full_lake_summary.json
runs/<RUN_ID>/timing/slow_steps.md
runs/<RUN_ID>/timing/inventory.jsonl
runs/<RUN_ID>/timing/table_timings.jsonl
runs/<RUN_ID>/timing/step_timings.jsonl
```

MinIO reports：

```text
s3://rubiksql-build-runs/phase0_5_assets/<RUN_ID>/reports
```

## 4. 全数据湖构建结果

本次全数据湖 asset 构建命令：

```bash
sudo -i
cd /home/admin/testpanxy/ray_job_test/rubiksql/dagster
RUN_ID=phase0_5_assets_full_lake_20260707T011657Z ./materialize_assets.sh 0
```

Dagster run：

```text
Dagster run id: 903ec702-9be8-4eb6-9777-95731f7d32a1
Job:            rubiksql_asset_build_job
Status:         SUCCESS
```

业务 run：

```text
Run ID:          phase0_5_assets_full_lake_20260707T011657Z
Tables built:    20
Success:         20
Failed:          0
Inventory time:  0.284s
Total time:      29.423s
```

本地输出：

```text
/home/admin/testpanxy/ray_job_test/rubiksql/dagster/runs/phase0_5_assets_full_lake_20260707T011657Z
```

MinIO 输出：

```text
s3://rubiksql-build-runs/phase0_5_assets/phase0_5_assets_full_lake_20260707T011657Z/
```

MinIO 验证：

```text
Objects: 350
Bytes:   33889854
```

Reports：

```text
s3://rubiksql-build-runs/phase0_5_assets/phase0_5_assets_full_lake_20260707T011657Z/reports
```

## 5. 性能观察

最慢表：

```text
BUDGET_AND_FORECAST_ACCESSORY  4.998s
INCOME_CYBVEHSYS               1.596s
SALES_LEDGER                   1.421s
INCOME_NOVDYN                  1.141s
INCOME_GALMOTGRO               1.051s
```

最慢步骤：

```text
BUDGET_AND_FORECAST_ACCESSORY write_agentheaven_sqlite_lance_embedding 2.433s
BUDGET_AND_FORECAST_ACCESSORY deserialize_ukfts                        1.563s
INCOME_CYBVEHSYS              write_agentheaven_sqlite_lance_embedding 0.917s
SALES_LEDGER                  write_agentheaven_sqlite_lance_embedding 0.755s
INCOME_NOVDYN                 write_agentheaven_sqlite_lance_embedding 0.598s
```

主要耗时集中在：

1. AgentHeaven / RubikSQL UKFT 反序列化。
2. SQLite + LanceDB 写入。
3. vLLM embedding。
4. S3/MinIO 上传。

Daft 读取和列 profile 在当前 `sample_rows=5000` 下并不是主要瓶颈。

## 6. 常用执行命令

检查服务：

```bash
sudo -i
cd /home/admin/testpanxy/ray_job_test/rubiksql/dagster
./check_services.sh
./check_assets.sh
```

启动 Asset UI：

```bash
./start_asset_ui.sh
```

全湖构建：

```bash
./materialize_assets.sh 0
```

构建前 8 张表：

```bash
./materialize_assets.sh 8
```

单表构建：

```bash
./materialize_table.sh RubikBench PROFIT_AND_LOSS
```

调整采样参数：

```bash
SAMPLE_ROWS=10000 ./materialize_assets.sh 0
```

更多参数：

```bash
SAMPLE_ROWS=20000 \
MAX_ENUM_COLUMNS=12 \
MAX_ENUM_VALUES=50 \
EMBEDDING_BATCH_SIZE=128 \
./materialize_assets.sh 0
```

## 7. 当前方案边界

当前 asset 化是全湖聚合资产方案：

```text
rubiksql_table_builds
```

这个 asset 内部会构建所有表，并在 JSONL/timing reports 里保存每张表的明细。

也就是说：

- Asset Graph 里能看到完整的三段资产链路。
- Run 日志里能看到每张表的构建过程。
- `table_timings.jsonl` / `step_timings.jsonl` 里有每张表的细粒度统计。
- 但 Asset Catalog 里还不是“每张表一个独立 asset”。

如果后续希望在 Dagster Asset Catalog 中直接看到：

```text
rubiksql_table_builds[RubikBench.PROFIT_AND_LOSS]
rubiksql_table_builds[RubikBench.SALES_LEDGER]
...
```

可以继续升级为 Dagster dynamic partitions 或 static partitions 方案。当前方案已经足够支撑 Phase0.5/Phase1 前的全湖构建验证。

## 8. Phase1 接入建议

当前 `build_table()` 核心逻辑已经适合后续复用：

```text
Dagster asset / op
  -> inventory table spec
  -> build_table(BuildTableConfig)
  -> Daft read_parquet
  -> UKFT
  -> AgentHeaven / VectorDatabase
  -> vLLM embedding
  -> SQLite + LanceDB
  -> MinIO output
```

Phase1 接入 Ray Job Submission 时，建议保留这个核心函数：

```text
build_table(config)
```

然后把“表级任务分发”替换成 Ray Worker 调用，而不是重写 RubikSQL 构建逻辑。

