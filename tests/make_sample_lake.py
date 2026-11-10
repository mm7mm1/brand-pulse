"""Write a synthetic 120-day lake through the real ingestion code, so CI can run the
whole dbt build - models, unit tests, data tests - with no network and no credentials.

    python tests/make_sample_lake.py data/lake
"""

import math
import random
import sys
from datetime import date, timedelta

from pulse.config import load_brands
from pulse.ingest import run
from pulse.lake import Lake


class SyntheticClient:
    def daily_views(self, project, article, start, end):
        rng = random.Random(article)
        base = rng.randint(500, 20_000)
        days = (end - start).days + 1
        items = []
        for i in range(days):
            weekly = 1 + 0.15 * math.sin(2 * math.pi * i / 7)
            views = base * weekly * rng.uniform(0.9, 1.1)
            if i == days - 15 and rng.random() < 0.4:
                views *= 6  # a buzz day for some brands
            items.append(
                {"timestamp": f"{start + timedelta(days=i):%Y%m%d}00", "views": int(views)}
            )
        return items


if __name__ == "__main__":
    end = date.today() - timedelta(days=1)
    run(load_brands(), SyntheticClient(), Lake(sys.argv[1]), end - timedelta(days=119), end)
