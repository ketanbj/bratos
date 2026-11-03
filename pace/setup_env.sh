#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_config
resolve_paths

load_modules

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; sys.exit("Python >=3.10 required; set PACE_MODULES to a newer Python module" if sys.version_info < (3, 10) else 0)'
VENV_ACTIVATE="${VENV_ACTIVATE:-.venv/bin/activate}"
if [[ "$VENV_ACTIVATE" != /* ]]; then
  VENV_ACTIVATE="$PROJECT_ROOT/$VENV_ACTIVATE"
fi
VENV_DIR="$(cd "$(dirname "$VENV_ACTIVATE")/.." 2>/dev/null && pwd || true)"
if [[ -z "$VENV_DIR" ]]; then
  VENV_DIR="$(dirname "$(dirname "$VENV_ACTIVATE")")"
fi

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "Creating virtual environment: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
python -c 'import sys; sys.exit("Existing virtualenv uses Python <3.10; upgrade or recreate it with the configured Python" if sys.version_info < (3, 10) else 0)'
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_ROOT/requirements.txt"
python -c 'import h3, sgp4; print("PACE environment ready: h3", h3.__version__, "sgp4", sgp4.__version__)'
