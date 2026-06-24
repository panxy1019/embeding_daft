#!/bin/bash
# Start Ray Head node on server00
# Run this on server00

set -e

echo "============================================"
echo "Starting Ray Head Node"
echo "============================================"

# Stop any existing Ray instance
ray stop -f 2>/dev/null || true

# Start Ray head
ray start --head \
    --port=6379 \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=8265 \
    --num-cpus=4 \
    --resources='{"head": 1}' \
    --include-dashboard=true

echo ""
echo "Ray Head started successfully!"
echo "Dashboard: http://$(hostname):8265"
echo ""
echo "On each worker, run:"
echo "  ray start --address='$(hostname):6379' --num-cpus=8"
echo ""
echo "Check status:"
echo "  ray status"
