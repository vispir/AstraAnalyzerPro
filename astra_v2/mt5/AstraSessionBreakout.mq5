//+------------------------------------------------------------------+
//|                                        AstraSessionBreakout.mq5 |
//|                                   Astra Session Breakout Strategy |
//|                                   Integrated with Supabase Signals |
//+------------------------------------------------------------------+
#property copyright "Astra Trading System"
#property link      "https://github.com/vispir/AstraAnalyzerPro"
#property version   "2.10"
#property strict

// Input parameters
input string SupabaseURL = "https://your-project.supabase.co";
input string SupabaseKey = "your-anon-key";
input string Symbol_Trade = "XAUUSD";
input int CheckInterval = 15; // Check for new signals every 15 seconds
input double MaxSlippage = 10.0; // Max slippage in points

// Global variables
datetime lastCheckTime = 0;
int currentSignalID = 0;
double initialSL = 0;
double initialEntry = 0;
double riskAmount = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("Astra Session Breakout EA initialized");
   Print("Supabase URL: ", SupabaseURL);
   Print("Symbol: ", Symbol_Trade);
   Print("Check interval: ", CheckInterval, " seconds");

   // Enable WebRequest for Supabase domain
   // Add to MT5: Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL
   Print("IMPORTANT: Enable WebRequest for: ", SupabaseURL);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("Astra Session Breakout EA stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check for new signals every CheckInterval seconds
   if(TimeCurrent() - lastCheckTime >= CheckInterval)
   {
      lastCheckTime = TimeCurrent();
      CheckForNewSignals();
   }

   // Update step trailing stop for active position
   if(PositionSelect(Symbol_Trade))
   {
      UpdateStepTrailing();
   }
}

//+------------------------------------------------------------------+
//| Check Supabase for new signals                                   |
//+------------------------------------------------------------------+
void CheckForNewSignals()
{
   // Don't check if we already have an open position
   if(PositionSelect(Symbol_Trade))
      return;

   string url = SupabaseURL + "/rest/v1/mt5_signals?status=eq.new&limit=1&order=created_at.desc";
   string headers = "apikey: " + SupabaseKey + "\r\n" +
                    "Authorization: Bearer " + SupabaseKey + "\r\n" +
                    "Content-Type: application/json\r\n";

   char post[], result[];
   string result_headers;

   int res = WebRequest("GET", url, headers, 5000, post, result, result_headers);

   if(res == 200)
   {
      string response = CharArrayToString(result);

      // Parse JSON response (simplified - in production use proper JSON parser)
      if(StringFind(response, "\"id\"") > 0)
      {
         // Extract signal data
         int signalID = ExtractIntValue(response, "\"id\":");
         string direction = ExtractStringValue(response, "\"direction\":\"");
         double entry = ExtractDoubleValue(response, "\"entry\":");
         double sl = ExtractDoubleValue(response, "\"sl\":");
         double tp = ExtractDoubleValue(response, "\"tp\":");
         double risk = ExtractDoubleValue(response, "\"risk_usd\":");
         string session = ExtractStringValue(response, "\"session\":\"");

         Print("New signal found: ", direction, " ", session, " @ ", entry, " SL:", sl, " TP:", tp);

         // Execute trade
         if(ExecuteTrade(signalID, direction, entry, sl, tp, risk, session))
         {
            UpdateSignalStatus(signalID, "active");
         }
      }
   }
   else if(res == -1)
   {
      Print("WebRequest error: ", GetLastError(), " - Make sure WebRequest is enabled for ", SupabaseURL);
   }
}

//+------------------------------------------------------------------+
//| Execute trade based on signal                                    |
//+------------------------------------------------------------------+
bool ExecuteTrade(int signalID, string direction, double entry, double sl, double tp, double risk, string session)
{
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   // Calculate lot size based on risk
   double slDistance = MathAbs(entry - sl);
   double tickValue = SymbolInfoDouble(Symbol_Trade, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol_Trade, SYMBOL_TRADE_TICK_SIZE);
   double lotSize = (risk / (slDistance / tickSize * tickValue));

   // Round to valid lot size
   double minLot = SymbolInfoDouble(Symbol_Trade, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(Symbol_Trade, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(Symbol_Trade, SYMBOL_VOLUME_STEP);
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));

   Print("Calculated lot size: ", lotSize, " (Risk: $", risk, ", SL distance: ", slDistance, ")");

   // Prepare order
   request.action = TRADE_ACTION_DEAL;
   request.symbol = Symbol_Trade;
   request.volume = lotSize;
   request.type = (direction == "LONG") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.price = (direction == "LONG") ? SymbolInfoDouble(Symbol_Trade, SYMBOL_ASK) : SymbolInfoDouble(Symbol_Trade, SYMBOL_BID);
   request.sl = sl;
   request.tp = tp;
   request.deviation = (ulong)MaxSlippage;
   request.magic = 20261 + signalID; // Unique magic number
   request.comment = "Astra " + session;
   request.type_filling = ORDER_FILLING_IOC;

   // Send order
   if(OrderSend(request, result))
   {
      if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED)
      {
         Print("Trade executed successfully: ", direction, " ", lotSize, " lots @ ", result.price);

         // Store for trailing
         currentSignalID = signalID;
         initialEntry = result.price;
         initialSL = sl;
         riskAmount = MathAbs(initialEntry - initialSL);

         return true;
      }
      else
      {
         Print("Order failed: ", result.retcode, " - ", result.comment);
         return false;
      }
   }
   else
   {
      Print("OrderSend error: ", GetLastError());
      return false;
   }
}

//+------------------------------------------------------------------+
//| Update step trailing stop (2R->1R, 3R->2R, 4R->3R, 5R->4R)      |
//+------------------------------------------------------------------+
void UpdateStepTrailing()
{
   if(!PositionSelect(Symbol_Trade))
      return;

   if(riskAmount == 0 || initialEntry == 0)
      return;

   double currentPrice = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ?
                         SymbolInfoDouble(Symbol_Trade, SYMBOL_BID) :
                         SymbolInfoDouble(Symbol_Trade, SYMBOL_ASK);

   double currentSL = PositionGetDouble(POSITION_SL);
   double profitR = 0;
   double newSL = currentSL;
   bool isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);

   if(isLong)
   {
      profitR = (currentPrice - initialEntry) / riskAmount;

      // Step trailing logic for LONG
      if(profitR >= 5.0)
         newSL = MathMax(currentSL, initialEntry + 4.0 * riskAmount);
      else if(profitR >= 4.0)
         newSL = MathMax(currentSL, initialEntry + 3.0 * riskAmount);
      else if(profitR >= 3.0)
         newSL = MathMax(currentSL, initialEntry + 2.0 * riskAmount);
      else if(profitR >= 2.0)
         newSL = MathMax(currentSL, initialEntry + 1.0 * riskAmount);
   }
   else // SHORT
   {
      profitR = (initialEntry - currentPrice) / riskAmount;

      // Step trailing logic for SHORT
      if(profitR >= 5.0)
         newSL = MathMin(currentSL, initialEntry - 4.0 * riskAmount);
      else if(profitR >= 4.0)
         newSL = MathMin(currentSL, initialEntry - 3.0 * riskAmount);
      else if(profitR >= 3.0)
         newSL = MathMin(currentSL, initialEntry - 2.0 * riskAmount);
      else if(profitR >= 2.0)
         newSL = MathMin(currentSL, initialEntry - 1.0 * riskAmount);
   }

   // Update SL if changed
   if(MathAbs(newSL - currentSL) > SymbolInfoDouble(Symbol_Trade, SYMBOL_POINT))
   {
      MqlTradeRequest request;
      MqlTradeResult result;
      ZeroMemory(request);
      ZeroMemory(result);

      request.action = TRADE_ACTION_SLTP;
      request.symbol = Symbol_Trade;
      request.sl = newSL;
      request.tp = PositionGetDouble(POSITION_TP);
      request.position = PositionGetInteger(POSITION_TICKET);

      if(OrderSend(request, result))
      {
         Print("Trailing stop updated: ", profitR, "R -> SL at ", newSL);
      }
   }
}

//+------------------------------------------------------------------+
//| Update signal status in Supabase                                 |
//+------------------------------------------------------------------+
void UpdateSignalStatus(int signalID, string status)
{
   string url = SupabaseURL + "/rest/v1/mt5_signals?id=eq." + IntegerToString(signalID);
   string headers = "apikey: " + SupabaseKey + "\r\n" +
                    "Authorization: Bearer " + SupabaseKey + "\r\n" +
                    "Content-Type: application/json\r\n" +
                    "Prefer: return=minimal\r\n";

   string body = "{\"status\":\"" + status + "\"}";
   char post[], result[];
   StringToCharArray(body, post, 0, StringLen(body));
   string result_headers;

   int res = WebRequest("PATCH", url, headers, 5000, post, result, result_headers);

   if(res == 204 || res == 200)
   {
      Print("Signal ", signalID, " status updated to: ", status);
   }
   else
   {
      Print("Failed to update signal status: ", res);
   }
}

//+------------------------------------------------------------------+
//| Helper: Extract integer value from JSON                          |
//+------------------------------------------------------------------+
int ExtractIntValue(string json, string key)
{
   int pos = StringFind(json, key);
   if(pos < 0) return 0;

   string sub = StringSubstr(json, pos + StringLen(key));
   int endPos = StringFind(sub, ",");
   if(endPos < 0) endPos = StringFind(sub, "}");

   string value = StringSubstr(sub, 0, endPos);
   StringTrimLeft(value);
   StringTrimRight(value);

   return (int)StringToInteger(value);
}

//+------------------------------------------------------------------+
//| Helper: Extract double value from JSON                           |
//+------------------------------------------------------------------+
double ExtractDoubleValue(string json, string key)
{
   int pos = StringFind(json, key);
   if(pos < 0) return 0.0;

   string sub = StringSubstr(json, pos + StringLen(key));
   int endPos = StringFind(sub, ",");
   if(endPos < 0) endPos = StringFind(sub, "}");

   string value = StringSubstr(sub, 0, endPos);
   StringTrimLeft(value);
   StringTrimRight(value);

   return StringToDouble(value);
}

//+------------------------------------------------------------------+
//| Helper: Extract string value from JSON                           |
//+------------------------------------------------------------------+
string ExtractStringValue(string json, string key)
{
   int pos = StringFind(json, key);
   if(pos < 0) return "";

   string sub = StringSubstr(json, pos + StringLen(key));
   int endPos = StringFind(sub, "\"");

   return StringSubstr(sub, 0, endPos);
}
//+------------------------------------------------------------------+
