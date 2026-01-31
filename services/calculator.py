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

logger = logging.getLogger(__name__)


class TradingCalculator:
    def __init__(self):
        self.smc_detector = smc_detector

    # --- БЛОК 1: ТОРГОВЫЕ РАСЧЕТЫ (РИСК И ЛОТЫ) ---

    def calculate_trade_params(self, entry: float, sl: float, tp: float, balance: float, risk_percent: float = 0.5) -> Dict:
        """
        Расчет параметров сделки: лот, R:R и валидация риска.
        
        Args:
            entry: Точка входа
            sl: Stop Loss
            tp: Take Profit
            balance: Баланс счета
            risk_percent: Процент риска на сделку (по умолчанию 0.5%)
        """
        try:
            if not all([entry, sl, tp]) or entry == sl:
                return {"error": "Некорректные уровни"}

            stop_points = abs(entry - sl)
            profit_points = abs(tp - entry)
            rr_ratio = round(profit_points / stop_points, 2)

            # Базовая цель риска (используем переданный процент)
            risk_target_usd = balance * (risk_percent / 100)
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
                    # Округляем вниз (math.floor) - без максимального лота
                    calculated = math.floor(raw_lot * 100) / 100
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
        Анализ рынка с использованием SMC детектора.
        Нормализует колонки и запускает полный анализ структур.
        
        Args:
            candles: Список свечей (OHLC)
            
        Returns:
            Dict с полным анализом для AI
        """
        if not candles or len(candles) < 30:
            return {"error": "Insufficient data"}

        df = pd.DataFrame(candles)
        
        # Убеждаемся что колонки в lowercase
        df.columns = [str(c).lower() for c in df.columns]

        try:
            # 1. Используем новый SMC детектор для всех структур
            smc_data = self.smc_detector.analyze(df)
            
            # 2. Считаем индикаторы
            indicators = self._calculate_indicators(df)
            
            # 3. Формируем анализ с полными данными (приоритет HEAD)
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
                "indicators": indicators,
                "advanced": smc_data.get('advanced', {}),  # Добавляем advanced данные
                "choch": smc_data.get('choch', []),
                "bos": smc_data.get('bos', []),
                "eqh": smc_data.get('eqh', []),
                "eql": smc_data.get('eql', []),
                
                # ============================================================
                # BOS/CHoCH для ВИЗУАЛИЗАЦИИ на графике (все уровни)
                # ============================================================
                "all_internal_bos": smc_data.get('all_internal_bos', []),
                "all_internal_choch": smc_data.get('all_internal_choch', []),
                "all_swing_bos": smc_data.get('all_swing_bos', []),
                "all_swing_choch": smc_data.get('all_swing_choch', []),
                
                # Свежие сигналы (bars_ago <= FRESH_SIGNAL_BARS)
                "internal_bos": smc_data.get('internal_bos', []),
                "internal_choch": smc_data.get('internal_choch', []),
                "swing_bos": smc_data.get('swing_bos', []),
                "swing_choch": smc_data.get('swing_choch', []),
                
                # Тренды
                "internal_trend": smc_data.get('internal_trend', 'NEUTRAL'),
                "swing_trend": smc_data.get('swing_trend', 'NEUTRAL'),
                
                # Pivot уровни
                "internal_pivot_high": smc_data.get('internal_pivot_high', 0.0),
                "internal_pivot_low": smc_data.get('internal_pivot_low', 0.0),
                "swing_pivot_high": smc_data.get('swing_pivot_high', 0.0),
                "swing_pivot_low": smc_data.get('swing_pivot_low', 0.0)
            }
            
            # 4. Формат для AI
            analysis["ai_transcript"] = self._format_for_ai(df, analysis, smc_data)
            
            return analysis

        except Exception as e:
            logger.error(f"Error in SMC analysis: {str(e)}")
            return {"error": f"Ошибка анализа: {str(e)}"}
    
    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ---
    
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
        Расчёт технических индикаторов (RSI, SMA) с использованием сглаживания Уайлдера
        """
        closes = df['close']
        delta = closes.diff()
        
        # Разделяем прибыли и убытки
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        # Используем RMA (Running Moving Average), как в TradingView
        # Это экспоненциальное среднее с alpha = 1/period
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        
        # Рассчитываем RS и RSI
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        
        # Получаем последнее значение и обрабатываем крайние случаи (NaN/Inf)
        last_rsi = rsi.iloc[-1]
        if np.isnan(last_rsi):
            # Если данных меньше 14, возвращаем нейтральные 50
            # Если потерь 0, а прибыли есть - это RSI 100
            if not avg_loss.empty and avg_loss.iloc[-1] == 0 and avg_gain.iloc[-1] > 0:
                last_rsi = 100.0
            else:
                last_rsi = 50.0
        
        return {
            "rsi": round(float(last_rsi), 2), 
            "sma_20": round(float(closes.tail(20).mean()), 2)
        }

    def _format_for_ai(self, df: pd.DataFrame, analysis: Dict, smc_data: Dict) -> str:
        """
        Форматирует данные для отправки в AI (Gemini) с полной детализацией
        """
        transcript = "=== MARKET DATA (Last 15 candles) ===\n"
        transcript += "Open | High | Low | Close | Volume\n"
        for _, r in df.tail(15).iterrows():
            volume_str = f"{int(r['volume'])}" if 'volume' in r and pd.notna(r['volume']) else "N/A"
            transcript += f"{r['open']:.2f} | {r['high']:.2f} | {r['low']:.2f} | {r['close']:.2f} | {volume_str}\n"
        
        transcript += f"\n=== MARKET STRUCTURE ===\n"
        transcript += f"Trend: {smc_data.get('trend', 'NEUTRAL')}\n"
        
        # BOS/CHOCH Events
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
        
        # Advanced SMC Data (Critical for precise entries/exits)
        if 'advanced' in smc_data:
            advanced = smc_data['advanced']
            key_levels = advanced.get('key_levels', {})
            structure_points = advanced.get('structure_points', {})
            range_data = advanced.get('range', {})
            
            transcript += f"\n=== CRITICAL LEVELS (for precise SL/TP) ===\n"
            transcript += f"Daily High (DH): {key_levels.get('DH', 0):.2f}\n"
            transcript += f"Daily Low (DL): {key_levels.get('DL', 0):.2f}\n"
            transcript += f"Previous Day High (PDH): {key_levels.get('PDH', 0):.2f}\n"
            transcript += f"Previous Day Low (PDL): {key_levels.get('PDL', 0):.2f}\n"
            transcript += f"Equilibrium (50%): {key_levels.get('Equilibrium_Price', 0):.2f}\n"
            transcript += f"Current Zone: {key_levels.get('Current_Zone', 'UNKNOWN')}\n"
            
            transcript += f"\nStructural Swing Points:\n"
            transcript += f"- Nearest Swing High: {structure_points.get('nearest_swing_high', 0):.2f}\n"
            transcript += f"- Nearest Swing Low: {structure_points.get('nearest_swing_low', 0):.2f}\n"
            
            # Дополнительные свинги для контекста
            all_highs = structure_points.get('all_swing_highs', [])
            all_lows = structure_points.get('all_swing_lows', [])
            if all_highs:
                transcript += f"- Recent Swing Highs: {', '.join([f'{h:.2f}' for h in all_highs[-3:]])}\n"
            if all_lows:
                transcript += f"- Recent Swing Lows: {', '.join([f'{l:.2f}' for l in all_lows[-3:]])}\n"
            
            transcript += f"\nRange (Last 50 bars):\n"
            transcript += f"- High: {range_data.get('high', 0):.2f}\n"
            transcript += f"- Low: {range_data.get('low', 0):.2f}\n"
            transcript += f"- Size: ${range_data.get('size', 0):.2f}\n"
        
        # Indicators
        transcript += f"\n=== INDICATORS ===\n"
        transcript += f"RSI: {analysis['indicators']['rsi']}\n"
        transcript += f"SMA(20): {analysis['indicators']['sma_20']:.2f}\n"
        transcript += f"Support: {analysis['levels']['support']:.2f}\n"
        transcript += f"Resistance: {analysis['levels']['resistance']:.2f}\n"
        
        return transcript

    @staticmethod
    def calculate_breakeven(entry: float, sl: float) -> float:
        """
        Расчет уровня безубыточности (breakeven) для частичного закрытия позиции
        """
        stop_points = abs(entry - sl)
        return round(entry + (stop_points * 0.5), 2) if entry > sl else round(entry - (stop_points * 0.5), 2)


calculator = TradingCalculator()
