# TODOS — AstraAnalyzerPro v2

Items deferred from the initial design review. Each has context for someone picking it up cold.

---

## LLM vs Proxy Divergence Measurement

**What:** Build a correlation test between `llm_proxy.py` and real LLM macro bias.
**Why:** The backtester validates proxy rules, not the real LLM. If they disagree 40% of the time, backtest results don't predict live performance.
**How:** Take 100 historical date-points (spread across 2020-2024, including macro regime changes). Feed identical FRED/DXY/VIX data to both proxy and LLM. Count direction matches. Target: >= 75% agreement.
**Where to start:** `backtest/llm_proxy.py` + `core/macro_engine.py`. Add `scripts/correlation_test.py`.
**Depends on:** Proxy backtester complete (step 3), LLM engine complete (step 5).
**Priority:** Must complete before trusting backtest results as predictive of live.

---

## Walk-Forward Window Sensitivity Analysis

**What:** Run the backtester with 3 walk-forward configurations: 3mo/1mo, 6mo/1mo, 12mo/2mo.
**Why:** Gold has multi-year macro regime cycles. A 6-month training window may overfit to the current regime and produce good backtest metrics that collapse when the rate cycle turns. If PF varies > 0.3 across window sizes, the strategy is regime-dependent.
**How:** Add a `--wf-train-months` and `--wf-test-months` parameter to `backtest/engine.py`. Run 3 configs, compare PF/MaxDD distributions.
**Where to start:** `backtest/engine.py` walk-forward loop.
**Depends on:** Step 4 passes (basic backtest validation done).
**Priority:** Important before live, not blocking initial validation.

---
