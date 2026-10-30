# G6 — Evaluation & hardening report

Date: 2026-10-27
Gate: Step 6 / Gate G6
Branch: `feature/g6`

This report pulls together the four G6 deliverables and reaches an HONEST verdict
against the buy-and-hold baseline, which is the whole point of Gate G6: "results
reviewed honestly vs the baseline; decide whether the system earns a place in
real decisions." The go/fix/stop decision is recorded in
`docs/decisions/0008-g6-evaluation-verdict.md`.

Read this alongside the raw artifacts it cites:
`docs/evaluations/backtest_grid.{csv,md}` and
`docs/evaluations/model_scorecard.{md,json}`.

## 1. What was built (and what it proves)

| # | Deliverable | Built | Verified here |
| --- | --- | --- | --- |
| 1 | Backtest grid (quant / buy-and-hold / deep vs Nifty) | yes | quant + buy-and-hold on all 10 goldens, byte-reproducible; deep deferred (hardware) |
| 2 | PostgreSQL backup + one-command restore | yes | real backup + `--clean` restore round-trip; 21 tables + extensions + migration head preserved |
| 3 | Model qualification scorecard (llama3.2 vs gemma2:9b) | yes | real run, both models, through the gateway |
| 4 | Paper-trading mechanism (dual quant + deep book) | yes | open/mark/close lifecycle on live Postgres; a real BUY opened a position at a real close |

Offline test suite across the gate: **229 passed, 0 failed**, run 3x stable
(173 at the start of G6 → +7 grid, +16 paper, +33 scorecard).

## 2. The backtest grid — quant vs the baseline

Source: `docs/evaluations/backtest_grid.csv`. A weekly walk-forward (step 5 /
horizon 5 trading days) of the rule-based quant lane over the 10 committed golden
frames (~6 months, 2026-04 to 2026-10), scored vs Nifty 50 buy-and-hold. The
quant/buy-and-hold/benchmark columns are deterministic (byte-identical on re-run,
SHA-256 `9E9EA9B6…`).

Aggregate over the 10 golden tickers:

| Lane | Mean return | Median return |
| --- | ---: | ---: |
| Quant (walk-forward) | **+0.91%** | +3.01% |
| Buy-and-hold (per stock) | −4.78% | −3.90% |
| Nifty benchmark | −5.16% | — |

- Quant beat **buy-and-hold on 6 of 10** tickers and the **Nifty benchmark on
  7 of 10**.
- Quant ratings (1–5 vs benchmark): `[1, 2, 3, 4, 4, 4, 4, 5, 5, 5]` — mostly 4s
  and 5s, with two clear losers.
- The spread is wide and honest: the quant lane returned **+18.1%** on YESBANK.BO
  (vs −32.7% buy-and-hold) and **+3.7%** on CUMMINSIND.NS (vs −23.3%), but
  **−20.3%** on INFY.NS (vs +0.9% buy-and-hold) — the rule-based strategy was
  whipsawed shorting a stock that kept recovering (verified: repeated SELL
  signals right before +10% moves). No bug; just an unflattering regime for the
  rules.

Honest reading: over this particular ~6-month window the quant lane **modestly
beat a down-market baseline on average and more often than not** — but with
enough dispersion (a −20% single-name loss) that this is a "worth continuing to
watch", not a proven edge. One window is not a track record.

### The deep lane is NOT in this result
The deep column was attempted on one ticker and ran >21 minutes without
returning on this 4 GB-VRAM machine (stuck past the news-gather phase; Yahoo has
no `.NS` news). Per the stop/pivot rule it is **deferred to capable hardware**.
So the grid's honest comparison here is **quant vs buy-and-hold vs benchmark
only**; deep-vs-baseline is an open item for TK's machine. The deep cell is
wired and flag-gated (`--deep-ticker`), so it populates with one command there.

## 3. Paper trading — mechanism, not yet a result

The dual quant+deep paper book (`paper_positions`, migration 007) opens on BUY,
marks to market, and closes on SELL or horizon, sized at a fixed ₹10,000
notional, scored per book vs the benchmark with the same `score_vs_benchmark`
the grid uses. The lifecycle is proven end-to-end on live Postgres and a real
BUY opened a RELIANCE.NS position at a real ₹1184.50 close.

What it is NOT yet: a ≥4-week paper-trade RESULT. That is calendar time, not a
build task — the mechanism must now simply run nightly. The **deep** paper book
additionally needs the deep lane to produce recommendations regularly, which is
the same hardware blocker as §2. So paper-trading contributes a proven mechanism
to G6; its result accrues over the coming weeks on whichever machine runs the
nightly pipeline.

## 4. Model comparison — the bigger model does not earn its cost

Source: `docs/evaluations/model_scorecard.{md,json}`. Both models were run
through the gateway (the only Ollama path), 20 schema-constrained generations +
5 number-preservation cases each.

| Model | Validity | Number-preservation | Median latency | Tokens/sec | Fits 4 GB VRAM |
| --- | ---: | ---: | ---: | ---: | :---: |
| llama3.2 (3B) | 100% (20/20) | 100% (5/5) | 3468 ms | 4.3 | yes |
| gemma2:9b | 100% (20/20) | 100% (5/5) | 5620 ms | 2.7 | no (spills to RAM) |

Verdict: **keep llama3.2.** Both models are perfect on the quality dimensions
that matter (valid structured output, no invented numbers), so there is no
quality reason to pay gemma2:9b's ~62%-higher median latency and RAM spill on
this hardware. This confirms ADR 0004's hardware-adapted model choice with
measured numbers. (On an 8 GB+ GPU, re-run the scorecard — gemma2:9b may become
attractive if it stops spilling; the harness is ready.)

## 5. Hardening

- **Backups:** `infra/backup-db.ps1` + `infra/restore-db.ps1` run the
  container's own `pg_dump`/`pg_restore` via `docker exec` + `docker cp` (no host
  client install, matching server version). A real backup + destructive
  `--clean` restore round-trip was verified: 21 tables, the `vector` +
  `pg_trgm` extensions, and the Alembic head all preserved; the restore requires
  a typed `yes` (or `-Force`). Documented in `docs/runbook.md`.
- **Determinism:** the grid's quant/buy-and-hold/benchmark table is
  byte-reproducible; the committed golden parquets are the point-in-time anchor.
  (Caught + reverted one accidental overwrite of the goldens by a live
  `test_golden_materialize` run during the gate — noted for operators.)

## 6. Measures table (STEERING §9)

| Measure | Target | Result at G6 |
| --- | --- | --- |
| Paid API cost | 0 | **0** — every model call through the local gateway, no keys |
| Valid structured output | ≥95% | **100%** (both models, 20/20) |
| Numbers not in the snapshot | <5% of reports | **0%** on the scorecard's number-preservation cases (5/5 both) |
| Backtest reproducibility | identical on two runs | **identical** (grid CSV SHA-256 stable) |
| Nightly pipeline finish before 09:00 IST | timed guarantee | **unverified here** — depends on deep-lane speed (TK hardware); sequencing + record built in G5 |
| Result vs baseline (by rating vs Nifty / buy-and-hold) | reported | **reported** (§2): quant beat buy-and-hold 6/10, benchmark 7/10, mean +0.91% vs −4.78%/−5.16% |
| Schedule slip | ≤2 weeks/phase | within tolerance |

## 7. What this machine could and could not prove

Could (done here): the backtest grid for quant + buy-and-hold + benchmark;
backups + restore; the model scorecard for both models; the paper-trading
mechanism; the full offline suite green.

Could NOT (hardware / calendar, deferred — not failures):
- the **deep lane's** multi-ticker backtest and its paper book — the deep lane is
  impractically slow on 4 GB VRAM (the documented, repeatedly-hit blocker);
- the **≥4-week paper-trade result** — calendar time;
- the **timed "finishes before 09:00 IST"** pipeline guarantee — needs real
  deep-run speed.

These are the same honest caveats carried since G4/G5 and are exactly what a
better machine is for. None of them block the G6 build; they bound what the G6
*numbers* currently cover.

## 8. Verdict (summary; full note in decisions/0008)

**GO, with honest caveats.** The system earns a place as a decision-SUPPORT and
monitoring tool: the quant lane modestly beat the baseline over the tested
window, the plumbing is deterministic and backed up, and the local-only,
zero-paid-API constraint holds end to end. It has NOT earned the status of a
proven edge — one 6-month window, a wide dispersion, no deep-lane result, and no
live paper-trade track record yet. Treat its signals as research input, not as a
reason to trade, until the deferred items (deep lane on capable hardware, ≥4
weeks of paper trading) are in. This matches STEERING §10's stance: if neither
lane beats buy-and-hold over real paper trading, keep the system as an
explanation/monitoring tool and stop treating its signals as an edge.
