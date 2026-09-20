# Retrieval Scoring

## Formula

```
final_score = (similarity * w1) + (recency_decay * w2) + (importance * w3)
```

| Term | Definition | Default |
|---|---|---|
| `similarity` | Cosine similarity between query embedding and memory embedding, normalized to [0, 1] | pgvector `<=>` operator, rescaled |
| `recency_decay` | `exp(-age_days / half_life_days)` | `half_life_days = 14` |
| `importance` | Stored `importance_score`, in [0, 1] | starts at `importance_hint` or 0.5, updated by access + decay job |
| `w1, w2, w3` | Blend weights, sum to 1.0 | `0.6, 0.2, 0.2` |

## Why not pure vector similarity?

A memory that's a near-perfect semantic match but was written six months ago
and never referenced again is usually less useful than a slightly-less-exact
match that's recent or has been repeatedly relevant. Pure cosine similarity
has no way to express that. Recency and importance terms let old, unused
memories fade in ranking without being deleted outright — that's what the
decay job (Phase 3) acts on.

## Why these starting weights?

`0.6/0.2/0.2` biases toward relevance being the dominant signal (this is
still fundamentally semantic search) while giving recency and importance
enough weight to break ties and surface memories that are contextually
"alive." These are defaults, not conclusions — they should move based on
real retrieval quality feedback once Phase 1 is running against real usage,
not be treated as fixed.

## Changelog

- Phase 1: weights set to `0.6/0.2/0.2` as a starting point, not yet tuned
  against real query/relevance data.
- Phase 2: full blended score implemented (`app/retrieval/scoring.py`,
  `app/retrieval/search.py`). Weights unchanged (`0.6/0.2/0.2`); they and the
  half-life can be overridden via `.env` (`SCORE_WEIGHT_*`,
  `RECENCY_HALF_LIFE_DAYS`) and must sum to 1.0 (checked at import).

## Implementation notes (Phase 2)

- **Recency** is measured from `created_at`. Note `exp(-age/half_life)` is an
  e-folding time, not a true half-life (at 14 days the value is ~0.37, not
  0.5). Kept as specified above; switch to `0.5 ** (age/half_life)` if a true
  half-life is wanted.
- **Two-stage ranking:** pgvector pulls a candidate pool of
  `max(4 * top_k, 20)` by similarity, then the blended score re-ranks it in
  Python. A memory outside the similarity pool can never surface, no matter
  how recent or important. Fine at current scale; revisit if quality
  suffers.
- **Access feedback:** every memory returned by a search gets
  `access_count += 1` and `importance_score += 0.02` (capped at 1.0,
  `ACCESS_IMPORTANCE_BUMP`). The update is atomic in SQL. Only returned
  results are counted, not the whole candidate pool. The response shows the
  post-update values.
- **Known feedback loop:** returned memories gain importance, which raises
  their rank, which makes them more likely to be returned again. The bump is
  deliberately small; the Phase 3 decay job is what counterbalances it.
- Phase 3: decay job (`app/jobs/decay.py`) now feeds back into the importance
  term. Stale memories (not accessed for 7 days; never-accessed ones count
  from `created_at`) have `importance_score` multiplied by 0.9 on each run,
  floored at 0.05. Searches stamp `last_accessed_at`, which is what keeps a
  memory from decaying. This is the counterweight to the Phase 2 access bump.
  Because the factor is applied per run, the effective decay rate depends on
  `DECAY_JOB_INTERVAL_HOURS` (default 24).
