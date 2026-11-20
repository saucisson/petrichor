# Project template - task runner. `make help` lists every target.
# The day cycle targets (day-start, day-report, smoke-test) implement
# docs/branching.md; the quality targets implement ADR-0007/0008.

UV := uv
PYTHON := $(UV) run python
CONSOLE_SCRIPT := $(UV) run notify
DAY_TAG := daily-$(shell date +%F)
UI_DIR := src/petrichor/api/ui

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck imports test check security build \
        day-start day-report smoke-test mutate notify review merge-pr pr claim \
        claim-pr update-pr wait-ci night review-bundle sync-daily clean \
        ui-gates ui-build

help: ## List available targets with their description.
	@grep --extended-regexp '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the environment and the pre-commit hooks.
	$(UV) sync --locked
	$(UV) run pre-commit install

lint: ## Lint every file (Ruff, ADR-0004/0008).
	$(UV) run ruff check .

format: ## Format every file in place (Ruff).
	$(UV) run ruff format .

typecheck: ## Run the reference type checker (mypy strict, ADR-0006).
	$(UV) run mypy tools/notify/src tools/notify/tests scripts src

imports: ## Verify the onion layer contract (import-linter, ADR-0002).
	$(UV) run lint-imports

test: ## Run the full test suite with the coverage floor (ADR-0007). The @ui browser e2e is excluded here: it runs in its own CI job and via `make ui-e2e` (issue #128).
	$(UV) run pytest -m "not ui"

check: lint typecheck imports test ui-gates ## Full quality gate: run before reporting work.

ui-gates: ## Frontend gates of the UI v2 (ADR-0101): ESLint, tsc --noEmit, vitest. Node pinned by .node-version.
	cd $(UI_DIR) && { [ -d node_modules ] || npm ci --silent; } && npm run lint && npm run typecheck && npm run test

ui-build: ## Build the UI v2 into src/petrichor/api/static (served under /ui/, ADR-0101).
	cd $(UI_DIR) && npm run build

ui-e2e-deps: ## Install the UI dependencies and the Playwright Chromium browser (for the @ui e2e, issue #128). Needs node on PATH (.node-version).
	cd $(UI_DIR) && { [ -d node_modules ] || npm ci --silent; } && npx playwright install --with-deps chromium

ui-e2e: ui-e2e-deps ## E2E navigateur de la voie UI (Playwright, marqueur @ui, issue #128): real browser against the real API on the real compose engines. Needs node on PATH (.node-version) and docker.
	$(UV) run pytest -m ui tests/test_ui_e2e.py --no-cov

security: ## Secret scan of the whole history (gitleaks, ADR-0008).
	gitleaks git --no-banner --redact .

build: ## Build the wheel and the sdist (ADR-0001).
	$(UV) build

day-start: ## Tag the morning restore point on `daily` (docs/branching.md).
	git tag "$(DAY_TAG)"

day-report: ## Print the end-of-day report since the morning tag.
	$(PYTHON) -m scripts day-report --since-tag "$(DAY_TAG)"

sync-daily: ## Rebase daily onto origin/main, early in the day, push with lease (ADR-0020).
	$(PYTHON) -m scripts sync-daily

smoke-test: ## Verify the notification pipeline via console delivery.
	$(CONSOLE_SCRIPT) smoke

mutate: ## Run mutation testing (ADR-0012; EOD on daily).
	$(UV) run mutmut run

notify: ## Notify the owner: make notify severity=... message=... [issue-identifier=...]
	$(CONSOLE_SCRIPT) send \
		--severity "$(severity)" \
		--message "$(message)" \
		--issue-identifier "$(issue_identifier)"

review: ## Run the local reviewer session on pull request PR=N (ADR-0010).
	$(PYTHON) -m scripts review --pr-number $(PR)

merge-pr: ## Merge pull request PR=N into daily after review and CI (ADR-0009).
	$(PYTHON) -m scripts merge-pr --pr-number $(PR)

pr: ## Open the pull request from the current branch: TITLE=... [BODY_FILE=...] (ADR-0017).
	$(PYTHON) -m scripts pr --title "$(TITLE)" --body-file "$(BODY_FILE)"

claim: ## Claim an issue before working it (auto-discovered night sessions): ISSUE=N (ADR-0017).
	$(PYTHON) -m scripts claim --issue "$(ISSUE)"

claim-pr: ## Claim a pull request before resuming it (night recovery): PR=N (ADR-0017).
	$(PYTHON) -m scripts claim --pr "$(PR)"

update-pr: ## Replace a pull request body from a file: PR=N BODY_FILE=... (ADR-0019).
	$(PYTHON) -m scripts update-pr --pr-number "$(PR)" --body-file "$(BODY_FILE)"

wait-ci: ## Actively wait for CI on the pull request head: PR=N (ADR-0018).
	$(PYTHON) -m scripts wait-ci --pr-number "$(PR)"

night: ## Run the unattended loop: ISSUES="12,13,14", MILESTONES="3,4", or auto-discovery when empty (ADR-0017).
	$(PYTHON) -m scripts night --issues "$(ISSUES)" --milestones "$(MILESTONES)"

review-bundle: ## Print the reviewer bundle against origin/daily (ADR-0010).
	$(PYTHON) -m scripts review-bundle --base-ref origin/daily

OUTPUT_DIR ?= .petrichor/study-p
CHECK_TIMEOUT ?= 300

study-p: ## Run the pilote study session on the frozen v1 materials (spec §20, issue #41).
	$(PYTHON) -m scripts study-p --output-dir "$(OUTPUT_DIR)" --check-timeout $(CHECK_TIMEOUT)

RECORD_DIR ?= docs/studies/pilote/v1/execution

study-p-audit: ## Audit the pilote execution record: no verdict without an archived run (spec §20.10, issue #42).
	$(PYTHON) -m scripts study-p-audit --record-dir "$(RECORD_DIR)"

clean: ## Remove caches, build outputs, mutation cache and runtime state.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .mutmut-cache htmlcov \
		build dist .coverage coverage.xml .review .petrichor
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
