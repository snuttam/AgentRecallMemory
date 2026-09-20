"""Synthetic usage generator: many users writing/querying memories over simulated time.

    python scripts/simulate.py --label decay_on  --decay on
    python scripts/simulate.py --label decay_off --decay off
    python scripts/report.py data/sim_*.json     # -> docs/usage_report.html

Runs the real FastAPI app in-process (real embeddings, real Postgres) with the
cache and rate limiter off, so latency/quality reflect retrieval itself.

Simulated time: at the end of each simulated day every timestamp of the
simulation's rows is shifted back one day (created_at, last_accessed_at,
expires_at), then the decay job runs. No clock mocking needed.

Eval set: each user has ground-truth facts (cuisine, city, job, ...). Facts get
restated (near-duplicates) and occasionally *change* (old value becomes stale).
Every day each user asks paraphrased questions; we record whether a memory
asserting the CURRENT value is in the top-k (hit@k), where it ranks (MRR), and
whether a stale value outranks it. Distractor memories share vocabulary with
the facts ("Tried a new sushi place with a coworker") to make retrieval earn it.

Metrics per simulated day are written to data/sim_<label>.json.
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

from app.config import settings

settings.cache_enabled = False
settings.rate_limit_per_minute = 0

from app.db import SessionLocal  # noqa: E402
from app.jobs import decay  # noqa: E402
from app.main import app  # noqa: E402
from app.retrieval import scoring  # noqa: E402

# name: (values, statement templates, query paraphrases, distractor templates)
ATTRS = {
    "cuisine": (["sushi", "ramen", "tacos", "pasta", "curry", "pizza"],
                ["My favorite cuisine is {v}", "I really love eating {v}", "Nothing beats {v} for dinner, it's my go-to"],
                ["What food does the user like most?", "Which cuisine do they prefer?", "What do they usually want for dinner?"],
                ["Tried a new {v} place with a coworker last week", "Cooked {v} for the family on Sunday"]),
    "language": (["Python", "Rust", "Go", "TypeScript", "Java", "Kotlin"],
                 ["I mostly write code in {v}", "{v} is my main programming language", "At work I use {v} every day"],
                 ["What programming language does the user code in?", "Which language do they use at work?", "What is their main tech stack?"],
                 ["Read a blog post comparing {v} to other languages", "Fixed a bug in a {v} service today"]),
    "city": (["Lisbon", "Berlin", "Toronto", "Osaka", "Austin", "Nairobi"],
             ["I live in {v}", "My home is in {v}", "I moved to {v} and settled there"],
             ["Where does the user live?", "What city are they based in?", "Where is their home?"],
             ["Flew through {v} on a layover", "Watched a documentary about {v}"]),
    "pet": (["a dog named Biscuit", "a cat named Miso", "a parrot named Kiwi", "a rabbit named Clover", "a turtle named Otto", "a hamster named Pepper"],
            ["I have {v}", "My pet is {v}", "At home I take care of {v}"],
            ["What pet does the user have?", "Do they have any animals at home?", "What is their pet's name?"],
            ["Saw a lovely dog at the park", "Bought pet supplies for a friend"]),
    "hobby": (["hiking", "chess", "pottery", "cycling", "photography", "gardening"],
              ["My favorite hobby is {v}", "I spend my weekends on {v}", "I'm really into {v}"],
              ["What does the user do for fun?", "What hobby do they have?", "How do they spend their weekends?"],
              ["Watched a video about {v} techniques", "Bought a magazine about {v}"]),
    "drink": (["coffee", "green tea", "oat latte", "espresso", "kombucha", "hot chocolate"],
              ["I drink {v} every morning", "My go-to drink is {v}", "I can't start the day without {v}"],
              ["What does the user drink in the morning?", "What is their favorite beverage?", "What do they drink daily?"],
              ["Ran out of {v} in the office kitchen", "Read that {v} is trending"]),
    "sport": (["tennis", "swimming", "climbing", "running", "football", "badminton"],
              ["I play {v} regularly", "{v} is my main sport", "I train for {v} three times a week"],
              ["What sport does the user play?", "How do they stay active?", "What do they train for?"],
              ["Watched a {v} match on TV", "Bought new gear for {v}, as a gift for my brother"]),
    "job": (["nurse", "teacher", "data engineer", "architect", "chef", "journalist"],
            ["I work as a {v}", "My job is being a {v}", "I've been a {v} for a few years"],
            ["What does the user do for work?", "What is their profession?", "What is their occupation?"],
            ["Met a {v} at a conference", "My cousin is a {v}"]),
}
NOISE = ["Reminder: dentist appointment on {day}", "Meeting with {name} moved to {day}",
         "Need to buy {item} tomorrow", "The weather this afternoon was {w}",
         "{name} sent me the slides about {topic}", "Watched a documentary about {topic}",
         "Booked a table for {day} evening", "Thinking about {topic} again today"]
FILL = {"day": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "name": ["Alex", "Sam", "Priya", "Jordan", "Mei", "Luca"],
        "item": ["batteries", "printer paper", "light bulbs", "stamps", "a charger"],
        "w": ["rainy", "sunny", "windy", "foggy"],
        "topic": ["climate policy", "deep sea life", "the roman empire", "space telescopes", "urban farming", "jazz history"]}

NOISE_TTL_DAYS = 14
TOP_K = 5


def noise_text(rng):
    return rng.choice(NOISE).format(**{k: rng.choice(v) for k, v in FILL.items()}) + f" ({rng.randrange(10**4)})"


def distractor_text(rng, attr):
    values, _, _, distractors = ATTRS[attr]
    return rng.choice(distractors).format(v=rng.choice(values))


class SimUser:
    def __init__(self, uid):
        self.uid = uid
        self.value = {}      # attr -> current value
        self.current = {}    # attr -> set of memory ids asserting the current value
        self.stale = {}      # attr -> set of memory ids asserting an old value


async def write(client, uid, txt):
    t0 = time.perf_counter()
    r = await client.post("/memories", json={"text": txt, "user_id": uid, "source": "sim"})
    r.raise_for_status()
    return r.json()["id"], time.perf_counter() - t0


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] * 1000


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--decay", choices=["on", "off"], default="on")
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--queries-per-day", type=int, default=2)
    ap.add_argument("--keep", action="store_true", help="keep simulated rows in the DB afterwards")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    prefix = f"sim-{args.label}-{uuid.uuid4().hex[:6]}-"
    users = [SimUser(f"{prefix}{i}") for i in range(args.users)]
    like = prefix + "%"
    days_out = []
    write_lat_all, search_lat_all = [], []

    async def sql(stmt, **params):
        async with SessionLocal() as s:
            r = await s.execute(text(stmt), params)
            await s.commit()
            return r

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim", timeout=120) as client:
        # Warm the model so day-1 latency isn't a cold start.
        await client.get("/memories/search", params={"query": "warmup", "user_id": prefix + "warm"})

        # Day 0: every user states every fact once (this is the eval set).
        for u in users:
            for attr, (values, stmts, _, _) in ATTRS.items():
                v = rng.choice(values)
                mid, _ = await write(client, u.uid, rng.choice(stmts).format(v=v))
                u.value[attr], u.current[attr], u.stale[attr] = v, {mid}, set()

        for day in range(1, args.days + 1):
            wl, sl = [], []
            hits = top1 = stale_out = n_q = 0
            rr_sum = 0.0
            new_noise_ids = []
            for u in users:
                # Restate a current fact (near-duplicate), and sometimes change one.
                if rng.random() < 0.20:
                    attr = rng.choice(list(ATTRS))
                    mid, dt = await write(client, u.uid, rng.choice(ATTRS[attr][1]).format(v=u.value[attr]))
                    u.current[attr].add(mid); wl.append(dt)
                if rng.random() < 0.08:
                    attr = rng.choice(list(ATTRS))
                    new_v = rng.choice([v for v in ATTRS[attr][0] if v != u.value[attr]])
                    mid, dt = await write(client, u.uid, rng.choice(ATTRS[attr][1]).format(v=new_v))
                    u.stale[attr] |= u.current[attr]
                    u.value[attr], u.current[attr] = new_v, {mid}; wl.append(dt)
                # Chatter: generic noise plus distractors that reuse fact vocabulary.
                for _ in range(2):
                    txt = distractor_text(rng, rng.choice(list(ATTRS))) if rng.random() < 0.4 else noise_text(rng)
                    mid, dt = await write(client, u.uid, txt)
                    new_noise_ids.append(mid); wl.append(dt)
                # Eval queries.
                for _ in range(args.queries_per_day):
                    attr = rng.choice(list(ATTRS))
                    t0 = time.perf_counter()
                    r = await client.get("/memories/search", params={
                        "query": rng.choice(ATTRS[attr][2]), "user_id": u.uid, "top_k": TOP_K})
                    sl.append(time.perf_counter() - t0)
                    ids = [m["id"] for m in r.json()]
                    rank = next((i for i, m in enumerate(ids) if m in u.current[attr]), None)
                    stale_rank = next((i for i, m in enumerate(ids) if m in u.stale[attr]), None)
                    n_q += 1
                    hits += rank is not None
                    top1 += rank == 0
                    rr_sum += 1 / (rank + 1) if rank is not None else 0.0
                    stale_out += stale_rank is not None and (rank is None or stale_rank < rank)

            # Chatter carries a client-side TTL; facts never expire.
            await sql("UPDATE memories SET expires_at = now() + make_interval(days => :d) WHERE id = ANY(CAST(:ids AS uuid[]))",
                      d=NOISE_TTL_DAYS, ids=new_noise_ids)

            row = (await sql(
                "SELECT count(*) AS n, coalesce(avg(importance_score), 0) AS imp, coalesce(sum(access_count), 0) AS acc "
                "FROM memories WHERE user_id LIKE :p", p=like)).one()
            row_dict = {
                "day": day, "memories": row.n, "memories_per_user": row.n / args.users,
                "avg_importance": float(row.imp), "queries": n_q,
                "hit_at_5": hits / n_q, "top1_current": top1 / n_q, "mrr": rr_sum / n_q,
                "stale_outranks": stale_out / n_q,
                "search_p50_ms": pct(sl, .5), "search_p95_ms": pct(sl, .95),
                "write_p50_ms": pct(wl, .5), "write_p95_ms": pct(wl, .95),
                "expired": 0, "decayed": 0, "merged_away": 0, "job_seconds": 0.0,
            }
            write_lat_all += wl; search_lat_all += sl

            # Advance the clock one day, then run the maintenance job.
            await sql("UPDATE memories SET created_at = created_at - interval '1 day', "
                      "last_accessed_at = last_accessed_at - interval '1 day', "
                      "expires_at = expires_at - interval '1 day' WHERE user_id LIKE :p", p=like)
            if args.decay == "on":
                t0 = time.perf_counter()
                for u in users:
                    st = await decay.run_decay_job(user_id=u.uid)
                    row_dict["expired"] += st.expired
                    row_dict["decayed"] += st.decayed
                    row_dict["merged_away"] += st.merged_away
                row_dict["job_seconds"] = time.perf_counter() - t0
            days_out.append(row_dict)
            print(f"[{args.label}] day {day:>2}: mem/user={row_dict['memories_per_user']:6.1f} "
                  f"hit@5={row_dict['hit_at_5']:.2f} top1={row_dict['top1_current']:.2f} "
                  f"stale>{row_dict['stale_outranks']:.2f} search p50={row_dict['search_p50_ms']:.0f}ms "
                  f"exp={row_dict['expired']} merged={row_dict['merged_away']}", flush=True)

        per_user = [r.n for r in (await sql(
            "SELECT count(*) AS n FROM memories WHERE user_id LIKE :p GROUP BY user_id", p=like)).all()]
        if not args.keep:
            await sql("DELETE FROM memories WHERE user_id LIKE :p", p=like)

    out = {
        "label": args.label,
        "config": {"decay": args.decay, "users": args.users, "days": args.days, "seed": args.seed,
                   "queries_per_day": args.queries_per_day, "top_k": TOP_K,
                   "noise_ttl_days": NOISE_TTL_DAYS,
                   "weights": [scoring.W_SIMILARITY, scoring.W_RECENCY, scoring.W_IMPORTANCE],
                   "recency_half_life_days": scoring.RECENCY_HALF_LIFE_DAYS,
                   "decay_factor": decay.DECAY_FACTOR, "stale_after_days": decay.STALE_AFTER_DAYS,
                   "duplicate_similarity": decay.DUPLICATE_SIMILARITY},
        "days": days_out,
        "final_memories_per_user": {"p50": statistics.median(per_user), "max": max(per_user), "min": min(per_user)},
        "overall_latency_ms": {"search_p50": pct(search_lat_all, .5), "search_p95": pct(search_lat_all, .95),
                               "write_p50": pct(write_lat_all, .5), "write_p95": pct(write_lat_all, .95)},
    }
    Path("data").mkdir(exist_ok=True)
    Path(f"data/sim_{args.label}.json").write_text(json.dumps(out, indent=1))
    print(f"wrote data/sim_{args.label}.json")


if __name__ == "__main__":
    asyncio.run(main())
