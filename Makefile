.PHONY: dev lint format test typecheck check build

dev:
	uv sync --all-groups --all-packages

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest

typecheck:
	uv run pyright

check: lint typecheck test

build:
	uv build --all-packages
