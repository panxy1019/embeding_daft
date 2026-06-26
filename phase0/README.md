# Phase0 Single Node Validation

This directory contains the Phase0 single-node validation code and report for RubikSQL lake building.

Phase0 validates the full local chain on serve00 without starting Ray, RayCluster, Ray Job Submission, or distributed Daft execution:

```text
MinIO Parquet
  -> Daft read_parquet + IOConfig(S3Config)
  -> Schema and column profiling
  -> DatabaseUKFT / TableUKFT / ColumnUKFT / EnumUKFT
  -> AgentHeaven LLM(preset="embedder")
  -> SQLite + LanceDB
```

Main entry point:

```python
from rubiksql_lake.single_node_build import BuildTableConfig, build_table
```

CLI helper:

```bash
export MINIO_SECRET_ACCESS_KEY='<minio-secret>'
./scripts/run_phase0_single_node.sh --output-dir ./phase0_output
```

Important files:

- `PHASE0_SINGLE_NODE_VALIDATION_TECHNICAL_REPORT_CN.md`: full technical report in Chinese.
- `src/rubiksql_lake/single_node_build.py`: reusable Phase0 build core.
- `scripts/run_phase0_single_node.sh`: wrapper for the validated `ray-submit` conda environment.
- `patches/phase0_compat.patch`: compatibility patch applied only inside the copied Phase0 validation directory.
- `vendor_overrides/`: patched file snapshots for RubikSQL and AgentHeaven compatibility.
- `artifacts/phase0_summary.json`: validation summary.
- `artifacts/ukfts.jsonl`: generated UKFT sample artifact from the validation run.

No cluster, Ray, Kubernetes, or NPU configuration is changed by this code.
