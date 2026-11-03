#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_config
resolve_paths

JOB_FILE="${JOB_FILE:-$SCRIPT_DIR/.jobid}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
if [[ ! -s "$JOB_FILE" ]]; then
  echo "No tracked job. Submit one with: make -C pace submit"
  exit 1
fi
JOB_ID="$(tr -d '[:space:]' < "$JOB_FILE")"
if [[ ! "$JOB_ID" =~ ^[0-9]+$ ]]; then
  echo "Invalid tracked job ID: $JOB_FILE"
  exit 2
fi
ACTION="${ACTION:-status}"

case "$ACTION" in
  status)
    echo "Tracked job: $JOB_ID"
    if command -v squeue >/dev/null 2>&1; then
      squeue -j "$JOB_ID" -o '%.18i %.12T %.10M %.10l %.6D %R' || true
    fi
    if command -v sacct >/dev/null 2>&1; then
      sacct -j "$JOB_ID" --format=JobID,State,ExitCode,Elapsed,MaxRSS,AllocCPUS,Reason -P
    elif ! command -v squeue >/dev/null 2>&1; then
      echo "Neither squeue nor sacct is available"
      exit 127
    fi
    ;;
  logs)
    LOG_PATTERN="${LOG_PATTERN:-beam-sim-%j.log}"
    LOG_PATH="$LOG_DIR/${LOG_PATTERN//%j/$JOB_ID}"
    if [[ ! -f "$LOG_PATH" ]]; then
      echo "Log has not been created yet: $LOG_PATH"
      if command -v squeue >/dev/null 2>&1; then squeue -j "$JOB_ID" || true; fi
      exit 0
    fi
    echo "Tailing: $LOG_PATH"
    tail -n "${TAIL_LINES:-200}" "$LOG_PATH"
    ;;
  cancel)
    SCANCEL_BIN="${SCANCEL_BIN:-scancel}"
    if ! command -v "$SCANCEL_BIN" >/dev/null 2>&1; then
      echo "scancel not found in PATH (SCANCEL_BIN=$SCANCEL_BIN)"
      exit 127
    fi
    echo "Cancelling SLURM job: $JOB_ID"
    "$SCANCEL_BIN" "$JOB_ID"
    rm -f "$JOB_FILE"
    echo "Removed tracked job file"
    ;;
  *)
    echo "Unknown ACTION: $ACTION"
    exit 2
    ;;
esac
