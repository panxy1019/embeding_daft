"""Daft Profiling: read Parquet files and generate column profiles and enum candidates.

This module runs on the driver (server00) and produces intermediate Parquet files
that are consumed by Ray workers during the build phase.
"""

import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger

import daft
import pyarrow as pa
import pyarrow.parquet as pq

from .spec import LakeManifest, DatabaseSpec, TableSpec, ColumnSpec


def run_profiling(manifest: LakeManifest, run_dir: Path) -> Dict[str, Any]:
    """Run Daft profiling on all tables defined in the manifest.

    For each table:
    1. Read Parquet files with Daft
    2. Compute column-level statistics (null count, distinct count, etc.)
    3. Generate enum candidates for eligible columns

    Args:
        manifest: Validated LakeManifest with database/table definitions.
        run_dir: Output directory for this build run.

    Returns:
        Dict with profiling summary:
        {
            "table_count": int,
            "column_count": int,
            "enum_candidate_count": int,
            "column_profile_path": str | None,
            "enum_candidates_path": str | None,
            "elapsed_sec": float,
        }
    """
    t_start = time.time()

    profile_dir = run_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    all_column_profiles: List[Dict[str, Any]] = []
    all_enum_candidates: List[Dict[str, Any]] = []
    table_count = 0
    column_count = 0

    for db_spec in manifest.databases:
        db_id = db_spec.db_id

        for tab_spec in db_spec.tables:
            table_id = tab_spec.table_id
            parquet_path = tab_spec.path
            table_count += 1

            logger.info(f"Profiling: {db_id}.{table_id} ({parquet_path})")

            try:
                # Read Parquet with Daft
                df = daft.read_parquet(parquet_path)
                schema = df.schema()

                # Get total row count
                try:
                    count_result = df.count().to_pandas()
                    total_rows = int(count_result.iloc[0, 0])
                except Exception:
                    logger.warning(f"  Could not get row count for {db_id}.{table_id}, using 0")
                    total_rows = 0

                # Build column spec lookup
                col_specs: Dict[str, ColumnSpec] = {}
                if tab_spec.columns:
                    col_specs = {c.col_id: c for c in tab_spec.columns}

                for col_name in schema.column_names():
                    col_type = schema[col_name].dtype
                    col_type_str = str(col_type)
                    col_spec = col_specs.get(col_name)

                    # Determine enum eligibility
                    enum_enabled = _should_enable_enum(
                        col_spec, col_name, col_type_str, manifest.enum_policy
                    )

                    # Compute column statistics
                    null_count, distinct_count = _compute_col_stats(
                        df, col_name, total_rows
                    )

                    # Compute ratios
                    null_ratio = null_count / total_rows if total_rows > 0 else 0.0
                    distinct_ratio = distinct_count / total_rows if total_rows > 0 else 0.0

                    # Sample values
                    sample_values = _get_sample_values(df, col_name)

                    # Record column profile
                    column_profile = {
                        "db_id": db_id,
                        "table_id": table_id,
                        "column_id": col_name,
                        "dtype": col_type_str,
                        "total_count": total_rows,
                        "null_count": null_count,
                        "distinct_count": distinct_count,
                        "null_ratio": null_ratio,
                        "distinct_ratio": distinct_ratio,
                        "enum_enabled": enum_enabled,
                        "sample_values": sample_values,
                    }
                    all_column_profiles.append(column_profile)
                    column_count += 1

                    # Generate enum candidates if eligible
                    if enum_enabled and distinct_count > 0:
                        if _check_enum_limits(distinct_count, distinct_ratio, manifest.enum_policy):
                            candidates = _build_enum_candidates(
                                df, db_id, table_id, col_name,
                                manifest.enum_policy.max_distinct_count
                            )
                            all_enum_candidates.extend(candidates)
                            logger.debug(
                                f"  {col_name}: {distinct_count} distincts, "
                                f"{len(candidates)} enum candidates"
                            )
                        else:
                            logger.debug(
                                f"  {col_name}: SKIPPED (distinct={distinct_count}, "
                                f"ratio={distinct_ratio:.3f} exceeds limits)"
                            )

            except Exception as e:
                logger.error(f"  Failed to profile {db_id}.{table_id}: {e}")
                continue

    # Write profile results to Parquet
    col_profile_path = None
    enum_candidates_path = None

    if all_column_profiles:
        col_profile_table = pa.Table.from_pylist(all_column_profiles)
        col_profile_path = profile_dir / "column_profile.parquet"
        pq.write_table(col_profile_table, str(col_profile_path))
        logger.info(f"Wrote {len(all_column_profiles)} column profiles to {col_profile_path}")

    if all_enum_candidates:
        enum_table = pa.Table.from_pylist(all_enum_candidates)
        enum_candidates_path = profile_dir / "enum_candidates.parquet"
        pq.write_table(enum_table, str(enum_candidates_path))
        logger.info(f"Wrote {len(all_enum_candidates)} enum candidates to {enum_candidates_path}")

    elapsed = time.time() - t_start

    result = {
        "table_count": table_count,
        "column_count": column_count,
        "enum_candidate_count": len(all_enum_candidates),
        "column_profile_path": str(col_profile_path) if col_profile_path else None,
        "enum_candidates_path": str(enum_candidates_path) if enum_candidates_path else None,
        "elapsed_sec": elapsed,
    }

    logger.info(
        f"Profiling complete: {table_count} tables, {column_count} columns, "
        f"{len(all_enum_candidates)} enum candidates ({elapsed:.1f}s)"
    )

    return result


def _compute_col_stats(
    df,
    col_name: str,
    total_rows: int,
) -> Tuple[int, int]:
    """Compute null count and distinct count for a column.

    Args:
        df: Daft DataFrame.
        col_name: Column name.
        total_rows: Total row count (used if Daft count fails).

    Returns:
        (null_count, distinct_count) tuple.
    """
    null_count = 0
    distinct_count = 0

    try:
        null_count = int(
            df.where(daft.col(col_name).is_null())
            .count()
            .to_pandas()
            .iloc[0, 0]
        )
    except Exception:
        null_count = 0

    try:
        distinct_count = int(
            df.select(daft.col(col_name))
            .distinct()
            .count()
            .to_pandas()
            .iloc[0, 0]
        )
    except Exception:
        # Fallback: use a smaller estimate
        try:
            distinct_count = int(
                df.select(daft.col(col_name))
                .limit(10000)
                .distinct()
                .count()
                .to_pandas()
                .iloc[0, 0]
            )
        except Exception:
            distinct_count = total_rows  # Conservative estimate

    return null_count, distinct_count


def _get_sample_values(df, col_name: str, max_samples: int = 40) -> List[str]:
    """Get sample values from a column (top-N most frequent + bottom-N).

    Args:
        df: Daft DataFrame.
        col_name: Column name.
        max_samples: Maximum number of samples to return.

    Returns:
        List of sample value strings.
    """
    try:
        # Try to get top-N by frequency
        freq_df = (
            df
            .where(daft.col(col_name).not_null())
            .select(
                daft.col(col_name).cast(daft.DataType.string()).alias("val")
            )
            .groupby("val")
            .agg(daft.col("val").count().alias("cnt"))
            .sort("cnt", desc=True)
            .limit(max_samples)
        )

        rows = freq_df.to_pydict()
        vals = rows.get("val", [])
        return [str(v) for v in vals if v is not None]

    except Exception:
        # Fallback: just take first N rows
        try:
            sample_rows = (
                df
                .where(daft.col(col_name).not_null())
                .select(
                    daft.col(col_name).cast(daft.DataType.string()).alias("val")
                )
                .limit(max_samples)
                .to_pydict()
            )
            vals = sample_rows.get("val", [])
            return [str(v) for v in vals if v is not None]
        except Exception:
            return []


def _build_enum_candidates(
    df,
    db_id: str,
    table_id: str,
    column_id: str,
    max_candidates: int = 100000,
) -> List[Dict[str, Any]]:
    """Build enum candidates using Daft GROUP BY + COUNT.

    Args:
        df: Daft DataFrame.
        db_id: Database identifier.
        table_id: Table identifier.
        column_id: Column name.
        max_candidates: Maximum number of candidates to return.

    Returns:
        List of dicts with keys: db_id, table_id, column_id, enum_value, freq.
    """
    col_name = column_id

    try:
        candidates_df = (
            df
            .where(daft.col(col_name).not_null())
            .select(
                daft.col(col_name).cast(daft.DataType.string()).alias("enum_value")
            )
            .groupby("enum_value")
            .agg(daft.col("enum_value").count().alias("freq"))
            .sort("freq", desc=True)
            .limit(max_candidates)
        )

        rows = candidates_df.to_pydict()
        result = []
        enum_values = rows.get("enum_value", [])
        freqs = rows.get("freq", [])

        for i in range(len(enum_values)):
            result.append({
                "db_id": db_id,
                "table_id": table_id,
                "column_id": column_id,
                "enum_value": str(enum_values[i]),
                "freq": int(freqs[i]) if i < len(freqs) else 0,
            })

        return result

    except Exception as e:
        logger.error(f"  Failed to build enum candidates for {table_id}.{column_id}: {e}")
        return []


def _should_enable_enum(
    col_spec: Optional[ColumnSpec],
    col_name: str,
    col_type: str,
    enum_policy: Any,
) -> bool:
    """Determine whether a column should have enum indexing enabled.

    Priority: explicit manifest setting > auto-detection by type.

    Args:
        col_spec: ColumnSpec from manifest (may be None).
        col_name: Column name.
        col_type: Parquet/Arrow data type string.
        enum_policy: EnumPolicySpec from manifest.

    Returns:
        True if enum indexing should be enabled.
    """
    # Explicit setting in manifest takes priority
    if col_spec is not None and col_spec.enum_index_enabled is not None:
        return col_spec.enum_index_enabled

    # Auto-detect by type
    dtype_lower = col_type.lower()
    default_types = getattr(enum_policy, 'default_enabled_for', ['text', 'categorical'])

    if 'text' in default_types:
        text_types = ('string', 'large_string', 'utf8', 'text', 'varchar', 'char')
        if any(t in dtype_lower for t in text_types):
            return True

    if 'categorical' in default_types:
        cat_types = ('bool', 'boolean', 'category', 'enum', 'dictionary')
        if any(t in dtype_lower for t in cat_types):
            return True

    return False


def _check_enum_limits(
    distinct_count: int,
    distinct_ratio: float,
    enum_policy: Any,
) -> bool:
    """Check if a column's cardinality is within enum indexing limits.

    Args:
        distinct_count: Number of distinct values.
        distinct_ratio: distinct_count / total_rows.
        enum_policy: EnumPolicySpec from manifest.

    Returns:
        True if within limits.
    """
    max_count = getattr(enum_policy, 'max_distinct_count', 100000)
    max_ratio = getattr(enum_policy, 'max_distinct_ratio', 0.2)

    if distinct_count > max_count:
        return False
    if distinct_ratio > max_ratio:
        return False
    return True
