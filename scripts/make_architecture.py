"""Generate docs/architecture.pdf (2 pages) from inline SVG via headless Chrome.

    python scripts/make_architecture.py

Page 1: system architecture. Page 2: request flows, background job, scoring.
Needs Google Chrome (macOS path below, or set CHROME=/path/to/chrome).
"""
import html
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

W, H = 1400, 1000
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# name: (fill, stroke) - light tints so dark text stays readable in print.
C = {
    "api":    ("#e8f1fc", "#2a78d6"),
    "svc":    ("#eef6ff", "#2a78d6"),
    "store":  ("#e6f6ef", "#12805c"),
    "job":    ("#fdf0e8", "#c2531f"),
    "tool":   ("#f1f0ed", "#6b6a66"),
    "client": ("#ffffff", "#0b0b0b"),
    "note":   ("#fffbea", "#b58900"),
}
INK, MUTED = "#0b0b0b", "#52514e"


class Page:
    def __init__(self):
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=11, weight="normal", fill=INK, anchor="start", style=""):
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                 f'text-anchor="{anchor}" {style}>{html.escape(s)}</text>')

    def box(self, x, y, w, h, title, lines=(), kind="svc", size=11, title_size=13, dashed=False):
        fill, stroke = C[kind]
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.6"{dash}/>')
        cy = y + 21
        for t in textwrap.wrap(title, int((w - 20) / (title_size * 0.56))) if title else []:
            self.text(x + 12, cy, t, title_size, "bold")
            cy += title_size + 4
        cy += 2
        per = int((w - 30) / (size * 0.56))
        for line in lines:
            wrapped = textwrap.wrap(line, per) or [""]
            for k, wl in enumerate(wrapped):
                self.text(x + 12 + (0 if k == 0 else 10), cy, wl, size, fill=MUTED if line.startswith("·") is False else INK)
                cy += size + 4
        assert cy - (size + 4) < y + h - 2, f"text overflows box '{title}': needs {cy - y}, has {h}"

    def arrow(self, pts, label="", dashed=False, color="#3a3a37", label_at=None):
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#ah)"/>')
        if label:
            lx, ly = label_at or ((pts[0][0] + pts[-1][0]) / 2 + 6, (pts[0][1] + pts[-1][1]) / 2 - 4)
            self.add(f'<rect x="{lx - 3}" y="{ly - 11}" width="{len(label) * 5.6 + 6}" height="15" fill="#fff" opacity="0.9"/>')
            self.text(lx, ly, label, 10.5, fill=MUTED, style='font-style="italic"')

    def svg(self):
        defs = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#3a3a37"/></marker></defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                f'font-family="Helvetica, Arial, sans-serif">{defs}<rect width="{W}" height="{H}" fill="#fff"/>'
                + "".join(self.parts) + "</svg>")


def page1():
    p = Page()
    p.text(40, 46, "Recall: system architecture", 26, "bold")
    p.text(40, 70, "Persistent memory service for AI agents. Redis is optional at runtime (cache and rate limiter fail open).", 13, fill=MUTED)

    p.box(430, 92, 540, 50, "Clients: AI agents / applications", ["HTTP + JSON, every call carries user_id"], "client")
    p.arrow([(700, 142), (700, 205)])

    # API container
    p.add(f'<rect x="40" y="170" width="1320" height="470" rx="14" fill="none" stroke="{C["api"][1]}" stroke-width="1.6" stroke-dasharray="8 5"/>')
    p.text(56, 192, "Recall API process: FastAPI, N uvicorn workers (each with its own model, DB pool, scheduler)", 12.5, "bold", "#1c5cab")
    p.box(60, 205, 1280, 92, "app/routers/memories.py + app/schemas.py (Pydantic validation)", [
        "POST /memories   ·   GET /memories/search?query&user_id&top_k   ·   DELETE /memories/{id}?user_id (idempotent, always 204)",
        "Per-user isolation is enforced in every query (WHERE user_id = ...), not only at the API layer.",
    ], "api")

    xs = [60, 320, 580, 840, 1100]
    w, y, h = 240, 350, 270
    p.box(xs[0], y, w, h, "Rate limiter", ["app/ratelimit.py", "Fixed 60s window per user_id (Redis INCR).",
          "Over limit: 429 + Retry-After.", "Default 600 requests/min/user.", "Redis down: allow (fail open)."], "svc")
    p.box(xs[1], y, w, h, "Search cache", ["app/cache.py", "Key: search:{user}:{version}:{sha1(query, top_k)}",
          "Value: ranked (id, score) pairs, TTL 60s.", "Writes/deletes bump the user's version, orphaning stale entries.",
          "5s circuit breaker if Redis is down."], "svc")
    p.box(xs[2], y, w, h, "Retrieval", ["retrieval/search.py, scoring.py", "1. pgvector cosine: top max(4k, 20) candidates.",
          "   (query embedded via the Embeddings service)", "2. Re-rank by blended score, keep top_k.", "3. record_access: lock rows in id order, then bump access_count, importance, last_accessed_at.",
          "score = 0.6 sim + 0.2 recency + 0.2 importance"], "svc")
    p.box(xs[3], y, w, h, "Embeddings", ["embeddings/base.py: EmbeddingProvider interface", "SentenceTransformerProvider: all-MiniLM-L6-v2, 384-d, CPU.",
          "Micro-batching: concurrent calls are coalesced (up to 32) into one inference, off the event loop.",
          "Swap provider via config, not code."], "svc")
    p.box(xs[4], y, w, h, "Decay & consolidation job", ["jobs/decay.py, APScheduler (in-process)", "Every DECAY_JOB_INTERVAL_HOURS (24).",
          "1. Expire rows past expires_at.", "2. Decay importance x0.9 for memories idle 7+ days.",
          "3. Merge near-duplicates (cosine >= 0.95): keep higher importance, sum access counts.", "Postgres advisory lock: one run at a time."], "job")

    for x in xs:
        p.arrow([(x + w / 2, 297), (x + w / 2, y)], "")
    # to stores
    p.arrow([(xs[0] + w / 2, 620), (xs[0] + w / 2, 740)], "INCR / EXPIRE", label_at=(xs[0] + w / 2 + 8, 690))
    p.arrow([(xs[1] + w / 2, 620), (xs[1] + w / 2, 740)], "GET / SET / INCR", label_at=(xs[1] + w / 2 + 8, 690))
    p.arrow([(xs[2] + w / 2, 620), (xs[2] + w / 2, 740)], "SQL (asyncpg pool)", label_at=(xs[2] + w / 2 + 8, 690))
    p.arrow([(xs[4] + w / 2, 620), (xs[4] + w / 2, 740)], "SQL", label_at=(xs[4] + w / 2 + 8, 690))
    p.arrow([(xs[2] + w, 440), (xs[3], 440)])

    p.box(60, 740, 500, 120, "Redis 7 (docker compose)", [
        "rl:{user}:{minute}: rate-limit counters",
        "ver:{user}: per-user cache version",
        "search:{user}:{ver}:{hash}: cached rankings (60s TTL)",
    ], "store")
    p.box(580, 740, 760, 120, "PostgreSQL 16 + pgvector (docker compose)", [
        "memories(id, user_id, text, embedding vector(384), source, importance_score, access_count,",
        "created_at, last_accessed_at, expires_at). Index on user_id; exact cosine scan (ivfflat deferred).",
        "Schema managed by Alembic (migrations 0001, 0002). Pool: 10 + 20 overflow per worker.",
    ], "store")

    p.box(40, 890, 1320, 84, "Tooling (not part of the running service)", [
        "scripts/loadtest.py: concurrent writes/searches over HTTP   ·   pytest suite against real Postgres + Redis",
        "scripts/simulate.py (in-process app + DB, simulated days) -> data/sim_*.json -> scripts/report.py -> docs/usage_report.html",
    ], "tool")
    return p.svg()


def page2():
    p = Page()
    p.text(40, 46, "Recall: request flows, background job, scoring", 26, "bold")
    p.text(40, 70, "Arrows show the order of steps. Every endpoint starts with the per-user rate limit.", 13, fill=MUTED)

    # --- Search column ---
    p.text(40, 102, "GET /memories/search", 15, "bold", "#1c5cab")
    p.box(40, 112, 700, 50, "1. Rate limit check (Redis)", [], "svc")
    p.box(40, 192, 700, 50, "2. Cache lookup: key = search:{user}:{version}:{hash(query, top_k)}", [], "svc")
    p.arrow([(390, 162), (390, 192)])
    p.text(120, 272, "HIT", 12, "bold", "#12805c")
    p.text(400, 272, "MISS", 12, "bold", "#c2531f")
    p.box(40, 282, 300, 100, "3a. Load rows by id", ["Postgres primary-key read. Rows deleted or expired since caching are dropped; scores come from cache."], "svc")
    p.box(400, 282, 340, 60, "3b. Embed query (micro-batched)", [], "svc")
    p.box(400, 372, 340, 66, "4. pgvector cosine top max(4k, 20)", ["WHERE user_id = ? AND not expired"], "svc")
    p.box(400, 468, 340, 66, "5. Re-rank by blended score", ["keep top_k"], "svc")
    p.box(400, 564, 340, 50, "6. Cache result (TTL 60s)", [], "svc")
    p.arrow([(190, 242), (190, 282)])
    p.arrow([(570, 242), (570, 282)])
    p.arrow([(570, 342), (570, 372)])
    p.arrow([(570, 438), (570, 468)])
    p.arrow([(570, 534), (570, 564)])
    p.box(40, 660, 700, 100, "7. Record access (both paths)", [
        "Lock the returned rows in id order (prevents deadlocks between concurrent searches),",
        "then access_count + 1, importance + 0.02 (cap 1.0), last_accessed_at = now(). Commit.",
    ], "job")
    p.arrow([(190, 382), (190, 660)])
    p.arrow([(570, 614), (570, 660)])
    p.box(40, 790, 700, 50, "8. 200: top_k results, each with its blended score", [], "client")
    p.arrow([(390, 760), (390, 790)])

    # --- Write column ---
    p.text(780, 102, "POST /memories", 15, "bold", "#1c5cab")
    wy = [112, 192, 272, 362, 442]
    p.box(780, wy[0], 280, 50, "1. Rate limit check", [], "svc")
    p.box(780, wy[1], 280, 50, "2. Embed text (micro-batched)", [], "svc")
    p.box(780, wy[2], 280, 70, "3. INSERT into memories", ["importance = hint or 0.5;", "expires_at if DEFAULT_TTL_DAYS"], "store")
    p.box(780, wy[3], 280, 50, "4. Commit, invalidate user cache", [], "svc")
    p.box(780, wy[4], 280, 50, "5. 201 + created memory", [], "client")
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4)]:
        ya = wy[a] + (70 if a == 2 else 50)
        p.arrow([(920, ya), (920, wy[b])])
    p.text(780, 526, "Invalidation = INCR ver:{user}; that user's cached", 11, fill=MUTED)
    p.text(780, 542, "searches are orphaned and expire via TTL.", 11, fill=MUTED)

    # --- Delete + job column ---
    p.text(1100, 102, "DELETE /memories/{id}?user_id", 15, "bold", "#1c5cab")
    p.box(1100, 112, 260, 50, "1. Rate limit check", [], "svc")
    p.box(1100, 192, 260, 70, "2. DELETE WHERE id AND user_id", ["Scoped to the caller"], "store")
    p.box(1100, 292, 260, 50, "3. Invalidate user cache", [], "svc")
    p.box(1100, 372, 260, 70, "4. 204, always", ["Idempotent; never reveals whether an id exists"], "client")
    p.arrow([(1230, 162), (1230, 192)])
    p.arrow([(1230, 262), (1230, 292)])
    p.arrow([(1230, 342), (1230, 372)])

    p.text(780, 590, "Decay job (background, every 24h)", 15, "bold", "#c2531f")
    p.box(780, 600, 580, 160, "Runs in one transaction, guarded by a Postgres advisory lock", [
        "1. Expire: delete rows with expires_at <= now().",
        "2. Decay: importance x 0.9 (floor 0.05) if not accessed in 7 days; never-accessed rows count from created_at.",
        "3. Consolidate, per user: group pairs with cosine >= 0.95, keep the higher-importance memory, sum access counts, delete the rest, log each merge.",
    ], "job")
    p.text(780, 792, "Counterweight to the access bump: searches raise importance, idleness lowers it.", 11, fill=MUTED)

    # --- Scoring strip ---
    p.box(40, 880, 1320, 96, "Scoring (docs/SCORING.md)", [
        "final_score = 0.6 x similarity + 0.2 x recency + 0.2 x importance",
        "similarity = cosine normalized to [0, 1];  recency = exp(-age_days / 14) from created_at;  importance = stored importance_score (access bumps up, decay job down).",
        "Weights are named constants in app/retrieval/scoring.py, overridable from .env, and must sum to 1.0.",
    ], "note")
    return p.svg()


def main():
    body = f'<section>{page1()}</section><section>{page2()}</section>'
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><style>'
           f'@page {{ size: {W}px {H}px; margin: 0 }} html,body {{ margin:0; padding:0 }} '
           f'section {{ width:{W}px; height:{H}px; overflow:hidden; page-break-after: always }} '
           f'section:last-child {{ page-break-after: auto }} svg {{ display:block }}'
           f'</style></head><body>{body}</body></html>')
    out = Path("docs/architecture.pdf").resolve()
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "arch.html"
        src.write_text(doc)
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", f"file://{src}"], check=True, capture_output=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
