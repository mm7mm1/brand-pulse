# One image for the two Cloud Run jobs (`pulse ingest`, `dbt build`) and the dashboard
# service (`streamlit run`) - see infra/main.tf
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PULSE_BRANDS_CSV=/app/transform/seeds/brands.csv \
    DBT_PROFILES_DIR=/app/transform

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[bigquery,dashboard]"

COPY transform ./transform
COPY dashboard ./dashboard
WORKDIR /app/transform
RUN useradd --create-home pulse && chown -R pulse /app
USER pulse
