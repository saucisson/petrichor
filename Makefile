# Project template - task runner. `make help` lists every target.
# The day cycle targets (day-start, day-report, smoke-test) implement
# docs/protocol.md; the quality targets implement ADR-0004.

UV := uv
PYTHON := $(UV) run python
CONSOLE_SCRIPT := $(UV) run notify
DAY_TAG := daily-$(shell date +%F)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck imports test check security build \
        day-start day-report smoke-test mutate notify review merge-pr pr claim \
        claim-pr update-pr wait-ci night review-bundle sync-daily clean

help: ## List available targets with their description.
	@grep --extended-regexp '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the environment and the pre-commit hooks.
	$(UV) sync --locked
	$(UV) run pre-commit install

lint: ## Lint every file (Ruff, ADR-0002/0004).
	$(UV) run ruff check .

format: ## Format every file in place (Ruff).
	$(UV) run ruff format .

typecheck: ## Run the reference type checker (mypy strict, ADR-0003).
	$(UV) run mypy tools/notify/src tools/notify/tests scripts

imports: ## Verify the onion layer contract (import-linter, ADR-0002).
	$(UV) run lint-imports

test: ## Run the full test suite with the coverage floor (ADR-0004).
	$(UV) run pytest

check: lint typecheck imports test ## Full quality gate: run before reporting work.

security: ## Secret scan of the whole history (gitleaks, ADR-0004).
	gitleaks git --no-banner --redact .

build: ## Build the wheel and the sdist (ADR-0001).
	$(UV) build

day-start: ## Tag the morning restore point on `daily` (docs/protocol.md).
	git tag "$(DAY_TAG)"

day-report: ## Print the end-of-day report since the morning tag.
	$(PYTHON) -m scripts day-report --since-tag "$(DAY_TAG)"

smoke-test: ## Verify the notification pipeline via console delivery.
	$(CONSOLE_SCRIPT) smoke

mutate: ## Run mutation testing (ADR-0004; EOD on daily).
	$(UV) run mutmut run

notify: ## Notify the owner: make notify severity=... message=... [issue-identifier=...]
	$(CONSOLE_SCRIPT) send \
		--severity "$(severity)" \
		--message "$(message)" \
		--issue-identifier "$(issue_identifier)"

review: ## Run the local reviewer session on pull request PR=N (ADR-0006).
	$(PYTHON) -m scripts review --pr-number $(PR)

merge-pr: ## Merge pull request PR=N into daily after review and CI (ADR-0005).
	$(PYTHON) -m scripts merge-pr --pr-number $(PR)

pr: ## Open the pull request from the current branch: TITLE=... [BODY_FILE=...] (ADR-0007).
	$(PYTHON) -m scripts pr --title "$(TITLE)" --body-file "$(BODY_FILE)"

claim: ## Claim an issue before working it (auto-discovered night sessions): ISSUE=N (ADR-0007).
	$(PYTHON) -m scripts claim --issue "$(ISSUE)"

claim-pr: ## Claim a pull request before resuming it (night recovery): PR=N (ADR-0007).
	$(PYTHON) -m scripts claim --pr "$(PR)"

update-pr: ## Replace a pull request body from a file: PR=N BODY_FILE=... (ADR-0005).
	$(PYTHON) -m scripts update-pr --pr-number "$(PR)" --body-file "$(BODY_FILE)"

sync-daily: ## Rebase daily onto origin/main, early in the day, push with lease (ADR-0005).
	$(PYTHON) -m scripts sync-daily

wait-ci: ## Actively wait for CI on the pull request head: PR=N (ADR-0006).
	$(PYTHON) -m scripts wait-ci --pr-number "$(PR)"

night: ## Run the unattended loop: ISSUES="12,13,14", MILESTONES="3,4", or auto-discovery when empty (ADR-0007).
	$(PYTHON) -m scripts night --issues "$(ISSUES)" --milestones "$(MILESTONES)"

review-bundle: ## Print the reviewer bundle against origin/daily (ADR-0006).
	$(PYTHON) -m scripts review-bundle --base-ref origin/daily

clean: ## Remove caches, build outputs and mutation cache.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .mutmut-cache htmlcov \
		build dist .coverage coverage.xml .review
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
