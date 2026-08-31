.PHONY: install dev down migrate test lint

# Makes .env vars (POSTGRES_USER etc.) available to recipes below, e.g. `migrate`.
-include .env
export

install:
	cd sdk && uv sync
	cd api && uv sync
	cd web && npm install

dev:
	docker compose up -d
	@echo "postgres -> localhost:5432   redis -> localhost:6379"

down:
	docker compose down

# Applies api/migrations/*.sql via psql inside the postgres container, in filename order.
# No local psql needed (the postgres image ships it), no separate migration-tool dependency.
# Each file is idempotent (IF NOT EXISTS), so re-running this is safe.
migrate:
	@for f in api/migrations/*.sql; do \
		echo "applying $$f"; \
		docker compose exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -v ON_ERROR_STOP=1 < $$f || exit 1; \
	done

# web tests join here in Phase 6, when Vitest and the first component exist.
test:
	cd sdk && uv run pytest
	cd api && uv run pytest

lint:
	cd sdk && uv run ruff check . && uv run mypy spanscope
	cd api && uv run ruff check . && uv run mypy app
	cd web && npm run lint
