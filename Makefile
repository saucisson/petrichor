# Petrichor - task runner. `make help` lists every target.
# The day cycle targets (day-start, day-report, smoke-test) implement
# docs/branching.md; the quality targets implement ADR-0008/0009.

UV := uv
PYTHON := $(UV) run python
CONSOLE_SCRIPT := $(UV) run notify
WEB_DIRECTORY := apps/web
WEB := npm --prefix $(WEB_DIRECTORY)
DAY_TAG := daily-$(shell date +%F)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck imports test check security build \
        day-start day-report smoke-test smoke-docker compose-up compose-down \
        mutate notify review-bundle web-install web-lint web-typecheck \
        web-test web-check clean

help: ## List available targets with their description.
	@grep --extended-regexp '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the environment and the pre-commit hooks.
	$(UV) sync --locked
	$(UV) run pre-commit install

lint: ## Lint every file (Ruff, ADR-0004/0009).
	$(UV) run ruff check .

format: ## Format every file in place (Ruff).
	$(UV) run ruff format .

typecheck: ## Run the reference type checker (mypy strict, ADR-0006/0007).
	$(UV) run mypy tools/notify/src tools/notify/tests scripts

imports: ## Verify the onion layer contract (import-linter, ADR-0002).
	$(UV) run lint-imports

test: ## Run the full test suite with the coverage floor (ADR-0008).
	$(UV) run pytest

check: lint typecheck imports test web-check ## Full quality gate (Python + web): run before reporting work.

security: ## Secret scan of the whole history (gitleaks, ADR-0009).
	gitleaks git --no-banner --redact .

build: ## Build the wheel and the sdist (hatchling, ADR-0014).
	$(UV) build

day-start: ## Tag the morning restore point on `daily` (docs/branching.md).
	git tag "$(DAY_TAG)"

day-report: ## Print the end-of-day report since the morning tag.
	$(PYTHON) scripts/day_report.py --since-tag "$(DAY_TAG)"

smoke-test: ## Verify the notification pipeline via console delivery.
	$(CONSOLE_SCRIPT) smoke

smoke-docker: ## Same, inside the production image.
	docker compose run --rm notify

compose-up: ## Build and start the shared runtime (notify + web, ADR-0018).
	docker compose up --build --detach

compose-down: ## Stop the shared runtime.
	docker compose down

mutate: ## Run mutation testing on src (ADR-0013; EOD on daily).
	$(UV) run mutmut run

notify: ## Notify the owner: make notify severity=... message=... [issue-identifier=...]
	$(CONSOLE_SCRIPT) send \
		--severity "$(severity)" \
		--message "$(message)" \
		--issue-identifier "$(issue_identifier)"

review-bundle: ## Print the reviewer bundle against origin/daily (ADR-0016).
	$(PYTHON) scripts/build_review_bundle.py --base-ref origin/daily

web-install: ## Install the web dependencies and write package-lock.json.
	$(WEB) install

web-lint: ## Lint the web app (strict type-checked eslint, ADR-0018).
	$(WEB) run lint

web-typecheck: ## Type-check the web app (tsc strict-plus, ADR-0018).
	$(WEB) run typecheck

web-test: ## Run the web test suite with the coverage floor (ADR-0018).
	$(WEB) run test

web-check: web-lint web-typecheck web-test ## Web quality gate.

clean: ## Remove caches, build outputs and mutation cache.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .mutmut-cache htmlcov \
		build dist .coverage coverage.xml .review
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
