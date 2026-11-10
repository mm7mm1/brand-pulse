from datetime import UTC, date, datetime

import pyarrow.compute as pc
import pytest

from pulse.config import Brand
from pulse.ingest import default_window, months_between, run
from pulse.lake import Lake
from pulse.wiki import SourceError

NIKE = Brand("nike", "Nike", "sportswear", "en.wikipedia", "Nike,_Inc.")
PUMA = Brand("puma", "Puma", "sportswear", "en.wikipedia", "Puma_(brand)")
NOW = datetime(2026, 11, 10, 6, tzinfo=UTC)


class FakeClient:
    """Returns `views_per_day` for every day of the asked range; raises for `failing`."""

    def __init__(self, views_per_day=10, failing=()):
        self.views, self.failing, self.calls = views_per_day, set(failing), []

    def daily_views(self, project, article, start, end):
        self.calls.append((article, start, end))
        if article in self.failing:
            raise SourceError(429, "rate limited")
        days = (end - start).days + 1
        return [
            {
                "timestamp": f"{date.fromordinal(start.toordinal() + i):%Y%m%d}00",
                "views": self.views,
            }
            for i in range(days)
        ]


def test_default_window_ends_yesterday():
    assert default_window(date(2026, 11, 10), 3) == (date(2026, 11, 7), date(2026, 11, 9))


def test_months_between_crosses_year():
    assert months_between(date(2025, 12, 30), date(2026, 1, 2)) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_window_across_months_writes_one_file_per_brand_month(tmp_path):
    lake, client = Lake(str(tmp_path)), FakeClient()
    summary = run([NIKE, PUMA], client, lake, date(2026, 10, 30), date(2026, 11, 2), now=NOW)

    # one call per brand, starting at the first day of the first month
    assert sorted(c[1:] for c in client.calls) == [(date(2026, 10, 1), date(2026, 11, 2))] * 2
    assert len(summary.written) == 4
    october = lake.read_slice(lake.slice_path("pageviews", date(2026, 10, 1), "nike"))
    assert october.num_rows == 31  # the whole month is rewritten, not just the window


def test_rerun_replaces_slices_instead_of_appending(tmp_path):
    lake = Lake(str(tmp_path))
    run([NIKE], FakeClient(views_per_day=10), lake, date(2026, 11, 1), date(2026, 11, 3), now=NOW)
    run([NIKE], FakeClient(views_per_day=7), lake, date(2026, 11, 1), date(2026, 11, 3), now=NOW)

    table = lake.read_slice(lake.slice_path("pageviews", date(2026, 11, 1), "nike"))
    assert table.num_rows == 3
    assert pc.sum(table.column("views")).as_py() == 21  # restated values, no duplicates
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_brand_keeps_its_previous_file(tmp_path, caplog):
    lake = Lake(str(tmp_path))
    run(
        [NIKE, PUMA],
        FakeClient(views_per_day=10),
        lake,
        date(2026, 11, 1),
        date(2026, 11, 3),
        now=NOW,
    )
    summary = run(
        [NIKE, PUMA],
        FakeClient(views_per_day=99, failing={"Puma_(brand)"}),
        lake,
        date(2026, 11, 1),
        date(2026, 11, 3),
        now=NOW,
    )

    puma = lake.read_slice(lake.slice_path("pageviews", date(2026, 11, 1), "puma"))
    assert pc.sum(puma.column("views")).as_py() == 30  # untouched
    assert summary.describe_failures() == "puma: 429"
    assert "skipped 1 of 2 brands" in caplog.text


def test_all_brands_failed_raises_and_writes_nothing(tmp_path):
    lake = Lake(str(tmp_path))
    with pytest.raises(RuntimeError, match="all 2 brands failed - lake not touched; nike: 429"):
        run(
            [NIKE, PUMA],
            FakeClient(failing={"Nike,_Inc.", "Puma_(brand)"}),
            lake,
            date(2026, 11, 1),
            date(2026, 11, 3),
            now=NOW,
        )
    assert not list(tmp_path.rglob("*.parquet"))


class RenamedArticleClient:
    """`New_title` misses Nov 2 (zero views) and Nov 4 (not published yet); the old title
    still gets redirect hits; a third former title has no traffic at all."""

    def daily_views(self, project, article, start, end):
        if article == "Gone_title":
            raise SourceError(404, "no pageviews")
        if article == "Old_title":
            return [{"timestamp": "2026110100", "views": 5}]
        return [{"timestamp": "2026110100", "views": 1}, {"timestamp": "2026110300", "views": 3}]


def test_former_titles_are_summed_and_quiet_days_written_as_zero(tmp_path):
    lake = Lake(str(tmp_path))
    brand = Brand("claude", "Claude", "ai", "en.wikipedia", "New_title|Old_title|Gone_title")
    run([brand], RenamedArticleClient(), lake, date(2026, 11, 1), date(2026, 11, 4), now=NOW)

    table = lake.read_slice(lake.slice_path("pageviews", date(2026, 11, 1), "claude"))
    assert table.column("date").to_pylist() == [date(2026, 11, d) for d in (1, 2, 3)]
    assert table.column("views").to_pylist() == [6, 0, 3]


def test_missing_current_title_fails_the_brand(tmp_path):
    lake = Lake(str(tmp_path))
    brand = Brand("x", "X", "ai", "en.wikipedia", "Gone_title|Old_title")
    with pytest.raises(RuntimeError, match="x: 404"):
        run([brand], RenamedArticleClient(), lake, date(2026, 11, 1), date(2026, 11, 3), now=NOW)
