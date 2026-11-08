# Maintainer shortcuts; product development uses bun/uv directly.

.DEFAULT_GOAL := release
.PHONY: release release-dry release-publish virustotal

PYTHON ?= python3
NODE ?= node

# Deletes only when DRY_RUN is unset or exactly one of 0/false/no; any other
# value, including several words such as 'false 0', previews (--dry-run).
CLEAN_DRY_RUN := $(strip $(DRY_RUN))
CLEAN_DRY_RUN_FLAG := $(if $(CLEAN_DRY_RUN),$(if $(and $(filter 1,$(words $(CLEAN_DRY_RUN))),$(filter 0 false no,$(CLEAN_DRY_RUN))),,--dry-run),)

.PHONY: clean\:all
clean\:all: ## Delete project dependencies, builds/caches and app data; DRY_RUN=1 previews
	$(NODE) scripts/clean-all.mjs $(CLEAN_DRY_RUN_FLAG)

release: ## Cut a release: asks stable/beta, draft/publish, then confirms
	uv run python scripts/release.py

release-dry: ## Show the release plan, change nothing
	uv run python scripts/release.py --dry-run

release-publish: ## Publish an existing draft: make release-publish TAG=v0.5.8
	@test -n "$(TAG)" || { echo "usage: make release-publish TAG=vX.Y.Z[-beta.N]"; exit 2; }
	uv run python scripts/release.py --publish $(TAG)

virustotal: export VIRUSTOTAL_FILE = $(FILE)
virustotal: ## Upload committed HEAD source ZIP, or make virustotal FILE=/path/to/public/file
	$(PYTHON) scripts/virustotal.py
