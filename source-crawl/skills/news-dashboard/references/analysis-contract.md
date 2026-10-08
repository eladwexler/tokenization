# Analyst input

Copy the latest snapshot's `analysis` object or use `dashboard/analysis/baseline.json` as the structural reference. Do not rerun `seed_analysis.py` for daily work: it imports the original conversation, not current research.

Required top-level fields: `summary`, `reviewed_at` (timezone-aware ISO timestamp), `author`, `trends`, `forecasts`. Optional `limitations`, `non_news_ids`, and `article_summaries` support the current UI.

Trend: stable `id`, `title`, `status` (strengthening/mixed/weakening/early), `confidence` (high/medium/low), `confidence_reason`, `thesis`, `invalidation`, `assets`, and `evidence`. Each evidence entry has an existing corpus `article_id`, `stance` (supports/challenges/context) and `note`. Preserve source attribution and distinguish speculative warnings from established facts.

Forecast: stable `id`, `symbol`, `name`, `kind` (stock/coin), `currency`, ISO date `horizon`, ordered `[low, high]` pairs in `bear`, `base`, `bull`, `method`, `assumptions`, `invalidation`, and source URLs in `sources`. The UI currently exposes October 2027 and December 2030; add horizon controls if changing the set. Optional `reference_quote` has `price`, `updated_at`, and `source`. Coins use the latest collected provider snapshot. Stock quotes currently require analyst-verified input; no automatic stock quote connector is implemented.

Review triggers: new evidence or changed article text; production adoption differing from forecasts; margin/rate sensitivity; token issuance or unlock changes; altered economic rights; material valuation or financing changes. Record a revision when the analyst view changes, including confidence, assumptions, horizon or beneficiary changes—not only numeric targets.

Snapshots contain the input analysis, archived article revision identifiers, market provenance, coverage and diffs. The archive does not reconstruct overwritten article history from before the first capture. Simulated multi-day tests belong only in temporary directories and never in the real dashboard store.
