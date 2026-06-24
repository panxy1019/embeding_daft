#!/bin/bash
# Start Ray Worker node
# Run this on each worker node

set -e

# === CONFIGURE THESE ===
RAY_HEAD_ADDRESS="${RAY_HEAD_ADDRESS:-server00:6379}"
NUM_CPUS="${NUM_CPUS:-8}"
# =======================

echo "============================================"
echo "Starting Ray Worker"
echo "  Head:    ${RAY_HEAD_ADDRESS}"
echo "  CPUs:    ${NUM_CPUS}"
echo "============================================"

# Stop any existing Ray instance
ray stop -f 2>/dev/null || true

# Start Ray worker
ray start \
    --address="${RAY_HEAD_ADDRESS}" \
    --num-cpus="${NUM_CPUS}" \
    --resources='{"worker": 1}'

echo ""
echo "Ray Worker started and connected to ${RAY_HEAD_ADDRESS}"
echo ""
echo "Check status on head node:"
echo "  ray status"
