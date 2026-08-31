.PHONY: install dev down test lint

install:
	cd sdk && uv sync
	cd api && uv sync
	cd web && npm install

dev:
	docker compose up -d
	@echo "postgres -> localhost:5432   redis -> localhost:6379"

down:
	docker compose down

# web tests join here in Phase 6, when Vitest and the first component exist.
test:
	cd sdk && uv run pytest
	cd api && uv run pytest

lint:
	cd sdk && uv run ruff check . && uv run mypy spanscope
	cd api && uv run ruff check . && uv run mypy app
	cd web && npm run lint
