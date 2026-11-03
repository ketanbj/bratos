#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
ACTION="${1:-help}"
if [[ "$ACTION" == env ]]; then
  ENV_FILE="${ENV_FILE:-.env}"
  if [[ "$ENV_FILE" != /* ]]; then ENV_FILE="$SCRIPT_DIR/$ENV_FILE"; fi
  if [[ -f "$ENV_FILE" ]]; then
    echo "$ENV_FILE already exists"
  else
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    echo "Created $ENV_FILE"
  fi
  exit 0
fi
load_config

if [[ "$ACTION" == dry-run ]]; then
  DRY_RUN=1 bash "$SCRIPT_DIR/submit.sh"
  exit
fi
if [[ "$ACTION" == sync ]]; then
  bash "$SCRIPT_DIR/sync.sh"
  exit
fi
if [[ -n "${PACE_LOGIN:-}" && -z "${REMOTE_BYPASS:-}" ]]; then
  if [[ ! "$PACE_LOGIN" =~ ^[A-Za-z0-9._@-]+$ || "$PACE_LOGIN" == -* ||
        ! "${PACE_REMOTE_DIR:-}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    echo "Set PACE_LOGIN=user@host and an absolute PACE_REMOTE_DIR without spaces"
    exit 2
  fi
  if [[ "$ACTION" == fetch ]]; then
    for key in RESULTS_DIR LOG_DIR RUNS_DIR; do
      case "$key" in
        RESULTS_DIR) remote_path="${RESULTS_DIR:-pace/results}"; local_path="$SCRIPT_DIR/results" ;;
        LOG_DIR) remote_path="${LOG_DIR:-pace/logs}"; local_path="$SCRIPT_DIR/logs" ;;
        RUNS_DIR) remote_path="${RUNS_DIR:-pace/runs}"; local_path="$SCRIPT_DIR/runs" ;;
      esac
      if [[ "$remote_path" != /* ]]; then remote_path="$PACE_REMOTE_DIR/$remote_path"; fi
      mkdir -p "$local_path"
      "${PACE_RSYNC:-rsync}" -az -e "${PACE_SSH:-ssh}" "$PACE_LOGIN:$remote_path/" "$local_path/"
    done
    exit
  fi
  case "$ACTION" in setup|submit|smoke) bash "$SCRIPT_DIR/sync.sh" ;; esac
  echo "Running $ACTION on $PACE_LOGIN"
  # Send effective settings, including custom ENV_FILE and make overrides.
  {
    echo 'set -euo pipefail'
    printf 'cd %q\n' "$PACE_REMOTE_DIR/pace"
    write_config
    printf 'export ENV_FILE=/dev/null REMOTE_BYPASS=1\n'
    printf 'exec bash workflow.sh %q\n' "$ACTION"
  } | "${PACE_SSH:-ssh}" "$PACE_LOGIN" bash -s
  exit
fi

case "$ACTION" in
  setup) bash "$SCRIPT_DIR/setup_env.sh" ;;
  submit) bash "$SCRIPT_DIR/submit.sh" ;;
  smoke)
    SERVICE_CELLS=240 EPOCHS=8 SEEDS=7 PROVIDERS=starlink,kuiper \
      SIM_WORKERS=1 SIM_EXTRA_ARGS= SBATCH_GPUS_PER_NODE= SBATCH_NODES=1 SBATCH_CPUS_PER_TASK=1 SBATCH_TIME=00:10:00 \
      SBATCH_MEM=4G bash "$SCRIPT_DIR/submit.sh"
    ;;
  status|logs|cancel) ACTION="$ACTION" bash "$SCRIPT_DIR/job.sh" ;;
  fetch) echo "fetch requires remote mode (PACE_LOGIN)"; exit 2 ;;
  *) echo "Unknown action: $ACTION"; exit 2 ;;
esac
