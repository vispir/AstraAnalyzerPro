@echo off
setlocal

REM Уничтожаем следы старого ключа в этой сессии
set "ANTHROPIC_API_KEY="

REM Настройки OmniRoute (заменил localhost на 127.0.0.1)
set "ANTHROPIC_AUTH_TOKEN=sk-3ab65b353ed34c2f-9814d3-8b070016"
set "ANTHROPIC_BASE_URL=http://127.0.0.1:20128/v1"

REM Настройка модели как в видео
set "ANTHROPIC_MODEL=kr/claude-sonnet-4.5"
set "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1"

echo Запуск...
claude
endlocal