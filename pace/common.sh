#!/usr/bin/env bash
# Shared configuration for the CPU adaptation of ray_run_harness.
CONFIG_KEYS=(
  PACE_LOGIN PACE_REMOTE_DIR PACE_SSH PACE_RSYNC PACE_MODULES PYTHON_BIN
  VENV_ACTIVATE SBATCH_BIN SBATCH_ACCOUNT SBATCH_PARTITION SBATCH_QOS
  SBATCH_TIME SBATCH_NODES SBATCH_GPUS_PER_NODE SBATCH_CPUS_PER_TASK SBATCH_MEM SBATCH_CONSTRAINT
  SBATCH_EXTRA_ARGS SLURM_JOB_NAME SERVICE_CELLS H3_RESOLUTION EPOCHS
  EPOCH_SECONDS SATELLITES ORBITAL_SNAPSHOT PROVIDERS SEEDS SIM_WORKERS SIM_EXTRA_ARGS
  RESUME_JOB_ID RESULTS_DIR LOG_DIR LOG_PATTERN JOB_FILE RUNS_DIR TAIL_LINES SCANCEL_BIN
)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/harness/config.sh"

load_modules() {
  if [[ -z "${PACE_MODULES:-}" ]]; then return; fi
  if ! command -v module >/dev/null 2>&1 && [[ -f /etc/profile.d/modules.sh ]]; then
    source /etc/profile.d/modules.sh
  fi
  if ! command -v module >/dev/null 2>&1; then
    echo "PACE_MODULES is set, but the module command is unavailable"
    return 127
  fi
  # shellcheck disable=SC2086
  module load $PACE_MODULES
}
resolve_paths() {
  VENV_ACTIVATE="${VENV_ACTIVATE:-.venv/bin/activate}"
  RESULTS_DIR="${RESULTS_DIR:-pace/results}"
  LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
  JOB_FILE="${JOB_FILE:-$SCRIPT_DIR/.jobid}"
  RUNS_DIR="${RUNS_DIR:-pace/runs}"
  local key
  for key in VENV_ACTIVATE RESULTS_DIR LOG_DIR JOB_FILE RUNS_DIR; do
    if [[ "${!key}" != /* ]]; then printf -v "$key" '%s' "$PROJECT_ROOT/${!key}"; fi
    export "$key"
  done
}
