# CloudLab backend for existing experiment nodes.
ENV_FILE ?= .env
THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
$(foreach key,$(filter-out ENV_FILE,$(.VARIABLES)),$(if $(filter command line,$(origin $(key))),$(eval export $(key))))

.PHONY: help env setup dry-run run smoke status logs cancel fetch wait
help:
	@echo "CloudLab adapter for existing SSH-accessible experiment nodes"
	@echo "env setup dry-run run smoke status logs cancel fetch wait"

env setup dry-run run smoke status logs cancel fetch wait:
	@ENV_FILE="$(ENV_FILE)" bash "$(THIS_DIR)/workflow.sh" "$@"
