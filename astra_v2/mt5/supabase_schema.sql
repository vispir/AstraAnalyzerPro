-- Supabase Table Schema for MT5 Signals
-- Run this in Supabase SQL Editor

CREATE TABLE mt5_signals (
    id BIGSERIAL PRIMARY KEY,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry NUMERIC(10, 2) NOT NULL,
    sl NUMERIC(10, 2) NOT NULL,
    tp NUMERIC(10, 2) NOT NULL,
    session TEXT NOT NULL CHECK (session IN ('asian', 'london', 'ny')),
    risk_usd NUMERIC(10, 2) NOT NULL DEFAULT 165,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'active', 'closed', 'cancelled')),
    exit_price NUMERIC(10, 2),
    pnl NUMERIC(10, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for faster queries
CREATE INDEX idx_mt5_signals_status ON mt5_signals(status);
CREATE INDEX idx_mt5_signals_created_at ON mt5_signals(created_at DESC);

-- Enable Row Level Security (RLS)
ALTER TABLE mt5_signals ENABLE ROW LEVEL SECURITY;

-- Create policy to allow all operations (adjust based on your security needs)
CREATE POLICY "Allow all operations on mt5_signals" ON mt5_signals
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Create function to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger to auto-update updated_at
CREATE TRIGGER update_mt5_signals_updated_at BEFORE UPDATE ON mt5_signals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions (if using service role)
GRANT ALL ON mt5_signals TO anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE mt5_signals_id_seq TO anon, authenticated;
