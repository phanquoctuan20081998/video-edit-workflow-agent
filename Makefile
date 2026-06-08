.PHONY: dev webui worker migrate sandbox-build install test lint

install:
	uv sync

dev:
	uvicorn api.main:app --reload --port 8000

webui:
	streamlit run webui/app.py

worker:
	celery -A app.orchestration.celery_app worker --loglevel=info --concurrency=1

migrate:
	alembic upgrade head

sandbox-build:
	docker build -t manim-sandbox ./docker/manim-sandbox/

sandbox-test:
	python -m app.sandbox.runner

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy app/ api/ --ignore-missing-imports

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache

# End-to-end smoke test (requires LLM API key in .env)
smoke:
	python -m app.orchestration.graph "Fast Fourier Transform"
