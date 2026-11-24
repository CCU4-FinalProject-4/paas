ENV_FILE ?= .env.floci

# First run: create the local profile from the template.
$(ENV_FILE):
	cp .env.example $@

-include $(ENV_FILE)
export

.PHONY: up down tables test

up: $(ENV_FILE) ## Start Floci, create the tables and start the API
	docker compose up -d floci
	@echo "Waiting for Floci..."
	@until curl -sf $(AWS_ENDPOINT_URL)/_floci/health >/dev/null; do sleep 1; done
	$(MAKE) tables
	docker compose up -d --build

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
