#!/usr/bin/env bash
set -euo pipefail
# SLURM executes a spooled copy; resolve helpers via the submitted environment.
WORKFLOW_DIR="${PACE_WORKFLOW_DIR:-${SLURM_SUBMIT_DIR:-$PWD}/pace}"
if [[ ! -f "$WORKFLOW_DIR/common.sh" ]]; then
  echo "Cannot resolve PACE workflow directory: $WORKFLOW_DIR"
  exit 2
fi
SCRIPT_DIR="$WORKFLOW_DIR"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$WORKFLOW_DIR/.." && pwd)}"
source "$WORKFLOW_DIR/common.sh"
load_config
load_modules
resolve_paths
if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "Virtual environment not found: $VENV_ACTIVATE"
  exit 2
fi
source "$VENV_ACTIVATE"
export PYTHONUNBUFFERED=1
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/pace/results}"
mkdir -p "$RESULTS_DIR"
RUN_ID="${SLURM_JOB_ID:-manual-$(date +%Y%m%d-%H%M%S)}"
OUT_PATH="$RESULTS_DIR/results-$RUN_ID.json"
WORKERS="${SIM_WORKERS:-0}"
if [[ "$WORKERS" == 0 ]]; then WORKERS="${SLURM_CPUS_PER_TASK:-1}"; fi
if (( WORKERS > ${SLURM_CPUS_PER_TASK:-1} )); then
  echo "Simulation workers exceed allocated CPUs"
  exit 2
fi
NODES="${SLURM_JOB_NUM_NODES:-${SBATCH_NODES:-1}}"
export SLURM_NTASKS="$NODES"
SHARD_DIR="$RESULTS_DIR/shards-$RUN_ID"
COMMAND=(python "$PROJECT_ROOT/harness/distributed.py" worker "$SHARD_DIR")

MANIFEST_PATH="$RESULTS_DIR/run-$RUN_ID.json"
python "$WORKFLOW_DIR/run_info.py" "$MANIFEST_PATH" "$PROJECT_ROOT" "$OUT_PATH" "${COMMAND[@]}"
finish() {
  local exit_code=$?
  trap - EXIT
  python "$WORKFLOW_DIR/run_info.py" "$MANIFEST_PATH" --finish "$exit_code" || true
  echo "Job $RUN_ID finished with exit code $exit_code"
  exit "$exit_code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Host: $(hostname); SLURM job: $RUN_ID"
echo "CPUs: ${SLURM_CPUS_PER_TASK:-1}; requested seed workers: $WORKERS"
echo "Output: $OUT_PATH"
printf 'Command:'
printf ' %q' "${COMMAND[@]}"
printf '\n'
cd "$PROJECT_ROOT"
if [[ -n "${RESUME_JOB_ID:-}" ]]; then
  python "$PROJECT_ROOT/harness/distributed.py" prepare "$SHARD_DIR" "$RESULTS_DIR/shards-$RESUME_JOB_ID"
fi
time srun --nodes="$NODES" --ntasks="$NODES" --ntasks-per-node=1 --kill-on-bad-exit=1 \
  --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}" "${COMMAND[@]}"
python "$PROJECT_ROOT/harness/distributed.py" merge "$SHARD_DIR" "$OUT_PATH"
echo "Completed: $OUT_PATH"
