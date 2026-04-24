//+------------------------------------------------------------------+
//|                                      AstraSessionBreakout.mq5    |
//|                                   Session Breakout Strategy v2.1 |
//|                                   File Exchange with Python      |
//+------------------------------------------------------------------+
#property copyright "Astra Analyzer Pro"
#property version   "2.10"
#property strict

//--- Input parameters
input double RiskUSD = 158.0;           // Risk per trade in USD (fixed)
input int MagicNumber = 20241121;       // Magic number
input int Slippage = 20;                // Slippage in points
input bool EnableTrailing = true;       // Enable step trailing stop
input int CheckInterval = 5;            // Check signals every N seconds
input bool TestMode = true;             // Test mode (no real trades)

//--- File paths
string SignalsFile = "astra_signals.json";
string CandlesFile = "astra_candles.json";
string TradesFile = "astra_trades.json";

//--- Global variables
datetime lastCheckTime = 0;
int lastSyncMinute = -1;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("==========================================================");
   Print("Astra Session Breakout EA v2.1 - Starting");
   Print("Risk: $", RiskUSD, " | Magic: ", MagicNumber);
   Print("Trailing: ", EnableTrailing ? "Enabled" : "Disabled");
   Print("Test Mode: ", TestMode ? "ON (no real trades)" : "OFF (live trading)");
   Print("==========================================================");

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("EA stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Check if should sync candles (10 sec after M15 close)           |
//+------------------------------------------------------------------+
bool ShouldSyncCandles()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   // Sync at 10 seconds after M15 close (00:10, 15:10, 30:10, 45:10)
   if(dt.min % 15 == 0 && dt.sec >= 10 && dt.sec <= 15)
   {
      if(dt.min != lastSyncMinute)
      {
         lastSyncMinute = dt.min;
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check signals every N seconds
   if(TimeCurrent() - lastCheckTime >= CheckInterval)
   {
      CheckNewSignals();
      lastCheckTime = TimeCurrent();
   }

   // Sync candles at 10 sec after M15 close (00/15/30/45)
   if(ShouldSyncCandles())
   {
      SyncCandlesToFile();
   }

   // Update trailing stops
   if(EnableTrailing)
   {
      UpdateTrailingStops();
   }
}

//+------------------------------------------------------------------+
//| Check for new signals from Python bridge                        |
//+------------------------------------------------------------------+
void CheckNewSignals()
{
   if(TestMode)
   {
      return;
   }

   int fileHandle = FileOpen(SignalsFile, FILE_READ|FILE_TXT|FILE_COMMON);

   if(fileHandle == INVALID_HANDLE)
   {
      return;
   }

   string jsonContent = "";
   while(!FileIsEnding(fileHandle))
   {
      jsonContent += FileReadString(fileHandle);
   }
   FileClose(fileHandle);

   if(StringLen(jsonContent) == 0)
   {
      return;
   }

   FileDelete(SignalsFile, FILE_COMMON);
   Print("New signals detected - processing...");
}

//+------------------------------------------------------------------+
//| Sync M15 candles to file for Python bridge                      |
//+------------------------------------------------------------------+
void SyncCandlesToFile()
{
   int barsToSync = 500;  // Increased from 300 to 500 for better H4 EMA20 calculation

   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int copied = CopyRates(_Symbol, PERIOD_M15, 0, barsToSync, rates);

   if(copied <= 0)
   {
      Print("Error copying rates: ", GetLastError());
      return;
   }

   string json = "[\n";

   for(int i = 0; i < copied; i++)
   {
      if(i > 0) json += ",\n";

      json += "  {\n";
      json += "    \"time\": \"" + TimeToString(rates[i].time, TIME_DATE|TIME_MINUTES) + "\",\n";
      json += "    \"open\": " + DoubleToString(rates[i].open, _Digits) + ",\n";
      json += "    \"high\": " + DoubleToString(rates[i].high, _Digits) + ",\n";
      json += "    \"low\": " + DoubleToString(rates[i].low, _Digits) + ",\n";
      json += "    \"close\": " + DoubleToString(rates[i].close, _Digits) + ",\n";
      json += "    \"volume\": " + IntegerToString(rates[i].tick_volume) + "\n";
      json += "  }";
   }

   json += "\n]";

   int fileHandle = FileOpen(CandlesFile, FILE_WRITE|FILE_TXT|FILE_COMMON);

   if(fileHandle == INVALID_HANDLE)
   {
      Print("Error opening candles file: ", GetLastError());
      return;
   }

   FileWriteString(fileHandle, json);
   FileClose(fileHandle);

   Print("Synced ", copied, " M15 candles to file");
}

//+------------------------------------------------------------------+
//| Update trailing stops for active positions                      |
//+------------------------------------------------------------------+
void UpdateTrailingStops()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);

      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      if(posType == POSITION_TYPE_BUY)
      {
         // LONG: SL below entry, profit when price goes UP
         double risk = entry - currentSL;
         if(risk <= 0) continue;

         double profitR = (currentPrice - entry) / risk;
         double newSL = currentSL;

         // Step trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R
         if(profitR >= 5.0)
            newSL = MathMax(newSL, entry + 4.0 * risk);
         else if(profitR >= 4.0)
            newSL = MathMax(newSL, entry + 3.0 * risk);
         else if(profitR >= 3.0)
            newSL = MathMax(newSL, entry + 2.0 * risk);
         else if(profitR >= 2.0)
            newSL = MathMax(newSL, entry + 1.0 * risk);

         if(newSL > currentSL + _Point * 10)
         {
            MqlTradeRequest request = {};
            MqlTradeResult result = {};

            request.action = TRADE_ACTION_SLTP;
            request.position = ticket;
            request.symbol = _Symbol;
            request.sl = NormalizeDouble(newSL, _Digits);
            request.tp = PositionGetDouble(POSITION_TP);

            if(OrderSend(request, result))
            {
               Print("LONG Trailing stop updated: ", ticket, " New SL: ", newSL, " (", profitR, "R)");
            }
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         // SHORT: SL above entry, profit when price goes DOWN
         double risk = currentSL - entry;
         if(risk <= 0) continue;

         double profitR = (entry - currentPrice) / risk;
         double newSL = currentSL;

         // Step trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R (inverse)
         if(profitR >= 5.0)
            newSL = MathMin(newSL, entry - 4.0 * risk);
         else if(profitR >= 4.0)
            newSL = MathMin(newSL, entry - 3.0 * risk);
         else if(profitR >= 3.0)
            newSL = MathMin(newSL, entry - 2.0 * risk);
         else if(profitR >= 2.0)
            newSL = MathMin(newSL, entry - 1.0 * risk);

         if(newSL < currentSL - _Point * 10)
         {
            MqlTradeRequest request = {};
            MqlTradeResult result = {};

            request.action = TRADE_ACTION_SLTP;
            request.position = ticket;
            request.symbol = _Symbol;
            request.sl = NormalizeDouble(newSL, _Digits);
            request.tp = PositionGetDouble(POSITION_TP);

            if(OrderSend(request, result))
            {
               Print("SHORT Trailing stop updated: ", ticket, " New SL: ", newSL, " (", profitR, "R)");
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Open trade from signal                                           |
//+------------------------------------------------------------------+
bool OpenTrade(string direction, double entry, double sl, double tp, double riskUSD)
{
   // Check if we already have an open position
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(PositionGetTicket(i) > 0)
      {
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            Print("Position already open - skipping signal");
            return false;
         }
      }
   }

   if(TestMode)
   {
      Print("=== TEST MODE SIGNAL ===");
      Print("Direction: ", direction);
      Print("Entry: ", entry, " | SL: ", sl, " | TP: ", tp);
      Print("Risk: $", riskUSD);
      Print("========================");
      return true;
   }

   ENUM_ORDER_TYPE orderType = (direction == "LONG") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   double risk = MathAbs(entry - sl);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double lot = riskUSD / (risk * tickValue);

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lot = MathMax(minLot, MathMin(maxLot, MathRound(lot / lotStep) * lotStep));

   MqlTradeRequest request = {};
   MqlTradeResult result = {};

   request.action = TRADE_ACTION_DEAL;
   request.symbol = _Symbol;
   request.volume = lot;
   request.type = orderType;
   request.price = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   request.sl = NormalizeDouble(sl, _Digits);
   request.tp = NormalizeDouble(tp, _Digits);
   request.deviation = Slippage;
   request.magic = MagicNumber;
   request.comment = "Astra_" + direction;

   if(OrderSend(request, result))
   {
      Print("=== TRADE OPENED ===");
      Print("Direction: ", direction);
      Print("Entry: ", entry, " | SL: ", sl, " | TP: ", tp);
      Print("Lot: ", lot, " | Risk: $", riskUSD);
      Print("Ticket: ", result.order);
      Print("====================");
      return true;
   }
   else
   {
      Print("ERROR opening trade: ", GetLastError());
      return false;
   }
}
//+------------------------------------------------------------------+
