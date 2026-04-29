# Session Breakout v4.0 - Deployment Checklist
## Commit: 911bdc0 (2026-04-26)

## ✅ COMPLETED

### 1. Code Fixes
- [x] EA: HasPositionForDirection() → HasPositionForSession()
- [x] EA: ParseSignals() extracts 'session' field
- [x] EA: OpenTrade() accepts session parameter
- [x] EA: Comment format "Astra_" + session
- [x] Bridge: write_signals_to_file() includes 'session' field
- [x] Validation: Logic tested and passed
- [x] Git: Committed and pushed to deploy branch

### 2. System Status
- [x] Render: Loading 2000 M15 candles
- [x] Render: Resampling to ~131 H4 bars
- [x] Render: H4 EMA20 calculated correctly
- [x] Supabase: Max Rows = 2000
- [x] Supabase: mt5_candles has 2000+ bars
- [x] Supabase: short_state initialized

## ⏳ PENDING (VPS Actions)

### 3. VPS Deployment
- [ ] **Compile EA v4.0** in MT5 MetaEditor
  - File: `AstraSessionBreakout_v4.mq5`
  - Check compilation log for errors
  - Verify .ex5 file created

- [ ] **Restart Bridge**
  ```bash
  # Stop current bridge
  pkill -f mt5_bridge_fixed.py
  
  # Start new bridge with updated code
  cd /root/astra
  git pull origin deploy
  nohup python3 vps/mt5_bridge_fixed.py > bridge.log 2>&1 &
  ```

- [ ] **Attach EA to Chart**
  - Symbol: XAUUSD
  - Timeframe: Any (EA uses M15 internally)
  - Settings:
    - RiskUSD = 120
    - MagicNumber = 20241121
    - EnableTrailing = true
    - TestMode = **true** (for 24h testing)
  - Allow AutoTrading

### 4. Testing Phase (24 hours)
- [ ] **Monitor EA Logs**
  - Check "TEST MODE SIGNAL" messages
  - Verify session field is parsed
  - Confirm multiple signals don't conflict

- [ ] **Monitor Bridge Logs**
  - Check signals include 'session' field
  - Verify JSON file format correct

- [ ] **Monitor Telegram**
  - Check signal notifications
  - Verify "Active Positions X/4" format

### 5. Go Live
- [ ] **Switch to Live Trading**
  - Change TestMode = false in EA settings
  - Restart EA
  - Monitor first real trade

## 📋 VERIFICATION COMMANDS

### Check EA is running:
```bash
# In MT5 Terminal window
# Look for: "Astra Session Breakout EA v4.0 - Starting"
# Look for: "Multiple Positions: Enabled (max 4: Asian+London+NY+SHORT)"
```

### Check Bridge is running:
```bash
ps aux | grep mt5_bridge_fixed.py
tail -f /root/astra/bridge.log
```

### Check Candles Sync:
```bash
# In MT5 Terminal window
# Look for: "Synced 2000 M15 candles to file"
```

### Check Signal Processing:
```bash
# In MT5 Terminal window
# Look for: "Signal 1: LONG asian @ 2650.50 SL:2645.00 TP:2680.75"
# NOT: "Signal 1: LONG @ 2650.50 SL:2645.00 TP:2680.75" (missing session)
```

## 🚨 CRITICAL CHECKS

### Before Going Live:
1. ✅ EA shows "Multiple Positions: Enabled (max 4: Asian+London+NY+SHORT)"
2. ✅ EA logs show session names in signal processing
3. ✅ TestMode signals show "Session: asian/london/ny/short"
4. ✅ No errors in EA compilation
5. ✅ Bridge includes 'session' in JSON output

### Expected Behavior:
- **Asian LONG** can open at 10:00-24:00 UTC
- **London LONG** can open at 16:00-24:00 UTC
- **NY LONG** can open at 18:00-21:00 UTC
- **SHORT** can open at 00:00-21:00 UTC
- All 4 can be active simultaneously
- Each session blocks only itself (asian blocks asian, not london/ny)

## 📊 PERFORMANCE EXPECTATIONS

**Backtest Results (2020-2026):**
- Total PnL: $80,501
- Trades: 881 (LONG: 693, SHORT: 188)
- Win Rate: 47.9%
- Max DD: 6.99%
- Risk: $120 per trade

**Live Expectations:**
- ~1-2 trades per week minimum
- Multiple positions common (2-3 active)
- 4 simultaneous positions rare but possible
- Never had 4 simultaneous losses in backtest

## 🔗 RELATED FILES

- EA: `vps/AstraSessionBreakout_v4.mq5`
- Bridge: `vps/mt5_bridge_fixed.py`
- Trader: `session_breakout_trader.py`
- Memory: `.claude/projects/.../memory/project_session_breakout_v4.md`
- Validation: `scripts/validate_ea_logic.py`

## 📅 TIMELINE

- **2026-04-25**: v4.0 developed, 2000 bars fix applied
- **2026-04-26 00:47**: Multiple positions fix committed (911bdc0)
- **2026-04-28**: Market opens Monday - first live test
- **2026-04-29**: Review 24h TestMode results, go live

---

**Status: READY FOR VPS DEPLOYMENT**
**Next Action: Compile EA v4.0 on VPS**
