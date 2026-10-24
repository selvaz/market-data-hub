"""Provider ledger reservations must not hold locks while pacing requests."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from types import SimpleNamespace

from filelock import FileLock
import pytest

from market_data_hub.macro_releases import http


def test_two_contenders_reserve_distinct_slots_without_sleeping_under_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(http.time, "time", lambda: 1000.0)
    http.RateLimiter("eurostat", tmp_path, interval=15).acquire()
    sleepers = Barrier(2)
    delays = []
    real_sleep = http.time.sleep
    def sleep(delay):
        if delay >= 15:
            delays.append(delay)
            sleepers.wait(timeout=2)
        else:
            real_sleep(delay)  # filelock's short acquisition polling interval
    monkeypatch.setattr(http.time, "sleep", sleep)
    def reserve(_):
        http.RateLimiter("eurostat", tmp_path, interval=15).acquire()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reserve, range(2)))
    assert sorted(delays) == [15, 30]
    ledger = json.loads((tmp_path / "eurostat.json").read_text())
    assert ledger == {"day": "1970-01-01", "count": 3, "last": 1000, "reserved_until": 1030}


def test_ledger_lock_timeout_is_a_classified_retryable_fetch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "FileLock", lambda path, timeout: FileLock(path, timeout=0.01))
    with FileLock(str(tmp_path / "ecfin.json") + ".lock"):
        with pytest.raises(http.RateLimitBusy, match="rate ledger lock is busy") as caught:
            http.RateLimiter("ecfin", tmp_path).acquire()
    assert isinstance(caught.value, http.FetchError)
    assert caught.value.retryable is True


@pytest.mark.parametrize("failures,attempts,succeeds", [(1, 3, True), (3, 3, False)])
def test_http_retries_busy_ledger_with_a_bounded_budget(monkeypatch, failures, attempts, succeeds):
    requests = []
    def request(*args, **kwargs):
        requests.append(args)
        return SimpleNamespace(status_code=200, content=b"{}")
    client = http.HttpClient(session=SimpleNamespace(request=request), persistent_rates=False, max_attempts=attempts)
    count, waits = [], []
    def acquire():
        count.append(True)
        if len(count) <= failures:
            raise http.RateLimitBusy("Provider rate ledger lock is busy; retry within the request budget")
    monkeypatch.setattr(client.limiters["ecfin"], "acquire", acquire)
    monkeypatch.setattr(client, "_backoff", waits.append)
    if succeeds:
        assert client.get("ecfin", "https://economy-finance.ec.europa.eu/release").body == b"{}"
        assert len(requests) == 1
    else:
        with pytest.raises(http.RateLimitBusy):
            client.get("ecfin", "https://economy-finance.ec.europa.eu/release")
        assert requests == []
    assert len(count) == min(failures + 1, attempts)
    assert len(waits) == min(failures, attempts - 1)
