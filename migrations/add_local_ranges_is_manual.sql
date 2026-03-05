-- =============================================
-- МИГРАЦИЯ: Колонка is_manual в local_ranges (Supabase)
-- =============================================
-- Ручные диапазоны задаются трейдером через TG бота (Astra Analyzer Pro).
-- Пока активен ручной диапазон, автодетектор не создаёт новый.

ALTER TABLE local_ranges
ADD COLUMN IF NOT EXISTS is_manual BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN local_ranges.is_manual IS 'True если диапазон задан вручную через TG бота';
