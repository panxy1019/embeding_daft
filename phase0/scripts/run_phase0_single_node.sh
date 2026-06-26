#!/usr/bin/env bash
set -euo pipefail

PHASE0_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$PHASE0_DIR/.." && pwd)}"
LAKE_SRC="${LAKE_SRC:-$PHASE0_DIR/src}"
RUBIKSQL_SRC="${RUBIKSQL_SRC:-$PROJECT_ROOT/RubikSQL-dev/src}"
AGENTHEAVEN_SRC="${AGENTHEAVEN_SRC:-$PROJECT_ROOT/AgentHeaven-dev-master/src}"
CONDA_ENV="${CONDA_ENV:-ray-submit}"

: "${MINIO_SECRET_ACCESS_KEY:?Set MINIO_SECRET_ACCESS_KEY before running Phase0.}"

export PYTHONPATH="$LAKE_SRC:$RUBIKSQL_SRC:$AGENTHEAVEN_SRC${PYTHONPATH:+:$PYTHONPATH}"
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:17894}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:17894}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,10.42.0.29}"

conda run -n "$CONDA_ENV" python -m rubiksql_lake.single_node_build "$@"
