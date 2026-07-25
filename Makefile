# Squirrel MCP -- dev & test tasks.
.DEFAULT_GOAL := help
VENV := .venv
PY := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
TY := $(VENV)/bin/ty
COMPOSE_TEST := docker compose -f docker-compose.test.yml

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

.PHONY: install
install: ## Create venv and install the package with dev extras
	uv venv --python 3.10
	uv pip install -e ".[dev]"

.PHONY: lint
lint: ## Run ruff + ty
	$(RUFF) check squirrel_mcp scripts tests
	$(TY) check

.PHONY: test
test: ## Unit tests (mocked, no docker)
	$(PYTEST) -m "not integration" -q

.PHONY: test-int
test-int: greenmail-up ## Real IMAP/SMTP e2e against GreenMail
	$(PYTEST) -m integration -q

.PHONY: greenmail-up
greenmail-up: ## Start just the GreenMail test mail server
	$(COMPOSE_TEST) up -d greenmail

.PHONY: docker-build
docker-build: ## Build the Squirrel image
	docker build -t squirrel-mcp:latest .

.PHONY: stack-up
stack-up: ## Build + start GreenMail and Squirrel (http) together
	$(COMPOSE_TEST) up -d --build

.PHONY: docker-smoke
docker-smoke: stack-up ## Full-stack Docker smoke test over MCP
	@echo "waiting for http://localhost:8000 ..."
	@for i in $$(seq 1 45); do $(PY) -c "import socket;socket.create_connection(('localhost',8000),1)" 2>/dev/null && break; sleep 1; done
	$(PY) scripts/mcp_smoke.py

.PHONY: playwright
playwright: stack-up ## Browser e2e (Playwright) against the running stack
	cd e2e && npm install && npx playwright install --with-deps chromium && npx playwright test

.PHONY: stack-down
stack-down: ## Stop and remove the test stack
	$(COMPOSE_TEST) down -v

.PHONY: test-all
test-all: ## Everything: lint, unit, integration, docker smoke, playwright
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) test-int
	$(MAKE) docker-smoke
	$(MAKE) playwright
	$(MAKE) stack-down

.PHONY: clean
clean: ## Remove caches and the test stack
	$(COMPOSE_TEST) down -v 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
