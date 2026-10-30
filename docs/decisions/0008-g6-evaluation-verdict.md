# 0008 — Gate G6 evaluation verdict

Date: 2026-10-27
Status: accepted
Gate: Step 6 / Gate G6

## Context

Gate G6 ("Evaluation & hardening") is a JUDGMENT gate, not a pass/fail test:
"results reviewed honestly vs the baseline; decide whether the system earns a
place in real decisions." The four G6 deliverables are built and verified on
`feature/g6` (backtest grid, DB backup + one-command restore, model scorecard,
paper-trading mechanism). The full evaluation is in
`docs/evaluations/g6-evaluation.md`; this note records the go/fix/stop decision
and its rationale, citing the measured results.

Key measured results feeding the decision:
- Backtest grid (10 golden tickers, ~6-month weekly walk-forward, deterministic):
  the rule-based quant lane returned mean **+0.91%** / median +3.01%, vs
  buy-and-hold mean **−4.78%** and Nifty mean **−5.16%**; quant beat buy-and-hold
  on **6/10** and the benchmark on **7/10**, with a wide spread (+18.1% best,
  −20.3% worst).
- Model scorecard: llama3.2 and gemma2:9b BOTH scored 100% structured-output
  validity and 100% number-preservation; llama3.2 is ~62% faster (median 3468 ms
  vs 5620 ms) and fits the 4 GB VRAM budget.
- Hardening: a real `pg_dump` backup + destructive `--clean` restore round-trip
  preserved all 21 tables, the pgvector + pg_trgm extensions, and the Alembic
  head. Offline suite 229 passed, 0 failed, 3x stable. Paid-API cost 0.
- Not provable on this hardware (deferred, not failed): the deep lane's
  multi-ticker backtest and paper book (deep is impractically slow on 4 GB
  VRAM); a ≥4-week paper-trade result (calendar time); the timed
  "pipeline finishes before 09:00 IST" guarantee.

## Decision

**GO — with explicit caveats. Adopt the system as a decision-SUPPORT and
monitoring tool, NOT as a proven trading edge.**

1. The build is accepted: all four G6 deliverables are complete, deterministic
   where they must be, backed up, and tested; the local-only / zero-paid-API
   constraint holds end to end.
2. The quant lane is accepted as RESEARCH INPUT: it modestly beat a down-market
   baseline over the one tested window (6/10 vs buy-and-hold, positive mean), but
   one window with a −20% single-name loss is NOT a proven edge. Its signals are
   explanation/monitoring material, not a reason to trade.
3. **Keep llama3.2** for both tiers (confirms ADR 0004 with measured numbers):
   gemma2:9b offers no quality gain to justify its latency + RAM spill on 4 GB
   VRAM. Re-run the scorecard if the GPU is upgraded.
4. The following are CARRIED as explicit open items to settle on capable
   hardware before any claim of an edge is made (STEERING §10 stop/pivot governs
   the outcome):
   - run the deep lane's multi-ticker backtest (grid `--deep-ticker`) and let the
     deep paper book accumulate;
   - run ≥4 weeks of paper trading (both books) vs the benchmark;
   - verify the nightly pipeline finishes before 09:00 IST under real deep-run
     speed.

## Consequences

- G6 is CLOSED as GO; G0–G6 are complete. `feature/g6` merges to `main`.
- The honest posture is on record: per STEERING §10, if over real paper trading
  neither lane beats buy-and-hold, the system stays an explanation/monitoring
  tool and its signals are not treated as an edge. Nothing in this verdict
  authorizes order execution — paper positions only (rule 9).
- The deferred items are hardware/calendar bound and are the first work for a
  capable machine; they do not reopen the gate, they refine the verdict when
  their data arrives.
- If the GPU is upgraded: re-run the model scorecard and the deep backtest, then
  revisit this verdict with a new note.
