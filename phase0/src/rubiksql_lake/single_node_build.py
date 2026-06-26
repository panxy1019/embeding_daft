"""Phase0 single-node RubikSQL lake build validation.

This module intentionally contains no Ray submission, Ray tasks, or Daft
partitioned/distributed scheduling. The reusable entry point is build_table(),
which can later be called from a Ray worker with the same config object.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import daft
import pandas as pd
from daft.io import IOConfig, S3Config
from loguru import logger

from .ukft_builder import (
    build_column_ukft,
    build_database_ukft,
    build_enum_ukft,
    build_table_ukft,
)

UKFT_TYPES = {
    "db:": "db-database",
    "tab:": "db-table",
    "col:": "db-column",
}


@dataclass
class BuildTableConfig:
    parquet_uri: str = "s3://rubikbench/rubikbench_parquet/RubikBench/PROFIT_AND_LOSS/data_0.parquet"
    db_id: str = "RubikBench"
    table_id: str = "PROFIT_AND_LOSS"
    output_dir: str = "./phase0_output"
    s3_endpoint_url: str = "http://10.42.0.29:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "admin"
    s3_secret_access_key: Optional[str] = None
    s3_output_root: Optional[str] = "s3://rubiksql-build-runs/phase0"
    run_id: Optional[str] = None
    skip_s3_upload: bool = False
    sample_rows: int = 5000
    max_columns: int = 0
    max_enum_columns: int = 8
    max_enum_values: int = 20
    embedding_batch_size: int = 64
    skip_embedding: bool = False


def _json_default(value: Any) -> Any:
    if pd.isna(value) if not isinstance(value, (list, tuple, dict, set)) else False:
        return None
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def _safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _safe_str(value: Any) -> str:
    value = _safe_scalar(value)
    return "" if value is None else str(value)


def _setup_logging(output_dir: Path) -> Path:
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "single_node_build.log"
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    logger.add(log_path, level="DEBUG", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    return log_path


def _get_s3_secret(config: BuildTableConfig) -> str:
    secret = config.s3_secret_access_key or os.environ.get("MINIO_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not secret:
        raise ValueError("Missing S3 secret. Set MINIO_SECRET_ACCESS_KEY or pass --s3-secret-access-key.")
    return secret


def _make_io_config(config: BuildTableConfig) -> IOConfig:
    return IOConfig(
        s3=S3Config(
            region_name=config.s3_region,
            endpoint_url=config.s3_endpoint_url,
            key_id=config.s3_access_key_id,
            access_key=_get_s3_secret(config),
            use_ssl=config.s3_endpoint_url.startswith("https://"),
            force_virtual_addressing=False,
        )
    )


def _sanitize_s3_path_part(value: Any) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value))
    return safe.strip("_") or "unknown"


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI must start with s3://, got: {uri}")
    rest = uri[5:]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"S3 URI is missing bucket: {uri}")
    return bucket, prefix.strip("/")


def _resolve_s3_output_uri(config: BuildTableConfig, run_id: str) -> Optional[str]:
    if config.skip_s3_upload or not config.s3_output_root:
        return None
    root = config.s3_output_root.rstrip("/")
    db_part = _sanitize_s3_path_part(config.db_id)
    table_part = _sanitize_s3_path_part(config.table_id)
    run_part = _sanitize_s3_path_part(run_id)
    return f"{root}/{db_part}/{table_part}/{run_part}"


def _make_s3_client(config: BuildTableConfig) -> Any:
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint_url,
        region_name=config.s3_region,
        aws_access_key_id=config.s3_access_key_id,
        aws_secret_access_key=_get_s3_secret(config),
        config=BotoConfig(s3={"addressing_style": "path"}),
    )


def _upload_path_to_s3(client: Any, bucket: str, prefix: str, local_root: Path, path: Path) -> Tuple[str, int]:
    rel = path.relative_to(local_root).as_posix()
    key = f"{prefix}/{rel}" if prefix else rel
    client.upload_file(str(path), bucket, key)
    return key, int(path.stat().st_size)


def _upload_paths_to_s3(paths: Iterable[Path], local_root: Path, s3_uri: str, config: BuildTableConfig) -> Dict[str, Any]:
    bucket, prefix = _parse_s3_uri(s3_uri)
    client = _make_s3_client(config)
    uploaded_keys: List[str] = []
    uploaded_bytes = 0
    for path in sorted(paths):
        if not path.is_file():
            continue
        key, size = _upload_path_to_s3(client, bucket, prefix, local_root, path)
        uploaded_keys.append(key)
        uploaded_bytes += size
    return {"uploaded_keys": uploaded_keys, "uploaded_bytes": uploaded_bytes}


def _upload_output_to_s3(output_dir: Path, s3_uri: str, config: BuildTableConfig) -> Dict[str, Any]:
    files = [path for path in sorted(output_dir.rglob("*")) if path.is_file()]
    logger.info("Uploading {} output files to {}", len(files), s3_uri)
    upload = _upload_paths_to_s3(files, output_dir, s3_uri, config)
    logger.info("Uploaded {} files to {}", len(upload["uploaded_keys"]), s3_uri)
    return {
        "s3_upload_status": "success",
        "s3_output_uri": s3_uri,
        "s3_uploaded_files": len(upload["uploaded_keys"]),
        "s3_uploaded_bytes": upload["uploaded_bytes"],
    }


def _load_sample(config: BuildTableConfig) -> Tuple[Any, str, List[str], pd.DataFrame]:
    logger.info("Reading parquet with Daft: {}", config.parquet_uri)
    io_config = _make_io_config(config)
    df = daft.read_parquet(config.parquet_uri, io_config=io_config)
    schema = df.schema()
    schema_text = str(schema)
    column_names = list(df.column_names)
    logger.info("Schema loaded: {} columns", len(column_names))
    sample_rows = max(1, int(config.sample_rows))
    logger.info("Collecting sample rows with Daft limit({})", sample_rows)
    sample_pdf = df.limit(sample_rows).to_pandas()
    logger.info("Sample collected: {} rows x {} columns", len(sample_pdf), len(sample_pdf.columns))
    return df, schema_text, column_names, sample_pdf


def _profile_columns(
    sample_pdf: pd.DataFrame,
    column_names: List[str],
    max_columns: int,
    max_enum_values: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Tuple[str, int]]]]:
    selected = column_names[:max_columns] if max_columns and max_columns > 0 else column_names
    profiles: List[Dict[str, Any]] = []
    enum_values: Dict[str, List[Tuple[str, int]]] = {}
    total_rows = int(len(sample_pdf))

    for col in selected:
        if col not in sample_pdf.columns:
            continue
        series = sample_pdf[col]
        non_null = series.dropna().map(_safe_str)
        counts = non_null.value_counts(dropna=True)
        top_pairs = [(str(k), int(v)) for k, v in counts.head(max_enum_values).items() if str(k) != ""]
        bot_pairs = [(str(k), int(v)) for k, v in counts.tail(max_enum_values).items() if str(k) != ""]
        dtype = str(series.dtype)
        profile = {
            "column_id": col,
            "dtype": dtype,
            "total_rows": total_rows,
            "distinct_count": int(non_null.nunique(dropna=True)),
            "null_count": int(series.isna().sum()),
            "top_enums": [k for k, _ in top_pairs],
            "bot_enums": [k for k, _ in bot_pairs],
        }
        profiles.append(profile)
        enum_values[col] = top_pairs
    return profiles, enum_values


def _is_enum_candidate(profile: Dict[str, Any]) -> bool:
    dtype = profile["dtype"].lower()
    if any(t in dtype for t in ("object", "string", "category", "bool")):
        return True
    return profile["distinct_count"] <= 50


def _ensure_type(d: Dict[str, Any]) -> Dict[str, Any]:
    if "type" not in d:
        name = str(d.get("name", ""))
        for prefix, type_name in UKFT_TYPES.items():
            if name.startswith(prefix):
                d["type"] = type_name
                break
        if "type" not in d:
            d["type"] = "db-enum"
    d.setdefault("creator", "rubiksql_lake.phase0")
    d.setdefault("owner", "rubiksql_lake")
    d.setdefault("workspace", "phase0")
    d.setdefault("collection", "rubiksql")
    return d


def _build_ukft_dicts(
    config: BuildTableConfig,
    column_profiles: List[Dict[str, Any]],
    enum_values: Dict[str, List[Tuple[str, int]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    column_ids = [p["column_id"] for p in column_profiles]
    total_rows = column_profiles[0]["total_rows"] if column_profiles else 0
    ukfts: List[Dict[str, Any]] = []

    ukfts.append(_ensure_type(build_database_ukft(config.db_id, [config.table_id], len(column_ids))))
    ukfts.append(_ensure_type(build_table_ukft(config.db_id, config.table_id, column_ids, total_rows)))

    for profile in column_profiles:
        ukfts.append(
            _ensure_type(
                build_column_ukft(
                    db_id=config.db_id,
                    table_id=config.table_id,
                    column_id=profile["column_id"],
                    dtype=profile["dtype"],
                    dtype_anno=None,
                    total_rows=profile["total_rows"],
                    distinct_count=profile["distinct_count"],
                    null_count=profile["null_count"],
                    top_enums=profile["top_enums"],
                    bot_enums=profile["bot_enums"],
                )
            )
        )

    enum_column_count = 0
    enum_count = 0
    for profile in column_profiles:
        if config.max_enum_columns and enum_column_count >= config.max_enum_columns:
            break
        if not _is_enum_candidate(profile):
            continue
        col = profile["column_id"]
        pairs = enum_values.get(col, [])[: config.max_enum_values]
        if not pairs:
            continue
        enum_column_count += 1
        for enum_value, freq in pairs:
            ukfts.append(_ensure_type(build_enum_ukft(config.db_id, config.table_id, col, enum_value, freq)))
            enum_count += 1

    counts = {
        "database_ukfts": 1,
        "table_ukfts": 1,
        "column_ukfts": len(column_profiles),
        "enum_ukfts": enum_count,
        "enum_columns": enum_column_count,
        "total_ukfts": len(ukfts),
    }
    return ukfts, counts


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")


def _deserialize_ukfts(ukft_dicts: List[Dict[str, Any]]) -> List[Any]:
    import rubiksql  # noqa: F401 - imports/registers RubikSQL UKFT classes
    from ahvn.ukf.base import BaseUKF

    objects = []
    for d in ukft_dicts:
        objects.append(BaseUKF.from_dict(d))
    return objects


def _lance_row_count(lance_dir: Path, table_name: str) -> int:
    try:
        import lancedb

        db = lancedb.connect(str(lance_dir))
        table_response = db.list_tables()
        table_names = getattr(table_response, "tables", table_response)
        if table_name not in table_names:
            return 0
        return int(db.open_table(table_name).count_rows())
    except Exception:
        logger.exception("Failed to read LanceDB row count from {}", lance_dir)
        return 0


def _write_agentheaven_kb(
    ukft_objects: List[Any],
    output_dir: Path,
    batch_size: int,
    skip_embedding: bool,
) -> Dict[str, Any]:
    from ahvn.klbase.base import KLBase
    from ahvn.klengine.vector_engine import VectorKLEngine
    from ahvn.klstore.db_store import DatabaseKLStore
    from ahvn.klstore.vdb_store import VectorKLStore
    from ahvn.utils.llm import LLM

    sqlite_dir = output_dir / "sqlite"
    lance_dir = output_dir / "lance"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    lance_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = sqlite_dir / "main.db"

    logger.info("Initializing SQLite KL store: {}", sqlite_path)
    main_storage = DatabaseKLStore(
        provider="sqlite",
        database=str(sqlite_path),
        name="main",
    )

    kb = KLBase(name="phase0_single_node")
    kb.add_storage(main_storage, name="main", main=True)

    enum_objects = [u for u in ukft_objects if getattr(u, "type", None) == "db-enum"]
    result = {
        "sqlite_path": str(sqlite_path),
        "sqlite_exists": False,
        "lance_path": str(lance_dir),
        "lance_exists": False,
        "embedding_count": 0,
        "embedding_time_sec": 0.0,
        "embedding_error": None,
    }

    if skip_embedding:
        logger.info("Embedding skipped by --skip-embedding; writing SQLite only")
        kb.batch_upsert(ukft_objects, storages=["main"], engines=[])
        result["sqlite_exists"] = sqlite_path.exists()
        result["lance_row_count"] = _lance_row_count(lance_dir, "vec_enums")
        result["lance_exists"] = result["lance_row_count"] > 0
        return result

    def enum_encoder(kl: Any) -> str:
        return str(kl.get("enum", getattr(kl, "content", ""))).strip().lower()

    def query_encoder(query: Any) -> str:
        return str(query).strip().lower()

    logger.info("Initializing LanceDB vector KL store: {}", lance_dir)
    phase0_ollama_api_base = os.environ.get("PHASE0_OLLAMA_API_BASE", "http://localhost:11434")
    embedder = LLM(preset="embedder", api_base=phase0_ollama_api_base)
    logger.info("Using AgentHeaven embedder preset with api_base={}", phase0_ollama_api_base)
    vector_storage = VectorKLStore(
        collection="vec_enums",
        provider="lancedb",
        uri=str(lance_dir),
        encoder=(enum_encoder, query_encoder),
        embedder=embedder,
        condition=lambda kl: getattr(kl, "type", None) == "db-enum",
        name="vec_enums",
    )
    vec_engine = VectorKLEngine(
        storage=vector_storage,
        inplace=True,
        condition=lambda kl: getattr(kl, "type", None) == "db-enum",
        name="vec_enums",
    )
    kb.add_storage(vector_storage, name="vec_enums", main=False)
    kb.add_engine(vec_engine, name="vec_enums")

    logger.info("Upserting {} UKFT objects; {} enum objects require embedding", len(ukft_objects), len(enum_objects))
    t0 = time.time()
    kb.batch_upsert(ukft_objects, batch_size=batch_size)
    result["embedding_time_sec"] = round(time.time() - t0, 3)
    result["embedding_count"] = len(enum_objects)
    result["sqlite_exists"] = sqlite_path.exists()
    result["lance_row_count"] = _lance_row_count(lance_dir, "vec_enums")
    result["lance_exists"] = result["lance_row_count"] >= result["embedding_count"] if result["embedding_count"] else result["lance_row_count"] > 0
    if result["embedding_count"] and result["lance_row_count"] < result["embedding_count"]:
        raise RuntimeError(
            f"LanceDB row count {result['lance_row_count']} is smaller than embedding count {result['embedding_count']}"
        )
    return result


def _write_report(output_dir: Path, summary: Dict[str, Any]) -> Path:
    report_path = output_dir / "PHASE0_SINGLE_NODE_VALIDATION_REPORT.md"
    lines = [
        "# Phase0 Single Node Validation Report",
        "",
        f"- Status: {summary.get('status')}",
        f"- Database: {summary.get('db_id')}",
        f"- Table: {summary.get('table_id')}",
        f"- Parquet: `{summary.get('parquet_uri')}`",
        f"- Run ID: {summary.get('run_id')}",
        f"- Output: `{output_dir}`",
        f"- S3 upload: {summary.get('s3_upload_status')}",
        f"- S3 output: `{summary.get('s3_output_uri')}`",
        f"- Schema columns: {summary.get('schema_column_count')}",
        f"- Profiled columns: {summary.get('profiled_column_count')}",
        f"- Sample rows: {summary.get('sample_rows_actual')}",
        f"- UKFT count: {summary.get('ukft_count')}",
        f"- Embedding count: {summary.get('embedding_count')}",
        f"- SQLite generated: {summary.get('sqlite_exists')}",
        f"- LanceDB generated: {summary.get('lance_exists')}",
        f"- Log file: `{summary.get('log_path')}`",
        "",
        "## Notes",
        "",
        summary.get("notes", ""),
    ]
    if summary.get("error"):
        lines.extend(["", "## Error", "", "```", str(summary["error"]), "```"])
    if summary.get("embedding_error"):
        lines.extend(["", "## Embedding Error", "", "```", str(summary["embedding_error"]), "```"])
    if summary.get("s3_upload_error"):
        lines.extend(["", "## S3 Upload Error", "", "```", str(summary["s3_upload_error"]), "```"])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def build_table(config: BuildTableConfig) -> Dict[str, Any]:
    output_dir = Path(config.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = _setup_logging(output_dir)
    run_id = config.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    s3_output_uri = _resolve_s3_output_uri(config, run_id)
    logger.info("Phase0 single-node validation started")
    safe_config = asdict(config) | {"s3_secret_access_key": "***" if config.s3_secret_access_key else None, "run_id": run_id}
    logger.info("Config: {}", safe_config)

    summary: Dict[str, Any] = {
        "status": "failed",
        "db_id": config.db_id,
        "table_id": config.table_id,
        "run_id": run_id,
        "parquet_uri": config.parquet_uri,
        "output_dir": str(output_dir),
        "log_path": str(log_path),
        "s3_upload_requested": bool(s3_output_uri),
        "s3_output_uri": s3_output_uri,
        "s3_upload_status": "skipped" if not s3_output_uri else "pending",
        "s3_upload_error": None,
        "error": None,
        "notes": "Phase0 uses one process on serve00 and does not start Ray, RayCluster, or Daft distributed scheduling.",
    }

    try:
        _, schema_text, column_names, sample_pdf = _load_sample(config)
        selected_columns = column_names[: config.max_columns] if config.max_columns and config.max_columns > 0 else column_names
        column_profiles, enum_values = _profile_columns(sample_pdf, selected_columns, config.max_columns, config.max_enum_values)
        ukft_dicts, counts = _build_ukft_dicts(config, column_profiles, enum_values)
        ukft_jsonl = output_dir / "ukfts.jsonl"
        _write_jsonl(ukft_jsonl, ukft_dicts)
        logger.info("Wrote UKFT JSONL: {}", ukft_jsonl)

        logger.info("Deserializing UKFT dicts through AgentHeaven BaseUKF/RubikSQL registry")
        ukft_objects = _deserialize_ukfts(ukft_dicts)
        class_counts: Dict[str, int] = {}
        for obj in ukft_objects:
            class_counts[obj.__class__.__name__] = class_counts.get(obj.__class__.__name__, 0) + 1
        logger.info("UKFT object class counts: {}", class_counts)

        kb_result = _write_agentheaven_kb(ukft_objects, output_dir, config.embedding_batch_size, config.skip_embedding)

        summary.update(
            {
                "status": "success",
                "schema_text": schema_text,
                "schema_column_count": len(column_names),
                "profiled_column_count": len(column_profiles),
                "sample_rows_actual": int(len(sample_pdf)),
                "ukft_count": len(ukft_dicts),
                "ukft_counts": counts,
                "ukft_class_counts": class_counts,
                "ukft_jsonl": str(ukft_jsonl),
            }
        )
        summary.update(kb_result)
        logger.info("Phase0 completed with status=success")
    except Exception as exc:
        summary["error"] = repr(exc)
        logger.exception("Phase0 failed")
    finally:
        summary_path = output_dir / "phase0_summary.json"
        summary["summary_path"] = str(summary_path)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
        report_path = _write_report(output_dir, summary)
        summary["report_path"] = str(report_path)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")

        if s3_output_uri:
            try:
                upload_result = _upload_output_to_s3(output_dir, s3_output_uri, config)
                summary.update(upload_result)
                summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
                report_path = _write_report(output_dir, summary)
                summary["report_path"] = str(report_path)
                summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
                _upload_paths_to_s3([summary_path, report_path], output_dir, s3_output_uri, config)
            except Exception as upload_exc:
                summary["status"] = "failed"
                summary["s3_upload_status"] = "failed"
                summary["s3_upload_error"] = repr(upload_exc)
                summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
                report_path = _write_report(output_dir, summary)
                summary["report_path"] = str(report_path)
                logger.exception("S3 upload failed")

        logger.info("Summary written: {}", summary_path)
        logger.info("Report written: {}", report_path)
        if summary.get("s3_output_uri"):
            logger.info("S3 output URI: {}", summary.get("s3_output_uri"))
    return summary


def parse_args() -> BuildTableConfig:
    parser = argparse.ArgumentParser(description="Phase0 single-node RubikSQL lake build validation")
    parser.add_argument("--parquet-uri", default=BuildTableConfig.parquet_uri)
    parser.add_argument("--db-id", default=BuildTableConfig.db_id)
    parser.add_argument("--table-id", default=BuildTableConfig.table_id)
    parser.add_argument("--output-dir", default=BuildTableConfig.output_dir)
    parser.add_argument("--s3-endpoint-url", default=BuildTableConfig.s3_endpoint_url)
    parser.add_argument("--s3-region", default=BuildTableConfig.s3_region)
    parser.add_argument("--s3-access-key-id", default=BuildTableConfig.s3_access_key_id)
    parser.add_argument("--s3-secret-access-key", default=None)
    parser.add_argument("--s3-output-root", default=BuildTableConfig.s3_output_root)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--skip-s3-upload", action="store_true")
    parser.add_argument("--sample-rows", type=int, default=BuildTableConfig.sample_rows)
    parser.add_argument("--max-columns", type=int, default=BuildTableConfig.max_columns)
    parser.add_argument("--max-enum-columns", type=int, default=BuildTableConfig.max_enum_columns)
    parser.add_argument("--max-enum-values", type=int, default=BuildTableConfig.max_enum_values)
    parser.add_argument("--embedding-batch-size", type=int, default=BuildTableConfig.embedding_batch_size)
    parser.add_argument("--skip-embedding", action="store_true")
    args = parser.parse_args()
    return BuildTableConfig(
        parquet_uri=args.parquet_uri,
        db_id=args.db_id,
        table_id=args.table_id,
        output_dir=args.output_dir,
        s3_endpoint_url=args.s3_endpoint_url,
        s3_region=args.s3_region,
        s3_access_key_id=args.s3_access_key_id,
        s3_secret_access_key=args.s3_secret_access_key,
        s3_output_root=args.s3_output_root,
        run_id=args.run_id,
        skip_s3_upload=args.skip_s3_upload,
        sample_rows=args.sample_rows,
        max_columns=args.max_columns,
        max_enum_columns=args.max_enum_columns,
        max_enum_values=args.max_enum_values,
        embedding_batch_size=args.embedding_batch_size,
        skip_embedding=args.skip_embedding,
    )


if __name__ == "__main__":
    result = build_table(parse_args())
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_json_default))
    raise SystemExit(0 if result.get("status") == "success" else 1)
