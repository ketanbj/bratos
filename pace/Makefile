ENV_FILE ?= .env
THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# .env is a shell file. Export only explicit make command-line overrides.
$(foreach key,$(filter-out ENV_FILE,$(.VARIABLES)),$(if $(filter command line,$(origin $(key))),$(eval export $(key))))

.PHONY: help env sync setup submit smoke dry-run status logs cancel fetch up down
help:
	@echo "PACE CPU beam-simulation harness (adapted from ray_run_harness)"
	@echo "env       Create configuration"
	@echo "setup     Sync and install Python dependencies on PACE"
	@echo "dry-run   Validate and print submission without SSH or sbatch"
	@echo "smoke     Submit a small two-provider validation job"
	@echo "submit    Sync and submit configured simulation (alias: up)"
	@echo "status    Show queue state, elapsed time, and memory"
	@echo "logs      Tail output; TAIL_LINES=1000 changes length"
	@echo "cancel    Cancel tracked job (alias: down)"
	@echo "sync      Mirror and checksum-verify remote checkout"
	@echo "fetch     Retrieve results, logs, and frozen run inputs"

env sync setup submit smoke dry-run status logs cancel fetch:
	@ENV_FILE="$(ENV_FILE)" bash "$(THIS_DIR)/workflow.sh" "$@"

up: submit
down: cancel
