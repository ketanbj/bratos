#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_config
if [[ "${DRY_RUN:-0}" == 1 && -n "${PACE_LOGIN:-}" ]]; then
  PROJECT_ROOT="${PACE_REMOTE_DIR:?PACE_REMOTE_DIR is required for remote preview}"
fi
resolve_paths

SBATCH_NODES="${SBATCH_NODES:-1}"
LOG_PATTERN="${LOG_PATTERN:-beam-sim-%j.log}"
export SBATCH_NODES LOG_PATTERN
for key in SERVICE_CELLS EPOCHS EPOCH_SECONDS SBATCH_CPUS_PER_TASK SBATCH_NODES; do
  value="${!key:-}"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "$key must be a positive integer, got: ${value:-<empty>}"
    exit 2
  fi
done
if [[ "${SATELLITES:-}" != all && ! "${SATELLITES:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "SATELLITES must be a positive integer or all"
  exit 2
fi
if [[ ! "${H3_RESOLUTION:-}" =~ ^([0-9]|1[0-5])$ ]]; then
  echo "H3_RESOLUTION must be an integer from 0 through 15"
  exit 2
fi
if [[ ! "${SIM_WORKERS:-0}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "SIM_WORKERS must be a nonnegative integer"
  exit 2
fi
if (( ${SIM_WORKERS:-0} > SBATCH_CPUS_PER_TASK )); then
  echo "SIM_WORKERS cannot exceed SBATCH_CPUS_PER_TASK"
  exit 2
fi
if [[ -n "${RESUME_JOB_ID:-}" && ! "$RESUME_JOB_ID" =~ ^[0-9]+$ ]]; then
  echo "RESUME_JOB_ID must be a numeric SLURM job ID"
  exit 2
fi
if [[ ! "${SEEDS:-}" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]]; then
  echo "SEEDS must be comma-separated integers"
  exit 2
fi
if [[ ! "${PROVIDERS:-}" =~ ^(all|starlink|kuiper|starlink,kuiper|kuiper,starlink)$ ]]; then
  echo "PROVIDERS must be all, starlink, kuiper, or starlink,kuiper"
  exit 2
fi
# Extra args cannot override the allocation or the frozen simulator settings.
if [[ -n "${SBATCH_EXTRA_ARGS:-}" ]]; then
  read -r -a extra_args <<< "$SBATCH_EXTRA_ARGS"
  for arg in "${extra_args[@]}"; do
    case "$arg" in
      --nodes*|--ntasks*|--cpus*|--export*|--wrap*|--chdir*|--output*|--error*|-N*|-n*|-c*|-o*|-e*|-D*)
        echo "Use harness settings instead of resource/output override: $arg"; exit 2 ;;
    esac
  done
fi
if [[ -n "${SIM_EXTRA_ARGS:-}" ]]; then
  echo "SIM_EXTRA_ARGS is unsupported; configure the named simulation settings"
  exit 2
fi

SBATCH_ARGS=(
  --export=ALL
  --job-name="${SLURM_JOB_NAME:-bratos-cells-$(date +%Y%m%d-%H%M%S)}"
  --nodes="$SBATCH_NODES" --ntasks="$SBATCH_NODES" --ntasks-per-node=1 --chdir="$PROJECT_ROOT"
  --output="$LOG_DIR/$LOG_PATTERN" --error="$LOG_DIR/$LOG_PATTERN"
)
for pair in \
  "account:${SBATCH_ACCOUNT:-}" "partition:${SBATCH_PARTITION:-}" \
  "qos:${SBATCH_QOS:-}" "time:${SBATCH_TIME:-}" \
  "cpus-per-task:$SBATCH_CPUS_PER_TASK" "mem:${SBATCH_MEM:-}" \
  "constraint:${SBATCH_CONSTRAINT:-}" "gpus-per-node:${SBATCH_GPUS_PER_NODE:-}"; do
  option="${pair%%:*}"
  value="${pair#*:}"
  if [[ -n "$value" ]]; then SBATCH_ARGS+=("--$option=$value"); fi
done
if [[ -n "${SBATCH_EXTRA_ARGS:-}" ]]; then SBATCH_ARGS+=("${extra_args[@]}"); fi

echo "Submitting $SERVICE_CELLS cells, $EPOCHS epochs, providers=$PROVIDERS, seeds=$SEEDS"
if [[ "${DRY_RUN:-0}" == 1 ]]; then
  echo "Preview only: no sync, directories, or job submission"
  printf 'Command: %q' "${SBATCH_BIN:-sbatch}"
  printf ' %q' "${SBATCH_ARGS[@]}" "$PROJECT_ROOT/pace/beam_sim.sbatch"
  printf '\nWorkers: %s (0 = allocated CPUs, capped by seed count)\n' "${SIM_WORKERS:-0}"
  echo "Results: $RESULTS_DIR/results-JOBID.json"
  exit 0
fi
SBATCH_BIN="${SBATCH_BIN:-sbatch}"
if ! command -v "$SBATCH_BIN" >/dev/null 2>&1; then
  echo "sbatch not found in PATH (SBATCH_BIN=$SBATCH_BIN)"
  exit 127
fi
load_modules
PYTHON_PATH="$(dirname "$VENV_ACTIVATE")/python"
if [[ ! -x "$PYTHON_PATH" ]]; then
  echo "Virtual environment not found: $VENV_ACTIVATE (run: make -C pace setup)"
  exit 2
fi
"$PYTHON_PATH" "$PROJECT_ROOT/harness/preflight.py" "$PROJECT_ROOT"

mkdir -p "$RESULTS_DIR" "$LOG_DIR" "$RUNS_DIR" "$(dirname "$JOB_FILE")"
RUN_DIR="$(mktemp -d "$RUNS_DIR/run-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
mkdir -p "$RUN_DIR/src" "$RUN_DIR/data" "$RUN_DIR/pace" "$RUN_DIR/harness"
cp "$PROJECT_ROOT/src/beam_sim.py" "$RUN_DIR/src/"
ORBITAL_SOURCE="${ORBITAL_SNAPSHOT:-data/orbital_snapshot.json}"
if [[ "$ORBITAL_SOURCE" != /* ]]; then ORBITAL_SOURCE="$PROJECT_ROOT/$ORBITAL_SOURCE"; fi
cp "$ORBITAL_SOURCE" "$RUN_DIR/data/orbital_snapshot.json"
export ORBITAL_SNAPSHOT=data/orbital_snapshot.json
cp "$PROJECT_ROOT/requirements.txt" "$RUN_DIR/"
cp "$SCRIPT_DIR/beam_sim.sbatch" "$SCRIPT_DIR/common.sh" "$SCRIPT_DIR/run_info.py" "$RUN_DIR/pace/"
cp "$PROJECT_ROOT/harness/config.sh" "$PROJECT_ROOT/harness/distributed.py" "$PROJECT_ROOT/harness/preflight.py" "$RUN_DIR/harness/"
write_config > "$RUN_DIR/config.env"
export ENV_FILE="$RUN_DIR/config.env"
export PACE_WORKFLOW_DIR="$RUN_DIR/pace"
export PROJECT_ROOT="$RUN_DIR"
git -C "$SCRIPT_DIR/.." rev-parse HEAD > "$RUN_DIR/git-revision.txt" 2>/dev/null || true

output=$("$SBATCH_BIN" "${SBATCH_ARGS[@]}" "$RUN_DIR/pace/beam_sim.sbatch")
echo "$output"
job_id=$(awk '/Submitted batch job/ {print $4; exit}' <<< "$output")
if [[ ! "$job_id" =~ ^[0-9]+$ ]]; then
  echo "Could not parse SLURM job id from sbatch output; frozen run: $RUN_DIR"
  exit 1
fi
printf '%s\n' "$job_id" > "$JOB_FILE"
printf '%s\n' "$job_id" > "$RUN_DIR/jobid"
echo "Tracked job id in $JOB_FILE"
echo "Frozen inputs: $RUN_DIR"
echo "Log: $LOG_DIR/${LOG_PATTERN//%j/$job_id}"
echo "Result: $RESULTS_DIR/results-$job_id.json"
