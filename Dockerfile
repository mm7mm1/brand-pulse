# One image, two Cloud Run jobs: `pulse ingest` and `dbt build` (see infra/main.tf)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PULSE_BRANDS_CSV=/app/transform/seeds/brands.csv \
    DBT_PROFILES_DIR=/app/transform

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[bigquery]"

COPY transform ./transform
WORKDIR /app/transform
RUN useradd --create-home pulse && chown -R pulse /app
USER pulse
