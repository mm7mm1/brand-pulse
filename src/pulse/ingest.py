"""Ingestion run: refetch every brand over the window, write it as brand-month slices.

One API call returns a brand's whole range; the rows are split by month and each month's
file is replaced as a whole. That makes a run idempotent (rerun = same files), absorbs
late restatements inside the window, and keeps the lake at one file per brand per month
instead of thousands of daily fragments.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa

from pulse.config import Brand
from pulse.lake import PAGEVIEWS_SCHEMA, Lake
from pulse.wiki import SourceError, WikiClient

DATASET = "pageviews"
log = logging.getLogger(__name__)


def default_window(today: date, lookback_days: int) -> tuple[date, date]:
    # Yesterday is the last complete day; the lookback re-reads days the API may restate
    end = today - timedelta(days=1)
    return end - timedelta(days=lookback_days - 1), end


def months_between(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError(f"empty window {start}..{end}")
    months, m = [], start.replace(day=1)
    while m <= end:
        months.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return months


@dataclass
class RunSummary:
    run_id: str
    written: list[str] = field(default_factory=list)
    failed: dict[str, Exception] = field(default_factory=dict)
    rows: int = 0

    def describe_failures(self) -> str:
        return ", ".join(
            f"{key}: {getattr(exc, 'status', type(exc).__name__)}"
            for key, exc in sorted(self.failed.items())
        )


def run(
    brands: Iterable[Brand],
    client: WikiClient,
    lake: Lake,
    start: date,
    end: date,
    max_workers: int = 4,
    now: datetime | None = None,
) -> RunSummary:
    """Fetch every brand, write the ones that came back, report the ones that did not.

    A failed brand is skipped, never cleared: its previous files stay, so the dashboard
    shows yesterday's numbers instead of a hole, and the next run fills it in. Only a run
    where every brand failed is an error - then the lake is left untouched.
    """
    loaded_at = now or datetime.now(UTC)
    summary = RunSummary(run_id=uuid.uuid4().hex[:12])
    brands = list(brands)
    months = months_between(start, end)
    # Fetch from the first day of the first month: every month file is replaced whole
    fetch_start = months[0]

    def fetch(brand: Brand) -> dict[date, pa.Table]:
        views: dict[date, int] = {}
        for i, title in enumerate(brand.titles):
            try:
                items = client.daily_views(brand.wiki_project, title, fetch_start, end)
            except SourceError as exc:
                # A former title may have no traffic left in the range; the current one must
                if exc.status == 404 and i > 0:
                    continue
                raise
            for item in items:
                day = datetime.strptime(item["timestamp"][:8], "%Y%m%d").date()
                views[day] = views.get(day, 0) + int(item["views"])
        if not views:
            raise ValueError(f"empty response for {brand.brand_id}")
        table = _to_table(brand, _fill_quiet_days(views, fetch_start), loaded_at, summary.run_id)
        return _split_by_month(table, months)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch, b): b for b in brands}
        for fut in as_completed(futures):
            brand = futures[fut]
            try:
                slices = fut.result()
            except (SourceError, KeyError, ValueError) as exc:
                summary.failed[brand.brand_id] = exc
                continue
            for month, table in slices.items():
                path = lake.slice_path(DATASET, month, brand.brand_id)
                lake.replace_slice(path, table)
                summary.written.append(path)
                summary.rows += table.num_rows

    if not summary.written:
        raise RuntimeError(
            f"all {len(brands)} brands failed - lake not touched; {summary.describe_failures()}"
        )
    if summary.failed:
        # One ERROR per run, naming each brand and its cause: the alert policy fires on it
        log.error(
            "skipped %d of %d brands, their previous files kept: %s",
            len(summary.failed),
            len(brands),
            summary.describe_failures(),
        )
    log.info(
        "run %s: %d slices, %d rows written for %s..%s",
        summary.run_id,
        len(summary.written),
        summary.rows,
        start,
        end,
    )
    return summary


def _fill_quiet_days(views: dict[date, int], start: date) -> dict[date, int]:
    """The API leaves out days with zero views. They are written as 0 up to the last day
    it returned, so downstream a missing row means a missing load, never a quiet day.
    Days after that stay absent: they may simply not be published yet."""
    days = (max(views) - start).days + 1
    return {start + timedelta(days=i): views.get(start + timedelta(days=i), 0) for i in range(days)}


def _to_table(brand: Brand, views: dict[date, int], loaded_at: datetime, run_id: str) -> pa.Table:
    rows = [
        {
            "brand_id": brand.brand_id,
            "wiki_project": brand.wiki_project,
            "articles": brand.articles,
            "date": day,
            "views": count,
            "_loaded_at": loaded_at,
            "_run_id": run_id,
        }
        for day, count in sorted(views.items())
    ]
    return pa.Table.from_pylist(rows, schema=PAGEVIEWS_SCHEMA)


def _split_by_month(table: pa.Table, months: list[date]) -> dict[date, pa.Table]:
    dates = table.column("date").to_pylist()
    out = {}
    for month in months:
        mask = pa.array([d.replace(day=1) == month for d in dates])
        part = table.filter(mask)
        # A month past the last published day has no rows yet; a later run writes it
        if part.num_rows:
            out[month] = part
    return out
