//+------------------------------------------------------------------+
//| AstraSessionBreakout_v5.mq5                                      |
//| LONG : Asian(3-6) / London(8-11) / NY(15-18)  TP=12R            |
//| SHORT: Failed Breakout — london_fb(6-9) TP=3R                   |
//|                        — ny_fb(12-15)   TP=10R                   |
//| Exact match: session_long_nolookahead_v1.py                      |
//|              + validate_combined_fb_short.py                      |
//| H4 EMA : UTC-aligned (bucket = utc/14400*14400), NOT broker H4   |
//+------------------------------------------------------------------+
#property copyright "Astra Analyzer"
#property version   "5.01"

//--- Inputs
input double RiskUSD         = 100.0;   // Risk per trade, USD
input int    MagicNumber     = 20241121;
input int    Slippage        = 20;
input bool   EnableTrailing  = true;
input bool   TestMode        = true;    // true = log only, no real trades
input int    ServerGMTOffset = 3;       // server time = UTC + N
input string SupabaseURL     = "";
input string SupabaseKey     = "";

//--- Strategy constants (match Python backtest exactly)
#define ATR_PERIOD     14
#define H4_EMA_PER     20
#define SLOPE_N        5
#define MIN_H4_BARS    22              // Python: skip until 22 closed H4 bars
#define K_EMA          (2.0 / (H4_EMA_PER + 1.0))
#define H4_HIST        (SLOPE_N + 1)  // 6: [0]=last closed, [5]=5 bars ago

//--- LONG
#define TP_RR_LONG     12.0
#define ATR_BUF_LONG   0.5

//--- FB SHORT
#define TP_RR_LDN_FB   3.0
#define TP_RR_NY_FB    10.0
#define ATR_BUF_FB     0.3
#define N_CONFIRM_FB   4

//--- Candle sync file (for Python bridge)
string CandlesFile      = "astra_candles.json";
int    g_lastSyncMinute = -1;

//--- ATR indicator handle (M15 ATR only — H4 EMA computed manually)
int g_atrHandle = INVALID_HANDLE;

//--- UTC-aligned H4 EMA state
// Replaces iMA(PERIOD_H4) which uses broker-aligned H4 bars (UTC+3 shifted)
// g_h4EMAHist[0] = EMA of last closed UTC H4; [SLOPE_N] = 5 bars ago
double   g_h4EMAHist[H4_HIST];
int      g_h4Closed  = 0;    // count of closed UTC H4 bars processed
datetime g_h4CurBkt  = 0;    // UTC start of current forming H4 bucket
double   g_h4CurClose= 0;    // last M15 close of the forming bucket

//--- Bar tracking
datetime g_lastM15Time = 0;

//-------------------------------------------------------------------
// GlobalVariable name helpers
//-------------------------------------------------------------------
#define GV_LASTDAY     "AV5_LastDay"

// FB SHORT state — london_fb (range 6-9)
#define GV_LDN_SH     "AV5_LDN_SH"
#define GV_LDN_BA     "AV5_LDN_BA"
#define GV_LDN_OK     "AV5_LDN_OK"
#define GV_LDN_PEAK   "AV5_LDN_PEAK"
#define GV_LDN_DONE   "AV5_LDN_DONE"

// FB SHORT state — ny_fb (range 12-15)
#define GV_NY_SH      "AV5_NY_SH"
#define GV_NY_BA      "AV5_NY_BA"
#define GV_NY_OK      "AV5_NY_OK"
#define GV_NY_PEAK    "AV5_NY_PEAK"
#define GV_NY_DONE    "AV5_NY_DONE"

// TestMode simulated positions
string SimOpenGV (string s){ return "AV5_"+s+"_Open";  }
string SimSLGV   (string s){ return "AV5_"+s+"_SL";    }
string SimTPGV   (string s){ return "AV5_"+s+"_TP";    }
string SimISLGV  (string s){ return "AV5_"+s+"_ISL";   }
string SimEntryGV(string s){ return "AV5_"+s+"_Entry"; }
string SimTRRGV  (string s){ return "AV5_"+s+"_TRR";   }

// Real-mode initial SL for SHORT trailing (survives EA restart; recovered on MT5 restart)
string InitSLGV(ulong ticket){ return "AV5_ISL_"+IntegerToString(ticket); }

//-------------------------------------------------------------------
// Utilities
//-------------------------------------------------------------------
datetime BarToUTC(datetime t){ return t - (datetime)(ServerGMTOffset * 3600); }

// UTC H4 bucket start: rounds UTC timestamp down to nearest 4-hour boundary
// Matches Python: h4p = int(ts_ns // int(14400*1e9)) * int(14400*1e9)
datetime H4BucketUTC(datetime utc_t){ return (utc_t / 14400) * 14400; }

double GVGet(string name, double def=0.0)
{ return GlobalVariableCheck(name) ? GlobalVariableGet(name) : def; }

bool GVTrue(string name){ return GVGet(name) > 0.5; }

bool ShouldSyncCandles()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    if(dt.min % 15 == 0 && dt.sec >= 10 && dt.sec <= 15 && dt.min != g_lastSyncMinute)
    { g_lastSyncMinute = dt.min; return true; }
    return false;
}

//-------------------------------------------------------------------
// UTC-aligned H4 EMA — initialization from 2000 closed M15 bars
// Matches Python: df.resample('4h') → EMA(20) on UTC-aligned H4 closes
//
// Algorithm:
//   1. Load 2000 closed M15 bars (skip forming bar[0])
//   2. Process oldest→newest, detect H4 bucket transitions
//   3. On each transition: previous bucket's last M15 close = H4 close
//      Apply EMA: new_ema = h4_close * K + prev_ema * (1-K)
//   4. Keep last H4_HIST=6 EMA values in g_h4EMAHist[]
//-------------------------------------------------------------------
bool InitH4EMA()
{
    ArrayInitialize(g_h4EMAHist, 0.0);
    g_h4Closed = 0; g_h4CurBkt = 0; g_h4CurClose = 0;

    MqlRates rates[];
    ArraySetAsSeries(rates, false);   // rates[0]=oldest, rates[N-1]=most recent closed
    int got = CopyRates(_Symbol, PERIOD_M15, 1, 2000, rates);  // skip bar[0] (forming)
    if(got < 100){ Print("InitH4EMA: not enough M15 data (", got, ")"); return false; }

    datetime prevBkt  = 0;
    double   bktClose = 0;

    for(int i = 0; i < got; i++)
    {
        datetime bkt = H4BucketUTC(BarToUTC(rates[i].time));

        if(prevBkt == 0)
        { prevBkt = bkt; bktClose = rates[i].close; continue; }

        if(bkt != prevBkt)
        {
            // Previous H4 bucket just closed; bktClose = its last M15 close
            double new_ema = (g_h4Closed == 0)
                             ? bktClose
                             : bktClose * K_EMA + g_h4EMAHist[0] * (1.0 - K_EMA);
            for(int j = H4_HIST-1; j > 0; j--) g_h4EMAHist[j] = g_h4EMAHist[j-1];
            g_h4EMAHist[0] = new_ema;
            g_h4Closed++;
            prevBkt  = bkt;
            bktClose = rates[i].close;
        }
        else bktClose = rates[i].close;
    }

    // After the loop, prevBkt = most recent UTC H4 bucket (still forming)
    g_h4CurBkt   = prevBkt;
    g_h4CurClose = bktClose;

    Print("InitH4EMA: ", g_h4Closed, " UTC-aligned H4 bars loaded");
    return g_h4Closed >= MIN_H4_BARS;
}

//-------------------------------------------------------------------
// Update H4 EMA state on each new M15 bar (called with bar[1] data)
// When a new UTC H4 bucket starts, the previous bucket's last close
// is treated as the H4 close → EMA is updated.
//-------------------------------------------------------------------
void UpdateH4State(datetime bar1UTC, double m15Close)
{
    datetime bkt = H4BucketUTC(bar1UTC);

    if(bkt != g_h4CurBkt)
    {
        // New H4 bucket started → previous bucket just closed
        if(g_h4CurBkt != 0 && g_h4CurClose > 0)
        {
            double new_ema = (g_h4Closed == 0)
                             ? g_h4CurClose
                             : g_h4CurClose * K_EMA + g_h4EMAHist[0] * (1.0 - K_EMA);
            for(int j = H4_HIST-1; j > 0; j--) g_h4EMAHist[j] = g_h4EMAHist[j-1];
            g_h4EMAHist[0] = new_ema;
            g_h4Closed++;
        }
        g_h4CurBkt   = bkt;
        g_h4CurClose = m15Close;
    }
    else
    {
        // Same H4 bucket — update forming close (last M15 close of the bucket)
        g_h4CurClose = m15Close;
    }
}

//-------------------------------------------------------------------
// Recover SHORT initial SL after MT5 restart
// GlobalVariables survive EA restart but are lost when MT5 closes.
// Recovery uses: initial_sl = entry + (entry - tp) / tpRR
// (derivable because tp = entry - tpRR * (initial_sl - entry))
//-------------------------------------------------------------------
void RecoverShortSL()
{
    if(TestMode) return;
    for(int i = 0; i < PositionsTotal(); i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
        if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != POSITION_TYPE_SELL) continue;
        if(GlobalVariableCheck(InitSLGV(ticket))) continue;  // already set

        string comment = PositionGetString(POSITION_COMMENT);
        double tpRR    = (comment == "Astra_short_ldn") ? TP_RR_LDN_FB : TP_RR_NY_FB;

        double entry = PositionGetDouble(POSITION_PRICE_OPEN);
        double tp    = PositionGetDouble(POSITION_TP);
        if(entry <= 0 || tp <= 0 || tp >= entry)
        { Print("RecoverShortSL: bad data for ticket ", ticket); continue; }

        // Derive: rsk = (entry - tp) / tpRR; initial_sl = entry + rsk
        double rsk    = (entry - tp) / tpRR;
        double initSL = entry + rsk;
        GlobalVariableSet(InitSLGV(ticket), initSL);
        Print("Recovered SHORT InitSL ticket=", ticket, " initSL=", initSL);
    }
}

//-------------------------------------------------------------------
// Init / Deinit
//-------------------------------------------------------------------
int OnInit()
{
    Print("===== AstraSessionBreakout v5.01 =====");
    Print("TestMode=", TestMode, "  Risk=$", RiskUSD, "  GMT+", ServerGMTOffset);
    Print("LONG: Asian(3-6)/London(8-11)/NY(15-18)  TP=12R  ATRbuf=0.5  Slope=5");
    Print("SHORT: london_fb(6-9) TP=3R | ny_fb(12-15) TP=10R  ATRbuf=0.3  nc=4");
    Print("H4 EMA: UTC-aligned (no broker shift)");
    Print("==========================================");

    g_atrHandle = iATR(_Symbol, PERIOD_M15, ATR_PERIOD);
    if(g_atrHandle == INVALID_HANDLE)
    { Print("ERROR: ATR handle creation failed"); return INIT_FAILED; }

    if(!InitH4EMA())
    { Print("ERROR: H4 EMA initialization failed (not enough history)"); return INIT_FAILED; }

    RecoverShortSL();

    EventSetTimer(1);
    SyncCandlesToFile();
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    EventKillTimer();
    IndicatorRelease(g_atrHandle);
}

//-------------------------------------------------------------------
// Tick / Timer
//-------------------------------------------------------------------
void OnTick()
{
    datetime m15Time = iTime(_Symbol, PERIOD_M15, 0);
    if(g_lastM15Time == 0) g_lastM15Time = m15Time;
    else if(m15Time != g_lastM15Time)
    { g_lastM15Time = m15Time; OnNewM15Bar(); }

    if(ShouldSyncCandles()) SyncCandlesToFile();
}

void OnTimer()
{ if(ShouldSyncCandles()) SyncCandlesToFile(); }

//-------------------------------------------------------------------
// Main — fires on each new M15 bar, processes bar[1] (just closed)
// Order mirrors Python backtest loop exactly:
//   1. Update H4 EMA state  2. Daily reset  3. H4 filter
//   4. Manage trades (trail → close)
//   5. Build LONG ranges  6. FB SHORT logic  7. LONG entries
//-------------------------------------------------------------------
void OnNewM15Bar()
{
    // --- Last closed M15 bar data
    double atrBuf[];
    ArraySetAsSeries(atrBuf, true);
    if(CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) < 1) return;
    double atr = atrBuf[0];
    if(atr <= 0) return;

    double m15Close = iClose(_Symbol, PERIOD_M15, 1);
    double m15High  = iHigh (_Symbol, PERIOD_M15, 1);
    double m15Low   = iLow  (_Symbol, PERIOD_M15, 1);
    if(m15Close <= 0) return;

    datetime bar1UTC = BarToUTC(iTime(_Symbol, PERIOD_M15, 1));
    MqlDateTime barDT;
    TimeToStruct(bar1UTC, barDT);
    int hour = barDT.hour;

    // --- Update UTC-aligned H4 EMA state (must be first)
    UpdateH4State(bar1UTC, m15Close);

    // --- Daily reset at UTC midnight (uses bar[1] UTC date)
    int todayInt = barDT.year * 10000 + barDT.mon * 100 + barDT.day;
    if((int)GVGet(GV_LASTDAY, 0) != todayInt)
    {
        GlobalVariableSet(GV_LASTDAY,  todayInt);
        GlobalVariableSet(GV_LDN_SH,  0); GlobalVariableSet(GV_LDN_BA,   0);
        GlobalVariableSet(GV_LDN_OK,  0); GlobalVariableSet(GV_LDN_PEAK, 0);
        GlobalVariableSet(GV_LDN_DONE,0);
        GlobalVariableSet(GV_NY_SH,   0); GlobalVariableSet(GV_NY_BA,    0);
        GlobalVariableSet(GV_NY_OK,   0); GlobalVariableSet(GV_NY_PEAK,  0);
        GlobalVariableSet(GV_NY_DONE, 0);
        Print("Daily reset: ", barDT.year, "-", barDT.mon, "-", barDT.day);
    }

    // --- H4 EMA filter (LONG only)
    // Matches Python exactly:
    //   ema_base  = h4_ema20[ptr_closed]          (last closed UTC H4 EMA)
    //   h4_ema    = forming_close * K + ema_base * (1-K)
    //   ema_ok    = forming_close > ema_base
    //   slope_ok  = h4_ema > h4_ema20[ptr_closed - SLOPE_N]
    bool h4FilterOK = false;
    if(g_h4Closed >= MIN_H4_BARS && g_h4EMAHist[0] > 0 && g_h4EMAHist[SLOPE_N] > 0)
    {
        double ema_base  = g_h4EMAHist[0];       // EMA of last closed UTC H4 bar
        double ema_slope = g_h4EMAHist[SLOPE_N]; // EMA from SLOPE_N closed H4 bars ago
        double h4_ema    = m15Close * K_EMA + ema_base * (1.0 - K_EMA);
        bool ema_ok   = (m15Close > ema_base);
        bool slope_ok = (h4_ema   > ema_slope);
        h4FilterOK = ema_ok && slope_ok;
    }

    // --- Manage open positions: trail first, then SL/TP (matches Python order)
    if(TestMode)
    {
        UpdateSimTrailing(m15Low, m15High);
        CheckSimSLTP(m15Low, m15High);
    }
    else
    {
        if(EnableTrailing)
        {
            UpdateLongTrailing(m15Low);
            UpdateShortTrailing(m15High);
        }
    }

    // --- LONG session ranges (scan today's closed bars)
    double sHigh[3], sLow[3];
    CalcLongRanges(sHigh, sLow, barDT);

    // --- FB SHORT: bar-by-bar state machine + entries
    UpdateFBState(m15Close, m15High, m15Low, atr, hour);

    // --- LONG entries (only when H4 filter passes)
    if(h4FilterOK)
        CheckLongEntries(m15Close, atr, hour, sHigh, sLow);
}

//-------------------------------------------------------------------
// LONG session ranges — scan today's M15 bars bar-by-bar equivalent
// Sessions: Asian 3-6, London 8-11, NY 15-18 (UTC hours)
// Uses bar[1]'s UTC date to avoid midnight boundary bug.
//-------------------------------------------------------------------
void CalcLongRanges(double &sHigh[], double &sLow[], const MqlDateTime &bar1DT)
{
    ArrayResize(sHigh, 3); ArrayResize(sLow, 3);
    for(int i = 0; i < 3; i++){ sHigh[i] = 0; sLow[i] = DBL_MAX; }

    int rangeS[3] = {3,  8,  15};
    int rangeE[3] = {6,  11, 18};

    for(int i = 1; i < 300; i++)
    {
        datetime bt = iTime(_Symbol, PERIOD_M15, i);
        if(bt == 0) break;
        MqlDateTime bDT;
        TimeToStruct(BarToUTC(bt), bDT);
        if(bDT.year != bar1DT.year || bDT.mon != bar1DT.mon || bDT.day != bar1DT.day) break;

        int    h  = bDT.hour;
        double hi = iHigh(_Symbol, PERIOD_M15, i);
        double lo = iLow (_Symbol, PERIOD_M15, i);

        for(int s = 0; s < 3; s++)
            if(h >= rangeS[s] && h < rangeE[s])
            {
                sHigh[s] = MathMax(sHigh[s], hi);
                sLow[s]  = MathMin(sLow[s],  lo);
            }
    }
}

//-------------------------------------------------------------------
// LONG entries — exact match to Python session_long_nolookahead_v1.py
//-------------------------------------------------------------------
void CheckLongEntries(double close, double atr, int hour,
                      double &sHigh[], double &sLow[])
{
    string names[3]  = {"asian",  "london", "ny"};
    int    entryS[3] = {6,        11,       18};

    for(int s = 0; s < 3; s++)
    {
        if(hour < entryS[s]) continue;
        if(sHigh[s] <= 0 || sLow[s] >= DBL_MAX) continue;
        if(HasPosition(names[s])) continue;

        if(close > sHigh[s])
        {
            double sl  = sLow[s] - ATR_BUF_LONG * atr;
            double rsk = close - sl;
            if(rsk <= 0) continue;
            double tp = close + TP_RR_LONG * rsk;
            Print("LONG ", names[s], " @ ", close, " SL=", sl, " TP=", tp);
            OpenTrade("LONG", close, sl, tp, names[s], TP_RR_LONG);
        }
    }
}

//-------------------------------------------------------------------
// FB SHORT state machine — bar-by-bar, exact match to Python backtest
//
// Python logic (per FB session):
//   range window:  sh = max(sh, high)                        [build range]
//   after window:  if !ok:  close > sh → bars_above++, peak=max(peak,high)
//                           bars_above >= nc → ok=True
//                  if ok:   peak = max(peak, high)
//                           close < sh → SHORT entry
//-------------------------------------------------------------------
void UpdateFBState(double close, double high, double low, double atr, int hour)
{
    // --- london_fb: range 6-9, TP=3R ---
    {
        int rs = 6, re = 9;
        if(hour >= rs && hour < re)
        {
            GlobalVariableSet(GV_LDN_SH, MathMax(GVGet(GV_LDN_SH), high));
        }
        else if(hour >= re
                && GVGet(GV_LDN_SH) > 0
                && !GVTrue(GV_LDN_DONE)
                && !HasPosition("short_ldn"))
        {
            double sh = GVGet(GV_LDN_SH);
            if(!GVTrue(GV_LDN_OK))
            {
                if(close > sh)
                {
                    GlobalVariableSet(GV_LDN_BA,   GVGet(GV_LDN_BA) + 1);
                    GlobalVariableSet(GV_LDN_PEAK, MathMax(GVGet(GV_LDN_PEAK), high));
                    if((int)GVGet(GV_LDN_BA) >= N_CONFIRM_FB)
                        GlobalVariableSet(GV_LDN_OK, 1);
                }
            }
            else
            {
                GlobalVariableSet(GV_LDN_PEAK, MathMax(GVGet(GV_LDN_PEAK), high));
                if(close < sh)
                {
                    double peak = GVGet(GV_LDN_PEAK);
                    double sl_s = peak + ATR_BUF_FB * atr;
                    double rsk  = sl_s - close;
                    if(rsk > 0)
                    {
                        double tp_s = close - TP_RR_LDN_FB * rsk;
                        Print("SHORT london_fb @ ", close,
                              " SL=", sl_s, " TP=", tp_s, " peak=", peak);
                        OpenTrade("SHORT", close, sl_s, tp_s, "short_ldn", TP_RR_LDN_FB);
                        GlobalVariableSet(GV_LDN_DONE, 1);
                    }
                }
            }
        }
    }

    // --- ny_fb: range 12-15, TP=10R ---
    {
        int rs = 12, re = 15;
        if(hour >= rs && hour < re)
        {
            GlobalVariableSet(GV_NY_SH, MathMax(GVGet(GV_NY_SH), high));
        }
        else if(hour >= re
                && GVGet(GV_NY_SH) > 0
                && !GVTrue(GV_NY_DONE)
                && !HasPosition("short_ny"))
        {
            double sh = GVGet(GV_NY_SH);
            if(!GVTrue(GV_NY_OK))
            {
                if(close > sh)
                {
                    GlobalVariableSet(GV_NY_BA,   GVGet(GV_NY_BA) + 1);
                    GlobalVariableSet(GV_NY_PEAK, MathMax(GVGet(GV_NY_PEAK), high));
                    if((int)GVGet(GV_NY_BA) >= N_CONFIRM_FB)
                        GlobalVariableSet(GV_NY_OK, 1);
                }
            }
            else
            {
                GlobalVariableSet(GV_NY_PEAK, MathMax(GVGet(GV_NY_PEAK), high));
                if(close < sh)
                {
                    double peak = GVGet(GV_NY_PEAK);
                    double sl_s = peak + ATR_BUF_FB * atr;
                    double rsk  = sl_s - close;
                    if(rsk > 0)
                    {
                        double tp_s = close - TP_RR_NY_FB * rsk;
                        Print("SHORT ny_fb @ ", close,
                              " SL=", sl_s, " TP=", tp_s, " peak=", peak);
                        OpenTrade("SHORT", close, sl_s, tp_s, "short_ny", TP_RR_NY_FB);
                        GlobalVariableSet(GV_NY_DONE, 1);
                    }
                }
            }
        }
    }
}

//-------------------------------------------------------------------
// Trailing helpers — exact match to Python _trail / _trail_short
//-------------------------------------------------------------------

// LONG: step trailing LOW-trigger (11R→10R, ..., 2R→1R)
double TrailLong(double m15Low, double entry, double curSL, double risk)
{
    double newSL = curSL;
    double rr    = (m15Low - entry) / risk;
    if     (rr >= 11.0) newSL = MathMax(newSL, entry + 10.0 * risk);
    else if(rr >= 10.0) newSL = MathMax(newSL, entry +  9.0 * risk);
    else if(rr >=  9.0) newSL = MathMax(newSL, entry +  8.0 * risk);
    else if(rr >=  8.0) newSL = MathMax(newSL, entry +  7.0 * risk);
    else if(rr >=  7.0) newSL = MathMax(newSL, entry +  6.0 * risk);
    else if(rr >=  6.0) newSL = MathMax(newSL, entry +  5.0 * risk);
    else if(rr >=  5.0) newSL = MathMax(newSL, entry +  4.0 * risk);
    else if(rr >=  4.0) newSL = MathMax(newSL, entry +  3.0 * risk);
    else if(rr >=  3.0) newSL = MathMax(newSL, entry +  2.0 * risk);
    else if(rr >=  2.0) newSL = MathMax(newSL, entry +  1.0 * risk);
    return newSL;
}

// SHORT: step trailing HIGH-trigger (down via MathMin)
// tpRR determines steps: TP=3R → [(2,1)]; TP=10R → [(9,8)...(2,1)]
// Matches Python: [(j, j-1) for j in range(int(tp)-1, 1, -1)]
double TrailShort(double m15High, double entry, double curSL, double risk, double tpRR)
{
    double newSL   = curSL;
    double rr      = (entry - m15High) / risk;
    int    maxStep = (int)tpRR - 1;   // 2 for TP=3R, 9 for TP=10R
    for(int trigger = maxStep; trigger >= 2; trigger--)
    {
        if(rr >= trigger)
        {
            newSL = MathMin(newSL, entry - (double)(trigger - 1) * risk);
            break;
        }
    }
    return newSL;
}

//-------------------------------------------------------------------
// Trailing stops — real positions
//-------------------------------------------------------------------
void UpdateLongTrailing(double m15Low)
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
        if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != POSITION_TYPE_BUY) continue;

        double entry = PositionGetDouble(POSITION_PRICE_OPEN);
        double curSL = PositionGetDouble(POSITION_SL);
        double tp    = PositionGetDouble(POSITION_TP);
        if(tp <= entry) continue;
        double risk = (tp - entry) / TP_RR_LONG;
        if(risk <= 0) continue;

        double newSL = TrailLong(m15Low, entry, curSL, risk);
        if(newSL > curSL + _Point * 10)
        {
            MqlTradeRequest req = {}; MqlTradeResult res = {};
            req.action = TRADE_ACTION_SLTP; req.position = ticket;
            req.symbol = _Symbol;
            req.sl = NormalizeDouble(newSL, _Digits); req.tp = tp;
            if(OrderSend(req, res))
                Print("Trail LONG ", ticket, " SL=", newSL);
        }
    }
}

void UpdateShortTrailing(double m15High)
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
        if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != POSITION_TYPE_SELL) continue;

        double entry   = PositionGetDouble(POSITION_PRICE_OPEN);
        double curSL   = PositionGetDouble(POSITION_SL);
        double tp      = PositionGetDouble(POSITION_TP);
        string comment = PositionGetString(POSITION_COMMENT);
        double tpRR    = (comment == "Astra_short_ldn") ? TP_RR_LDN_FB : TP_RR_NY_FB;

        double isl = GVGet(InitSLGV(ticket));
        if(isl <= entry) continue;
        double risk = isl - entry;

        double newSL = TrailShort(m15High, entry, curSL, risk, tpRR);
        if(newSL < curSL - _Point * 10)
        {
            MqlTradeRequest req = {}; MqlTradeResult res = {};
            req.action = TRADE_ACTION_SLTP; req.position = ticket;
            req.symbol = _Symbol;
            req.sl = NormalizeDouble(newSL, _Digits); req.tp = tp;
            if(OrderSend(req, res))
                Print("Trail SHORT ", ticket, " SL=", newSL);
        }
    }
}

//-------------------------------------------------------------------
// TestMode: simulated trailing
//-------------------------------------------------------------------
void UpdateSimTrailing(double m15Low, double m15High)
{
    string longSess[3] = {"asian", "london", "ny"};
    for(int i = 0; i < 3; i++)
    {
        string s = longSess[i];
        if(!GVTrue(SimOpenGV(s))) continue;
        double entry = GVGet(SimEntryGV(s));
        double curSL = GVGet(SimSLGV(s));
        double tp    = GVGet(SimTPGV(s));
        if(tp <= entry || entry <= 0) continue;
        double risk = (tp - entry) / TP_RR_LONG;
        if(risk <= 0) continue;
        double newSL = TrailLong(m15Low, entry, curSL, risk);
        if(newSL > curSL + _Point * 10)
        {
            GlobalVariableSet(SimSLGV(s), newSL);
            Print("[TEST] Trail LONG ", s, " SL=", newSL);
        }
    }

    string shortSess[2] = {"short_ldn", "short_ny"};
    double tpRR_S[2]    = {TP_RR_LDN_FB, TP_RR_NY_FB};
    for(int i = 0; i < 2; i++)
    {
        string s = shortSess[i];
        if(!GVTrue(SimOpenGV(s))) continue;
        double entry = GVGet(SimEntryGV(s));
        double curSL = GVGet(SimSLGV(s));
        double isl   = GVGet(SimISLGV(s));
        if(isl <= entry || entry <= 0) continue;
        double risk = isl - entry;
        double newSL = TrailShort(m15High, entry, curSL, risk, tpRR_S[i]);
        if(newSL < curSL - _Point * 10)
        {
            GlobalVariableSet(SimSLGV(s), newSL);
            Print("[TEST] Trail SHORT ", s, " SL=", newSL);
        }
    }
}

//-------------------------------------------------------------------
// TestMode: check simulated SL/TP against the just-closed M15 bar
//-------------------------------------------------------------------
void CheckSimSLTP(double m15Low, double m15High)
{
    string allSess[5] = {"asian", "london", "ny", "short_ldn", "short_ny"};
    bool   isShort[5] = {false,   false,    false, true,        true};

    for(int i = 0; i < 5; i++)
    {
        string s = allSess[i];
        if(!GVTrue(SimOpenGV(s))) continue;

        double sl  = GVGet(SimSLGV(s));
        double tp  = GVGet(SimTPGV(s));
        double trr = GVGet(SimTRRGV(s));
        if(sl <= 0 || tp <= 0) continue;

        bool slHit = isShort[i] ? (m15High >= sl) : (m15Low  <= sl);
        bool tpHit = isShort[i] ? (m15Low  <= tp) : (m15High >= tp);
        if(!slHit && !tpHit) continue;

        double exitPrice = slHit ? sl : tp;
        double pnl       = slHit ? -RiskUSD : RiskUSD * trr;
        Print("[TEST] ", s, " ", (slHit ? "SL" : "TP"), " @ ", exitPrice, " PnL=", pnl);

        GlobalVariableSet(SimOpenGV(s),  0);
        GlobalVariableSet(SimSLGV(s),    0);
        GlobalVariableSet(SimTPGV(s),    0);
        GlobalVariableSet(SimISLGV(s),   0);
        GlobalVariableSet(SimEntryGV(s), 0);
        GlobalVariableSet(SimTRRGV(s),   0);
        CloseSignalInSupabase(s, exitPrice, pnl);
    }
}

//-------------------------------------------------------------------
// Position check
//-------------------------------------------------------------------
bool HasPosition(string session)
{
    if(TestMode)
        return GVTrue(SimOpenGV(session));

    string comment = "Astra_" + session;
    for(int i = 0; i < PositionsTotal(); i++)
        if(PositionGetTicket(i) > 0 &&
           PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
           PositionGetString(POSITION_SYMBOL) == _Symbol &&
           PositionGetString(POSITION_COMMENT) == comment)
            return true;
    return false;
}

//-------------------------------------------------------------------
// Open trade + Supabase signal
//-------------------------------------------------------------------
bool OpenTrade(string direction, double entry, double sl, double tp,
               string session, double tpRR)
{
    WriteSignalToSupabase(direction, entry, sl, tp, session);

    if(TestMode)
    {
        Print("[TEST] Signal logged: ", direction, " ", session,
              " entry=", entry, " SL=", sl, " TP=", tp);
        GlobalVariableSet(SimOpenGV(session),  1);
        GlobalVariableSet(SimSLGV(session),    sl);
        GlobalVariableSet(SimTPGV(session),    tp);
        GlobalVariableSet(SimISLGV(session),   sl);
        GlobalVariableSet(SimEntryGV(session), entry);
        GlobalVariableSet(SimTRRGV(session),   tpRR);
        return true;
    }

    ENUM_ORDER_TYPE orderType = (direction == "LONG") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
    double riskPts = MathAbs(entry - sl);
    if(riskPts <= 0) return false;

    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickValue <= 0 || tickSize <= 0) return false;

    double lot     = RiskUSD / ((riskPts / tickSize) * tickValue);
    double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    lot = MathMax(minLot, MathMin(maxLot, MathRound(lot / lotStep) * lotStep));

    MqlTradeRequest req = {}; MqlTradeResult res = {};
    req.action    = TRADE_ACTION_DEAL;
    req.symbol    = _Symbol;
    req.volume    = lot;
    req.type      = orderType;
    req.price     = (orderType == ORDER_TYPE_BUY)
                    ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                    : SymbolInfoDouble(_Symbol, SYMBOL_BID);
    req.sl        = NormalizeDouble(sl, _Digits);
    req.tp        = NormalizeDouble(tp, _Digits);
    req.deviation = Slippage;
    req.magic     = MagicNumber;
    req.comment   = "Astra_" + session;

    if(OrderSend(req, res))
    {
        Print("TRADE OPENED: ", direction, " ", session,
              " lot=", lot, " ticket=", res.order);
        if(direction == "SHORT")
            GlobalVariableSet(InitSLGV(res.order), sl);
        return true;
    }
    Print("ERROR OrderSend: ", GetLastError(), " retcode=", res.retcode);
    return false;
}

//-------------------------------------------------------------------
// Supabase: write new signal
//-------------------------------------------------------------------
void WriteSignalToSupabase(string direction, double entry, double sl, double tp,
                           string session)
{
    if(StringLen(SupabaseURL) == 0 || StringLen(SupabaseKey) == 0)
    { Print("Supabase not configured"); return; }

    string url = SupabaseURL + "/rest/v1/mt5_signals";
    string headers = "apikey: "             + SupabaseKey + "\r\n" +
                     "Authorization: Bearer " + SupabaseKey + "\r\n" +
                     "Content-Type: application/json\r\n" +
                     "Prefer: return=minimal\r\n";
    string body = StringFormat(
        "{\"direction\":\"%s\",\"entry\":%.2f,\"sl\":%.2f,\"tp\":%.2f,"
        "\"session\":\"%s\",\"risk_usd\":%.2f,\"status\":\"active\","
        "\"signal_type\":\"session_breakout\"}",
        direction, entry, sl, tp, session, RiskUSD);

    char data[], result[];
    StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
    ArrayResize(data, ArraySize(data) - 1);
    string resHeaders;
    int rc = WebRequest("POST", url, headers, 5000, data, result, resHeaders);
    if(rc == -1) Print("WriteSignal error: ", GetLastError());
    else         Print("Signal → Supabase: ", direction, " ", session, " @ ", entry);
}

//-------------------------------------------------------------------
// Supabase: close signal
//-------------------------------------------------------------------
void CloseSignalInSupabase(string session, double exitPrice, double profit)
{
    if(StringLen(SupabaseURL) == 0) return;

    string url = SupabaseURL + "/rest/v1/mt5_signals?status=eq.active&session=eq." + session;
    string headers = "apikey: "             + SupabaseKey + "\r\n" +
                     "Authorization: Bearer " + SupabaseKey + "\r\n" +
                     "Content-Type: application/json\r\n";
    string body = StringFormat("{\"status\":\"closed\",\"exit_price\":%.2f,\"pnl\":%.2f}",
                               exitPrice, profit);

    char data[], result[];
    StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
    ArrayResize(data, ArraySize(data) - 1);
    string resHeaders;
    int rc = WebRequest("PATCH", url, headers, 5000, data, result, resHeaders);
    if(rc == -1) Print("CloseSignal error: ", GetLastError());
    else         Print("Signal closed → Supabase: ", session, " @ ", exitPrice, " PnL=", profit);
}

//-------------------------------------------------------------------
// Detect real position close → update Supabase
//-------------------------------------------------------------------
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    if(!HistoryDealSelect(trans.deal)) return;
    if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != MagicNumber) return;

    ENUM_DEAL_ENTRY de = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
    if(de != DEAL_ENTRY_OUT && de != DEAL_ENTRY_OUT_BY) return;

    string comment   = HistoryDealGetString(trans.deal, DEAL_COMMENT);
    double exitPrice = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
    double profit    = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
    ulong  posId     = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);

    string session = StringSubstr(comment, 6);  // "Astra_asian" → "asian"
    Print("Position closed: ", session, " @ ", exitPrice, " PnL=", profit);
    CloseSignalInSupabase(session, exitPrice, profit);

    if(session == "short_ldn" || session == "short_ny")
        GlobalVariableDel(InitSLGV(posId));
}

//-------------------------------------------------------------------
// Sync 2000 M15 candles → file for Python bridge
//-------------------------------------------------------------------
void SyncCandlesToFile()
{
    MqlRates rates[];
    ArraySetAsSeries(rates, true);
    int copied = CopyRates(_Symbol, PERIOD_M15, 1, 2000, rates);
    if(copied <= 0){ Print("SyncCandles: no data (", GetLastError(), ")"); return; }

    string json = "[\n";
    for(int i = 0; i < copied; i++)
    {
        if(i > 0) json += ",\n";
        json += "  {\"time\":\""   + TimeToString(rates[i].time, TIME_DATE|TIME_MINUTES) + "\","
              + "\"open\":"         + DoubleToString(rates[i].open,  _Digits) + ","
              + "\"high\":"         + DoubleToString(rates[i].high,  _Digits) + ","
              + "\"low\":"          + DoubleToString(rates[i].low,   _Digits) + ","
              + "\"close\":"        + DoubleToString(rates[i].close, _Digits) + ","
              + "\"volume\":"       + IntegerToString(rates[i].tick_volume)   + "}";
    }
    json += "\n]";

    int fh = FileOpen(CandlesFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
    if(fh == INVALID_HANDLE){ Print("SyncCandles: can't open file (", GetLastError(), ")"); return; }
    FileWriteString(fh, json);
    FileClose(fh);
    Print("Synced ", copied, " M15 candles → ", CandlesFile);
}
//+------------------------------------------------------------------+
