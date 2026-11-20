PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: setup run test reset-db clean

## setup: create the virtualenv, install dependencies, create .env and build the database
setup: $(VENV)/.installed .env reset-db

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e '.[dev]'
	touch $@

.env:
	cp .env.example .env
	@echo ">> Created .env - add your LLM_API_KEY (OpenRouter key) to it."

## run: start the web app on http://127.0.0.1:8000
run:
	$(BIN)/uvicorn app.web.main:app --reload

## test: run the automated tests (no API key or network needed)
test:
	$(BIN)/pytest -q

## reset-db: rebuild data/app.db from the seed data (drops all bookings and chats made in the app)
reset-db:
	$(BIN)/python -m app.db reset

clean:
	rm -rf $(VENV) data/app.db data/checkpoints.db
