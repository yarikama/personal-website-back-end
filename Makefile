SHELL := /bin/bash

# Variables definitions
# -----------------------------------------------------------------------------

ifeq ($(TIMEOUT),)
TIMEOUT := 60
endif

ENV ?= local
ENV_FILE := .env.${ENV}

# Target section and Global definitions
# -----------------------------------------------------------------------------
.PHONY: all clean test coverage install run deploy down lint format logs shell rebuild

all: clean install test

install: generate_dot_env
	uv sync --all-extras

test:
	uv run pytest -vv --show-capture=all

# Settings in pyproject.toml [tool.coverage]; the report lists the lines no
# test runs.
coverage:
	uv run pytest -q --cov

run:
	PYTHONPATH=app/ uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080

lint:
	uv run ruff check app/

format:
	uv run ruff format app/
	uv run ruff check --fix app/

deploy: generate_dot_env
	@echo "Running in ${ENV} environment..."
	ENV_FILE=$(ENV_FILE) docker-compose build
	ENV_FILE=$(ENV_FILE) docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f app

shell:
	docker-compose exec app bash

rebuild:
	docker-compose build --no-cache
	docker-compose up -d

generate_dot_env:
	@if [[ ! -e .env.local ]]; then \
		cp .env.example .env.local; \
		echo "Created .env.local (Modify for Docker)"; \
	fi
	@if [[ ! -e .env.prod ]]; then \
		cp .env.example .env.prod; \
		echo "Created .env.prod (Modify for production secrets: auth, R2)"; \
	fi

clean:
	@find . -name '*.pyc' -exec rm -rf {} \;
	@find . -name '__pycache__' -exec rm -rf {} \;
	@find . -name 'Thumbs.db' -exec rm -rf {} \;
	@find . -name '*~' -exec rm -rf {} \;
	rm -rf .cache
	rm -rf build
	rm -rf dist
	rm -rf *.egg-info
	rm -rf htmlcov
	rm -rf .tox/
	rm -rf docs/_build

# Database Migrations
# -----------------------------------------------------------------------------

revision:
	docker-compose exec app alembic revision --autogenerate -m "$(msg)"

upgrade:
	docker-compose exec app alembic upgrade head

downgrade:
	docker-compose exec app alembic downgrade -1

history:
	docker-compose exec app alembic history --verbose

stamp:
	docker-compose exec app alembic stamp head
