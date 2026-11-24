ENV_FILE ?= .env.floci

# First run: create the local profile from the template.
$(ENV_FILE):
	cp .env.example $@

-include $(ENV_FILE)
export

.PHONY: up infra deploy down tables test e2e

TAG ?= local

up: deploy ## Start the whole stack built from local sources

infra: $(ENV_FILE) ## Start Floci and create the tables
	docker compose up -d floci
	@echo "Waiting for Floci..."
	@until curl -sf $(AWS_ENDPOINT_URL)/_floci/health >/dev/null; do sleep 1; done
	$(MAKE) tables

deploy: infra ## Deploy TAG (default: local build) and record it in SSM
	./scripts/deploy.sh $(TAG)

tables: ## Create the DynamoDB tables
	./scripts/create-tables.sh

down: ## Stop the stack (Floci data is kept in the volume)
	docker compose down

api/.venv: api/requirements-dev.txt
	python3.12 -m venv api/.venv
	api/.venv/bin/pip install -q -r api/requirements-dev.txt
	touch api/.venv

test: api/.venv ## Lint and run the API unit tests
	cd api && ruff check . && ruff format --check . && .venv/bin/pytest -q

e2e: api/.venv ## Statelessness and concurrency tests against the running stack
	api/.venv/bin/pytest -q tests/e2e
