# Makefile for Atlas

.PHONY: install deps lint test run dev grant-worker-privileges

install:
	python -m pip install -e .

deps:
	python -m pip install -e .

lint:
	flake8 src tests

test:
	pytest

run:
	uvicorn src.atlas.main:app --host 0.0.0.0 --port 8000

dev:
	uvicorn src.atlas.main:app --reload --host 0.0.0.0 --port 8000

# Run once, after `alembic upgrade head` has created the schema -- see
# docker/grant-worker-privileges.sql for why this can't run automatically
# at container-init time.
grant-worker-privileges:
	docker compose exec -T postgres psql -U atlas -d atlas_db < docker/grant-worker-privileges.sql
