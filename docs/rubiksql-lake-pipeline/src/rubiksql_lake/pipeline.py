"""Phase 1 Pipeline: main orchestration logic.

Coordinates the entire Phase 1 build pipeline:
1. Load manifest
2. Run Daft profiling (on driver)
3. Partition build tasks
4. Dispatch to Ray workers
5. Collect results
6. Merge into global KB
"""

import os
import sys
import json
import time
import shutil
from pathlib import Path
from typing import Dict, Any, List, Tuple

from loguru import logger

from .spec import load_manifest, LakeManifest
from .profiling import run_profiling


def run_phase1_pipeline(manifest_path: str) -> Dict[str, Any]:
    """Run the complete Phase 1 pipeline.

    This is the main entry point that runs on the driver (server00).

    Args:
        manifest_path: Path to lake_manifest.yaml.

    Returns:
        Pipeline result dict with run_id, status, and statistics.
    """
    t_pipeline_start = time.time()

    # ================================================================
    # Step 0: Load manifest
    # ================================================================
    logger.info("=" * 70)
    logger.info("PHASE 1 PIPELINE STARTING")
    logger.info("=" * 70)

    manifest = load_manifest(manifest_path)
    run_id = manifest.run.run_id
    if run_id == "auto":
        run_id = time.strftime("%Y%m%d_%H%M%S")

    output_base = manifest.run.output_base
    run_dir = Path(output_base) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Run ID:  {run_id}")
    logger.info(f"Output:  {run_dir}")
    logger.info(f"Runner:  {manifest.daft.runner}")
    logger.info(f"DBs:     {len(manifest.databases)}")

    # Save manifest copy in run directory
    shutil.copy(manifest_path, run_dir / "manifest.yaml")

    # ================================================================
    # Step 1: Daft Profiling (runs on driver/server00)
    # ================================================================
    logger.info("=" * 70)
    logger.info("STEP 1: DAFT PROFILING")
    logger.info("=" * 70)

    t0 = time.time()
    profile_result = run_profiling(manifest, run_dir)
    t1 = time.time()

    logger.info(f"Profiling time: {t1 - t0:.1f}s")
    logger.info(f"  Tables:        {profile_result['table_count']}")
    logger.info(f"  Columns:       {profile_result['column_count']}")
    logger.info(f"  Enum candidates: {profile_result['enum_candidate_count']}")

    if profile_result["enum_candidate_count"] == 0:
        logger.warning("No enum candidates found. Check manifest enum_policy settings.")
        return {
            "status": "no_data",
            "run_id": run_id,
            "profile_result": profile_result,
        }

    # ================================================================
    # Step 2: Partition build tasks
    # ================================================================
    logger.info("=" * 70)
    logger.info("STEP 2: TASK PARTITIONING")
    logger.info("=" * 70)

    # Get unique (db_id, table_id) pairs from enum candidates
    import pyarrow.parquet as pq

    candidates_path = profile_result["enum_candidates_path"]
    if not candidates_path:
        return {"status": "no_data", "run_id": run_id}

    candidates_table = pq.read_table(candidates_path)
    candidates_df = candidates_table.to_pandas()
    unique_tables = candidates_df[["db_id", "table_id"]].drop_duplicates()

    # Partition across workers
    num_workers = 3
    shards: List[List[Tuple[str, str]]] = [[] for _ in range(num_workers)]

    for _, row in unique_tables.iterrows():
        db_id = row["db_id"]
        table_id = row["table_id"]
        shard_idx = abs(hash(f"{db_id}.{table_id}")) % num_workers
        shards[shard_idx].append((db_id, table_id))

    # Filter empty shards
    active_shards = [(i, s) for i, s in enumerate(shards) if s]

    logger.info(f"Partitioned into {len(active_shards)} active shards:")
    for shard_idx, tables in active_shards:
        logger.info(f"  Shard {shard_idx}: {len(tables)} tables")

    # ================================================================
    # Step 3: Ray dispatch
    # ================================================================
    logger.info("=" * 70)
    logger.info("STEP 3: RAY DISPATCH")
    logger.info("=" * 70)

    import ray

    if not ray.is_initialized():
        ray.init(address="auto", ignore_reinit_error=True)

    cluster_info = ray.cluster_resources()
    logger.info(f"Ray cluster resources: {cluster_info}")

    # Import worker and create Ray remote
    from .worker import build_ukft_shard

    BuildWorker = ray.remote(num_cpus=2)(build_ukft_shard)

    # Submit all tasks
    futures = []
    for shard_idx, tables in active_shards:
        task_spec = {
            "shard_id": f"shard_id={shard_idx:05d}",
            "tables": tables,
            "run_dir": str(run_dir),
            "profile_dir": str(run_dir / "profile"),
            "output_shard_dir": str(
                run_dir / "ukft_shards" / f"shard_id={shard_idx:05d}"
            ),
            "output_kb_dir": str(
                run_dir / "shard_kb" / f"shard_id={shard_idx:05d}"
            ),
        }
        future = BuildWorker.remote(task_spec)
        futures.append((shard_idx, future))

    logger.info(f"Submitted {len(futures)} Ray tasks")

    # ================================================================
    # Step 4: Wait for completion
    # ================================================================
    logger.info("=" * 70)
    logger.info("STEP 4: WAITING FOR WORKERS")
    logger.info("=" * 70)

    t2 = time.time()

    # Gather results (with timeout per task: 1 hour)
    results = []
    for shard_idx, future in futures:
        try:
            result = ray.get(future, timeout=3600)
            results.append(result)

            if result["status"] == "success":
                logger.info(
                    f"  {result['shard_id']}: OK - "
                    f"{result['ukft_count']} UKFTs, "
                    f"{result['embedding_count']} embeddings "
                    f"({result['embedding_time_sec']:.1f}s)"
                )
            else:
                logger.error(
                    f"  {result['shard_id']}: FAILED - {result['error']}"
                )

        except ray.exceptions.GetTimeoutError:
            logger.error(f"  Shard {shard_idx}: TIMEOUT")
            results.append({
                "shard_id": f"shard_id={shard_idx:05d}",
                "status": "timeout",
                "ukft_count": 0,
                "embedding_count": 0,
                "embedding_time_sec": 0,
                "error": "Task timed out (>1 hour)",
                "output_paths": {},
            })
        except Exception as e:
            logger.error(f"  Shard {shard_idx}: ERROR - {e}")
            results.append({
                "shard_id": f"shard_id={shard_idx:05d}",
                "status": "failed",
                "ukft_count": 0,
                "embedding_count": 0,
                "embedding_time_sec": 0,
                "error": str(e),
                "output_paths": {},
            })

    t3 = time.time()
    build_time = t3 - t2

    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = len(results) - success_count
    total_ukfts = sum(r["ukft_count"] for r in results)
    total_embeds = sum(r["embedding_count"] for r in results)

    logger.info(f"Build time: {build_time:.1f}s")
    logger.info(f"  Success: {success_count}/{len(results)} shards")
    logger.info(f"  UKFTs:   {total_ukfts}")
    logger.info(f"  Embeds:  {total_embeds}")

    # ================================================================
    # Step 5: Merge into global KB
    # ================================================================
    if manifest.phase1.merge_after_build and success_count > 0:
        logger.info("=" * 70)
        logger.info("STEP 5: MERGING INTO GLOBAL KB")
        logger.info("=" * 70)

        from .merge import merge_shards_to_global

        kb_name = manifest.rubiksql.kb_name
        kb_root = manifest.rubiksql.kb_root

        merge_result = merge_shards_to_global(
            run_dir=run_dir,
            shard_results=results,
            kb_name=kb_name,
            kb_root=kb_root,
        )

        logger.info(f"Merge complete: {merge_result}")
    else:
        merge_result = {"skipped": True, "reason": "merge_after_build=False or no successful shards"}

    # ================================================================
    # Step 6: Save run metadata
    # ================================================================
    total_time = time.time() - t_pipeline_start

    run_meta = {
        "run_id": run_id,
        "manifest_path": manifest_path,
        "start_time": t_pipeline_start,
        "total_time_sec": total_time,
        "profiling_time_sec": t1 - t0,
        "build_time_sec": build_time,
        "profile_result": profile_result,
        "shard_results": [
            {
                "shard_id": r["shard_id"],
                "status": r["status"],
                "ukft_count": r["ukft_count"],
                "embedding_count": r["embedding_count"],
                "error": r["error"],
            }
            for r in results
        ],
        "merge_result": merge_result,
        "success_count": success_count,
        "failed_count": failed_count,
        "total_ukfts": total_ukfts,
        "total_embeddings": total_embeds,
    }

    with open(run_dir / "run_meta.json", "w") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False, default=str)

    # ================================================================
    # Final summary
    # ================================================================
    logger.info("=" * 70)
    logger.info("PHASE 1 PIPELINE COMPLETE")
    logger.info(f"  Run ID:      {run_id}")
    logger.info(f"  Total time:  {total_time:.1f}s")
    logger.info(f"  UKFTs:       {total_ukfts}")
    logger.info(f"  Embeddings:  {total_embeds}")
    logger.info(f"  Success:     {success_count}/{len(results)} shards")
    logger.info(f"  Output:      {run_dir}")
    logger.info(f"  Global KB:   {kb_root}/{kb_name}")
    logger.info("=" * 70)

    return run_meta
