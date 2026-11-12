"""Brand Pulse dashboard - reads the gold layer only, never bronze or silver."""

from __future__ import annotations

import os
import tempfile
import urllib.request
from datetime import timedelta
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

DB_PATH = os.environ.get(
    "PULSE_DUCKDB_PATH", str(Path(__file__).resolve().parents[1] / "data/warehouse.duckdb")
)
# Where the hosted app gets its data: the folder the daily GitHub Action publishes
# (see dashboard/export_snapshot.py). A local path works too, for testing.
SNAPSHOT_URL = os.environ.get(
    "PULSE_SNAPSHOT_URL", "https://raw.githubusercontent.com/mm7mm1/brand-pulse/snapshot"
)
SNAPSHOT_TABLES = {
    "config.brands": "brands",
    "gold.fct_brand_daily": "fct_brand_daily",
    "gold.fct_share_of_attention": "fct_share_of_attention",
    "gold.fct_buzz_events": "fct_buzz_events",
}
# Categorical slots in fixed order. A brand keeps its slot (brand_id order inside its
# category) whatever the filters, so its colour never changes between views.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
PERIODS = {"30 days": 30, "90 days": 90, "1 year": 365}
CATEGORY_LABELS = {
    "ai_assistants": "AI assistants",
    "sportswear": "Sportswear",
    "streaming": "Streaming",
}

st.set_page_config(page_title="Brand Pulse", page_icon="📈", layout="wide")


# Set on Cloud Run: the dashboard reads gold straight from BigQuery
BQ_PROJECT = os.environ.get("PULSE_BQ_PROJECT", "")
USE_WAREHOUSE = not BQ_PROJECT and Path(DB_PATH).exists()
if BQ_PROJECT:
    SOURCE = f"BigQuery {BQ_PROJECT}"
elif USE_WAREHOUSE:
    SOURCE = "local warehouse"
else:
    SOURCE = "daily snapshot, refreshed by GitHub Actions"


@st.cache_resource(ttl=3600)
def snapshot() -> duckdb.DuckDBPyConnection:
    """Gold from BigQuery or from the published snapshot, loaded into an in-memory DuckDB
    under the warehouse's table names - so every query below is the same SQL in all modes.

    The tables are a few thousand rows: copying them once an hour costs one small query
    per table, where querying BigQuery on every widget change would cost one per click.
    """
    con = duckdb.connect()
    if BQ_PROJECT:
        from google.cloud import bigquery

        client = bigquery.Client(project=BQ_PROJECT)
    tmp = Path(tempfile.mkdtemp())
    for table, name in SNAPSHOT_TABLES.items():
        con.execute(f"create schema if not exists {table.split('.')[0]}")
        if BQ_PROJECT:
            # Arrow, not pandas: DATE and TIMESTAMP arrive as native types
            rows = client.query(f"select * from `{BQ_PROJECT}.{table}`").to_arrow()
            con.register("rows", rows)
            con.execute(f"create table {table} as select * from rows")
            con.unregister("rows")
            continue
        src = f"{SNAPSHOT_URL}/{name}.parquet"
        path = tmp / f"{name}.parquet"
        if src.startswith("http"):
            urllib.request.urlretrieve(src, path)
        else:
            path = Path(src)
        con.execute(f"create table {table} as select * from read_parquet('{path}')")
    return con


@st.cache_data(ttl=600)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    if USE_WAREHOUSE:
        # Opened per query and closed: a long-lived handle would lock dbt out of the file
        with duckdb.connect(DB_PATH, read_only=True) as con:
            con.execute("set TimeZone = 'UTC'")
            return con.execute(sql, list(params)).df()
    # A cursor per query: Streamlit serves sessions from several threads
    cur = snapshot().cursor()
    cur.execute("set TimeZone = 'UTC'")
    return cur.execute(sql, list(params)).df()


brands = query(
    "select brand_id, brand_name, category from config.brands where is_active "
    "order by category, brand_id"
)
meta = query(
    "select max(view_date) as last_day, max(_loaded_at) as loaded from gold.fct_brand_daily"
)
last_day = meta.last_day[0].date() if hasattr(meta.last_day[0], "date") else meta.last_day[0]

st.title("Brand Pulse")
st.caption(
    "Share of attention across competing brands, from Wikipedia pageviews. "
    f"Data through {last_day:%d %b %Y}, loaded {meta.loaded[0]:%d %b %H:%M} UTC "
    f"({SOURCE})."
)

f1, f2, _ = st.columns([1, 1, 2])
categories = sorted(brands.category.unique())
category = f1.selectbox(
    "Category",
    categories,
    index=categories.index("streaming") if "streaming" in categories else 0,
    format_func=lambda c: CATEGORY_LABELS.get(c, c),
)
period = f2.radio("Period", list(PERIODS), index=1, horizontal=True)
start = last_day - timedelta(days=PERIODS[period] - 1)

in_category = brands[brands.category == category]
names = in_category.brand_name.tolist()
color = alt.Scale(domain=names, range=PALETTE[: len(names)])

share = query(
    "select * from gold.fct_share_of_attention "
    "where category = ? and view_date between ? and ? order by view_date",
    (category, start, last_day),
)
events = query(
    "select * from gold.fct_buzz_events "
    "where category = ? and view_date between ? and ? order by z_score desc",
    (category, start, last_day),
)

# --- Headline numbers ----------------------------------------------------------------

week = share[share.view_date > pd.Timestamp(last_day - timedelta(days=7))]
prev = share[
    (share.view_date <= pd.Timestamp(last_day - timedelta(days=7)))
    & (share.view_date > pd.Timestamp(last_day - timedelta(days=14)))
]
leader = share[share.view_date == pd.Timestamp(last_day)].sort_values(
    "share_7d_avg", ascending=False
)

t1, t2, t3 = st.columns(3)
change = week.views.sum() / prev.views.sum() - 1 if prev.views.sum() else None
t1.metric(
    "Category views, last 7 days",
    f"{week.views.sum():,.0f}",
    f"{change:+.1%} vs previous week" if change is not None else None,
)
if not leader.empty:
    t2.metric(
        "Attention leader, 7-day share",
        f"{leader.brand_name.iloc[0]} · {leader.share_7d_avg.iloc[0]:.0%}",
    )
t3.metric(f"Buzz events, last {period}", len(events))

# --- Share of attention over time ------------------------------------------------------

st.subheader("Share of attention, 7-day average")
hover = alt.selection_point(nearest=True, on="pointerover", fields=["view_date"], empty=False)
base = alt.Chart(share).encode(x=alt.X("view_date:T", title=None))
lines = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
    y=alt.Y("share_7d_avg:Q", title=None, axis=alt.Axis(format="%", grid=True)),
    color=alt.Color("brand_name:N", scale=color, legend=alt.Legend(orient="top", title=None)),
)
dots = base.mark_point(filled=True, size=70).encode(
    y="share_7d_avg:Q",
    color=alt.Color("brand_name:N", scale=color, legend=None),
    opacity=alt.condition(hover, alt.value(1), alt.value(0)),
)
# One tooltip per day listing every brand: pivot to one column per brand_id
tooltip = [alt.Tooltip("view_date:T", title="Date", format="%d %b %Y")] + [
    alt.Tooltip(f"{bid}:Q", title=name, format=".1%")
    for bid, name in zip(in_category.brand_id, in_category.brand_name, strict=True)
]
crosshair = (
    base.transform_pivot("brand_id", value="share_7d_avg", groupby=["view_date"])
    .mark_rule(color="#8a8a85", strokeWidth=1)
    .encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0)), tooltip=tooltip)
    .add_params(hover)
)
st.altair_chart((lines + dots + crosshair).properties(height=340), use_container_width=True)

with st.expander("Table view"):
    st.dataframe(
        share.pivot(index="view_date", columns="brand_name", values="share_7d_avg")
        .sort_index(ascending=False)
        .style.format("{:.1%}"),
        use_container_width=True,
    )

# --- One brand: daily views and buzz events --------------------------------------------

st.subheader("Daily views and buzz events")
brand = st.selectbox("Brand", names)
brand_color = PALETTE[names.index(brand)]
daily = share[share.brand_name == brand]
spikes = events[events.brand_name == brand]

day_hover = alt.selection_point(nearest=True, on="pointerover", fields=["view_date"], empty=False)
line = (
    alt.Chart(daily)
    .mark_line(strokeWidth=2, color=brand_color)
    .encode(
        x=alt.X("view_date:T", title=None),
        y=alt.Y("views:Q", title=None, axis=alt.Axis(format="~s")),
    )
)
rule = (
    alt.Chart(daily)
    .mark_rule(color="#8a8a85", strokeWidth=1)
    .encode(
        x="view_date:T",
        opacity=alt.condition(day_hover, alt.value(0.6), alt.value(0)),
        tooltip=[
            alt.Tooltip("view_date:T", title="Date", format="%d %b %Y"),
            alt.Tooltip("views:Q", title="Views", format=","),
            alt.Tooltip("views_7d_avg:Q", title="7-day avg", format=",.0f"),
        ],
    )
    .add_params(day_hover)
)
markers = (
    alt.Chart(spikes)
    .mark_point(filled=True, size=110, color=brand_color, stroke="#ffffff", strokeWidth=2)
    .encode(
        x="view_date:T",
        y="views:Q",
        tooltip=[
            alt.Tooltip("view_date:T", title="Buzz event", format="%d %b %Y"),
            alt.Tooltip("views:Q", title="Views", format=","),
            alt.Tooltip("baseline_views:Q", title="Baseline", format=",.0f"),
            alt.Tooltip("lift:Q", title="Lift", format=".1f"),
            alt.Tooltip("z_score:Q", title="z-score", format=".1f"),
        ],
    )
)
st.altair_chart((line + rule + markers).properties(height=280), use_container_width=True)
st.caption(
    "A buzz event is a day at least 3 standard deviations above the brand's previous "
    "28 days. Hover a marker for its lift and z-score."
)

st.subheader(f"Buzz events: {CATEGORY_LABELS.get(category, category)}")
st.dataframe(
    events[["brand_name", "view_date", "views", "baseline_views", "lift", "z_score"]],
    hide_index=True,
    use_container_width=True,
    column_config={
        "brand_name": "Brand",
        "view_date": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
        "views": st.column_config.NumberColumn("Views", format="%d"),
        "baseline_views": st.column_config.NumberColumn("Baseline", format="%d"),
        "lift": st.column_config.NumberColumn("Lift", format="%.1fx"),
        "z_score": st.column_config.NumberColumn("z-score", format="%.1f"),
    },
)
