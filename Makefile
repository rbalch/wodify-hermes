.PHONY: help install dev test lint login discover get-classes book clean

.DEFAULT_GOAL := help

# Overridable on the command line, e.g. `make get-classes DATE=2026-07-04 PROGRAM=119335`
DATE    ?= $(shell date +%F)
PROGRAM ?=
CLASS   ?=

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package (runtime deps only)
	python -m pip install -e .

dev: ## Install with test dependencies
	python -m pip install -e ".[test]"

test: ## Run the test suite (mocked, no live calls)
	pytest -q

lint: ## Lint with ruff (if installed)
	ruff check wodify tests

discover: ## First-time setup: resolve & save all config (prompts for creds)
	hermes-wodify discover

login: ## Authenticate and persist session config
	hermes-wodify login

get-classes: ## Fetch schedule. Vars: DATE=YYYY-MM-DD PROGRAM=<numeric id>
	hermes-wodify get-classes --date $(DATE) $(if $(PROGRAM),--program-filter $(PROGRAM),)

book: ## Book a class (REAL reservation). Required: CLASS=<class id>
	@test -n "$(CLASS)" || { echo "Usage: make book CLASS=<class id> [PROGRAM=<id>]"; exit 1; }
	hermes-wodify book $(CLASS) $(if $(PROGRAM),--program-id $(PROGRAM),)

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache **/__pycache__ *.egg-info wodify_hermes.egg-info
