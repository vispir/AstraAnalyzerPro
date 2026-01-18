import pandas as pd
import numpy as np
from typing import Dict, List, Optional

class TradingCalculator:
    def __init__(self):
        pass

    def get_market_analysis(self, candles: List[Dict]) -> Dict:
        if not candles or len(candles) < 30:
            return {"error": "Insufficient data"}

        df = pd.DataFrame(candles)
        
        # 1. Считаем индикаторы
        indicators = self._calculate_indicators(df)
        
        # 2. Ищем структурные точки (Swings)
        swings = self._get_swings(df)
        
        # 3. На основе свингов ищем BOS, CHoCH и Equal Levels
        structure = self._analyze_structure(df, swings)
        
        analysis = {
            "fvg": self._detect_fvg(df),
            "bos_choch": structure["events"],
            "liquidity": structure["liquidity"],
            "order_blocks": self._detect_order_blocks(df),
            "levels": {
                "resistance": float(df['high'].max()),
                "support": float(df['low'].min())
            },
            "indicators": indicators
        }
        
        analysis["ai_transcript"] = self._format_for_ai(df, analysis)
        return analysis

    def _get_swings(self, df: pd.DataFrame, window=5):
        """Находит значимые пики и впадины (Fractals)"""
        swings = []
        for i in range(window, len(df) - window):
            is_high = df['high'].iloc[i] == df['high'].iloc[i-window:i+window+1].max()
            is_low = df['low'].iloc[i] == df['low'].iloc[i-window:i+window+1].min()
            
            if is_high:
                swings.append({"type": "HIGH", "price": float(df['high'].iloc[i]), "index": i})
            if is_low:
                swings.append({"type": "LOW", "price": float(df['low'].iloc[i]), "index": i})
        return swings

    def _analyze_structure(self, df: pd.DataFrame, swings: List[Dict]):
        """Математика BOS, CHoCH и Equal Highs/Lows"""
        events = []
        liquidity = {"EQH": [], "EQL": []}
        if len(swings) < 4: return {"events": events, "liquidity": liquidity}

        current_trend = None # 'BULL' or 'BEAR'
        last_high = swings[0]["price"]
        last_low = swings[1]["price"]

        # Ищем Equal Highs/Lows (порог 0.05% разницы)
        threshold = 0.5 # Для золота 50 центов - это "равные" уровни
        
        for i in range(len(swings)):
            for j in range(i + 1, min(i + 5, len(swings))):
                if swings[i]["type"] == swings[j]["type"] == "HIGH":
                    if abs(swings[i]["price"] - swings[j]["price"]) < threshold:
                        liquidity["EQH"].append(swings[i]["price"])
                if swings[i]["type"] == swings[j]["type"] == "LOW":
                    if abs(swings[i]["price"] - swings[j]["price"]) < threshold:
                        liquidity["EQL"].append(swings[i]["price"])

        # Логика BOS и CHoCH (упрощенная для LLM)
        for i in range(2, len(swings)):
            s = swings[i]
            curr_close = df['close'].iloc[-1]

            if s["type"] == "HIGH":
                if curr_close > s["price"]:
                    etype = "BOS" if current_trend == "BULL" else "CHoCH"
                    events.append({"type": f"BULLISH_{etype}", "level": s["price"]})
                    current_trend = "BULL"
            else: # LOW
                if curr_close < s["price"]:
                    etype = "BOS" if current_trend == "BEAR" else "CHoCH"
                    events.append({"type": f"BEARISH_{etype}", "level": s["price"]})
                    current_trend = "BEAR"

        return {"events": events[-3:], "liquidity": liquidity}

    def _detect_fvg(self, df: pd.DataFrame) -> List[Dict]:
        fvgs = []
        for i in range(2, len(df)):
            if df['low'].iloc[i] > df['high'].iloc[i-2]:
                fvgs.append({"type": "BULL", "price": (df['low'].iloc[i] + df['high'].iloc[i-2])/2})
            elif df['high'].iloc[i] < df['low'].iloc[i-2]:
                fvgs.append({"type": "BEAR", "price": (df['low'].iloc[i-2] + df['high'].iloc[i])/2})
        return fvgs[-3:]

    def _detect_order_blocks(self, df: pd.DataFrame) -> List[Dict]:
        obs = []
        df['body_size'] = abs(df['close'] - df['open'])
        avg_body = df['body_size'].rolling(window=20).mean()
        for i in range(1, len(df) - 5):
            is_impulsive = df['body_size'].iloc[i+1] > avg_body.iloc[i] * 2.5
            if is_impulsive:
                obs.append({
                    "type": "BULL_OB" if df['close'].iloc[i+1] > df['open'].iloc[i+1] else "BEAR_OB",
                    "top": float(df['high'].iloc[i]),
                    "bottom": float(df['low'].iloc[i])
                })
        return obs[-2:]

    def _calculate_indicators(self, df: pd.DataFrame) -> Dict:
        closes = df['close']
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        return {"rsi": round(float(rsi.iloc[-1]), 2), "sma_20": round(float(closes.tail(20).mean()), 2)}

    def _format_for_ai(self, df: pd.DataFrame, analysis: Dict) -> str:
        transcript = "DATA (O|H|L|C|V):\n"
        for _, r in df.tail(15).iterrows():
            transcript += f"{r['open']:.2f}|{r['high']:.2f}|{r['low']:.2f}|{r['close']:.2f}|{int(r['volume'])}\n"
        
        transcript += "\nSMC STRUCTURES:\n"
        for ev in analysis['bos_choch']:
            transcript += f"- {ev['type']} @ {ev['level']:.2f}\n"
        
        liq = analysis['liquidity']
        if liq['EQH']: transcript += f"- EQH (Liquidity Above): {liq['EQH'][-1]:.2f}\n"
        if liq['EQL']: transcript += f"- EQL (Liquidity Below): {liq['EQL'][-1]:.2f}\n"
        
        if analysis['fvg']: transcript += f"- FVG: {analysis['fvg'][-1]['type']} at {analysis['fvg'][-1]['price']:.2f}\n"
        
        for ob in analysis['order_blocks']:
            transcript += f"- OB: {ob['type']} [{ob['bottom']:.2f}-{ob['top']:.2f}]\n"
            
        transcript += f"- RSI: {analysis['indicators']['rsi']} | S/R: {analysis['levels']['support']:.2f}-{analysis['levels']['resistance']:.2f}\n"
        return transcript

calculator = TradingCalculator() 