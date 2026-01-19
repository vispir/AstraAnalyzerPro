import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from services.smc_detector import smc_detector

class TradingCalculator:
    def __init__(self):
        self.smc_detector = smc_detector

    def get_market_analysis(self, candles: List[Dict]) -> Dict:
        """
        Анализ рынка с использованием SMC детектора
        
        Args:
            candles: Список свечей (OHLC)
            
        Returns:
            Dict с полным анализом для AI
        """
        if not candles or len(candles) < 30:
            return {"error": "Insufficient data"}

        df = pd.DataFrame(candles)
        
        # 1. Используем новый SMC детектор для всех структур
        smc_data = self.smc_detector.analyze(df)
        
        # 2. Считаем индикаторы
        indicators = self._calculate_indicators(df)
        
        # 3. Формируем анализ в старом формате для совместимости
        analysis = {
            "order_blocks": smc_data.get('order_blocks', []),
            "fvg": smc_data.get('fvg', []),
            "liquidity": self._format_liquidity_legacy(smc_data),
            "bos_choch": self._format_bos_choch_legacy(smc_data),
            "trend": smc_data.get('trend', 'NEUTRAL'),
            "levels": {
                "resistance": float(df['high'].max()),
                "support": float(df['low'].min())
            },
            "indicators": indicators
        }
        
        # 4. Формат для AI
        analysis["ai_transcript"] = self._format_for_ai(df, analysis, smc_data)
        
        return analysis
    
    def _format_liquidity_legacy(self, smc_data: Dict) -> Dict:
        """
        Преобразует новый формат EQH/EQL в старый формат для совместимости
        """
        eqh_prices = [level['price'] for level in smc_data.get('eqh', [])]
        eql_prices = [level['price'] for level in smc_data.get('eql', [])]
        
        return {
            "EQH": eqh_prices,
            "EQL": eql_prices
        }
    
    def _format_bos_choch_legacy(self, smc_data: Dict) -> List[Dict]:
        """
        Преобразует новый формат BOS/CHOCH в старый формат для совместимости
        """
        events = []
        
        # Добавляем CHOCH
        for choch in smc_data.get('choch', []):
            events.append({
                "type": choch.get('type', 'CHOCH'),
                "level": choch.get('price', 0)
            })
        
        # Добавляем BOS
        for bos in smc_data.get('bos', []):
            events.append({
                "type": bos.get('type', 'BOS'),
                "level": bos.get('price', 0)
            })
        
        return events

    def _calculate_indicators(self, df: pd.DataFrame) -> Dict:
        """
        Расчёт технических индикаторов
        """
        closes = df['close']
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        return {
            "rsi": round(float(rsi.iloc[-1]), 2), 
            "sma_20": round(float(closes.tail(20).mean()), 2)
        }

    def _format_for_ai(self, df: pd.DataFrame, analysis: Dict, smc_data: Dict) -> str:
        """
        Форматирует данные для отправки в AI (Gemini)
        """
        transcript = "=== MARKET DATA (Last 15 candles) ===\n"
        transcript += "Open | High | Low | Close | Volume\n"
        for _, r in df.tail(15).iterrows():
            transcript += f"{r['open']:.2f} | {r['high']:.2f} | {r['low']:.2f} | {r['close']:.2f} | {int(r['volume'])}\n"
        
        transcript += f"\n=== MARKET STRUCTURE ===\n"
        transcript += f"Trend: {smc_data.get('trend', 'NEUTRAL')}\n"
        
        # BOS/CHOCH
        if analysis['bos_choch']:
            transcript += "\nBOS/CHOCH Events:\n"
            for ev in analysis['bos_choch']:
                transcript += f"- {ev['type']} @ {ev['level']:.2f}\n"
        
        # Order Blocks
        if analysis['order_blocks']:
            transcript += "\nOrder Blocks:\n"
            for ob in analysis['order_blocks']:
                ob_type = ob.get('type', 'OB')
                strength = ob.get('strength', 0)
                transcript += f"- {ob_type} [{ob['bottom']:.2f}-{ob['top']:.2f}]"
                if strength > 0:
                    transcript += f" (strength: {strength})"
                transcript += "\n"
        
        # FVG (Fair Value Gaps)
        if analysis['fvg']:
            transcript += "\nFair Value Gaps:\n"
            for fvg in analysis['fvg']:
                fvg_type = fvg.get('type', 'FVG')
                gap_size = fvg.get('gap_size', 0)
                gap_percent = fvg.get('gap_percent', 0)
                transcript += f"- {fvg_type} [{fvg.get('bottom', 0):.2f}-{fvg.get('top', 0):.2f}] "
                transcript += f"(gap: ${gap_size:.2f} / {gap_percent:.2f}%)\n"
        
        # Liquidity (Support/Resistance)
        liquidity = smc_data.get('liquidity', [])
        if liquidity:
            transcript += "\nKey Liquidity Levels:\n"
            for liq in liquidity:
                liq_type = liq.get('type', 'LEVEL')
                strength = liq.get('strength', 1)
                transcript += f"- {liq_type} @ {liq['price']:.2f} (touches: {strength})\n"
        
        # Equal Highs/Lows
        liq_legacy = analysis['liquidity']
        if liq_legacy['EQH']:
            transcript += f"\nEqual Highs (Liquidity Above): {', '.join([f'{p:.2f}' for p in liq_legacy['EQH']])}\n"
        if liq_legacy['EQL']:
            transcript += f"Equal Lows (Liquidity Below): {', '.join([f'{p:.2f}' for p in liq_legacy['EQL']])}\n"
        
        # Indicators
        transcript += f"\n=== INDICATORS ===\n"
        transcript += f"RSI: {analysis['indicators']['rsi']}\n"
        transcript += f"SMA(20): {analysis['indicators']['sma_20']:.2f}\n"
        transcript += f"Support: {analysis['levels']['support']:.2f}\n"
        transcript += f"Resistance: {analysis['levels']['resistance']:.2f}\n"
        
        return transcript

calculator = TradingCalculator() 