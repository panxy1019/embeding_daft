"""CLI entry point for rubiksql-lake pipeline.

Provides command-line interface for:
- build: Run the full Phase 1 pipeline
- profile: Run only the profiling step
- status: Check Ray cluster status
- generate-manifest: Auto-generate manifest from Parquet directory
"""

import sys
import click
from pathlib import Path
from loguru import logger


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """RubikSQL Data Lake Build Pipeline.

    Build RubikSQL knowledge bases from Parquet data lakes using
    Daft for data processing and Ray for distributed execution.
    """
    pass


@cli.command()
@click.option(
    "--manifest", "-m",
    required=True,
    type=click.Path(exists=True),
    help="Path to lake_manifest.yaml",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose (DEBUG) logging",
)
def build(manifest: str, verbose: bool):
    """Run the complete Phase 1 build pipeline.

    Reads Parquet files defined in the manifest, profiles columns,
    distributes build tasks to Ray workers, and merges results
    into a global knowledge base.

    \b
    Example:
        rubiksql-lake build -m lake_manifest.yaml
        rubiksql-lake build -m lake_manifest.yaml -v
    """
    # Configure logging
    logger.remove()
    log_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        colorize=True,
    )

    from .pipeline import run_phase1_pipeline

    try:
        result = run_phase1_pipeline(manifest)

        if result.get("status") == "failed":
            click.echo(f"\n❌ Pipeline failed: {result}", err=True)
            sys.exit(1)
        elif result.get("status") == "no_data":
            click.echo("\n⚠️  No enum candidates found. Check manifest configuration.")
            sys.exit(0)
        else:
            click.echo(f"\n✅ Pipeline complete. Run ID: {result['run_id']}")

    except FileNotFoundError as e:
        click.echo(f"\n❌ File not found: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\n❌ Pipeline error: {e}", err=True)
        if verbose:
            raise
        sys.exit(1)


@cli.command()
@click.option(
    "--manifest", "-m",
    required=True,
    type=click.Path(exists=True),
    help="Path to lake_manifest.yaml",
)
@click.option(
    "--output", "-o",
    required=True,
    type=click.Path(),
    help="Output directory for profiling results",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose logging",
)
def profile(manifest: str, output: str, verbose: bool):
    """Run only the profiling step (no build, no embedding).

    Useful for:
    - Checking data quality before a full build
    - Debugging enum candidate generation
    - Estimating build scope

    \b
    Example:
        rubiksql-lake profile -m lake_manifest.yaml -o ./profile_check/
    """
    logger.remove()
    log_level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=log_level)

    from .spec import load_manifest
    from .profiling import run_profiling

    manifest_obj = load_manifest(manifest)
    run_dir = Path(output)
    run_dir.mkdir(parents=True, exist_ok=True)

    result = run_profiling(manifest_obj, run_dir)

    click.echo(f"\n📊 Profiling complete:")
    click.echo(f"   Tables:  {result['table_count']}")
    click.echo(f"   Columns: {result['column_count']}")
    click.echo(f"   Enums:   {result['enum_candidate_count']}")
    click.echo(f"   Time:    {result['elapsed_sec']:.1f}s")
    click.echo(f"   Output:  {output}")


@cli.command()
def status():
    """Check Ray cluster status and available resources.

    \b
    Example:
        rubiksql-lake status
    """
    try:
        import ray

        if not ray.is_initialized():
            ray.init(address="auto", ignore_reinit_error=True)

        resources = ray.cluster_resources()
        available = ray.available_resources()

        click.echo("\n🖥️  Ray Cluster Resources:")
        click.echo("-" * 50)
        for key in sorted(resources.keys()):
            total = resources[key]
            avail = available.get(key, 0)
            percentage = (avail / total * 100) if total > 0 else 0
            bar = "█" * int(percentage / 10) + "░" * (10 - int(percentage / 10))
            click.echo(f"  {key:20s} {bar} {avail:.0f}/{total:.0f}")

        nodes = ray.nodes()
        click.echo(f"\n📦 Nodes: {len(nodes)}")
        click.echo("-" * 50)
        for node in nodes:
            alive = "✅" if node.get("Alive") else "❌"
            node_name = node.get("NodeName", "unknown")
            node_resources = node.get("Resources", {})
            cpu = node_resources.get("CPU", 0)
            gpu = node_resources.get("GPU", 0)
            click.echo(f"  {alive} {node_name}: CPU={cpu}, GPU={gpu}")

        click.echo()

    except Exception as e:
        click.echo(f"\n❌ Error connecting to Ray cluster: {e}", err=True)
        click.echo("   Make sure Ray is running (ray start --head / ray start --address=...)")
        sys.exit(1)


@cli.command()
@click.option(
    "--parquet-root", "-p",
    required=True,
    type=click.Path(exists=True),
    help="Root directory containing {db}/{table}/*.parquet",
)
@click.option(
    "--output", "-o",
    default="lake_manifest.yaml",
    type=click.Path(),
    help="Output path for generated manifest (default: lake_manifest.yaml)",
)
def generate_manifest(parquet_root: str, output: str):
    """Auto-generate a lake manifest by scanning Parquet directory structure.

    Assumes directory layout: {root}/{database}/{table}/*.parquet

    \b
    Example:
        rubiksql-lake generate-manifest -p /data/rubikbench/ -o my_manifest.yaml
    """
    from .spec import auto_generate_manifest

    click.echo(f"Scanning: {parquet_root}")
    manifest = auto_generate_manifest(parquet_root, output)

    click.echo(f"\n📝 Generated manifest: {output}")
    click.echo(f"   Databases: {len(manifest.databases)}")
    total_tables = sum(len(db.tables) for db in manifest.databases)
    click.echo(f"   Tables:    {total_tables}")
    click.echo(f"\n⚠️  Review the generated manifest before running 'build'!")
    click.echo(f"   - Verify enum_index_enabled for each column")
    click.echo(f"   - Add descriptions for databases/tables/columns")
    click.echo(f"   - Adjust enum_policy limits if needed")


if __name__ == "__main__":
    cli()
