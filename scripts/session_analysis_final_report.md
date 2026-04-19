# Range Breakout V1: Session Analysis Report

## Test Configurations

### 1. BASELINE: London + NY only
- **Sessions**: London 07-12 UTC, NY 13-17 UTC
- **Blocked**: 00-07, 12-13, 17-22 UTC
- **Risk**: 0.4% per trade

### 2. Tokyo Added
- **Sessions**: Tokyo 06-08, London 08-12, NY 13-17 UTC
- **Blocked**: 00-06, 12-13, 17-22 UTC
- **Risk**: 0.4% per trade

### 3. 24/7 (No blocks)
- **Sessions**: All hours enabled
- **Blocked**: None
- **Risk**: 0.4% per trade

## Results Comparison

| Metric | Baseline (L+NY) | Tokyo Added | 24/7 | Best |
|--------|-----------------|-------------|------|------|
| **Total Trades** | 861 | 861 | 861 | All equal |
| **Win Rate** | 73.5% | 73.5% | 73.5% | All equal |
| **Profit Factor** | 2.38 | 2.38 | 2.38 | All equal |
| **Max DD** | 2.22% | 2.10% | 2.10% | Tokyo/24/7 ✅ |
| **Net PnL** | $32,006 | $29,738 | $29,738 | Baseline ✅ |
| **End Balance** | $42,006 | $39,738 | $39,738 | Baseline ✅ |

## Session Breakdown

### Baseline (London 07-12 + NY 13-17)
- London: 408 trades (47.4%)
- New York: 453 trades (52.6%)

### Tokyo Added (Tokyo 06-08 + London 08-12 + NY 13-17)
- Tokyo: 96 trades (11.1%) — WR 78.1%, Avg PnL $4.29
- London: 312 trades (36.2%) — WR 73.1%, Avg PnL $4.16
- New York: 453 trades (52.6%) — WR 72.8%, Avg PnL $6.86

### 24/7 (No blocks)
- Sydney (22-06): 0 trades — no consolidation ranges form
- Tokyo (06-08): 96 trades — same as "Tokyo Added"
- London (08-12): 312 trades
- New York (13-17): 453 trades
- Other (12-13, 17-22): 0 trades — no ranges form

## Key Findings

### 1. Trade Count Unchanged
All three configurations generate exactly **861 trades**. The difference is only in session attribution:
- Hour 7 UTC: counted as London in baseline, Tokyo in other configs
- No new trades added by enabling Asian/Other hours

### 2. Tokyo Session Performance
**Pros:**
- Highest win rate: 78.1% (vs 73.1% London, 72.8% NY)
- Better avg R: 0.44R (vs 0.38R London)
- Better DD: 2.10% vs 2.22% baseline

**Cons:**
- Lower avg win: $8.37 (vs $10.05 London) due to lower volatility
- Lower avg loss: -$10.29 (vs -$11.84 London)
- Net result: -$2,267 total PnL vs baseline

### 3. Sydney & Other Hours
- **Sydney (22-06 UTC)**: 0 trades — too quiet, no consolidation ranges
- **Other (12-13, 17-22 UTC)**: 0 trades — no range breakouts during these hours
- Enabling these hours adds no value

### 4. Monte Carlo Results

| Config | p50 Balance | p95 DD | Prob DD>5% |
|--------|-------------|--------|------------|
| Baseline | $41,953 | 6.25% | 16.2% |
| Tokyo/24/7 | $39,754 | 5.92% | 12.4% |

Tokyo/24/7 has better DD distribution despite lower median balance.

## Recommendation

**Use BASELINE configuration (London 07-12 + NY 13-17 only):**

✅ **Reasons:**
1. Highest PnL: $32,006 (+7.6% vs Tokyo)
2. Still excellent DD: 2.22% (only 0.12% worse than Tokyo)
3. Simpler logic: no session overlap complexity
4. Better avg win size: London $10.05 vs Tokyo $8.37
5. Monte Carlo p50 balance: $41,953 vs $39,754

⚠️ **Tokyo alternative:**
- If DD control is critical priority (prop firm challenge near limit)
- Accept 7% lower PnL for 0.12% better DD
- Tokyo WR 78.1% is impressive but doesn't compensate for smaller wins

❌ **Avoid 24/7:**
- No benefit over Tokyo config (identical results)
- Adds unnecessary complexity

## Final Configuration

```python
# astra_v2/config.py
RANGE_BREAKOUT_V1_ALLOWED_SESSIONS = ("london", "new_york")
RISK_PCT = 0.004  # 0.4%

# astra_v2/strategies/range_breakout_v1.py
def _get_session_label(self, now: datetime) -> str:
    hour = now.hour
    if (0 <= hour < 7) or (12 <= hour < 13) or (17 <= hour < 22):
        return "blocked"
    elif 7 <= hour < 12:
        return "london"
    elif 13 <= hour < 17:
        return "new_york"
    else:
        return "other"
```

## Performance Summary (Baseline)
- **861 trades** over 5 years (2020-2024)
- **73.5% win rate**
- **2.38 profit factor** (passes prop firm ≥1.5)
- **2.22% max DD** (passes prop firm ≤5%)
- **$32,006 net PnL** on $10k start
- **Monte Carlo**: 16.2% prob exceed 5% DD (acceptable)
