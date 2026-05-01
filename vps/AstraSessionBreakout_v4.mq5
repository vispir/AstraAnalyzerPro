//+------------------------------------------------------------------+
//| AstraSessionBreakout_v4.mq5                                      |
//| Session Breakout Strategy v4.10                                   |
//| Full strategy logic in EA — no Python signal delay               |
//| LONG: Asian/London/NY  |  SHORT: Type1/Type2 Reversal           |
//+------------------------------------------------------------------+
#property copyright "Astra Analyzer Pro"
#property version   "4.11"

//--- Inputs
input double RiskUSD          = 120.0;  // Risk per trade in USD
input int    MagicNumber      = 20241121;
input int    Slippage         = 20;
input bool   EnableTrailing   = true;
input bool   TestMode         = true;   // No real trades
input int    ServerGMTOffset  = 3;      // Server time = UTC + N (e.g. 3 for UTC+3)
input string SupabaseURL      = "";     // e.g. https://xxx.supabase.co
input string SupabaseKey      = "";     // anon key

//--- Strategy constants (match Python v4.0)
#define ATR_PERIOD      14
#define ATR_BUFFER_VAL  0.5
#define TP_RR_VAL       5.5
#define H4_EMA_PERIOD   20
#define TYPE1_LOOKBACK  5
#define TYPE2_LOOKBACK  3
#define TYPE2_ATR_MULT  2.0

//--- Files (for Python bridge candle sync)
string CandlesFile = "astra_candles.json";

//--- Indicator handles
int g_m15ATR = INVALID_HANDLE;

//--- Bar tracking
datetime g_lastM15Time = 0;

//--- Candle sync tracking
int g_lastSyncMinute = -1;

//--- GlobalVariable names — SHORT state machine (persist across EA restarts)
#define GV_T1_ACTIVE      "Astra_T1_Active"
#define GV_T1_H4HIGH      "Astra_T1_H4High"
#define GV_T2_ACTIVE      "Astra_T2_Active"
#define GV_T2_H4HIGH      "Astra_T2_H4High"
#define GV_LAST_H4        "Astra_LastH4Time"
#define GV_SHORT_WAS_OPEN "Astra_Short_WasOpen"  // M15-bar SHORT close detector

//--- GlobalVariable names — TEST_MODE simulated open tracking
#define GV_SIM_ASIAN  "Astra_Sim_Asian"
#define GV_SIM_LONDON "Astra_Sim_London"
#define GV_SIM_NY     "Astra_Sim_NY"
#define GV_SIM_SHORT  "Astra_Sim_Short"

//--- GlobalVariable names — TEST_MODE simulated SL/TP (EA-internal simulation, no Supabase poll)
#define GV_SIM_ASIAN_SL   "Astra_Sim_Asian_SL"
#define GV_SIM_ASIAN_TP   "Astra_Sim_Asian_TP"
#define GV_SIM_LONDON_SL  "Astra_Sim_London_SL"
#define GV_SIM_LONDON_TP  "Astra_Sim_London_TP"
#define GV_SIM_NY_SL      "Astra_Sim_NY_SL"
#define GV_SIM_NY_TP      "Astra_Sim_NY_TP"
#define GV_SIM_SHORT_SL   "Astra_Sim_Short_SL"
#define GV_SIM_SHORT_TP   "Astra_Sim_Short_TP"

//+------------------------------------------------------------------+
//| Helpers                                                           |
//+------------------------------------------------------------------+
datetime BarToUTC(datetime serverTime)
{
    return serverTime - (datetime)(ServerGMTOffset * 3600);
}

bool ShouldSyncCandles()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    if(dt.min % 15 == 0 && dt.sec >= 10 && dt.sec <= 15 && dt.min != g_lastSyncMinute)
    {
        g_lastSyncMinute = dt.min;
        return true;
    }
    return false;
}

string SimGV(string session)
{
    if(session == "asian")  return GV_SIM_ASIAN;
    if(session == "london") return GV_SIM_LONDON;
    if(session == "ny")     return GV_SIM_NY;
    return GV_SIM_SHORT;
}

string SimSLGV(string session)
{
    if(session == "asian")  return GV_SIM_ASIAN_SL;
    if(session == "london") return GV_SIM_LONDON_SL;
    if(session == "ny")     return GV_SIM_NY_SL;
    return GV_SIM_SHORT_SL;
}

string SimTPGV(string session)
{
    if(session == "asian")  return GV_SIM_ASIAN_TP;
    if(session == "london") return GV_SIM_LONDON_TP;
    if(session == "ny")     return GV_SIM_NY_TP;
    return GV_SIM_SHORT_TP;
}

//+------------------------------------------------------------------+
//| UTC H4 bar — built from M15 data, aligned to UTC 4h boundaries  |
//| bars[0] = forming, bars[1] = last completed, bars[n] = n ago    |
//+------------------------------------------------------------------+
struct UTC4HBar {
    datetime startUTC;
    double   open, high, low, close;
    bool     valid;
};

int BuildUTCH4Bars(UTC4HBar &bars[], int histCount)
{
    int      m15Count = (histCount + 2) * 16 + 10;
    MqlRates rates[];
    ArraySetAsSeries(rates, false); // oldest first
    int copied = CopyRates(_Symbol, PERIOD_M15, 0, m15Count, rates);
    if(copied <= 0) return 0;

    datetime nowUTC     = BarToUTC(TimeCurrent());
    datetime h4StartUTC = (nowUTC / 14400) * 14400; // floor to UTC 4h boundary

    ArrayResize(bars, histCount + 1);
    for(int b = 0; b <= histCount; b++)
    {
        bars[b].startUTC = h4StartUTC - (datetime)(b * 14400);
        bars[b].open  = 0;  bars[b].high  = 0;
        bars[b].low   = DBL_MAX; bars[b].close = 0; bars[b].valid = false;
    }

    for(int i = 0; i < copied; i++)
    {
        datetime barUTC     = BarToUTC(rates[i].time);
        datetime barH4Start = (barUTC / 14400) * 14400;
        int      b          = (int)((h4StartUTC - barH4Start) / 14400);
        if(b < 0 || b > histCount) continue;
        if(!bars[b].valid) { bars[b].open = rates[i].open; bars[b].valid = true; }
        bars[b].high  = MathMax(bars[b].high, rates[i].high);
        bars[b].low   = MathMin(bars[b].low,  rates[i].low);
        bars[b].close = rates[i].close; // oldest→newest: last assigned = most recent M15 close
    }
    for(int b = 0; b <= histCount; b++) if(!bars[b].valid) bars[b].low = 0;
    return histCount + 1;
}

double CalcUTCH4EMA(UTC4HBar &bars[], int histCount, int period)
{
    int start = histCount;
    while(start > 0 && !bars[start].valid) start--;
    if(!bars[start].valid) return 0;
    double k   = 2.0 / (period + 1.0);
    double ema = bars[start].close;
    for(int b = start - 1; b >= 0; b--)
    {
        if(!bars[b].valid) continue;
        ema = bars[b].close * k + ema * (1.0 - k);
    }
    return ema;
}

double CalcUTCH4ATR(UTC4HBar &bars[], int histCount, int period)
{
    if(histCount < period + 1) return 0;
    double trs[];
    int    trCount = 0;
    ArrayResize(trs, histCount + 1);
    for(int b = histCount - 1; b >= 0; b--)
    {
        if(!bars[b].valid || !bars[b + 1].valid) continue;
        double prevClose = bars[b + 1].close;
        double tr = MathMax(bars[b].high - bars[b].low,
                   MathMax(MathAbs(bars[b].high - prevClose),
                           MathAbs(bars[b].low  - prevClose)));
        trs[trCount++] = tr;
    }
    if(trCount < period) return 0;
    double atr = 0;
    for(int i = 0; i < period; i++) atr += trs[i];
    atr /= period;
    for(int i = period; i < trCount; i++) atr = (atr * (period - 1) + trs[i]) / period;
    return atr;
}

//+------------------------------------------------------------------+
//| Init / Deinit                                                     |
//+------------------------------------------------------------------+
int OnInit()
{
    Print("====================================================");
    Print("Astra Session Breakout EA v4.11 — UTC H4 alignment");
    Print("TestMode=", TestMode, "  Risk=$", RiskUSD, "  GMTOffset=", ServerGMTOffset);
    Print("====================================================");

    g_m15ATR = iATR(_Symbol, PERIOD_M15, ATR_PERIOD);
    if(g_m15ATR == INVALID_HANDLE)
    {
        Print("ERROR: M15 ATR handle creation failed");
        return INIT_FAILED;
    }

    EventSetTimer(1);
    SyncCandlesToFile();
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    EventKillTimer();
    IndicatorRelease(g_m15ATR);
}

//+------------------------------------------------------------------+
//| Tick / Timer                                                      |
//+------------------------------------------------------------------+
void OnTick()
{
    datetime m15Time = iTime(_Symbol, PERIOD_M15, 0);
    if(g_lastM15Time == 0)
        g_lastM15Time = m15Time;
    else if(m15Time != g_lastM15Time)
    {
        g_lastM15Time = m15Time;
        OnNewM15Bar();
    }

    if(EnableTrailing)
        UpdateTrailingStops();

    if(ShouldSyncCandles())
        SyncCandlesToFile();
}

void OnTimer()
{
    if(ShouldSyncCandles())
        SyncCandlesToFile();
}

//+------------------------------------------------------------------+
//| Main strategy — called on each new M15 bar                       |
//+------------------------------------------------------------------+
void OnNewM15Bar()
{
    // --- ATR (last closed M15 bar, index 1)
    double m15ATRbuf[];
    ArraySetAsSeries(m15ATRbuf, true);
    if(CopyBuffer(g_m15ATR, 0, 1, 1, m15ATRbuf) < 1) return;
    double m15ATR = m15ATRbuf[0];
    if(m15ATR <= 0) return;

    // --- Build UTC-aligned H4 bars from M15 data (matches Python backtest resample('4h'))
    UTC4HBar h4bars[];
    if(BuildUTCH4Bars(h4bars, 25) <= 0) return;
    double h4EMA20     = CalcUTCH4EMA(h4bars, 25, H4_EMA_PERIOD);
    double h4ATR       = CalcUTCH4ATR(h4bars, 25, ATR_PERIOD);
    double h4CloseCurr = h4bars[0].valid ? h4bars[0].close : 0;

    // --- Last closed M15 bar data
    datetime barServerTime = iTime(_Symbol, PERIOD_M15, 1);
    datetime barUTC        = BarToUTC(barServerTime);
    MqlDateTime barDT;
    TimeToStruct(barUTC, barDT);
    int utcHour = barDT.hour;

    double m15Close   = iClose(_Symbol, PERIOD_M15, 1);
    double m15High    = iHigh (_Symbol, PERIOD_M15, 1);
    double m15Low     = iLow  (_Symbol, PERIOD_M15, 1);
    double prevM15Low = iLow  (_Symbol, PERIOD_M15, 2);

    // --- Detect new UTC H4 bar → UpdateShortStateMachine
    datetime utcH4Start0 = h4bars[0].startUTC;
    datetime savedH4     = GlobalVariableCheck(GV_LAST_H4) ? (datetime)GlobalVariableGet(GV_LAST_H4) : 0;
    if(utcH4Start0 != savedH4)
    {
        Print("New UTC H4 bar: ", TimeToString(utcH4Start0));
        GlobalVariableSet(GV_LAST_H4, (double)utcH4Start0);
        UpdateShortStateMachine(h4bars, h4EMA20, h4ATR);
    }

    // --- In TEST_MODE, check simulated SL/TP against the bar that just closed
    if(TestMode)
        CheckSimulatedSLTP(m15Low, m15High);

    // --- Detect SHORT position close (broker may overwrite comment on SL/TP hit)
    // Check BEFORE CheckShortEntries so T1/T2 are cleared before new entry is evaluated
    DetectShortClose();

    // --- LONG entries
    double sessionRanges[][2]; // dynamic — required for passing to function by ref
    CalculateSessionRanges(sessionRanges);
    CheckLongEntries(m15Close, m15ATR, h4CloseCurr, h4EMA20, utcHour, sessionRanges);

    // --- SHORT entries (active 0-21 UTC)
    if(utcHour < 21)
        CheckShortEntries(m15Close, prevM15Low, m15ATR);
}

//+------------------------------------------------------------------+
//| Session ranges — scan today's M15 bars (UTC date)               |
//| ranges[0] = Asian, [1] = London, [2] = NY                       |
//| Each: [0] = high, [1] = low (low=DBL_MAX means no bars yet)     |
//+------------------------------------------------------------------+
void CalculateSessionRanges(double &ranges[][2])
{
    ArrayResize(ranges, 3);
    for(int s = 0; s < 3; s++) { ranges[s][0] = 0; ranges[s][1] = DBL_MAX; }

    datetime utcNow = BarToUTC(TimeCurrent());
    MqlDateTime todayDT;
    TimeToStruct(utcNow, todayDT);

    for(int i = 1; i < 250; i++)
    {
        datetime barServer = iTime(_Symbol, PERIOD_M15, i);
        if(barServer == 0) break;

        MqlDateTime barDT;
        TimeToStruct(BarToUTC(barServer), barDT);

        if(barDT.year != todayDT.year || barDT.mon != todayDT.mon || barDT.day != todayDT.day)
            break; // past today

        int    h  = barDT.hour;
        double hi = iHigh(_Symbol, PERIOD_M15, i);
        double lo = iLow (_Symbol, PERIOD_M15, i);

        if(h >= 7  && h < 10) { ranges[0][0] = MathMax(ranges[0][0], hi); ranges[0][1] = MathMin(ranges[0][1], lo); } // Asian
        if(h >= 13 && h < 16) { ranges[1][0] = MathMax(ranges[1][0], hi); ranges[1][1] = MathMin(ranges[1][1], lo); } // London
        if(h >= 13 && h < 17) { ranges[2][0] = MathMax(ranges[2][0], hi); ranges[2][1] = MathMin(ranges[2][1], lo); } // NY
    }
}

bool RangeValid(double &ranges[][2], int idx) { return ranges[idx][1] < DBL_MAX; }

//+------------------------------------------------------------------+
//| SHORT state machine — called on each new H4 bar                  |
//+------------------------------------------------------------------+
void UpdateShortStateMachine(UTC4HBar &h4bars[], double h4EMA20, double h4ATR)
{
    double h4CloseCurr = h4bars[0].valid ? h4bars[0].close : 0;
    double h4ClosePrev = h4bars[1].valid ? h4bars[1].close : 0;

    // EMA20 filter: SHORT only when H4 close BELOW EMA20
    if(h4EMA20 <= 0 || h4CloseCurr >= h4EMA20)
    {
        GlobalVariableSet(GV_T1_ACTIVE, 0); GlobalVariableSet(GV_T1_H4HIGH, 0);
        GlobalVariableSet(GV_T2_ACTIVE, 0); GlobalVariableSet(GV_T2_H4HIGH, 0);
        Print("SHORT state reset: H4 close ", h4CloseCurr, " >= EMA20 ", h4EMA20);
        return;
    }

    double h4HighCurr = h4bars[0].valid ? h4bars[0].high : 0;
    bool   bearish    = (h4CloseCurr < h4ClosePrev);

    bool t1Active = GlobalVariableCheck(GV_T1_ACTIVE) && GlobalVariableGet(GV_T1_ACTIVE) > 0.5;
    bool t2Active = GlobalVariableCheck(GV_T2_ACTIVE) && GlobalVariableGet(GV_T2_ACTIVE) > 0.5;

    // TYPE 1: new H4 high > max of prev 5 H4 highs + bearish close
    if(!t1Active)
    {
        double maxPrev = 0;
        for(int i = 1; i <= TYPE1_LOOKBACK; i++)
            if(i <= 25 && h4bars[i].valid) maxPrev = MathMax(maxPrev, h4bars[i].high);

        if(h4HighCurr > maxPrev && bearish)
        {
            GlobalVariableSet(GV_T1_ACTIVE, 1);
            GlobalVariableSet(GV_T1_H4HIGH, h4HighCurr);
            Print("SHORT Type1 ACTIVE: H4high=", h4HighCurr, " > maxPrev5=", maxPrev);
        }
    }

    // TYPE 2: price move >= 2*H4ATR from min of prev 3 H4 lows + bearish close
    if(!t2Active && h4ATR > 0)
    {
        double minLow = DBL_MAX;
        for(int i = 1; i <= TYPE2_LOOKBACK; i++)
            if(i <= 25 && h4bars[i].valid) minLow = MathMin(minLow, h4bars[i].low);

        double move = h4HighCurr - minLow;
        if(move >= TYPE2_ATR_MULT * h4ATR && bearish)
        {
            GlobalVariableSet(GV_T2_ACTIVE, 1);
            GlobalVariableSet(GV_T2_H4HIGH, h4HighCurr);
            Print("SHORT Type2 ACTIVE: move=", move, " >= 2*ATR=", 2*h4ATR);
        }
    }
}

//+------------------------------------------------------------------+
//| LONG entries — 3 sessions                                         |
//+------------------------------------------------------------------+
void CheckLongEntries(double m15Close, double m15ATR, double h4Close, double h4EMA20,
                      int utcHour, double &ranges[][2])
{
    // H4 EMA20 filter: price must be ABOVE EMA20 for LONG
    if(h4EMA20 <= 0 || h4Close <= h4EMA20) return;

    // session 0 = Asian  (range 7-10, entry 10+)
    // session 1 = London (range 13-16, entry 16+)
    // session 2 = NY     (range 13-17, entry 18-21)
    string names[3] = {"asian", "london", "ny"};
    int    entryFrom[3] = {10, 16, 18};
    int    entryTo[3]   = {24, 24, 21};

    for(int s = 0; s < 3; s++)
    {
        if(utcHour < entryFrom[s] || utcHour >= entryTo[s]) continue;
        if(!RangeValid(ranges, s)) continue;
        if(HasPosition(names[s])) continue;

        double sessionHigh = ranges[s][0];
        double sessionLow  = ranges[s][1];

        if(m15Close > sessionHigh)
        {
            double sl   = sessionLow - ATR_BUFFER_VAL * m15ATR;
            double risk = m15Close - sl;
            if(risk <= 0) continue;
            double tp = m15Close + risk * TP_RR_VAL;

            string sessUp = names[s];
            StringToUpper(sessUp);
            Print("LONG ", sessUp, " @ ", m15Close, " SL=", sl, " TP=", tp);
            OpenTrade("LONG", m15Close, sl, tp, names[s]);
        }
    }
}

//+------------------------------------------------------------------+
//| SHORT entries                                                      |
//+------------------------------------------------------------------+
void CheckShortEntries(double m15Close, double prevM15Low, double m15ATR)
{
    // EMA filter is applied only in UpdateShortStateMachine (on new H4 bar)
    // to match backtest behavior — no re-check here
    if(HasPosition("short")) return;
    if(m15Close >= prevM15Low) return; // entry: M15 close breaks prev M15 low

    bool   t1Active = GlobalVariableCheck(GV_T1_ACTIVE) && GlobalVariableGet(GV_T1_ACTIVE) > 0.5;
    bool   t2Active = GlobalVariableCheck(GV_T2_ACTIVE) && GlobalVariableGet(GV_T2_ACTIVE) > 0.5;
    double t1H4High = GlobalVariableCheck(GV_T1_H4HIGH) ? GlobalVariableGet(GV_T1_H4HIGH) : 0;
    double t2H4High = GlobalVariableCheck(GV_T2_H4HIGH) ? GlobalVariableGet(GV_T2_H4HIGH) : 0;

    // Type 1 priority
    if(t1Active && t1H4High > 0)
    {
        double sl   = t1H4High + ATR_BUFFER_VAL * m15ATR;
        double risk = sl - m15Close;
        if(risk > 0)
        {
            double tp = m15Close - risk * TP_RR_VAL;
            Print("SHORT Type1 @ ", m15Close, " SL=", sl, " TP=", tp);
            if(OpenTrade("SHORT", m15Close, sl, tp, "short"))
            {
                GlobalVariableSet(GV_T1_ACTIVE, 0);
                GlobalVariableSet(GV_T1_H4HIGH, 0);
            }
        }
        return;
    }

    if(t2Active && t2H4High > 0)
    {
        double sl   = t2H4High + ATR_BUFFER_VAL * m15ATR;
        double risk = sl - m15Close;
        if(risk > 0)
        {
            double tp = m15Close - risk * TP_RR_VAL;
            Print("SHORT Type2 @ ", m15Close, " SL=", sl, " TP=", tp);
            if(OpenTrade("SHORT", m15Close, sl, tp, "short"))
            {
                GlobalVariableSet(GV_T2_ACTIVE, 0);
                GlobalVariableSet(GV_T2_H4HIGH, 0);
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Detect SHORT close between M15 bars (broker overwrites comment   |
//| on SL/TP hit, so OnTradeTransaction can't rely on it)            |
//+------------------------------------------------------------------+
void DetectShortClose()
{
    bool wasOpen = GlobalVariableCheck(GV_SHORT_WAS_OPEN) && GlobalVariableGet(GV_SHORT_WAS_OPEN) > 0.5;
    bool isOpen  = HasPosition("short");

    if(wasOpen && !isOpen)
    {
        GlobalVariableSet(GV_T1_ACTIVE, 0); GlobalVariableSet(GV_T1_H4HIGH, 0);
        GlobalVariableSet(GV_T2_ACTIVE, 0); GlobalVariableSet(GV_T2_H4HIGH, 0);
        Print("SHORT closed — T1/T2 state reset (M15 detector)");
    }

    GlobalVariableSet(GV_SHORT_WAS_OPEN, isOpen ? 1.0 : 0.0);
}

//+------------------------------------------------------------------+
//| Position check                                                    |
//+------------------------------------------------------------------+
bool HasPosition(string session)
{
    if(TestMode)
    {
        string gv = SimGV(session);
        return GlobalVariableCheck(gv) && GlobalVariableGet(gv) > 0.5;
    }

    string comment = "Astra_" + session;
    for(int i = 0; i < PositionsTotal(); i++)
    {
        if(PositionGetTicket(i) > 0 &&
           PositionGetInteger(POSITION_MAGIC)  == MagicNumber &&
           PositionGetString(POSITION_SYMBOL)  == _Symbol &&
           PositionGetString(POSITION_COMMENT) == comment)
            return true;
    }
    return false;
}

//+------------------------------------------------------------------+
//| TEST_MODE: check simulated SL/TP against the last closed M15 bar |
//| No Supabase poll — avoids false "closed" on network/RLS errors   |
//+------------------------------------------------------------------+
void CheckSimulatedSLTP(double m15Low, double m15High)
{
    string sessions[4] = {"asian", "london", "ny", "short"};
    bool   isShort[4]  = {false,   false,    false, true};

    for(int i = 0; i < 4; i++)
    {
        string gv = SimGV(sessions[i]);
        if(!GlobalVariableCheck(gv) || GlobalVariableGet(gv) < 0.5) continue;

        double sl = GlobalVariableCheck(SimSLGV(sessions[i])) ? GlobalVariableGet(SimSLGV(sessions[i])) : 0;
        double tp = GlobalVariableCheck(SimTPGV(sessions[i])) ? GlobalVariableGet(SimTPGV(sessions[i])) : 0;

        // Legacy position (opened before fix, no SL/TP stored):
        // use simple Supabase poll — only clears GV if signal is truly gone
        if(sl <= 0 || tp <= 0)
        {
            if(StringLen(SupabaseURL) > 0 && !SupabaseSignalIsActive(sessions[i]))
            {
                GlobalVariableSet(gv, 0);
                Print("[TEST] ", sessions[i], " legacy position no longer active — state cleared");
                if(sessions[i] == "short")
                {
                    GlobalVariableSet(GV_T1_ACTIVE, 0); GlobalVariableSet(GV_T1_H4HIGH, 0);
                    GlobalVariableSet(GV_T2_ACTIVE, 0); GlobalVariableSet(GV_T2_H4HIGH, 0);
                }
            }
            continue;
        }

        bool   slHit     = isShort[i] ? (m15High >= sl) : (m15Low <= sl);
        bool   tpHit     = isShort[i] ? (m15Low  <= tp) : (m15High >= tp);
        double exitPrice = slHit ? sl : tp;
        double pnl       = slHit ? -RiskUSD : RiskUSD * TP_RR_VAL;

        if(slHit || tpHit)
        {
            Print("[TEST] ", sessions[i], " simulated ", (slHit ? "SL" : "TP"), " hit @ ",
                  exitPrice, "  PnL=", pnl);
            GlobalVariableSet(gv, 0);
            GlobalVariableSet(SimSLGV(sessions[i]), 0);
            GlobalVariableSet(SimTPGV(sessions[i]), 0);
            CloseSignalInSupabase(sessions[i], exitPrice, pnl);

            if(sessions[i] == "short")
            {
                GlobalVariableSet(GV_T1_ACTIVE, 0); GlobalVariableSet(GV_T1_H4HIGH, 0);
                GlobalVariableSet(GV_T2_ACTIVE, 0); GlobalVariableSet(GV_T2_H4HIGH, 0);
                Print("SHORT state reset after simulated close");
            }
        }
    }
}

// Simple active-signal check — uses status=eq.active only (avoids in.() encoding issues)
bool SupabaseSignalIsActive(string session)
{
    string url = SupabaseURL + "/rest/v1/mt5_signals?status=eq.active&session=eq." + session + "&select=id&limit=1";
    string headers = "apikey: " + SupabaseKey + "\r\n" +
                     "Authorization: Bearer " + SupabaseKey + "\r\n";
    char   data[], result[];
    string resHeaders;
    int rc = WebRequest("GET", url, headers, 5000, data, result, resHeaders);
    if(rc == -1) return true; // network error → assume still active (safe default)
    string body = CharArrayToString(result);
    return (StringLen(body) > 2); // "[]" = not active
}

//+------------------------------------------------------------------+
//| Open trade + log to Supabase                                     |
//+------------------------------------------------------------------+
bool OpenTrade(string direction, double entry, double sl, double tp, string session)
{
    // Write signal to Supabase (both modes)
    WriteSignalToSupabase(direction, entry, sl, tp, session);

    if(TestMode)
    {
        Print("[TEST] Signal logged. No real trade.");
        GlobalVariableSet(SimGV(session),   1);
        GlobalVariableSet(SimSLGV(session), sl);
        GlobalVariableSet(SimTPGV(session), tp);
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

    MqlTradeRequest req = {};
    MqlTradeResult  res = {};
    req.action    = TRADE_ACTION_DEAL;
    req.symbol    = _Symbol;
    req.volume    = lot;
    req.type      = orderType;
    req.price     = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                                   : SymbolInfoDouble(_Symbol, SYMBOL_BID);
    req.sl        = NormalizeDouble(sl, _Digits);
    req.tp        = NormalizeDouble(tp, _Digits);
    req.deviation = Slippage;
    req.magic     = MagicNumber;
    req.comment   = "Astra_" + session;

    if(OrderSend(req, res))
    {
        Print("TRADE OPENED: ", direction, " ", session, " lot=", lot, " ticket=", res.order);
        return true;
    }

    Print("ERROR OrderSend: ", GetLastError(), " retcode=", res.retcode);
    return false;
}

//+------------------------------------------------------------------+
//| Supabase: write new signal                                        |
//+------------------------------------------------------------------+
void WriteSignalToSupabase(string direction, double entry, double sl, double tp, string session)
{
    if(StringLen(SupabaseURL) == 0 || StringLen(SupabaseKey) == 0)
    {
        Print("Supabase not configured — signal not logged");
        return;
    }

    string url = SupabaseURL + "/rest/v1/mt5_signals";
    string headers = "apikey: "       + SupabaseKey + "\r\n" +
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
    ArrayResize(data, ArraySize(data) - 1); // strip null terminator

    string resHeaders;
    int rc = WebRequest("POST", url, headers, 5000, data, result, resHeaders);
    if(rc == -1)
        Print("WriteSignal WebRequest error: ", GetLastError(), " (whitelist URL in MT5 settings)");
    else
        Print("Signal logged to Supabase: ", direction, " ", session, " @ ", entry);
}

//+------------------------------------------------------------------+
//| Supabase: mark position closed (called from OnTradeTransaction)  |
//+------------------------------------------------------------------+
void CloseSignalInSupabase(string session, double exitPrice, double profit)
{
    if(StringLen(SupabaseURL) == 0) return;

    string url = SupabaseURL + "/rest/v1/mt5_signals?status=eq.active&session=eq." + session;
    string headers = "apikey: "       + SupabaseKey + "\r\n" +
                     "Authorization: Bearer " + SupabaseKey + "\r\n" +
                     "Content-Type: application/json\r\n";

    string body = StringFormat("{\"status\":\"closed\",\"exit_price\":%.2f,\"pnl\":%.2f}",
                               exitPrice, profit);

    char data[], result[];
    StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
    ArrayResize(data, ArraySize(data) - 1);

    string resHeaders;
    int rc = WebRequest("PATCH", url, headers, 5000, data, result, resHeaders);
    if(rc == -1)
        Print("CloseSignal WebRequest error: ", GetLastError());
    else
        Print("Signal closed in Supabase: ", session, " @ ", exitPrice, " PnL=", profit);
}

//+------------------------------------------------------------------+
//| Detect closed positions → update Supabase                        |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    if(!HistoryDealSelect(trans.deal)) return;
    if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != MagicNumber) return;

    ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
    if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) return;

    string comment   = HistoryDealGetString(trans.deal, DEAL_COMMENT);
    double exitPrice = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
    double profit    = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);

    // comment = "Astra_asian" → session = "asian"
    string session = StringSubstr(comment, 6);
    Print("Position closed: ", session, " @ ", exitPrice, " PnL=", profit);
    CloseSignalInSupabase(session, exitPrice, profit);

    // Reset SHORT state on close — matches backtest behavior
    if(session == "short")
    {
        GlobalVariableSet(GV_T1_ACTIVE, 0); GlobalVariableSet(GV_T1_H4HIGH, 0);
        GlobalVariableSet(GV_T2_ACTIVE, 0); GlobalVariableSet(GV_T2_H4HIGH, 0);
        Print("SHORT state reset after position close");
    }
}

//+------------------------------------------------------------------+
//| Step trailing stop                                                |
//+------------------------------------------------------------------+
void UpdateTrailingStops()
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

        double posEntry = PositionGetDouble(POSITION_PRICE_OPEN);
        double curSL    = PositionGetDouble(POSITION_SL);
        double curTP    = PositionGetDouble(POSITION_TP);
        double curPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
        ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

        double risk = 0, newSL = curSL, profitR = 0;

        if(posType == POSITION_TYPE_BUY)
        {
            risk = posEntry - curSL;
            if(risk <= 0) continue;
            profitR = (curPrice - posEntry) / risk;
            if(profitR >= 5.0) newSL = MathMax(newSL, posEntry + 4.0 * risk);
            else if(profitR >= 4.0) newSL = MathMax(newSL, posEntry + 3.0 * risk);
            else if(profitR >= 3.0) newSL = MathMax(newSL, posEntry + 2.0 * risk);
            else if(profitR >= 2.0) newSL = MathMax(newSL, posEntry + 1.0 * risk);

            if(newSL > curSL + _Point * 10)
            {
                MqlTradeRequest req = {}; MqlTradeResult res = {};
                req.action = TRADE_ACTION_SLTP; req.position = ticket;
                req.symbol = _Symbol;
                req.sl = NormalizeDouble(newSL, _Digits); req.tp = curTP;
                if(OrderSend(req, res))
                    Print("Trail BUY  ", ticket, " SL=", newSL, " (", profitR, "R)");
            }
        }
        else if(posType == POSITION_TYPE_SELL)
        {
            risk = curSL - posEntry;
            if(risk <= 0) continue;
            profitR = (posEntry - curPrice) / risk;
            if(profitR >= 5.0) newSL = MathMin(newSL, posEntry - 4.0 * risk);
            else if(profitR >= 4.0) newSL = MathMin(newSL, posEntry - 3.0 * risk);
            else if(profitR >= 3.0) newSL = MathMin(newSL, posEntry - 2.0 * risk);
            else if(profitR >= 2.0) newSL = MathMin(newSL, posEntry - 1.0 * risk);

            if(newSL < curSL - _Point * 10)
            {
                MqlTradeRequest req = {}; MqlTradeResult res = {};
                req.action = TRADE_ACTION_SLTP; req.position = ticket;
                req.symbol = _Symbol;
                req.sl = NormalizeDouble(newSL, _Digits); req.tp = curTP;
                if(OrderSend(req, res))
                    Print("Trail SELL ", ticket, " SL=", newSL, " (", profitR, "R)");
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Sync 2000 M15 candles to file for Python bridge                  |
//+------------------------------------------------------------------+
void SyncCandlesToFile()
{
    MqlRates rates[];
    ArraySetAsSeries(rates, true);
    int copied = CopyRates(_Symbol, PERIOD_M15, 1, 2000, rates);
    if(copied <= 0) { Print("SyncCandles: no data (", GetLastError(), ")"); return; }

    string json = "[\n";
    for(int i = 0; i < copied; i++)
    {
        if(i > 0) json += ",\n";
        json += "  {\"time\":\""  + TimeToString(rates[i].time, TIME_DATE|TIME_MINUTES) + "\","
              + "\"open\":"        + DoubleToString(rates[i].open,  _Digits) + ","
              + "\"high\":"        + DoubleToString(rates[i].high,  _Digits) + ","
              + "\"low\":"         + DoubleToString(rates[i].low,   _Digits) + ","
              + "\"close\":"       + DoubleToString(rates[i].close, _Digits) + ","
              + "\"volume\":"      + IntegerToString(rates[i].tick_volume)   + "}";
    }
    json += "\n]";

    int fh = FileOpen(CandlesFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
    if(fh == INVALID_HANDLE) { Print("SyncCandles: can't open file (", GetLastError(), ")"); return; }
    FileWriteString(fh, json);
    FileClose(fh);
    Print("Synced ", copied, " M15 candles");
}
//+------------------------------------------------------------------+
