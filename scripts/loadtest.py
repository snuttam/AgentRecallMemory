"""Async load test for Recall: concurrent writes + searches against a running server.

    uvicorn app.main:app --port 8000 &
    python scripts/loadtest.py --concurrency 32 --duration 20

Each simulated user is seeded with --seed memories, then workers hammer the API
for --duration seconds with a mix of writes and searches. Queries are drawn from
a skewed pool so some are "hot" (repeat often), like real traffic.
"""
import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict

import httpx

TOPICS = ["hiking", "python", "coffee", "travel", "music", "cooking", "cycling", "reading",
          "movies", "gardening", "chess", "photography", "running", "painting", "sailing"]
FACTS = ["loves {t}", "started {t} last year", "is looking for a {t} partner",
         "reads a lot about {t}", "said {t} is their favorite hobby", "wants to get better at {t}"]
QUERIES = [f"what does the user think about {t}?" for t in TOPICS]
# Zipf-like weights: the first few queries are hot.
QUERY_WEIGHTS = [1 / (i + 1) for i in range(len(QUERIES))]


def fact() -> str:
    return f"User {random.choice(FACTS).format(t=random.choice(TOPICS))} ({uuid.uuid4().hex[:6]})"


async def seed(client, users, per_user, concurrency):
    sem = asyncio.Semaphore(concurrency)

    async def one(uid):
        async with sem:
            r = await client.post("/memories", json={"text": fact(), "user_id": uid, "source": "seed"})
            r.raise_for_status()

    await asyncio.gather(*(one(u) for u in users for _ in range(per_user)))


async def worker(client, users, write_ratio, deadline, stats):
    while time.perf_counter() < deadline:
        uid = random.choice(users)
        if random.random() < write_ratio:
            op = "write"
            req = client.post("/memories", json={"text": fact(), "user_id": uid, "source": "load"})
        else:
            op = "search"
            q = random.choices(QUERIES, QUERY_WEIGHTS)[0]
            req = client.get("/memories/search", params={"query": q, "user_id": uid, "top_k": 5})
        t0 = time.perf_counter()
        try:
            r = await req
            code = r.status_code
        except httpx.HTTPError as e:
            code = type(e).__name__
        stats[op].append((time.perf_counter() - t0, code))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] * 1000


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20, help="memories seeded per user")
    ap.add_argument("--write-ratio", type=float, default=0.2)
    args = ap.parse_args()

    users = [f"load-{uuid.uuid4().hex[:8]}-{i}" for i in range(args.users)]
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(base_url=args.url, timeout=60, limits=limits) as client:
        print(f"seeding {args.users} users x {args.seed} memories ...")
        t0 = time.perf_counter()
        await seed(client, users, args.seed, min(args.concurrency, 8))
        print(f"seeded in {time.perf_counter() - t0:.1f}s")

        stats = defaultdict(list)
        deadline = time.perf_counter() + args.duration
        print(f"load: concurrency={args.concurrency} duration={args.duration}s write_ratio={args.write_ratio}")
        await asyncio.gather(*(worker(client, users, args.write_ratio, deadline, stats) for _ in range(args.concurrency)))

    total = 0
    print(f"\n{'op':8}{'count':>8}{'rps':>8}{'p50ms':>9}{'p95ms':>9}{'p99ms':>9}  status")
    for op, rows in sorted(stats.items()):
        lat = [x[0] for x in rows]
        total += len(rows)
        codes = dict(Counter(x[1] for x in rows))
        print(f"{op:8}{len(rows):>8}{len(rows) / args.duration:>8.1f}{pct(lat, .5):>9.0f}{pct(lat, .95):>9.0f}{pct(lat, .99):>9.0f}  {codes}")
    print(f"{'total':8}{total:>8}{total / args.duration:>8.1f}")


if __name__ == "__main__":
    asyncio.run(main())
