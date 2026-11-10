"""`pulse ingest [--start YYYY-MM-DD --end YYYY-MM-DD]` - daily run or backfill."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime

from pulse import config
from pulse.ingest import default_window, run
from pulse.lake import Lake
from pulse.logs import setup_logging
from pulse.wiki import RateLimiter, WikiClient, find_redirects


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pulse")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="refetch the lookback window, or a backfill range")
    ingest.add_argument("--start", type=date.fromisoformat)
    ingest.add_argument("--end", type=date.fromisoformat)
    ingest.add_argument("--brand", action="append", help="limit to brand_id (repeatable)")
    sub.add_parser("check-registry", help="fail if a registry title is a Wikipedia redirect")
    args = parser.parse_args(argv)

    # Cloud Run sets CLOUD_RUN_JOB; locally the logs stay human-readable
    setup_logging(json_format="CLOUD_RUN_JOB" in os.environ)
    brands = config.load_brands()
    if args.command == "check-registry":
        check_registry(brands)
        return

    if args.brand:
        unknown = set(args.brand) - {b.brand_id for b in brands}
        if unknown:
            parser.error(f"unknown brand_id: {', '.join(sorted(unknown))}")
        brands = [b for b in brands if b.brand_id in args.brand]
    start, end = default_window(datetime.now(UTC).date(), config.LOOKBACK_DAYS)
    start, end = args.start or start, args.end or end

    client = WikiClient(limiter=RateLimiter(config.REQUESTS_PER_MINUTE))
    run(brands, client, Lake(config.LAKE_URI), start, end, config.MAX_WORKERS)


def check_registry(brands: list[config.Brand]) -> None:
    """The current title must be a real article and every former title must redirect to
    it - otherwise a rename silently cuts a brand's views to a trickle."""
    by_project: dict[str, list[config.Brand]] = {}
    for b in brands:
        by_project.setdefault(b.wiki_project, []).append(b)
    problems = []
    for project, group in by_project.items():
        redirects = find_redirects([t for b in group for t in b.titles], project)
        for b in group:
            current, *former = b.titles
            if current in redirects:
                problems.append(f"{b.brand_id}: {current} redirects to {redirects[current]}")
            problems += [
                f"{b.brand_id}: former title {t} does not redirect to {current}"
                for t in former
                if redirects.get(t) != current
            ]
    if problems:
        raise SystemExit("brand registry: " + "; ".join(problems))
    print(f"registry ok: {len(brands)} brands, current titles canonical, former ones redirect")
