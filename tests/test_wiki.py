from datetime import date

import pytest

from pulse.wiki import RateLimiter, SourceError, WikiClient, find_redirects


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._payload = status, payload or {}
        self.headers, self.text = headers or {}, "body"

    def json(self):
        return self._payload


class ScriptedSession:
    def __init__(self, *responses):
        self.responses, self.headers, self.urls = list(responses), {}, []

    def get(self, url, timeout):
        self.urls.append(url)
        return self.responses.pop(0)


def client(session, sleeps):
    return WikiClient(session=session, sleep=sleeps.append, backoff_seconds=1)


def test_retries_429_honouring_retry_after():
    ok = FakeResponse(200, {"items": [{"timestamp": "2026110100", "views": 5}]})
    sleeps = []
    session = ScriptedSession(FakeResponse(429, headers={"Retry-After": "19"}), ok)
    items = client(session, sleeps).daily_views(
        "en.wikipedia", "Nike,_Inc.", date(2026, 11, 1), date(2026, 11, 1)
    )
    assert items[0]["views"] == 5
    assert sleeps == [19.0]
    assert "Nike%2C_Inc." in session.urls[0]


def test_404_is_not_retried():
    sleeps = []
    with pytest.raises(SourceError) as err:
        client(ScriptedSession(FakeResponse(404)), sleeps).daily_views(
            "en.wikipedia", "Nope", date(2026, 11, 1), date(2026, 11, 2)
        )
    assert err.value.status == 404 and sleeps == []


def test_gives_up_after_max_attempts_with_backoff():
    sleeps = []
    session = ScriptedSession(*[FakeResponse(503)] * 4)
    with pytest.raises(SourceError) as err:
        client(session, sleeps).daily_views(
            "en.wikipedia", "X", date(2026, 11, 1), date(2026, 11, 2)
        )
    assert err.value.status == 503
    assert sleeps == [1, 2, 4]


def test_rate_limiter_spaces_calls_across_threads():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)

    limiter = RateLimiter(per_minute=6, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [10.0, 20.0]


def test_find_redirects_maps_registry_titles_back_from_normalized_form():
    payload = {
        "query": {
            "normalized": [{"from": "Claude_(language_model)", "to": "Claude (language model)"}],
            "redirects": [
                {"from": "Claude (language model)", "to": "Claude (AI)"},
                {"from": "ASICS", "to": "Asics"},
            ],
        }
    }

    class Session:
        def get(self, url, params, headers, timeout):
            assert params["titles"] == "Claude_(language_model)|ASICS|Adidas"
            resp = FakeResponse(200, payload)
            resp.raise_for_status = lambda: None
            return resp

    found = find_redirects(["Claude_(language_model)", "ASICS", "Adidas"], session=Session())
    assert found == {"Claude_(language_model)": "Claude_(AI)", "ASICS": "Asics"}
