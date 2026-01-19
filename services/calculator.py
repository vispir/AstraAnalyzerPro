"""
Astra Analyzer Pro - Quantitative Analysis Module
Математический расчет Price Action структур (BOS, FVG, Order Blocks) и нормализация для ИИ.
"""
import logging
import pandas as pd
import numpy as np
import math
from typing import Dict, List, Optional
from services.smc_detector import smc_detector
from config.settings import MAX_LOT_SIZE, RISK_PERCENT

logger = logging.getLogger(__name__)

class TradingCalculator:
    def __init__(self):
        self.smc_detector = smc_detector

    # --- БЛОК 1: ТОРГОВЫЕ РАСЧЕТЫ (РИСК И ЛОТЫ) ---

    def calculate_trade_params(self, entry: float, sl: float, tp: float, balance: float) -> Dict:
        """
        Расчет параметров сделки: лот, R:R и валидация риска.
        """
        try:
            if not all([entry, sl, tp]) or entry == sl:
                return {"error": "Некорректные уровни"}

            stop_points = abs(entry - sl)
            profit_points = abs(tp - entry)
            rr_ratio = round(profit_points / stop_points, 2)

            # Базовая цель риска 0.5%
            risk_target_usd = balance * RISK_PERCENT
            raw_lot = risk_target_usd / (stop_points * 100)

            lot = "0.00"
            # Логика блокировки по R:R (минимум 1:2)
            if rr_ratio < 2.0:
                lot = "0.00"
            else:
                if raw_lot < 0.01:
                    # Стратегическое исключение: если RR хороший и риск лотом 0.01 не выше 1%
                    if stop_points <= (balance * 0.01):
                        lot = "0.01"
                    else:
                        lot = "0.00"
                else:
                    # Ограничиваем максимальный лот и округляем вниз (math.floor)
                    calculated = min(MAX_LOT_SIZE, math.floor(raw_lot * 100) / 100)
                    lot = f"{calculated:.2f}"

            return {
                "success": True,
                "rr_ratio": rr_ratio,
                "stop_points": round(stop_points, 2),
                "lot": lot,
                "direction": "BUY" if entry > sl else "SELL"
            }
        except Exception as e:
            logger.error(f"Error in params calc: {e}")
            return {"error": str(e)}

    # --- БЛОК 2: АНАЛИЗ РЫНОЧНЫХ СТРУКТУР ---

    def get_market_analysis(self, candles: List[Dict]) -> Dict:
        """
        Главный метод анализа. Нормализует колонки и запускает SMC детектор.
        """
        if not candles or len(candles) < 30:
            return {"error": "Insufficient data"}

        df = pd.DataFrame(candles)
        
        # КРИТИЧЕСКИЙ ФИКС: Переводим колонки в 'Title Case' (High, Low, Close)
        df.columns = [str(c).title() for c in df.columns]

        try:
            # 1. Запуск детектора Роберта
            smc_data = self.smc_detector.analyze(df)
            
            # 2. Технические индикаторы
            indicators = self._calculate_indicators(df)
            
            # 3. Сборка объекта анализа
            analysis = {
                "order_blocks": smc_data.get('order_blocks', []),
                "fvg": smc_data.get('fvg', []),
                "liquidity": self._format_liquidity_legacy(smc_data),
                "bos_choch": self._format_bos_choch_legacy(smc_data),
                "trend": smc_data.get('trend', 'NEUTRAL'),
                "levels": {
                    "resistance": float(df['High'].max()),
                    "support": float(df['Low'].min())
                },
                "indicators": indicators
            }
            
            # 4. Создание транскрипта для Gemini
            analysis["ai_transcript"] = self._format_for_ai(df, analysis, smc_data)
            
            return analysis

        except Exception as e:
            logger.error(f"Error in SMC analysis: {str(e)}")
            return {"error": f"Ошибка анализа: {str(e)}"}

    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ---

    def _calculate_indicators(self, df: pd.DataFrame) -> Dict:
        closes = df['Close']
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        
        return {
            "rsi": round(float(rsi.fillna(50).iloc[-1]), 2), 
            "sma_20": round(float(closes.tail(20).mean()), 2)
        }

    def _format_liquidity_legacy(self, smc_data: Dict) -> Dict:
        eqh_prices = [level['price'] for level in smc_data.get('eqh', [])]
        eql_prices = [level['price'] for level in smc_data.get('eql', [])]
        return {"EQH": eqh_prices, "EQL": eql_prices}
    
    def _format_bos_choch_legacy(self, smc_data: Dict) -> List[Dict]:
        events = []
        for choch in smc_data.get('choch', []):
            events.append({"type": choch.get('type', 'CHOCH'), "level": choch.get('price', 0)})
        for bos in smc_data.get('bos', []):
            events.append({"type": bos.get('type', 'BOS'), "level": bos.get('price', 0)})
        return events

    def _format_for_ai(self, df: pd.DataFrame, analysis: Dict, smc_data: Dict) -> str:
        transcript = "=== MARKET DATA (Last 15 candles) ===\n"
        transcript += "Open | High | Low | Close | Volume\n"
        for _, r in df.tail(15).iterrows():
            transcript += f"{r['Open']:.2f} | {r['High']:.2f} | {r['Low']:.2f} | {r['Close']:.2f} | {int(r['Volume'])}\n"
        
        transcript += f"\n=== MARKET STRUCTURE ===\n"
        transcript += f"Trend: {smc_data.get('trend', 'NEUTRAL')}\n"
        
        if analysis['bos_choch']:
            transcript += "\nBOS/CHOCH Events:\n"
            for ev in analysis['bos_choch']:
                transcript += f"- {ev['type']} @ {ev['level']:.2f}\n"
        
        if analysis['order_blocks']:
            transcript += "\nOrder Blocks:\n"
            for ob in analysis['order_blocks']:
                transcript += f"- {ob.get('type', 'OB')} [{ob['bottom']:.2f}-{ob['top']:.2f}]\n"
        
        if analysis['fvg']:
            transcript += "\nFair Value Gaps:\n"
            # Исправленная строка цикла (использовано 'in' вместо '=')
            for fvg in analysis['fvg'][-1:]: 
                transcript += f"- {fvg.get('type', 'FVG')} [{fvg.get('bottom', 0):.2f}-{fvg.get('top', 0):.2f}]\n"
        
        transcript += f"\n=== INDICATORS ===\n"
        transcript += f"RSI: {analysis['indicators']['rsi']} | SMA(20): {analysis['indicators']['sma_20']:.2f}\n"
        transcript += f"Support: {analysis['levels']['support']:.2f} | Resistance: {analysis['levels']['resistance']:.2f}\n"
        
        return transcript

    @staticmethod
    def calculate_breakeven(entry: float, sl: float) -> float:
        stop_points = abs(entry - sl)
        return round(entry + (stop_points * 0.5), 2) if entry > sl else round(entry - (stop_points * 0.5), 2)

calculator = TradingCalculator()