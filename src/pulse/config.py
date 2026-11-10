"""Runtime settings and the brand registry."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The registry is the dbt seed itself: ingestion and models read one file, so a brand
# added for the models is never missing from the lake (and the other way round).
BRANDS_CSV = Path(os.environ.get("PULSE_BRANDS_CSV", REPO_ROOT / "transform/seeds/brands.csv"))
LAKE_URI = os.environ.get("PULSE_LAKE_URI", str(REPO_ROOT / "data/lake"))
LOOKBACK_DAYS = int(os.environ.get("PULSE_LOOKBACK_DAYS", "3"))
MAX_WORKERS = int(os.environ.get("PULSE_MAX_WORKERS", "4"))
REQUESTS_PER_MINUTE = int(os.environ.get("PULSE_REQUESTS_PER_MINUTE", "8"))


@dataclass(frozen=True)
class Brand:
    brand_id: str
    brand_name: str
    category: str
    wiki_project: str
    articles: str

    @property
    def titles(self) -> list[str]:
        """Current title first, then former ones. A renamed article keeps its history - and
        the hits that still arrive through the redirect - on the old title, so a brand's
        attention is the sum over all of them."""
        return self.articles.split("|")


def load_brands(path: Path = BRANDS_CSV) -> list[Brand]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["is_active"].strip().lower() == "true"]
    if not rows:
        raise RuntimeError(f"brand registry {path} has no active brands - nothing to ingest")
    return [
        Brand(r["brand_id"], r["brand_name"], r["category"], r["wiki_project"], r["articles"])
        for r in rows
    ]
