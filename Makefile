# ephemeral-sites - local developer quality gate.
#
# Mirror of .github/workflows/test.yml, runnable in <2s on a warm venv.
# See CLAUDE.md §4bis for the "run `make check` before every push" rule.

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF   ?= ruff

# Unless overridden, let pytest pick up src/ via PYTHONPATH. This mirrors
# how the installed package would resolve imports inside a poetry venv.
export PYTHONPATH := src

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "ephemeral-sites — make targets:\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install dev dependencies (prefers poetry, falls back to pip)
	@if command -v poetry >/dev/null 2>&1; then \
		echo ">>> poetry install --with dev"; \
		poetry install --with dev --no-interaction; \
	else \
		echo ">>> pip install (poetry not found)"; \
		$(PIP) install --quiet pytest pytest-asyncio pytest-cov ruff; \
		$(PIP) install --quiet fastapi 'uvicorn[standard]' pydantic pydantic-settings python-multipart bcrypt prometheus-client || true; \
	fi

.PHONY: lint
lint: ## Ruff check + format --check (same as CI)
	$(RUFF) check .
	$(RUFF) format --check .

.PHONY: format
format: ## Auto-apply ruff format + fix
	$(RUFF) format .
	$(RUFF) check --fix .

.PHONY: test
test: ## Run the full test suite with coverage
	$(PYTEST) -v --cov --cov-report=term-missing

.PHONY: test-fast
test-fast: ## Run tests without coverage (fastest feedback)
	$(PYTEST) -x --ff

.PHONY: test-unit
test-unit: ## Run only unit tests (marked @pytest.mark.unit)
	$(PYTEST) -v -m unit

.PHONY: test-security
test-security: ## Run only security tests (marked @pytest.mark.security)
	$(PYTEST) -v -m security

.PHONY: check
check: lint test ## Full pre-push gate: lint + test + coverage (== CI)
	@echo ""
	@echo "✔  make check passed — safe to commit and push."

.PHONY: docker-build
docker-build: ## Build the production Docker image locally
	docker build -t ephemeral-sites:dev .

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
