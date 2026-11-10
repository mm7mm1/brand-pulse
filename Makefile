VENV := .venv
BIN  := $(CURDIR)/$(VENV)/bin
DBT  := cd transform && DBT_PROFILES_DIR=. $(BIN)/dbt

.PHONY: setup ingest backfill transform freshness pipeline dashboard test lint sample docs

setup:                ## local env: ingestion, dbt-duckdb, dashboard, dev tools
	python3.11 -m venv $(VENV)
	$(BIN)/pip install -e ".[transform,dashboard,dev]"

ingest:               ## daily run: refetch the lookback window
	$(BIN)/pulse ingest

backfill:             ## make backfill START=2025-11-01 END=2026-11-09 [BRAND=nike]
	$(BIN)/pulse ingest --start $(START) --end $(END) $(if $(BRAND),--brand $(BRAND))
	$(DBT) build --full-refresh

transform:            ## dbt build: seeds, models, unit + data tests
	$(DBT) build

freshness:
	$(DBT) source freshness

pipeline: ingest transform freshness

dashboard:
	$(BIN)/streamlit run dashboard/app.py

test:
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff check src tests dashboard
	$(BIN)/ruff format --check src tests dashboard

sample:               ## synthetic lake for offline runs (what CI builds on)
	$(BIN)/python tests/make_sample_lake.py data/lake

docs:                 ## dbt docs with the lineage graph
	$(DBT) docs generate && $(DBT) docs serve
