#!/usr/bin/env python3
"""
Скрипт для отладки H1 pivots и breaks.
Запуск: python debug_h1_pivots.py
Требует: сервер не нужен, но нужны env переменные для OANDA/twelvedata.
"""
import logging
import sys
import pandas as pd

# Настройка логирования — показываем DEBUG
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

def main():
    from services.oanda_service import oanda_service
    
    print("=" * 60)
    print("DEBUG H1: Загружаем 300 свечей H1...")
    print("=" * 60)
    
    res = oanda_service.get_candles('H1', limit=300)
    if 'error' in res:
        print("Ошибка:", res['error'])
        return 1
    
    candles = res.get('candles', [])
    if len(candles) < 50:
        print(f"Мало свечей: {len(candles)}")
        return 1
    
    df = pd.DataFrame(candles)
    df.columns = [str(c).lower() for c in df.columns]
    
    print(f"Загружено {len(df)} свечей")
    print(f"Диапазон: {df['low'].min():.2f} - {df['high'].max():.2f}")
    print(f"Последний close: {df['close'].iloc[-1]:.2f}")
    print()
    
    from services.smc_detector import smc_detector
    
    print("Запуск analyze() с timeframe=H1...")
    print()
    result = smc_detector.analyze(df, timeframe='H1', zone_lookback=0)
    
    print()
    print("=" * 60)
    print("РЕЗУЛЬТАТ:")
    print("=" * 60)
    print("trend:", result.get('trend', 'N/A'))
    print("swing_pivot_high:", result.get('swing_pivot_high', 0))
    print("swing_pivot_low:", result.get('swing_pivot_low', 0))
    print("key_levels:", result.get('advanced', {}).get('key_levels', {}))
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
