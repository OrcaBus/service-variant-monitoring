.PHONY: install test check fix

install:
	pip install -r requirements-dev.txt

test:
	pytest tests/ --cov=variant_monitoring --cov-report=term-missing

check:
	ruff check variant_monitoring tests

fix:
	ruff check --fix variant_monitoring tests
	ruff format variant_monitoring tests
