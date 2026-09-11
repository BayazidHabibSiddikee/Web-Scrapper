"""
distributed_crawler.py
====================
Distributed crawler using Redis as a task queue.

Architecture:
  - Master: reads seeds, pushes URLs into Redis queue
  - Workers: pop URLs, scrape, extract, store results, push new URLs back
  - Redis: task queue + seen-set + result stream

Requirements:
    pip install redis[async] asyncio-redis  # or just `pip install redis`

Run:
    # Terminal 1 — master
    python distributed_crawler.py master --seeds urls.txt --max-pages 500

    # Terminal 2+ — workers
    python distributed_crawler.py worker --concurrency 5 --profile cloudflare

    # Monitor
    python distributed_crawler.py status
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("distributed_crawler")

# Redis queue keys
QUEUE_KEY = "crawler:queue"
SEEN_KEY = "crawler:seen"
RESULTS_KEY = "crawler:results"
STATS_KEY = "crawler:stats"


# ---------------------------------------------------------------------------
# Task / Result containers
# ---------------------------------------------------------------------------

@dataclass
class CrawlTask:
    url: str
    depth: int = 0
    priority: int = 0
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_url: str = ""

    def key(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()


@dataclass
class CrawlResult:
    task_id: str
    url: str
    status: str = "ok"       # ok | error | blocked | captcha
    http_status: int = 0
    title: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)
    waf: str = ""
    screenshot: str = ""
    error: str = ""
    duration_ms: float = 0.0
    worker_id: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Redis client abstraction
# ---------------------------------------------------------------------------

class CrawlerRedis:
    """
    Thin wrapper around redis.asyncio with crawler-specific helpers.
    Falls back to in-memory implementation if redis is unavailable.
    """

    def __init__(self, url: str = "redis://localhost:6379/0"):
        self.url = url
        self._redis = None
        self._fallback = None

    async def connect(self):
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self.url, decode_responses=True)
            await self._redis.ping()
            logger.info("Connected to Redis at %s", self.url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — using in-memory queue", exc)
            self._redis = None
            self._fallback = _InMemoryCrawlerStore()

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def push_url(self, url: str, depth: int, priority: int = 0) -> bool:
        key = hashlib.sha256(url.encode()).hexdigest()
        if self._redis:
            added = await self._redis.sadd(SEEN_KEY, key)
            if not added:
                return False  # already seen
            await self._redis.zadd(QUEUE_KEY, {json.dumps({"url": url, "depth": depth}): -priority})
            return True
        else:
            return self._fallback.push_url(url, depth, priority)

    async def pop_url(self) -> Optional[dict]:
        if self._redis:
            items = await self._redis.zrange(QUEUE_KEY, -1, -1, withscores=True)
            if items:
                raw, score = items[0]
                await self._redis.zrem(QUEUE_KEY, raw)
                return json.loads(raw)
            return None
        else:
            return self._fallback.pop_url()

    async def queue_size(self) -> int:
        if self._redis:
            return await self._redis.zcard(QUEUE_KEY)
        return len(self._fallback.queue)

    async def seen_count(self) -> int:
        if self._redis:
            return await self._redis.scard(SEEN_KEY)
        return len(self._fallback.seen)

    async def push_result(self, result: CrawlResult):
        data = asdict(result) if hasattr(result, "__dataclass_fields__") else result.__dict__
        if self._redis:
            await self._redis.lpush(RESULTS_KEY, json.dumps(data, default=str))
        else:
            self._fallback.results.append(data)

    async def increment_stat(self, key: str, amount: int = 1):
        stat_key = f"{STATS_KEY}:{key}"
        if self._redis:
            await self._redis.incrby(stat_key, amount)
        else:
            self._fallback.stats[key] = self._fallback.stats.get(key, 0) + amount

    async def get_stats(self) -> Dict[str, int]:
        if self._redis:
            keys = await self._redis.keys(f"{STATS_KEY}:*")
            pipeline = self._redis.pipeline()
            for k in keys:
                pipeline.get(k)
            vals = await pipeline.execute()
            return {k.replace(STATS_KEY + ":", ""): int(v or 0) for k, v in zip(keys, vals)}
        return dict(self._fallback.stats)


class _InMemoryCrawlerStore:
    """Fallback when Redis isn't available."""

    def __init__(self):
        self.queue: deque = deque()
        self.seen: Set[str] = set()
        self.results: List[dict] = []
        self.stats: Dict[str, int] = {}

    def push_url(self, url: str, depth: int, priority: int = 0) -> bool:
        key = hashlib.sha256(url.encode()).hexdigest()
        if key in self.seen:
            return False
        self.seen.add(key)
        self.queue.append({"url": url, "depth": depth, "priority": priority})
        return True

    def pop_url(self) -> Optional[dict]:
        if self.queue:
            return self.queue.popleft()
        return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class CrawlWorker:
    """
    Single worker process. Pops tasks, scrapes, stores results, emits new URLs.
    """

    def __init__(self, worker_id: str, redis: CrawlerRedis,
                 max_depth: int = 3, max_pages: int = 200,
                 profile: str = "cloudflare", use_proxy: bool = False,
                 proxy_file: str = "config/proxies.txt",
                 concurrency: int = 2, capture_har: bool = False,
                 solve_captcha: bool = False):
        self.worker_id = worker_id
        self.redis = redis
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.profile = profile
        self.use_proxy = use_proxy
        self.proxy_file = proxy_file
        self.concurrency = concurrency
        self.capture_har = capture_har
        self.solve_captcha = solve_captcha
        self._semaphore = asyncio.Semaphore(concurrency)
        self._running = True

    async def run(self):
        logger.info("Worker %s started (concurrency=%d, profile=%s)", self.worker_id, self.concurrency, self.profile)
        while self._running:
            task_data = await self.redis.pop_url()
            if not task_data:
                await asyncio.sleep(1)
                continue

            url = task_data["url"]
            depth = task_data.get("depth", 0)
            if depth > self.max_depth:
                continue

            async with self._semaphore:
                await self._process(url, depth)

        logger.info("Worker %s stopped", self.worker_id)

    async def _process(self, url: str, depth: int):
        start = time.time()
        result = CrawlResult(
            task_id=uuid.uuid4().hex,
            url=url,
            depth=depth,
            worker_id=self.worker_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            # Delegate to master pipeline for the actual scrape
            from master_pipeline import run_pipeline
            pipeline = await run_pipeline(
                url=url,
                use_proxy=self.use_proxy,
                stealth_profile=self.profile,
                capture_har=self.capture_har,
                solve_captcha=self.solve_captcha,
                screenshot=False,
                full_page=False,
                wait_seconds=3.0,
                extract_content=True,
                export=False,
            )

            scrape_step = pipeline.get("steps", {}).get("scrape", {})
            result.title = scrape_step.get("title", "")
            result.http_status = 200
            result.waf = pipeline.get("waf", {}).get("name", "")

            # Extract from content step if available
            extract_step = pipeline.get("steps", {}).get("extraction", {})
            if extract_step:
                result.text = f"{extract_step.get('text_chars', 0)} chars extracted"

            await self.redis.increment_stat("pages_crawled")

        except Exception as exc:
            result.status = "error"
            result.error = str(exc)
            await self.redis.increment_stat("errors")

        result.duration_ms = (time.time() - start) * 1000
        await self.redis.push_result(result)
        logger.info("[%s] %s — %s (%.0fms)", self.worker_id, url[:60], result.status, result.duration_ms)

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Master
# ---------------------------------------------------------------------------

class CrawlMaster:
    """
    Pushes seed URLs into the queue.
    """

    def __init__(self, redis: CrawlerRedis, max_pages: int = 500):
        self.redis = redis
        self.max_pages = max_pages

    async def load_seeds(self, seeds_path: str, depth: int = 0):
        """Load URLs from a file (one per line)."""
        path = Path(seeds_path)
        if not path.exists():
            logger.error("Seed file not found: %s", seeds_path)
            return 0
        urls = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
        count = 0
        for url in urls:
            if await self.redis.push_url(url, depth=depth):
                count += 1
        logger.info("Seeded %d URLs", count)
        return count

    async def status(self):
        queue = await self.redis.queue_size()
        seen = await self.redis.seen_count()
        stats = await self.redis.get_stats()
        print(f"\nCrawler status:")
        print(f"  Queue  : {queue} pending")
        print(f"  Seen   : {seen} URLs")
        print(f"  Crawled: {stats.get('pages_crawled', 0)} pages")
        print(f"  Errors : {stats.get('errors', 0)}")
        return {"queue": queue, "seen": seen, "stats": stats}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Distributed Redis crawler")
    sub = parser.add_subparsers(dest="mode")

    master_p = sub.add_parser("master", help="Master: seed URLs")
    master_p.add_argument("--seeds", required=True, help="URL list file")
    master_p.add_argument("--max-pages", type=int, default=500)
    master_p.add_argument("--redis", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    worker_p = sub.add_parser("worker", help="Worker: scrape URLs")
    worker_p.add_argument("--concurrency", type=int, default=2)
    worker_p.add_argument("--profile", default="cloudflare")
    worker_p.add_argument("--use-proxy", action="store_true")
    worker_p.add_argument("--proxy-file", default="config/proxies.txt")
    worker_p.add_argument("--capture-har", action="store_true")
    worker_p.add_argument("--solve-captcha", action="store_true")
    worker_p.add_argument("--redis", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    worker_p.add_argument("--id", default=uuid.uuid4().hex[:8])

    sub.add_parser("status", help="Show crawler status")

    args = parser.parse_args()
    if not args.mode:
        parser.print_help()
        return

    redis = CrawlerRedis(args.redis)
    await redis.connect()

    try:
        if args.mode == "master":
            master = CrawlMaster(redis, max_pages=args.max_pages)
            await master.load_seeds(args.seeds)
            await master.status()

        elif args.mode == "worker":
            worker = CrawlWorker(
                worker_id=args.id,
                redis=redis,
                profile=args.profile,
                use_proxy=args.use_proxy,
                proxy_file=args.proxy_file,
                concurrency=args.concurrency,
                capture_har=args.capture_har,
                solve_captcha=args.solve_captcha,
            )
            try:
                await worker.run()
            except KeyboardInterrupt:
                worker.stop()
                logger.info("Worker %s shutting down", args.id)

        elif args.mode == "status":
            master = CrawlMaster(redis)
            await master.status()
    finally:
        await redis.close()


def asdict(obj):
    return {f: getattr(obj, f) for f in obj.__dataclass_fields__}


if __name__ == "__main__":
    asyncio.run(main())
