"""Result collection and merge: combine shard outputs into global KB.

Runs on the driver (server00) after all Ray workers complete.
Reads each shard's UKFT JSONL and vectorstore, merges into a single
global knowledge base.
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


def merge_shards_to_global(
    run_dir: Path,
    shard_results: List[Dict[str, Any]],
    kb_name: str,
    kb_root: str,
) -> Dict[str, Any]:
    """Merge all shard outputs into a single global knowledge base.

    Three-phase merge:
    1. UKFT JSONL → deserialize → batch write to global SQLite
    2. Shard LanceDB → copy vectors to global LanceDB
    3. Create queryable global KB with engines

    Args:
        run_dir: Build run directory containing ukft_shards/ and shard_kb/.
        shard_results: List of result dicts from each Ray worker.
        kb_name: Name for the global knowledge base.
        kb_root: Root directory for knowledge base storage.

    Returns:
        Merge summary dict.
    """
    global_kb_dir = Path(kb_root) / kb_name
    global_kb_dir.mkdir(parents=True, exist_ok=True)

    ukft_shards_dir = run_dir / "ukft_shards"
    shard_kb_dir = run_dir / "shard_kb"

    t_start = time.time()

    # ================================================================
    # Phase 1: Merge UKFT JSONL → Global SQLite
    # ================================================================
    logger.info("[Merge] Phase 1: Merging UKFT shards → Global SQLite...")
    global_main_db = global_kb_dir / "main.db"

    total_merged = 0
    try:
        from ahvn.ukf.base import BaseUKF
        from ahvn.klstore.db_store import DatabaseKLStore
        from ahvn.adapter.db import DbUKFAdapter

        global_main = DatabaseKLStore(
            provider="sqlite",
            database=f"sqlite:///{global_main_db}",
            table="ukf_records",
            adapter=DbUKFAdapter(),
        )
        global_main._init()

        for result in shard_results:
            if result.get("status") != "success":
                continue

            shard_id = result["shard_id"]
            jsonl_path = ukft_shards_dir / shard_id / "ukfts.jsonl"

            if not jsonl_path.exists():
                logger.warning(f"  [Merge] Missing UKFT file: {jsonl_path}")
                continue

            # Read and deserialize UKFTs
            ukfts = []
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ukft_dict = json.loads(line)
                        ukft = BaseUKF.from_dict(ukft_dict)
                        ukfts.append(ukft)
                    except Exception as e:
                        logger.warning(f"  [Merge] Failed to parse UKFT: {e}")

            # Batch write to global storage
            if ukfts:
                global_main.batch_upsert(ukfts)
                total_merged += len(ukfts)
                logger.info(f"  [Merge] Merged {len(ukfts)} UKFTs from {shard_id}")

    except ImportError as e:
        logger.warning(f"  [Merge] AgentHeaven not available ({e}). Skipping SQLite merge.")
        total_merged = 0

    logger.info(f"  [Merge] Total UKFTs merged to SQLite: {total_merged}")

    # ================================================================
    # Phase 2: Merge Shard VectorStores → Global VectorStore
    # ================================================================
    logger.info("[Merge] Phase 2: Merging shard VectorStores → Global VectorStore...")
    global_vec_dir = global_kb_dir / "vec"
    global_vec_dir.mkdir(parents=True, exist_ok=True)

    total_vec_merged = 0
    try:
        import lancedb

        first_write = True
        for result in shard_results:
            if result.get("status") != "success":
                continue

            shard_id = result["shard_id"]
            shard_vec_path = shard_kb_dir / shard_id / "vec"

            if not shard_vec_path.exists():
                continue

            try:
                shard_db = lancedb.connect(str(shard_vec_path))
                if "vec_enums" not in shard_db.table_names():
                    continue

                shard_table = shard_db.open_table("vec_enums")
                shard_data = shard_table.to_arrow()

                global_db = lancedb.connect(str(global_vec_dir))
                if "vec_enums" in global_db.table_names():
                    global_table = global_db.open_table("vec_enums")
                    global_table.add(shard_data)
                else:
                    global_table = global_db.create_table("vec_enums", shard_data)

                total_vec_merged += len(shard_data)
                logger.info(
                    f"  [Merge] Merged {len(shard_data)} vectors from {shard_id}"
                )

            except Exception as e:
                logger.warning(f"  [Merge] Failed to merge vectors from {shard_id}: {e}")

    except ImportError:
        logger.warning("  [Merge] LanceDB not available. Skipping vector merge.")

    logger.info(f"  [Merge] Total vectors merged: {total_vec_merged}")

    # ================================================================
    # Phase 3: Create queryable global KB
    # ================================================================
    logger.info("[Merge] Phase 3: Creating global queryable KB...")

    try:
        from ahvn.klbase.base import KLBase
        from ahvn.klstore.vdb_store import VectorKLStore
        from ahvn.klengine.vector_engine import VectorKLEngine
        from ahvn.klengine.facet_engine import FacetKLEngine

        global_kb = KLBase(name=kb_name)

        # Re-attach main storage
        global_main = DatabaseKLStore(
            provider="sqlite",
            database=f"sqlite:///{global_main_db}",
            table="ukf_records",
            adapter=DbUKFAdapter(),
        )
        global_main._init()
        global_kb.add_storage(global_main, name="main", main=True)

        # Add vector engine
        global_vec_store = VectorKLStore(
            provider="lancedb",
            uri=str(global_vec_dir),
            table_name="vec_enums",
            encoder=None,
            embedder="embedder",
        )

        vec_engine = VectorKLEngine(
            storage=global_vec_store,
            inplace=False,
            provider="lancedb",
            uri=str(global_vec_dir),
            encoder=[
                "lambda kl: str(kl.enum).strip().lower()",
                "lambda q: str(q).strip().lower()",
            ],
            embedder="embedder",
            condition=lambda kl: getattr(kl, 'type', None) == 'db-enum',
        )
        global_kb.add_engine(vec_engine, name="vec-enums")

        # Add facet engine for tag-based search
        facet_engine = FacetKLEngine(storage=global_main, inplace=True)
        global_kb.add_engine(facet_engine, name="facet")

        # Verification search
        try:
            facet_results = global_kb.search("test", engine="facet", mode="facet")
            logger.info(f"  [Merge] Verification facet search: {len(facet_results)} results")
        except Exception as e:
            logger.debug(f"  [Merge] Facet search test skipped: {e}")

        try:
            vec_results = global_kb.search(
                "test query",
                engine="vec-enums",
                mode="vector",
                topk=5,
            )
            logger.info(f"  [Merge] Verification vector search: {len(vec_results)} results")
        except Exception as e:
            logger.debug(f"  [Merge] Vector search test skipped: {e}")

    except ImportError:
        logger.warning("  [Merge] AgentHeaven not available. Skipping KB creation.")

    # ================================================================
    # Save merge metadata
    # ================================================================
    elapsed = time.time() - t_start

    merge_meta = {
        "kb_name": kb_name,
        "kb_root": kb_root,
        "total_ukfts_merged": total_merged,
        "total_vectors_merged": total_vec_merged,
        "shard_count": len(shard_results),
        "elapsed_sec": elapsed,
        "timestamp": time.time(),
    }

    with open(global_kb_dir / "merge_meta.json", "w") as f:
        json.dump(merge_meta, f, indent=2, ensure_ascii=False)

    logger.info(
        f"[Merge] Complete! {total_merged} UKFTs, {total_vec_merged} vectors "
        f"in {elapsed:.1f}s → {global_kb_dir}"
    )

    return merge_meta
