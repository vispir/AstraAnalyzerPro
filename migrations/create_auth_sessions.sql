-- =============================================
-- МИГРАЦИЯ: Таблица auth_sessions для входа через бота
-- =============================================
-- Эта таблица хранит сессии авторизации через t.me/bot?start=TOKEN
-- Фронтенд генерирует токен, отправляет юзера в бота, 
-- бот обрабатывает токен и закрывает сессию

CREATE TABLE IF NOT EXISTS auth_sessions (
    token TEXT PRIMARY KEY,                    -- UUID токен авторизации
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | completed | expired
    tg_user_id BIGINT,                         -- Telegram User ID (после завершения)
    created_at TIMESTAMPTZ DEFAULT NOW(),      -- Время создания сессии
    completed_at TIMESTAMPTZ,                  -- Время завершения авторизации
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '10 minutes') -- Автоэкспирация через 10 минут
);

-- Индекс для быстрого поиска по токену
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token);

-- Индекс для быстрого поиска по статусу
CREATE INDEX IF NOT EXISTS idx_auth_sessions_status ON auth_sessions(status);

-- Включаем RLS (Row Level Security)
ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY;

-- Политика: anon может читать и создавать сессии
CREATE POLICY "Allow anonymous to create sessions" ON auth_sessions
    FOR INSERT TO anon
    WITH CHECK (true);

CREATE POLICY "Allow anonymous to read sessions" ON auth_sessions
    FOR SELECT TO anon
    USING (true);

CREATE POLICY "Allow anonymous to update sessions" ON auth_sessions
    FOR UPDATE TO anon
    USING (true);

-- Функция для автоматической очистки истекших сессий (опционально)
CREATE OR REPLACE FUNCTION cleanup_expired_auth_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM auth_sessions
    WHERE expires_at < NOW()
    AND status = 'pending';
END;
$$ LANGUAGE plpgsql;

-- Комментарии для документации
COMMENT ON TABLE auth_sessions IS 'Сессии авторизации через Telegram Bot (альтернатива Login Widget)';
COMMENT ON COLUMN auth_sessions.token IS 'UUID токен для авторизации через t.me/bot?start=TOKEN';
COMMENT ON COLUMN auth_sessions.status IS 'Статус сессии: pending (ожидание), completed (завершена), expired (истекла)';
COMMENT ON COLUMN auth_sessions.tg_user_id IS 'Telegram User ID после успешной авторизации';
