# Daily research dashboard: first review version

## Product
A local HTTP working dashboard for the existing source crawler. Open on daily changes and the current analyst thesis. Use an immutable snapshot selector to inspect prior decisions. Evidence and prices have source timestamps; forecasts have an explicit horizon, assumptions and revision history.

## Tasks
- [x] Design: overview, trends, forecasts, evidence and history views; restrained light research workspace with green/amber/red signals.
- [x] Implement archived article revisions and immutable report snapshots.
- [x] Import prior research as an attributed baseline, with subjective confidence and scenario assumptions.
- [x] Compare news hashes, prices, source coverage, trend assessments and forecast ranges.
- [x] Add conservative review flags: evidence change is not an automatic thesis change.
- [x] Package local daily-update skill; preserve existing token-daily collection workflow.
- [x] Validate idempotency, revisions, dates, history, HTTP and browser behavior.
- [x] Hand off self-contained preview and screenshot for user review; local listener is blocked by this runtime.

## Update contract
1. Optionally collect news through existing token-daily; never represent cached ingestion as a new crawl.
2. Archive date-qualified source text and metadata by content hash (2025 onward, publication <= snapshot cutoff).
3. Compare against latest committed snapshot. New/changed evidence and market movement flag review.
4. An analyst supplies a JSON analysis with linked evidence, supporting/challenging positions, confidence rationale, invalidation conditions and forecast assumptions. Unreviewed data updates retain previous opinions and explicitly show pending review.
5. Commit only after validating analysis. A semantic fingerprint makes unchanged commits a no-op. Files are append-only; one atomic latest pointer follows the successful snapshot write.
6. Serve locally. Scheduling and external publication are later decisions, not part of this prototype.

## Interpretation
Confidence is analyst judgment, not statistical probability. Forecast ranges are scenarios, not validated fair values. Forecast targets never rebase merely because a quote moved. First snapshot is a baseline: no invented previous days. New history is created by subsequent real data or analyst changes.

## Review priorities
Information density, trend labels, evidence grouping, desired forecast horizons, and whether the analyst-review workflow should remain manual or be invoked by a scheduled agent.

## Verification result

11 pipeline/HTTP-route tests passed. A cloud browser checked all five views, evidence search, trend and forecast dialogs, horizon/type filters and mobile overflow with no page errors. UI verification used the real snapshot with shortened article bodies; the shipped HTML contains the complete archived bodies. Local Chrome and local HTTP sockets are blocked by the execution environment. No hosted URL or schedule was created.
