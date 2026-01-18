"""
Калькулятор для торговых расчетов
"""
import logging
from typing import Dict, Optional

from config.settings import MAX_LOT_SIZE, RISK_PERCENT

logger = logging.getLogger(__name__)


class TradingCalculator:
    """Калькулятор для расчета лотов, R:R и рисков"""
    
    @staticmethod
    def calculate_trade_params(
        entry: float,
        sl: float,
        tp: float,
        balance: float,
        max_lot: float = MAX_LOT_SIZE,
        risk_percent: float = RISK_PERCENT
    ) -> Dict:
        """
        Расчет параметров сделки
        
        Args:
            entry: Точка входа
            sl: Stop Loss
            tp: Take Profit
            balance: Баланс счета
            max_lot: Максимальный размер лота
            risk_percent: Процент риска от баланса
            
        Returns:
            Dict с параметрами сделки
        """
        try:
            # Валидация
            if not all([entry, sl, tp]) or entry == sl:
                return {"error": "Некорректные входные данные"}
            
            # Расчет пунктов
            stop_points = abs(entry - sl)
            profit_points = abs(tp - entry)
            
            # Расчет R:R
            rr_ratio = round(profit_points / stop_points, 2)
            
            # Расчет лота
            lot = 0.0
            if rr_ratio >= 2.0:  # Минимум 1:2 R:R
                risk_target_usd = balance * risk_percent
                raw_lot = risk_target_usd / (stop_points * 100)
                
                if raw_lot < 0.01:
                    if stop_points <= (balance * 0.01):
                        lot = 0.01
                else:
                    lot = min(max_lot, round(raw_lot, 2))
            
            # Определение направления
            direction = "BUY" if entry > sl else "SELL"
            
            # Расчет потенциальных прибыли/убытков
            potential_loss = stop_points * lot * 100
            potential_profit = profit_points * lot * 100
            
            return {
                "success": True,
                "rr_ratio": rr_ratio,
                "stop_points": round(stop_points, 2),
                "profit_points": round(profit_points, 2),
                "lot": round(lot, 2),
                "direction": direction,
                "valid": rr_ratio >= 2.0,
                "potential_loss": round(potential_loss, 2),
                "potential_profit": round(potential_profit, 2),
                "risk_reward_usd": f"-${round(potential_loss, 2)} / +${round(potential_profit, 2)}"
            }
            
        except Exception as e:
            logger.error(f"Error calculating trade params: {str(e)}")
            return {"error": f"Ошибка расчета: {str(e)}"}
    
    @staticmethod
    def calculate_breakeven(entry: float, sl: float, commission: float = 0) -> float:
        """
        Расчет уровня безубытка
        
        Args:
            entry: Точка входа
            sl: Stop Loss
            commission: Комиссия (в пунктах)
            
        Returns:
            Уровень безубытка
        """
        stop_points = abs(entry - sl)
        be_distance = stop_points * 0.5 + commission  # 50% от SL + комиссия
        
        if entry > sl:  # BUY
            return round(entry + be_distance, 2)
        else:  # SELL
            return round(entry - be_distance, 2)
    
    @staticmethod
    def calculate_position_size(
        account_balance: float,
        risk_percent: float,
        stop_loss_points: float,
        point_value: float = 100  # Для золота: $100 на 1 пункт для 1 лота
    ) -> float:
        """
        Расчет размера позиции на основе риска
        
        Args:
            account_balance: Баланс счета
            risk_percent: Процент риска (например, 0.005 для 0.5%)
            stop_loss_points: Размер SL в пунктах
            point_value: Стоимость пункта
            
        Returns:
            Размер позиции в лотах
        """
        risk_amount = account_balance * risk_percent
        position_size = risk_amount / (stop_loss_points * point_value)
        return round(position_size, 2)
    
    @staticmethod
    def calculate_daily_drawdown(
        start_balance: float,
        current_equity: float,
        daily_limit: float
    ) -> Dict:
        """
        Расчет дневной просадки
        
        Args:
            start_balance: Начальный баланс
            current_equity: Текущий эквити
            daily_limit: Лимит дневной просадки
            
        Returns:
            Dict с информацией о просадке
        """
        daily_loss = max(0, start_balance - current_equity)
        percent = min(100, (daily_loss / daily_limit) * 100)
        
        return {
            "daily_loss": round(daily_loss, 2),
            "percent": round(percent, 2),
            "remaining": round(daily_limit - daily_loss, 2),
            "warning": percent > 80,
            "critical": percent >= 90
        }


# Глобальный экземпляр калькулятора
calculator = TradingCalculator()
