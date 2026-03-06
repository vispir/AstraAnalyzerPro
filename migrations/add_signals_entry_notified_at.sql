-- Время активации входа (когда цена достигла entry и отправили уведомление).
-- Используется Manager: не вызывать LLM первые 30 мин после входа.
ALTER TABLE signals
ADD COLUMN IF NOT EXISTS entry_notified_at TIMESTAMPTZ;

COMMENT ON COLUMN signals.entry_notified_at IS 'Когда отправлено уведомление «Вход достигнут» (entry_notified=True)';
