.PHONY: setup test lint clean

PYTHON ?= python3.12
VENV := .venv

setup: $(VENV)/bin/activate

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e "packages/parser[test]"
	$(VENV)/bin/pip install -e "packages/trace-sdk[test]"
	@echo "✅ venv ready — activate with: source .venv/bin/activate"

test: $(VENV)/bin/activate
	$(VENV)/bin/python -m pytest packages/parser/tests packages/trace-sdk/tests -v

lint: $(VENV)/bin/activate
	$(VENV)/bin/python -m ruff check packages/

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
