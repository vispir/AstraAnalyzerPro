from __future__ import annotations

from typing import Optional
import pandas as pd
import numpy as np

from astra_v2 import config
from astra_v2.core.signal_gate import Signal
from .base import StrategyContext


class ShortReversalStrategy:
    """
    SHORT Reversal Strategy: Type 1 (Historical High) + Type 2 (Local Reversal)

    Type 1: Reversal After Historical High
    - New high over last 5 H4 bars
    - 1 H4 bar closes lower
    - M15 breaks below previous M15 low

    Type 2: Local Reversal After Strong Move
    - Price rises 2+ ATR in last 3 H4 bars
    - Current H4 closes lower
    - M15 breaks below previous M15 low

    Filter: H4 close < EMA20 (SHORT only in downtrend)
    Risk: $158, TP: 5.5R
    Step Trailing: 2R->1R, 3R->2R, 4R->3R, 5R->4R
    """

    strategy_id = "short_reversal_v1"

    # Type 1 parameters
    TYPE1_LOOKBACK_H4_BARS = 5
    TYPE1_H4_REVERSAL_BARS = 1

    # Type 2 parameters
    TYPE2_H4_LOOKBACK = 3
    TYPE2_ATR_MULTIPLIER = 2.0

    # Common parameters
    H4_EMA_PERIOD = 20
    ATR_PERIOD = 14
    ATR_BUFFER = 0.5
    TP_RR = 5.5
    RISK_PER_TRADE = 158

    def __init__(self):
        self.type1_reversal_active = False
        self.type1_reversal_h4_high = None
        self.type2_reversal_active = False
        self.type2_reversal_h4_high = None

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()

        return atr

    def calculate_ema(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Calculate Exponential Moving Average"""
        return df['close'].ewm(span=period, adjust=False).mean()

    def check_type1_reversal(
        self,
        h4_bars: pd.DataFrame,
        current_h4: pd.Series,
        prev_h4: pd.Series
    ) -> Optional[float]:
        """
        Check Type 1: Reversal After Historical High
        Returns H4 high if reversal detected, None otherwise
        """
        if len(h4_bars) < self.TYPE1_LOOKBACK_H4_BARS + 2:
            return None

        # Get lookback highs (excluding current bar)
        lookback_highs = h4_bars.iloc[-self.TYPE1_LOOKBACK_H4_BARS-1:-1]['high']
        historical_high = lookback_highs.max()

        # Check if current H4 made new high
        if current_h4['high'] > historical_high:
            # Check if current H4 closed lower than previous
            if current_h4['close'] < prev_h4['close']:
                return float(current_h4['high'])

        return None

    def check_type2_reversal(
        self,
        h4_bars: pd.DataFrame,
        current_h4: pd.Series,
        prev_h4: pd.Series,
        h4_atr: float
    ) -> Optional[float]:
        """
        Check Type 2: Local Reversal After Strong Move
        Returns H4 high if reversal detected, None otherwise
        """
        if len(h4_bars) < self.TYPE2_H4_LOOKBACK + 2:
            return None

        if np.isnan(h4_atr) or h4_atr <= 0:
            return None

        # Get lookback bars (excluding current)
        lookback_bars = h4_bars.iloc[-self.TYPE2_H4_LOOKBACK-1:-1]

        # Calculate price change
        price_change = current_h4['high'] - lookback_bars['low'].min()

        # Check if price rose by 2+ ATR
        if price_change >= self.TYPE2_ATR_MULTIPLIER * h4_atr:
            # Check if current H4 closed lower than previous
            if current_h4['close'] < prev_h4['close']:
                return float(current_h4['high'])

        return None

    def check_m15_entry(
        self,
        m15_bars: pd.DataFrame,
        current_m15: pd.Series
    ) -> bool:
        """
        Check M15 entry: close below previous M15 low
        """
        if len(m15_bars) < 2:
            return False

        prev_m15 = m15_bars.iloc[-2]

        # Entry: M15 close below previous M15 low
        if current_m15['close'] < prev_m15['low']:
            return True

        return False

    def analyze(self, context: StrategyContext) -> Optional[Signal]:
        """
        Main analysis method called by the engine
        """
        # Check if H4 bars available
        if context.h4_bars is None or len(context.h4_bars) < self.TYPE1_LOOKBACK_H4_BARS + 2:
            return None

        h4_bars = context.h4_bars
        current_h4 = h4_bars.iloc[-1]
        prev_h4 = h4_bars.iloc[-2]

        # Calculate H4 EMA20
        if 'ema20' not in h4_bars.columns:
            h4_bars['ema20'] = self.calculate_ema(h4_bars, self.H4_EMA_PERIOD)

        # H4 EMA20 Filter: SHORT only if price BELOW EMA20
        if pd.isna(current_h4['ema20']):
            return None

        if current_h4['close'] >= current_h4['ema20']:
            # Reset reversal flags if price above EMA20
            self.type1_reversal_active = False
            self.type2_reversal_active = False
            return None

        # Calculate H4 ATR
        if 'atr' not in h4_bars.columns:
            h4_bars['atr'] = self.calculate_atr(h4_bars, self.ATR_PERIOD)

        h4_atr = current_h4.get('atr', np.nan)

        # Check Type 1 reversal
        if not self.type1_reversal_active:
            type1_high = self.check_type1_reversal(h4_bars, current_h4, prev_h4)
            if type1_high is not None:
                self.type1_reversal_active = True
                self.type1_reversal_h4_high = type1_high

        # Check Type 2 reversal
        if not self.type2_reversal_active:
            type2_high = self.check_type2_reversal(h4_bars, current_h4, prev_h4, h4_atr)
            if type2_high is not None:
                self.type2_reversal_active = True
                self.type2_reversal_h4_high = type2_high

        # Check M15 entry if reversal is active
        if not (self.type1_reversal_active or self.type2_reversal_active):
            return None

        # Get M15 bars
        m15_bars = context.bars_so_far
        if len(m15_bars) < 2:
            return None

        current_m15 = context.current_bar

        # Check M15 entry trigger
        if not self.check_m15_entry(m15_bars, current_m15):
            return None

        # Calculate M15 ATR for SL calculation
        if 'atr' not in m15_bars.columns:
            m15_bars['atr'] = self.calculate_atr(m15_bars, self.ATR_PERIOD)

        m15_atr = current_m15.get('atr', h4_atr)  # Fallback to H4 ATR
        if np.isnan(m15_atr) or m15_atr <= 0:
            return None

        # Determine signal type and reversal high
        if self.type1_reversal_active:
            signal_type = "Type1_HistoricalHigh"
            reversal_high = self.type1_reversal_h4_high
            self.type1_reversal_active = False  # Reset after entry
        else:
            signal_type = "Type2_LocalReversal"
            reversal_high = self.type2_reversal_h4_high
            self.type2_reversal_active = False  # Reset after entry

        # Build SHORT signal
        entry_price = float(current_m15['close'])
        stop_loss = reversal_high + self.ATR_BUFFER * m15_atr
        risk = stop_loss - entry_price

        if risk <= 0:
            return None

        take_profit = entry_price - risk * self.TP_RR

        # Create signal
        signal = Signal(
            strategy_id=self.strategy_id,
            direction="SHORT",
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=0.75,  # Base confidence for reversal patterns
            timestamp=context.now,
            metadata={
                "signal_type": signal_type,
                "reversal_high": reversal_high,
                "risk": risk,
                "tp_rr": self.TP_RR,
                "h4_ema20": float(current_h4['ema20']),
                "h4_close": float(current_h4['close']),
                "atr": m15_atr,
            }
        )

        return signal
