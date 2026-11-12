"""Export the tables the dashboard reads into a folder of parquet files plus a manifest.

The hosted dashboard has no warehouse: the daily GitHub Action builds gold, runs this,
and publishes the folder to the `snapshot` branch, which the app downloads.

    python dashboard/export_snapshot.py snapshot
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import duckdb

DB_PATH = os.environ.get(
    "PULSE_DUCKDB_PATH", str(Path(__file__).resolve().parents[1] / "data/warehouse.duckdb")
)
# Everything the dashboard queries - and nothing below gold
TABLES = {
    "config.brands": "brands",
    "gold.fct_brand_daily": "fct_brand_daily",
    "gold.fct_share_of_attention": "fct_share_of_attention",
    "gold.fct_buzz_events": "fct_buzz_events",
}


def export(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    with duckdb.connect(DB_PATH, read_only=True) as con:
        con.execute("set TimeZone = 'UTC'")
        for table, name in TABLES.items():
            path = out_dir / f"{name}.parquet"
            con.execute(f"copy (select * from {table}) to '{path}' (format parquet)")
            rows[name] = con.execute(f"select count(*) from {table}").fetchone()[0]
        last_day, loaded_at = con.execute(
            "select max(view_date), max(_loaded_at) from gold.fct_brand_daily"
        ).fetchone()
    if not rows["fct_brand_daily"]:
        # Publishing an empty snapshot would blank the live dashboard; keep yesterday's
        raise SystemExit("gold.fct_brand_daily is empty - snapshot not exported")
    manifest = {"last_day": str(last_day), "loaded_at": loaded_at.isoformat(), "rows": rows}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    print(json.dumps(export(Path(sys.argv[1])), indent=2))
