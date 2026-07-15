.PHONY: install
install: ## Install the virtual environment and install the pre-commit hooks
	@echo "🚀 Creating virtual environment using uv"
	@uv sync --all-extras
	@uv run pre-commit install

.PHONY: check
check: ## Run code quality tools.
	@echo "🚀 Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "🚀 Linting code: Running pre-commit"
	@uv run pre-commit run -a
	@echo "🚀 Static type checking: Running ty type check"
	@uv run ty check

.PHONY: test
test: ## Run unit tests
	@echo "🚀 Testing code: Running pytest"
	@uv run python -m pytest tests/unit --doctest-modules

.PHONY: integration
integration: ## Run integration tests (requires .env)
	@echo "🚀 Running integration tests"
	@uv run --env-file .env python -m pytest tests/integration --doctest-modules --logfire

# Compose resolves its default .env relative to the compose file's directory, so the
# repo-root .env must be passed explicitly to keep the container and the integration
# tests reading the same LLM_PORT / LLM_MODEL values.
COMPOSE := docker compose --env-file .env -f tests/integration/docker-compose.yml

.PHONY: llm-up
llm-up: ## Start vLLM docker container for integration tests (requires .env)
	@test -f .env || { echo "❌ .env not found — copy .env.example to .env first"; exit 1; }
	@echo "🚀 Starting vLLM docker container"
	@$(COMPOSE) up -d

.PHONY: llm-logs
llm-logs: ## Follow the vLLM container logs
	@$(COMPOSE) logs -f

.PHONY: llm-down
llm-down: ## Stop the vLLM docker container
	@echo "🚀 Stopping vLLM docker container"
	@$(COMPOSE) down

.PHONY: build
build: clean-build ## Build wheel file
	@echo "🚀 Creating wheel file"
	@uvx --from build pyproject-build --installer uv

.PHONY: clean-build
clean-build: ## Clean build artifacts
	@echo "🚀 Removing build artifacts"
	@uv run python -c "import shutil; import os; shutil.rmtree('dist') if os.path.exists('dist') else None"

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
