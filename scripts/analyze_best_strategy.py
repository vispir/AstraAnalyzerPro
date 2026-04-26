#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализ бэктестов для поиска лучшей стратегии под проп-фирму Funding Pips"""

import json
import glob
import sys
from pathlib import Path
from typing import Dict, List
import pandas as pd

# Фикс кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def load_backtest(filepath: str) -> Dict:
    """Загрузка результатов бэктеста"""
    with open(filepath, 'r') as f:
        return json.load(f)

def analyze_for_prop_firm(data: Dict) -> Dict:
    """Анализ метрик для проп-фирмы"""
    summary = data.get('summary', {})
    prop = data.get('prop_firm', {})

    # Ключевые метрики для Funding Pips
    return {
        'strategy': data['params'].get('strategy', 'unknown'),
        'file': Path(data.get('file', '')).name if 'file' in data else '',
        'total_trades': summary.get('total_trades', 0),
        'win_rate': summary.get('win_rate', 0),
        'profit_factor': summary.get('profit_factor', 0),
        'max_drawdown_pct': summary.get('max_drawdown_pct', 0),
        'avg_rr': summary.get('avg_rr', 0),
        'net_pnl': summary.get('net_pnl', 0),
        'end_balance': summary.get('end_balance', 0),
        'roi_pct': ((summary.get('end_balance', 10000) - 10000) / 10000) * 100,
        # Проп-фирма метрики
        'pf_pass': prop.get('profit_factor_pass', False),
        'dd_pass': prop.get('max_dd_pass', False),
        'consistency_pass': prop.get('consistency_pass', False),
        'overall_pass': prop.get('overall_pass', False),
    }

def main():
    results_dir = Path('backtest_results')
    all_files = list(results_dir.glob('*.json'))

    print(f"Найдено {len(all_files)} файлов бэктестов\n")

    analyzed = []
    skipped = []
    for filepath in all_files:
        try:
            data = load_backtest(filepath)
            data['file'] = str(filepath)
            metrics = analyze_for_prop_firm(data)
            analyzed.append(metrics)
        except Exception as e:
            skipped.append(filepath.name)
            continue

    if skipped:
        print(f"Пропущено {len(skipped)} поврежденных файлов\n")

    df = pd.DataFrame(analyzed)

    # Фильтр: только стратегии с положительным PnL и прохождением проп-теста
    prop_ready = df[
        (df['net_pnl'] > 0) &
        (df['overall_pass'] == True)
    ].copy()

    if len(prop_ready) == 0:
        print("Нет стратегий, полностью прошедших проп-тест\n")
        print("Топ-5 по Profit Factor (все стратегии):")
        top_pf = df.nlargest(5, 'profit_factor')
    else:
        print(f"Найдено {len(prop_ready)} стратегий, прошедших проп-тест\n")
        print("Топ-5 стратегий для Funding Pips:")
        top_pf = prop_ready.nlargest(5, 'profit_factor')

    for idx, row in top_pf.iterrows():
        print(f"\n{'='*70}")
        print(f"Стратегия: {row['strategy']}")
        print(f"Файл: {row['file']}")
        print(f"  ROI: {row['roi_pct']:.2f}%")
        print(f"  Profit Factor: {row['profit_factor']:.3f}")
        print(f"  Win Rate: {row['win_rate']*100:.1f}%")
        print(f"  Max DD: {row['max_drawdown_pct']:.2f}%")
        print(f"  Avg R:R: {row['avg_rr']:.2f}")
        print(f"  Trades: {row['total_trades']}")
        print(f"  Net PnL: ${row['net_pnl']:.2f}")
        print(f"  Проп-тест: {'PASS' if row['overall_pass'] else 'FAIL'}")

    print(f"\n{'='*70}")
    print("\n📊 Сводная статистика по стратегиям:")
    strategy_stats = df.groupby('strategy').agg({
        'profit_factor': 'mean',
        'win_rate': 'mean',
        'max_drawdown_pct': 'mean',
        'roi_pct': 'mean',
        'overall_pass': 'sum'
    }).round(3)
    print(strategy_stats.to_string())

    # Лучшая стратегия
    if len(prop_ready) > 0:
        best = prop_ready.loc[prop_ready['profit_factor'].idxmax()]
        print(f"\nЛУЧШАЯ СТРАТЕГИЯ ДЛЯ FUNDING PIPS:")
        print(f"   {best['strategy']}")
        print(f"   Profit Factor: {best['profit_factor']:.3f}")
        print(f"   ROI: {best['roi_pct']:.2f}%")
        print(f"   Max DD: {best['max_drawdown_pct']:.2f}%")
        print(f"   Файл: {best['file']}")

if __name__ == '__main__':
    main()
