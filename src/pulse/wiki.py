"""Wikimedia Pageviews API client: rate limited, retried, one call per brand-month."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from urllib.parse import quote

import requests

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
ACTION_API = "https://{lang}.wikipedia.org/w/api.php"
USER_AGENT = "BrandPulse/0.1 (https://github.com/mm7mm1/brand-pulse; data pipeline demo)"
RETRY_STATUSES = {429, 500, 502, 503, 504}

log = logging.getLogger(__name__)


class SourceError(Exception):
    """A request that failed for good; `status` feeds the run summary (`nike 2026-10: 429`)."""

    def __init__(self, status: int | str, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status


class RateLimiter:
    """Spaces requests evenly across all worker threads.

    The API answers anonymous bursts with 429 after ~10 calls, so pacing up front is
    cheaper than burning retries on every brand.
    """

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self.clock, self.sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            self.sleep(slot - now)


class WikiClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        max_attempts: int = 4,
        backoff_seconds: float = 2.0,
        sleep=time.sleep,
    ):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.limiter = limiter or RateLimiter(0)
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def daily_views(self, project: str, article: str, start: date, end: date) -> list[dict]:
        url = (
            f"{API}/{project}/all-access/user/{quote(article, safe='')}"
            f"/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
        )
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=30)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_attempts:
                    raise SourceError("network", str(exc)) from exc
                self._pause(attempt, None, f"network error: {exc}")
                continue
            if resp.status_code == 200:
                return resp.json()["items"]
            if resp.status_code == 404:
                # 404 means a wrong title or a range with no traffic - retrying will not help
                raise SourceError(404, f"no pageviews for {project}/{article}")
            if resp.status_code not in RETRY_STATUSES or attempt == self.max_attempts:
                raise SourceError(resp.status_code, resp.text[:200])
            self._pause(attempt, resp.headers.get("Retry-After"), f"HTTP {resp.status_code}")
        raise AssertionError("unreachable")

    def _pause(self, attempt: int, retry_after: str | None, reason: str) -> None:
        delay = float(retry_after) if retry_after else self.backoff_seconds * 2 ** (attempt - 1)
        log.warning("retrying in %.0fs after %s (attempt %d)", delay, reason, attempt)
        self.sleep(delay)


def find_redirects(
    titles: list[str], project: str = "en.wikipedia", session=None
) -> dict[str, str]:
    """Registry titles that are redirects, mapped to their target article.

    The pageviews API counts the exact title and does not follow redirects, so a renamed
    article silently drops to a trickle of views instead of failing.
    """
    session = session or requests.Session()
    lang = project.split(".")[0]
    found = {}
    for i in range(0, len(titles), 50):  # the action API takes up to 50 titles per call
        resp = session.get(
            ACTION_API.format(lang=lang),
            params={
                "action": "query",
                "format": "json",
                "redirects": 1,
                "titles": "|".join(titles[i : i + 50]),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        query = resp.json()["query"]
        normalized = {n["to"]: n["from"] for n in query.get("normalized", [])}
        for r in query.get("redirects", []):
            found[normalized.get(r["from"], r["from"])] = r["to"].replace(" ", "_")
    return found
