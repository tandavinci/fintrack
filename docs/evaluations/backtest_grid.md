# Backtest grid — quant vs buy-and-hold vs deep (G6 deliverable 1)

Three lanes across the 10 golden tickers, scored against the Nifty 50 (`^NSEI`) benchmark. The quant lane is a WEEKLY walk-forward (step 5 trading days, forward horizon 5 trading days) of the rule-based `quant.recommend` lane over the committed golden parquet frames — fully offline and deterministic. Buy-and-hold is the stock's own first-anchor-to-last close return over the same window. The benchmark column is Nifty buy-and-hold over the same date window. All floats are rounded to 4 dp so two runs are byte-identical.

## Deterministic grid (reproducible)

| Ticker | Quant return % | Quant rating (vs Nifty) | Quant signals | Buy & hold % | Benchmark % |
| --- | ---: | ---: | ---: | ---: | ---: |
| RELIANCE.NS | -2.7706 | 4 | 17 | -9.2819 | -5.2909 |
| TCS.BO | 2.3191 | 4 | 17 | 11.9256 | -4.9687 |
| HDFCBANK.NS | -6.8674 | 3 | 17 | -8.6974 | -5.2909 |
| INFY.NS | -20.3167 | 1 | 17 | 0.8897 | -5.2909 |
| ICICIBANK.NS | 5.6580 | 5 | 17 | 4.4791 | -5.2909 |
| CUMMINSIND.NS | 3.6961 | 4 | 17 | -23.2500 | -5.2909 |
| PERSISTENT.NS | 13.9455 | 5 | 17 | 16.5282 | -5.2909 |
| MPHASIS.BO | -9.2852 | 2 | 17 | 9.6219 | -4.9687 |
| YESBANK.BO | 18.0535 | 5 | 17 | -32.7360 | -4.9687 |
| IDEA.BO | 4.7016 | 4 | 17 | -17.2922 | -4.9687 |

## Deep lane (1 ticker only on this hardware)

The deep lane (TradingAgents multi-agent graph via the local gateway + Ollama) is ~20 min per ticker on the 4 GB VRAM / `llama3.2` stack, so the grid runs it for at most ONE `--deep-ticker` and never across all ten. Its output is non-deterministic LLM text and is reported here separately from the reproducible table above. The full multi-ticker deep grid is deferred to faster hardware (known caveat).

_No deep cell was run (offline grid). Pass `--deep-ticker <TICKER>` with the gateway + Ollama up to populate this section._
