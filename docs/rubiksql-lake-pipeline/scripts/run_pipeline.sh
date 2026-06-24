#!/bin/bash
# Run the full Phase 1 pipeline
# Run this on server00

set -e

# === CONFIGURE THESE ===
MANIFEST_PATH="${1:-lake_manifest.yaml}"
# =======================

if [ ! -f "$MANIFEST_PATH" ]; then
    echo "Error: Manifest file not found: $MANIFEST_PATH"
    echo "Usage: $0 <path/to/lake_manifest.yaml>"
    echo ""
    echo "To auto-generate a manifest:"
    echo "  rubiksql-lake generate-manifest -p /data/rubikbench/ -o lake_manifest.yaml"
    exit 1
fi

echo "============================================"
echo "RubikSQL Data Lake Pipeline - Phase 1"
echo "Manifest: ${MANIFEST_PATH}"
echo "============================================"

# Check Ray cluster
echo ""
echo "Checking Ray cluster..."
python -c "
import ray
ray.init(address='auto', ignore_reinit_error=True)
nodes = ray.nodes()
alive = sum(1 for n in nodes if n.get('Alive'))
print(f'  Ray nodes: {len(nodes)} total, {alive} alive')
if alive < 2:
    print('  WARNING: Less than 2 nodes alive. Make sure workers are connected.')
"

# Check shared storage
echo ""
echo "Checking shared storage..."
OUTPUT_BASE=$(python -c "
import yaml
with open('${MANIFEST_PATH}') as f:
    m = yaml.safe_load(f)
print(m.get('run', {}).get('output_base', '/mnt/shared/rubiksql-build-runs'))
")

if [ -d "$OUTPUT_BASE" ]; then
    echo "  Shared storage OK: $OUTPUT_BASE"
else
    echo "  WARNING: Shared storage path not accessible: $OUTPUT_BASE"
    echo "  Make sure NFS/SMB is mounted on all nodes."
fi

# Run pipeline
echo ""
echo "Starting pipeline..."
echo "============================================"

rubiksql-lake build -m "$MANIFEST_PATH" -v

EXIT_CODE=$?

echo ""
echo "============================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "Pipeline completed successfully!"
else
    echo "Pipeline failed with exit code: $EXIT_CODE"
    echo "Check logs in: $OUTPUT_BASE/<run_id>/logs/"
fi
echo "============================================"

exit $EXIT_CODE
