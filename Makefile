ENV_FILE ?= .env.floci

# First run: create the local profile from the template.
$(ENV_FILE):
	cp .env.example $@

-include $(ENV_FILE)
export

.PHONY: up down tables

up: $(ENV_FILE) ## Start Floci and create the tables
	docker compose up -d
	@echo "Waiting for Floci..."
	@until curl -sf $(AWS_ENDPOINT_URL)/_floci/health >/dev/null; do sleep 1; done
	$(MAKE) tables

tables: ## Create the DynamoDB tables
	./scripts/create-tables.sh

down: ## Stop the stack (Floci data is kept in the volume)
	docker compose down
