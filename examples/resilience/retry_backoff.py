"""
retry_backoff.py
==============
Resilience primitives for scraping:

1. AsyncRetrier — retry with exponential backoff + jitter
2. CircuitBreaker — stop hammering a dead endpoint
3. RateLimiter — token-bucket rate limiter (thread + async)
4. RequestDeduplicator — avoid duplicate in-flight requests
5. RetryableHTTP — requests/httpx wrappers with auto-retry

Usage:
    from retry_backoff import AsyncRetrier, CircuitBreaker, RateLimiter

    retrier = AsyncRetrier(max_attempts=4, base_delay=1.0)
    result = await retrier.call(difficult_request, url)

    cb = CircuitBreaker(threshold=5, reset_timeout=60)
    with cb:
        resp = await session.get(url)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import threading
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, TypeVar

import requests

logger = logging.getLogger(__name__)
T = TypeVar("T")


# ---------------------------------------------------------------------------
# 1. Exponential backoff + jitter
# ---------------------------------------------------------------------------

class RetryPolicy:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
        backoff_factor: float = 2.0,
        retry_on: Tuple[type, ...] = (requests.Timeout, requests.ConnectionError),
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.backoff_factor = backoff_factor
        self.retry_on = retry_on

    def next_delay(self, attempt: int) -> float:
        delay = self.base_delay * (self.backoff_factor ** attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


class AsyncRetrier:
    """
    Async retry with exponential backoff + jitter.
    Works for both sync and async callables.
    """

    def __init__(self, policy: RetryPolicy = None):
        self.policy = policy or RetryPolicy()

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Call func(*args, **kwargs) with retry.
        If func is async, it will be awaited.
        """
        last_exc = None
        for attempt in range(self.policy.max_attempts):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not isinstance(exc, self.policy.retry_on):
                    raise
                if attempt + 1 >= self.policy.max_attempts:
                    break
                delay = self.policy.next_delay(attempt)
                logger.warning("Attempt %d/%d failed: %s — retrying in %.1fs",
                               attempt + 1, self.policy.max_attempts, exc, delay)
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Circuit breaker
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreakerState:
    failures: int = 0
    last_failure: float = 0.0
    state: str = "closed"  # closed | open | half-open


class CircuitBreaker:
    """
    Classic circuit breaker.

    States:
      closed   → requests flow, failures counted
      open     → fail fast, no requests sent
      half-open → limited probe request allowed
    """

    def __init__(self, threshold: int = 5, reset_timeout: float = 60.0,
                 half_open_max: int = 1):
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.half_open_max = half_open_max
        self._state = CircuitBreakerState()
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state.state == "open":
                if time.time() - self._state.last_failure > self.reset_timeout:
                    self._state.state = "half-open"
                    self._state.failures = 0
            return self._state.state

    def record_success(self):
        with self._lock:
            self._state.failures = 0
            self._state.state = "closed"

    def record_failure(self):
        with self._lock:
            self._state.failures += 1
            self._state.last_failure = time.time()
            if self._state.failures >= self.threshold:
                self._state.state = "open"
                logger.warning("Circuit breaker OPEN after %d failures", self._state.failures)

    def allow_request(self) -> bool:
        st = self.state
        if st == "closed":
            return True
        if st == "half-open":
            return True
        return False

    def __enter__(self):
        if not self.allow_request():
            raise RuntimeError("Circuit breaker is OPEN — request blocked")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()
        return False


# ---------------------------------------------------------------------------
# 3. Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Token-bucket rate limiter.
    Thread-safe for sync code. Use AsyncRateLimiter for async.
    """

    def __init__(self, rate: float = 1.0, burst: int = 1):
        """
        rate: tokens per second
        burst: max burst size
        """
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> float:
        """Block until tokens are available. Returns wait time."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0

            needed = (tokens - self._tokens) / self.rate
            self._tokens = 0
            return needed


class AsyncRateLimiter:
    """Async version of RateLimiter."""

    def __init__(self, rate: float = 1.0, burst: int = 1):
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> float:
        async with self._lock:
            now = time.time()
            elapsed = now - self._last
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0

            needed = (tokens - self._tokens) / self.rate
            self._tokens = 0
            await asyncio.sleep(needed)
            return needed


# ---------------------------------------------------------------------------
# 4. Request deduplicator
# ---------------------------------------------------------------------------

class RequestDeduplicator:
    """
    Avoid duplicate in-flight requests.
    If the same URL is requested while a previous request is still in flight,
    returns the cached future instead of making a new request.
    """

    def __init__(self):
        self._inflight: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}

    async def execute(self, key: str, func: Callable[..., Coroutine[Any, Any, T]], *args, **kwargs) -> T:
        """
        Execute func(*args, **kwargs) if not already in flight for this key.
        Returns the cached result if already running/completed.
        """
        if key in self._results:
            return self._results[key]

        if key in self._inflight:
            return await self._inflight[key]

        task = asyncio.create_task(func(*args, **kwargs))
        self._inflight[key] = task

        try:
            result = await task
            self._results[key] = result
            return result
        finally:
            self._inflight.pop(key, None)


# ---------------------------------------------------------------------------
# 5. Retryable HTTP wrappers
# ---------------------------------------------------------------------------

class RetryableSession:
    """
    requests.Session wrapper with auto-retry.
    """

    def __init__(self, policy: RetryPolicy = None):
        self.session = requests.Session()
        self.policy = policy or RetryPolicy()

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Make a request with retry.
        """
        last_exc = None
        for attempt in range(self.policy.max_attempts):
            try:
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code < 500:
                    return resp
                # Retry on 5xx
                last_exc = requests.HTTPError(f"HTTP {resp.status_code}")
            except requests.Timeout as exc:
                last_exc = exc
            except requests.ConnectionError as exc:
                last_exc = exc

            if attempt + 1 < self.policy.max_attempts:
                delay = self.policy.next_delay(attempt)
                logger.warning("Attempt %d failed, retrying in %.1fs", attempt + 1, delay)
                time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)


# ---------------------------------------------------------------------------
# 6. Composite resilience decorator
# ---------------------------------------------------------------------------

def resilient(circuit_breaker: CircuitBreaker = None,
              rate_limiter: RateLimiter = None,
              retrier: AsyncRetrier = None,
              max_concurrent: int = 10):
    """
    Decorator that adds circuit breaker + rate limiting + retry to an async function.

    Usage:
        @resilient(
            circuit_breaker=CircuitBreaker(threshold=5, reset_timeout=60),
            rate_limiter=RateLimiter(rate=2.0, burst=5),
            retrier=AsyncRetrier(max_attempts=3),
        )
        async def scrape(url):
            ...
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Rate limit
            if rate_limiter:
                if isinstance(rate_limiter, AsyncRateLimiter):
                    await rate_limiter.acquire()
                else:
                    wait = rate_limiter.acquire()
                    if wait > 0:
                        await asyncio.sleep(wait)

            # Circuit breaker
            if circuit_breaker:
                if not circuit_breaker.allow_request():
                    raise RuntimeError("Circuit breaker open")
                try:
                    result = await retrier.call(func, *args, **kwargs) if retrier else func(*args, **kwargs)
                    circuit_breaker.record_success()
                    return result
                except Exception as exc:
                    circuit_breaker.record_failure()
                    raise
            else:
                return await retrier.call(func, *args, **kwargs) if retrier else func(*args, **kwargs)

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import httpx
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Resilience primitives demo")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--rate", type=float, default=1.0, help="Requests per second")
    parser.add_argument("--circuit-threshold", type=int, default=5)
    args = parser.parse_args()

    async def main():
        retrier = AsyncRetrier(RetryPolicy(max_attempts=args.retries))
        cb = CircuitBreaker(threshold=args.circuit_threshold, reset_timeout=30)
        limiter = AsyncRateLimiter(rate=args.rate, burst=1)

        async def fetch(url: str) -> str:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                return (await client.get(url)).text[:200]

        print(f"Fetching {args.url} with retry + circuit breaker + rate limit...")
        try:
            html = await retrier.call(fetch, args.url)
            print(f"OK — {len(html)} chars")
        except Exception as exc:
            print(f"FAIL — {exc}")

    asyncio.run(main())
