"""Lake manifest loading and validation.

The lake manifest is the single configuration file that defines:
- Which databases/tables/columns to build from
- Where Parquet files are located
- Which columns should have enum indexes
- Build policies (enum limits, partitioning, etc.)
"""

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator


class ColumnSpec(BaseModel):
    """Specification for a single column in the data lake."""
    col_id: str
    description: str = ""
    datatype: Optional[str] = None
    enum_index_enabled: Optional[bool] = None  # None = auto-detect


class TableSpec(BaseModel):
    """Specification for a single table in the data lake."""
    table_id: str
    path: str
    description: str = ""
    primary_key: List[str] = Field(default_factory=list)
    columns: List[ColumnSpec] = Field(default_factory=list)


class DatabaseSpec(BaseModel):
    """Specification for a single database in the data lake."""
    db_id: str
    description: str = ""
    tables: List[TableSpec] = Field(default_factory=list)


class RunSpec(BaseModel):
    """Run configuration."""
    run_id: str = "auto"
    output_base: str = "/mnt/shared/rubiksql-build-runs"


class DaftSpec(BaseModel):
    """Daft runner configuration."""
    runner: str = "ray"
    target_partitions: int = 256


class RubikSQLSpec(BaseModel):
    """RubikSQL knowledge base configuration."""
    kb_name: str = "rubikbench"
    kb_root: str = "/mnt/shared/rubiksql-kb"


class Phase1Spec(BaseModel):
    """Phase 1 pipeline configuration."""
    enabled: bool = True
    agentheaven_embedding: bool = True
    shard_vectorstore: bool = True
    merge_after_build: bool = True


class EnumPolicySpec(BaseModel):
    """Enumeration indexing policy."""
    default_enabled_for: List[str] = Field(default_factory=lambda: ["text", "categorical"])
    max_distinct_count: int = 100000
    max_distinct_ratio: float = 0.2
    min_frequency: int = 1


class LakeManifest(BaseModel):
    """Top-level lake build manifest."""
    run: RunSpec = Field(default_factory=RunSpec)
    daft: DaftSpec = Field(default_factory=DaftSpec)
    rubiksql: RubikSQLSpec = Field(default_factory=RubikSQLSpec)
    phase1: Phase1Spec = Field(default_factory=Phase1Spec)
    enum_policy: EnumPolicySpec = Field(default_factory=EnumPolicySpec)
    databases: List[DatabaseSpec] = Field(default_factory=list)

    @field_validator("databases")
    @classmethod
    def check_not_empty(cls, v):
        if not v:
            raise ValueError("At least one database must be specified in the manifest")
        return v


def load_manifest(path: str) -> LakeManifest:
    """Load and validate a lake manifest YAML file.

    Args:
        path: Path to the manifest YAML file.

    Returns:
        Validated LakeManifest object.

    Raises:
        FileNotFoundError: If the manifest file doesn't exist.
        ValidationError: If the manifest is invalid.
    """
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return LakeManifest(**data)


def auto_generate_manifest(
    parquet_root: str,
    output_path: str,
    run_id: str = "auto_001",
) -> LakeManifest:
    """Auto-generate a lake manifest by scanning Parquet directory structure.

    Assumes directory structure: {root}/{db_id}/{table_id}/*.parquet

    Args:
        parquet_root: Root directory containing database/table subdirectories.
        output_path: Where to save the generated manifest YAML.
        run_id: Build run identifier.

    Returns:
        Generated LakeManifest object.
    """
    import daft
    from pathlib import Path

    root = Path(parquet_root)
    databases = []

    for db_dir in sorted(root.iterdir()):
        if not db_dir.is_dir():
            continue
        db_id = db_dir.name
        tables = []

        for tab_dir in sorted(db_dir.iterdir()):
            if not tab_dir.is_dir():
                continue
            tab_id = tab_dir.name
            parquet_files = list(tab_dir.glob("*.parquet"))
            if not parquet_files:
                continue

            # Read schema from first parquet file
            try:
                df = daft.read_parquet(str(parquet_files[0]))
                schema = df.schema()
            except Exception:
                # Fallback: no columns specified
                tables.append({
                    'table_id': tab_id,
                    'path': str(tab_dir / "*.parquet"),
                    'columns': [],
                })
                continue

            columns = []
            for col_name in schema.column_names():
                col_type = schema[col_name].dtype
                is_text = str(col_type).lower() in ('string', 'large_string', 'utf8')
                columns.append({
                    'col_id': col_name,
                    'datatype': str(col_type),
                    'enum_index_enabled': is_text,
                })

            tables.append({
                'table_id': tab_id,
                'path': str(tab_dir / "*.parquet"),
                'columns': columns,
            })

        if tables:
            databases.append({'db_id': db_id, 'tables': tables})

    manifest = LakeManifest(
        run=RunSpec(run_id=run_id),
        databases=[
            DatabaseSpec(
                db_id=db['db_id'],
                tables=[
                    TableSpec(
                        table_id=t['table_id'],
                        path=t['path'],
                        columns=[
                            ColumnSpec(
                                col_id=c['col_id'],
                                datatype=c.get('datatype'),
                                enum_index_enabled=c.get('enum_index_enabled'),
                            )
                            for c in t['columns']
                        ],
                    )
                    for t in db['tables']
                ],
            )
            for db in databases
        ],
    )

    # Save to file
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        yaml.dump(manifest.model_dump(), f, default_flow_style=False, allow_unicode=True)

    print(f"Auto-generated manifest with {len(databases)} databases saved to: {output_path}")
    return manifest
