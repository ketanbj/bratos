#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$PROJECT_ROOT/harness/config.sh"
CONFIG_KEYS=(SERVICE_CELLS H3_RESOLUTION EPOCHS EPOCH_SECONDS SATELLITES
  ORBITAL_SNAPSHOT PROVIDERS SEEDS SIM_WORKERS
  CLOUDLAB_HOSTS CLOUDLAB_REMOTE_DIR CLOUDLAB_CPUS_PER_NODE
  CLOUDLAB_PYTHON CLOUDLAB_LOCAL_PYTHON CLOUDLAB_SSH CLOUDLAB_RSYNC
  CLOUDLAB_STATE_DIR CLOUDLAB_RUN_ID CLOUDLAB_RESUME_RUN_ID
  CLOUDLAB_TIME_LIMIT_SECONDS CLOUDLAB_POLL_SECONDS)
if [[ "${1:-}" == env ]]; then
  ENV_FILE="${ENV_FILE:-.env}"
  if [[ "$ENV_FILE" != /* ]]; then ENV_FILE="$SCRIPT_DIR/$ENV_FILE"; fi
  if [[ ! -f "$ENV_FILE" ]]; then cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"; fi
  echo "Configuration: $ENV_FILE"
  exit
fi
load_config
local_python="${CLOUDLAB_LOCAL_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! -x "$local_python" ]]; then local_python=python3; fi
exec "$local_python" "$SCRIPT_DIR/launcher.py" "${1:-help}"
