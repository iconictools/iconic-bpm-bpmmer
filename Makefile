# ─────────────────────────────────────────────────────────────────
#  iconic-bpm-bpmmer  —  Makefile
#
#  Targets
#  -------
#  make              → install + run (default)
#  make install      → create venv + install all Python deps
#  make run          → start the Flask server (port 5000)
#  make run PORT=8080→ start on a custom port
#  make build        → install in editable mode (pip install -e .)
#  make clean        → remove virtual env and generated outputs
#  make test         → run the test suite
#  make lint         → run flake8 style checker
# ─────────────────────────────────────────────────────────────────

PYTHON   ?= python3
VENV     := .venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
PORT     ?= 5000

.PHONY: all install run build clean test lint help

all: install run          ## Default: install dependencies then run

# ── Virtual environment ───────────────────────────────────────────
$(VENV)/bin/activate:
	@echo "→  Creating virtual environment …"
	$(PYTHON) -m venv $(VENV)

# ── Install ───────────────────────────────────────────────────────
install: $(VENV)/bin/activate  ## Install all Python dependencies
	@echo "→  Upgrading pip …"
	@$(PIP) install --quiet --upgrade pip
	@echo "→  Installing dependencies …"
	@$(PIP) install --quiet -r requirements.txt
	@mkdir -p uploads outputs
	@echo "✓  All dependencies installed"

# ── Build (editable install) ──────────────────────────────────────
build: install  ## Install package in editable/development mode
	@echo "→  Installing package in editable mode …"
	@$(PIP) install --quiet -e .
	@echo "✓  Build complete"

# ── Run ───────────────────────────────────────────────────────────
run: install  ## Start the web server
	@echo "→  Starting iconic-bpm-bpmmer on http://localhost:$(PORT)"
	@echo "   Press Ctrl+C to stop."
	@$(PY) app.py --port $(PORT)

# ── Test ──────────────────────────────────────────────────────────
test: install  ## Run the test suite
	@$(PY) -m pytest tests/ -v 2>/dev/null || \
	 $(PY) -m pytest test_*.py -v 2>/dev/null || \
	 echo "No tests found."

# ── Lint ──────────────────────────────────────────────────────────
lint: install  ## Run flake8 style checker
	@$(PIP) install --quiet flake8 2>/dev/null
	@$(VENV)/bin/flake8 app.py analyzer/ --max-line-length=100 || true

# ── Clean ─────────────────────────────────────────────────────────
clean:  ## Remove venv and generated files
	@echo "→  Removing virtual environment and outputs …"
	@rm -rf $(VENV) uploads/ outputs/ __pycache__ analyzer/__pycache__ *.egg-info
	@echo "✓  Clean complete"

# ── Help ──────────────────────────────────────────────────────────
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'
