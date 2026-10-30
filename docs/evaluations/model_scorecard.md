# Model qualification scorecard (G6 deliverable 3)

Compares local Ollama models on structured-output validity, number-preservation (the "models explain, code computes" discipline), and latency / throughput. Every model call was routed through the gateway (`gateway.client.generate_sync`) — the only path to Ollama.

## Comparison

| Model | Validity rate | Number-preservation | Mean latency (ms) | Median latency (ms) | Tokens/sec | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `llama3.2` | 100.0% (20/20) | 100.0% (5/5) | 3467 | 3468 | 4.33 |  |
| `gemma2:9b` | 100.0% (20/20) | 100.0% (5/5) | 6902 | 5620 | 2.73 | spills into RAM on 4 GB VRAM |

## Recommendation

**Recommended model: `llama3.2`**

llama3.2 passed the validity bar (validity 100.0%, number-preservation 100.0%), matched or beat the alternatives on number-preservation, and is fastest among them (median latency 3468 ms), which also fits the 4 GB VRAM budget.

## Hardware note (4 GB VRAM budget)

This machine has an RTX 2050 with **4 GB VRAM**. `llama3.2` (~2 GB, 3B) fits in VRAM. `gemma2:9b` (~5.4 GB) does NOT fit and **spills into system RAM**, which makes it materially slower per token. The bigger model only earns its cost if it clears the validity bar AND preserves numbers at least as well as the smaller model; the latency columns above quantify what that cost is.

## Method

- Validity: 20 schema-constrained generations against a strict BUY/HOLD/SELL + confidence schema; validity rate = valid / N; bar = 95%.
- Number-preservation: each case hands the model a small set of allowed figures and asks it to report one of them; any number not in the allowed set (within tolerance) is a FAILURE. The schema is never loosened to make a model pass.
- Latency / throughput: mean & median latency and mean tokens/sec (completion tokens / elapsed seconds) over the validity generations.
