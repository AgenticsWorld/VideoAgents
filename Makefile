.PHONY: install install-dev run test

install:
	python -m pip install -e .

install-dev:
	python -m pip install -e ".[dev]"

run:
	python webui/server.py

test:
	pytest
