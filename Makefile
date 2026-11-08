# Project template - task runner. `make help` lists every target.
# The day cycle targets (day-start, day-report, smoke-test) implement
# docs/branching.md; the quality targets implement ADR-0007/0008.

UV := uv
PYTHON := $(UV) run python
CONSOLE_SCRIPT := $(UV) run notify
DAY_TAG := daily-$(shell date +%F)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck imports test check security build \
        day-start day-report smoke-test mutate notify review merge-pr pr claim \
        claim-pr wait-ci night review-bundle clean

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
	$(UV) run mypy tools/notify/src tools/notify/tests scripts

imports: ## Verify the onion layer contract (import-linter, ADR-0002).
	$(UV) run lint-imports

test: ## Run the full test suite with the coverage floor (ADR-0007).
	$(UV) run pytest

check: lint typecheck imports test ## Full quality gate: run before reporting work.

security: ## Secret scan of the whole history (gitleaks, ADR-0008).
	gitleaks git --no-banner --redact .

build: ## Build the wheel and the sdist (ADR-0001).
	$(UV) build

day-start: ## Tag the morning restore point on `daily` (docs/branching.md).
	git tag "$(DAY_TAG)"

day-report: ## Print the end-of-day report since the morning tag.
	$(PYTHON) scripts/day_report.py --since-tag "$(DAY_TAG)"

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
	$(PYTHON) scripts/pr_review.py --pr-number $(PR)

merge-pr: ## Merge pull request PR=N into daily after review and CI (ADR-0009).
	$(PYTHON) scripts/merge_pr.py --pr-number $(PR)

pr: ## Open the pull request from the current branch: TITLE=... [BODY_FILE=...] (ADR-0017).
	$(PYTHON) scripts/open_pr.py --title "$(TITLE)" --body-file "$(BODY_FILE)"

claim: ## Claim an issue before working it (auto-discovered night sessions): ISSUE=N (ADR-0017).
	$(PYTHON) scripts/claim_issue.py --issue "$(ISSUE)"

claim-pr: ## Claim a pull request before resuming it (night recovery): PR=N (ADR-0017).
	$(PYTHON) scripts/claim_issue.py --pr "$(PR)"

wait-ci: ## Actively wait for CI on the pull request head: PR=N (ADR-0018).
	$(PYTHON) scripts/wait_ci.py --pr-number "$(PR)"

night: ## Run the unattended loop: ISSUES="12,13,14", MILESTONES="3,4", or auto-discovery when empty (ADR-0017).
	$(PYTHON) scripts/night_loop.py --issues "$(ISSUES)" --milestones "$(MILESTONES)"

review-bundle: ## Print the reviewer bundle against origin/daily (ADR-0010).
	$(PYTHON) scripts/build_review_bundle.py --base-ref origin/daily

clean: ## Remove caches, build outputs and mutation cache.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .mutmut-cache htmlcov \
		build dist .coverage coverage.xml .review
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
