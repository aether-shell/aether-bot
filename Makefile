SHELL := /bin/bash

.PHONY: lint lint-py test test-py build ci

lint: lint-py

lint-py:
	python -m ruff check nanobot aether_bot_web

test: test-py

test-py:
	python -m pytest -q

build:

ci: lint test build
