"""Ray Worker: build UKFT shards and run AgentHeaven native embedding.

This module contains the function that runs on each Ray worker node.
It reads profile data, constructs UKFT objects, writes UKFT JSONL files,
and uses AgentHeaven's VectorKLEngine + LLM.embed() for embedding.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

from loguru import logger

from .ukft_builder import (
    build_database_ukft,
    build_table_ukft,
    build_column_ukft,
    build_enum_ukft,
)


def build_ukft_shard(task_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build a single UKFT shard with AgentHeaven native embedding.

    This is the core worker function. It is designed to be called as a
    Ray remote function (ray.remote).

    What it does:
    1. Reads profile data (column_profile.parquet + enum_candidates.parquet)
    2. Filters to only the tables assigned to this shard
    3. Constructs DatabaseUKFT, TableUKFT, ColumnUKFT, EnumUKFT objects
    4. Writes UKFT shard as JSONL file
    5. Initializes a shard-local KB (SQLite + LanceDB)
    6. Runs AgentHeaven batch_upsert → triggers VectorKLEngine embedding
    7. Writes shard vectorstore
    8. Returns build summary

    Args:
        task_spec: Dictionary with shard configuration:
            {
                "shard_id": "shard_id=00001",
                "tables": [("sales", "orders"), ...],
                "run_dir": "/mnt/shared/.../20260624_001/",
                "profile_dir": "/mnt/shared/.../20260624_001/profile/",
                "output_shard_dir": "/mnt/shared/.../ukft_shards/shard_id=00001/",
                "output_kb_dir": "/mnt/shared/.../shard_kb/shard_id=00001/",
            }

    Returns:
        {
            "shard_id": str,
            "status": "success" | "failed",
            "ukft_count": int,
            "embedding_count": int,
            "embedding_time_sec": float,
            "error": str | None,
            "output_paths": { ... },
        }
    """
    shard_id = task_spec["shard_id"]
    tables: List[Tuple[str, str]] = task_spec["tables"]
    profile_dir = Path(task_spec["profile_dir"])
    output_shard_dir = Path(task_spec["output_shard_dir"])
    output_kb_dir = Path(task_spec["output_kb_dir"])

    # Create output directories
    output_shard_dir.mkdir(parents=True, exist_ok=True)
    output_kb_dir.mkdir(parents=True, exist_ok=True)

    # Setup per-worker logging
    log_dir = output_shard_dir.parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / f"worker_{shard_id}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        level="DEBUG",
        rotation="100 MB",
    )

    logger.info(f"[{shard_id}] Starting build for {len(tables)} tables")

    try:
        # ================================================================
        # Step 1: Read and filter profile data for this shard
        # ================================================================
        logger.info(f"[{shard_id}] Reading profile data...")

        col_profiles = _read_column_profiles(profile_dir, tables)
        enum_candidates = _read_enum_candidates(profile_dir, tables)

        logger.info(
            f"[{shard_id}] Loaded {len(col_profiles)} column profiles, "
            f"{len(enum_candidates)} enum candidates"
        )

        # ================================================================
        # Step 2: Build UKFT dictionaries
        # ================================================================
        logger.info(f"[{shard_id}] Building UKFT objects...")

        all_ukft_dicts = _build_all_ukfts(
            tables, col_profiles, enum_candidates
        )

        ukft_count = len(all_ukft_dicts)
        logger.info(f"[{shard_id}] Built {ukft_count} UKFT objects")

        # ================================================================
        # Step 3: Write UKFT JSONL file
        # ================================================================
        logger.info(f"[{shard_id}] Writing UKFT shard file...")

        ukft_jsonl_path = output_shard_dir / "ukfts.jsonl"
        with open(ukft_jsonl_path, "w", encoding="utf-8") as f:
            for d in all_ukft_dicts:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")

        logger.info(f"[{shard_id}] Wrote {ukft_count} UKFTs to {ukft_jsonl_path}")

        # ================================================================
        # Step 4: Initialize shard-local KB and run AgentHeaven embedding
        # ================================================================
        logger.info(f"[{shard_id}] Initializing shard-local KB...")

        embedding_count = 0
        embedding_time_sec = 0.0
        main_db_path = output_kb_dir / "main.db"
        vec_db_path = output_kb_dir / "vec"

        try:
            from ahvn.ukf.base import BaseUKF
            from ahvn.klbase.base import KLBase
            from ahvn.klstore.db_store import DatabaseKLStore
            from ahvn.klstore.vdb_store import VectorKLStore
            from ahvn.klengine.vector_engine import VectorKLEngine
            from ahvn.adapter.db import DbUKFAdapter

            # 4a. Create SQLite main storage
            main_storage = DatabaseKLStore(
                provider="sqlite",
                database=f"sqlite:///{main_db_path}",
                table="ukf_records",
                adapter=DbUKFAdapter(),
            )
            main_storage._init()

            # 4b. Create LanceDB vector storage
            vec_db_path.mkdir(parents=True, exist_ok=True)
            vector_storage = VectorKLStore(
                provider="lancedb",
                uri=str(vec_db_path),
                table_name="vec_enums",
                encoder=None,
                embedder="embedder",
            )

            # 4c. Create KLBase with engines
            kb = KLBase(name=f"shard_{shard_id}")
            kb.add_storage(main_storage, name="main", main=True)

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
                condition=lambda kl: getattr(kl, 'type', None) == 'db-enum',
            )
            kb.add_engine(vec_engine, name="vec-enums")

            # 4d. Deserialize UKFT dicts to BaseUKF objects
            ukft_objects = []
            for d in all_ukft_dicts:
                try:
                    ukft = BaseUKF.from_dict(d)
                    ukft_objects.append(ukft)
                except Exception as e:
                    logger.warning(f"[{shard_id}] Failed to deserialize UKFT: {e}")

            logger.info(
                f"[{shard_id}] KB ready, upserting {len(ukft_objects)} UKFTs "
                f"with AgentHeaven native embedding..."
            )

            # 4e. Batch upsert → triggers embedding
            t_embed_start = time.time()
            kb.batch_upsert(ukft_objects, batch_size=256)
            embedding_time_sec = time.time() - t_embed_start

            embedding_count = sum(
                1 for u in ukft_objects
                if getattr(u, 'type', None) == 'db-enum'
            )

            logger.info(
                f"[{shard_id}] Embedding complete: {embedding_count} enums "
                f"in {embedding_time_sec:.1f}s"
            )

        except ImportError as e:
            logger.warning(
                f"[{shard_id}] AgentHeaven not available ({e}). "
                f"Skipping embedding. UKFT JSONL written to disk."
            )
            embedding_count = 0
            embedding_time_sec = 0.0

        # ================================================================
        # Step 5: Write shard metadata
        # ================================================================
        shard_meta = {
            "shard_id": shard_id,
            "tables": tables,
            "ukft_count": ukft_count,
            "embedding_count": embedding_count,
            "embedding_time_sec": embedding_time_sec,
            "timestamp": time.time(),
        }
        with open(output_shard_dir / "shard_meta.json", "w") as f:
            json.dump(shard_meta, f, indent=2, ensure_ascii=False)

        return {
            "shard_id": shard_id,
            "status": "success",
            "ukft_count": ukft_count,
            "embedding_count": embedding_count,
            "embedding_time_sec": embedding_time_sec,
            "error": None,
            "output_paths": {
                "ukft_jsonl": str(ukft_jsonl_path),
                "shard_meta": str(output_shard_dir / "shard_meta.json"),
                "shard_kb_main": str(main_db_path),
                "shard_kb_vec": str(vec_db_path),
            },
        }

    except Exception as e:
        logger.exception(f"[{shard_id}] Build failed!")
        return {
            "shard_id": shard_id,
            "status": "failed",
            "ukft_count": 0,
            "embedding_count": 0,
            "embedding_time_sec": 0.0,
            "error": str(e),
            "output_paths": {},
        }


def _read_column_profiles(
    profile_dir: Path,
    tables: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """Read column_profile.parquet and filter to specified tables."""
    profile_path = profile_dir / "column_profile.parquet"
    if not profile_path.exists():
        logger.warning(f"Column profile not found: {profile_path}")
        return []

    import pyarrow.parquet as pq
    table = pq.read_table(str(profile_path))
    df = table.to_pandas()

    # Filter to only our tables
    mask = df.apply(
        lambda row: (row["db_id"], row["table_id"]) in tables,
        axis=1,
    )
    filtered = df[mask]

    return filtered.to_dict("records")


def _read_enum_candidates(
    profile_dir: Path,
    tables: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """Read enum_candidates.parquet and filter to specified tables."""
    candidates_path = profile_dir / "enum_candidates.parquet"
    if not candidates_path.exists():
        logger.warning(f"Enum candidates not found: {candidates_path}")
        return []

    import pyarrow.parquet as pq
    table = pq.read_table(str(candidates_path))
    df = table.to_pandas()

    mask = df.apply(
        lambda row: (row["db_id"], row["table_id"]) in tables,
        axis=1,
    )
    filtered = df[mask]

    return filtered.to_dict("records")


def _build_all_ukfts(
    tables: List[Tuple[str, str]],
    col_profiles: List[Dict[str, Any]],
    enum_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build all UKFT dictionaries for the assigned tables.

    Constructs DatabaseUKFT, TableUKFT, ColumnUKFT, and EnumUKFT dictionaries
    from profile data without requiring a live database connection.

    Args:
        tables: List of (db_id, table_id) tuples assigned to this shard.
        col_profiles: List of column profile dicts.
        enum_candidates: List of enum candidate dicts.

    Returns:
        List of UKFT-compatible dictionaries.
    """
    all_ukfts = []
    seen_dbs = set()
    seen_tables = set()

    # Organize column profiles by (db_id, table_id) -> list of col profiles
    table_cols: Dict[Tuple[str, str], List[Dict]] = {}
    for cp in col_profiles:
        key = (cp["db_id"], cp["table_id"])
        if key not in table_cols:
            table_cols[key] = []
        table_cols[key].append(cp)

    # Organize enum candidates by (db_id, table_id, column_id) -> list of (value, freq)
    col_enums: Dict[Tuple[str, str, str], List[Tuple[str, int]]] = {}
    for ec in enum_candidates:
        key = (ec["db_id"], ec["table_id"], ec["column_id"])
        if key not in col_enums:
            col_enums[key] = []
        col_enums[key].append((str(ec["enum_value"]), int(ec.get("freq", 0))))

    # Count total columns per db for DatabaseUKFT
    db_total_cols: Dict[str, int] = {}
    for key, cols in table_cols.items():
        db_id = key[0]
        db_total_cols[db_id] = db_total_cols.get(db_id, 0) + len(cols)

    # Build UKFTs for each assigned table
    for db_id, table_id in tables:
        key = (db_id, table_id)
        cols = table_cols.get(key, [])

        if not cols:
            logger.warning(f"No column profiles found for {db_id}.{table_id}")
            continue

        # DatabaseUKFT (once per db)
        if db_id not in seen_dbs:
            db_ukft = build_database_ukft(
                db_id=db_id,
                table_ids=list(set(t for t, _ in table_cols if t == db_id)),
                total_columns=db_total_cols.get(db_id, 0),
            )
            all_ukfts.append(db_ukft)
            seen_dbs.add(db_id)

        # TableUKFT (once per table)
        table_full_key = f"{db_id}.{table_id}"
        if table_full_key not in seen_tables:
            total_rows = cols[0].get("total_count", 0) if cols else 0
            tab_ukft = build_table_ukft(
                db_id=db_id,
                table_id=table_id,
                column_ids=[c["column_id"] for c in cols],
                total_rows=total_rows,
            )
            all_ukfts.append(tab_ukft)
            seen_tables.add(table_full_key)

        # ColumnUKFT + EnumUKFT for each column
        for col_info in cols:
            col_id = col_info["column_id"]
            dtype = col_info.get("dtype", "string")
            total_rows = col_info.get("total_count", 0)
            distinct_count = col_info.get("distinct_count", 0)
            null_count = col_info.get("null_count", 0)
            sample_values = col_info.get("sample_values", []) or []

            top_enums = sample_values[:20]
            bot_enums = sample_values[-20:] if len(sample_values) > 20 else []

            col_ukft = build_column_ukft(
                db_id=db_id,
                table_id=table_id,
                column_id=col_id,
                dtype=dtype,
                dtype_anno=None,
                total_rows=total_rows,
                distinct_count=distinct_count,
                null_count=null_count,
                top_enums=top_enums,
                bot_enums=bot_enums,
            )
            all_ukfts.append(col_ukft)

            # EnumUKFT for each enum value
            enum_key = (db_id, table_id, col_id)
            if enum_key in col_enums:
                for enum_val, freq in col_enums[enum_key]:
                    enum_ukft = build_enum_ukft(
                        db_id=db_id,
                        table_id=table_id,
                        column_id=col_id,
                        enum_value=enum_val,
                        freq=freq,
                    )
                    all_ukfts.append(enum_ukft)

    return all_ukfts
