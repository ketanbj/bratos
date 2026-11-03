#!/usr/bin/env bash
# Configuration loading shared by cluster backends. Each defines CONFIG_KEYS.
load_config() {
  local key index=0 saved_count=0
  local saved_keys=() saved_values=()
  for key in "${CONFIG_KEYS[@]}"; do
    if [[ -n "${!key+x}" ]]; then
      saved_keys+=("$key")
      saved_values+=("${!key}")
      saved_count=$((saved_count + 1))
    fi
  done
  ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
  if [[ "$ENV_FILE" != /* ]]; then ENV_FILE="$SCRIPT_DIR/$ENV_FILE"; fi
  if [[ "$ENV_FILE" != /dev/null && ! -f "$ENV_FILE" ]]; then
    echo "Missing configuration: $ENV_FILE (run make env in the selected backend directory)"
    return 2
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  while (( index < saved_count )); do
    key="${saved_keys[$index]}"
    printf -v "$key" '%s' "${saved_values[$index]}"
    export "$key"
    index=$((index + 1))
  done
}

write_config() {
  local key
  for key in "${CONFIG_KEYS[@]}"; do
    if [[ -n "${!key+x}" ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
  done
}
