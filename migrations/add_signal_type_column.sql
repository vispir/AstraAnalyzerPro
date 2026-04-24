-- Migration: Add signal_type column to mt5_signals
-- This allows distinguishing between different signal types:
-- - session_breakout (LONG strategy)
-- - reversal_type1 (SHORT: Historical High Reversal)
-- - reversal_type2 (SHORT: Local Reversal After Strong Move)

ALTER TABLE mt5_signals
ADD COLUMN signal_type TEXT DEFAULT 'session_breakout'
CHECK (signal_type IN ('session_breakout', 'reversal_type1', 'reversal_type2'));

-- Create index for filtering by signal type
CREATE INDEX idx_mt5_signals_signal_type ON mt5_signals(signal_type);

-- Add comment for documentation
COMMENT ON COLUMN mt5_signals.signal_type IS 'Type of trading signal: session_breakout (LONG), reversal_type1 (SHORT Type1), reversal_type2 (SHORT Type2)';
