#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PACE_LOGIN="${PACE_LOGIN:-}"
PACE_REMOTE_DIR="${PACE_REMOTE_DIR:-}"
PACE_SSH="${PACE_SSH:-ssh}"
PACE_RSYNC="${PACE_RSYNC:-rsync}"
SYNC_MARKER=".bratos-pace-sync-root"

if [[ -z "$PACE_LOGIN" || -z "$PACE_REMOTE_DIR" ]]; then
  echo "PACE_LOGIN and PACE_REMOTE_DIR are required"
  exit 2
fi
if [[ ! "$PACE_LOGIN" =~ ^[A-Za-z0-9._@-]+$ || "$PACE_LOGIN" == -* ]]; then
  echo "PACE_LOGIN contains unsupported characters: $PACE_LOGIN"
  exit 2
fi
if [[ ! "$PACE_REMOTE_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "PACE_REMOTE_DIR must be an absolute path without spaces: $PACE_REMOTE_DIR"
  exit 2
fi
case "$PACE_REMOTE_DIR" in
  /|/home|/scratch|/storage|/data|/tmp|/var|/usr|/Users)
    echo "Refusing to synchronize into broad remote directory: $PACE_REMOTE_DIR"
    exit 2
    ;;
esac
if ! command -v "$PACE_SSH" >/dev/null 2>&1; then
  echo "SSH command not found: $PACE_SSH"
  exit 127
fi
if ! command -v "$PACE_RSYNC" >/dev/null 2>&1; then
  echo "rsync not found: $PACE_RSYNC"
  exit 127
fi

echo "Preparing sync target $PACE_LOGIN:$PACE_REMOTE_DIR"
"$PACE_SSH" "$PACE_LOGIN" bash -s -- "$PACE_REMOTE_DIR" "$SYNC_MARKER" <<'REMOTE'
set -euo pipefail
target="$1"
marker="$2"
if [[ -e "$target" && ! -d "$target" ]]; then
  echo "Remote sync target exists but is not a directory: $target"
  exit 2
fi
mkdir -p "$target"
if [[ ! -f "$target/$marker" ]]; then
  if find "$target" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "Refusing to mirror into a non-empty, unmarked directory: $target"
    echo "Choose an empty PACE_REMOTE_DIR, or verify the directory and create $target/$marker manually."
    exit 2
  fi
  touch "$target/$marker"
fi
REMOTE

RSYNC_COMMON=(
  -az
  -e "$PACE_SSH"
  --no-owner
  --no-group
  --omit-dir-times
  --delete
  --exclude ".git/"
  --exclude ".venv/"
  --exclude "__pycache__/"
  --exclude "demo/node_modules/"
  --exclude "demo/.next/"
  --exclude "demo/dist/"
  --exclude "demo/.wrangler/"
  --exclude "demo/.vinext/"
  --exclude "pace/runs/"
  --exclude "pace/.jobid"
  --exclude "pace/logs/"
  --exclude "pace/results/"
  --exclude "cloudlab/state/"
  --exclude "$SYNC_MARKER"
)
for key in RESULTS_DIR LOG_DIR RUNS_DIR; do
  value="${!key:-}"
  if [[ -n "$value" ]]; then
    if [[ "$value" == "$PROJECT_ROOT/"* ]]; then value="${value#"$PROJECT_ROOT/"}"; fi
    if [[ "$value" != /* ]]; then RSYNC_COMMON+=(--exclude "$value/"); fi
  fi
done
SOURCE="$PROJECT_ROOT/"
DESTINATION="$PACE_LOGIN:$PACE_REMOTE_DIR/"

echo "Mirroring local checkout; stale remote source files will be removed"
"$PACE_RSYNC" "${RSYNC_COMMON[@]}" --itemize-changes "$SOURCE" "$DESTINATION"

echo "Verifying remote checkout by checksum"
verification=$(
  "$PACE_RSYNC" "${RSYNC_COMMON[@]}" --checksum --dry-run --itemize-changes \
    "$SOURCE" "$DESTINATION"
)
if [[ -n "$verification" ]]; then
  echo "Checking reported checksum differences independently with SHA-256"
  python3 "$SCRIPT_DIR/verify_rsync.py" "$PROJECT_ROOT" <<< "$verification" |
    "$PACE_SSH" "$PACE_LOGIN" python3 - "$PACE_REMOTE_DIR"
fi
echo "Remote checkout is byte-for-byte in sync for all managed files"
