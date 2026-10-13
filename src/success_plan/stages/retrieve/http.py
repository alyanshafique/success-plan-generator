"""HTTP client with per-host rate limiting, retries, and an on-disk cache for immutable documents."""

import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

# EDGAR allows up to 10 requests per second per user; we stay well below.
# All sec.gov hosts share one budget. GDELT asks for at most one request every 5 seconds.
_HOST_GROUPS = {
    "www.sec.gov": "sec",
    "data.sec.gov": "sec",
    "api.gdeltproject.org": "gdelt",
}
DEFAULT_MIN_INTERVALS = {"sec": 0.25, "gdelt": 5.0, "other": 1.0}
# GDELT throttles for minutes after a 429, well beyond its documented 5-second spacing.
# Seconds to wait before each retry after a 429, per host group. Other groups use 2**attempt.
DEFAULT_BACKOFF_429 = {"gdelt": [60.0, 180.0, 300.0]}

RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


class RateLimitedClient:
    def __init__(
        self,
        user_agent: str,
        *,
        transport: httpx.BaseTransport | None = None,
        min_intervals: dict[str, float] | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        backoff_429: dict[str, list[float]] | None = None,
        on_backoff=None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._min_intervals = min_intervals or DEFAULT_MIN_INTERVALS
        self._last_request: dict[str, float] = {}
        self._max_retries = max_retries
        self._backoff_429 = DEFAULT_BACKOFF_429 if backoff_429 is None else backoff_429
        self._on_backoff = on_backoff  # callback(message) so the CLI can say why it is waiting
        self._sleep = sleep
        self._clock = clock
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _group(url: str) -> str:
        return _HOST_GROUPS.get(urlparse(url).hostname or "", "other")

    def _retry_delay(self, url: str, status: int, attempt: int, retry_after: str) -> float:
        delay = float(2**attempt)
        schedule = self._backoff_429.get(self._group(url)) if status == 429 else None
        if schedule:
            delay = schedule[min(attempt, len(schedule) - 1)]
        if retry_after.isdigit():
            delay = max(delay, float(retry_after))
        return delay

    def _wait_for_slot(self, url: str) -> None:
        group = self._group(url)
        interval = self._min_intervals[group]
        last = self._last_request.get(group)
        if last is not None:
            remaining = interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[group] = self._clock()

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self._wait_for_slot(url)
            self.request_count += 1
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise FetchError(f"Request to {url} failed: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                delay = self._retry_delay(url, response.status_code, attempt, response.headers.get("Retry-After", ""))
                if self._on_backoff and delay >= 10:
                    host = urlparse(url).hostname
                    self._on_backoff(f"{host} returned HTTP {response.status_code}; waiting {delay:.0f}s before retry {attempt + 1} of {self._max_retries}.")
                self._sleep(delay)
                continue
            if response.status_code == 403 and "sec.gov" in url:
                raise FetchError(
                    f"SEC returned 403 for {url}. Check that SEC_USER_AGENT has a real name and email."
                )
            if response.status_code >= 400:
                raise FetchError(f"Request to {url} returned HTTP {response.status_code}")
            return response
        raise FetchError(f"Request to {url} failed after {self._max_retries + 1} attempts")

    def get_cached(self, url: str, dest: Path, *, force: bool = False) -> bytes:
        """Fetch an immutable document (e.g. an EDGAR archive file), reusing a local copy."""
        if dest.exists() and not force:
            return dest.read_bytes()
        content = self.get(url).content
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return content
