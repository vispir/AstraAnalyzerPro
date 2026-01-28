import React, { useState, useEffect, useRef, memo, useCallback } from 'react';
import { User, Lock, X } from 'lucide-react';
import axios from 'axios';

// ========== ГЛОБАЛЬНЫЙ SINGLETON - МАКСИМАЛЬНАЯ ЗАЩИТА ==========
// Этот флаг живет на уровне window и предотвращает ВСЕ попытки повторной инициализации
if (typeof window !== 'undefined') {
  window.__TELEGRAM_WIDGET_INITIALIZED__ = window.__TELEGRAM_WIDGET_INITIALIZED__ || false;
  window.__TELEGRAM_AUTH_IN_PROGRESS__ = window.__TELEGRAM_AUTH_IN_PROGRESS__ || false;
}

// --- КОМПОНЕНТ-ОБЕРТКА ДЛЯ ТВОЕГО КОДА ТЕЛЕГРАМ ---
// Обернут в memo чтобы предотвратить ре-рендер при обновлении родителя
const TelegramWidget = memo(({ onAuth }) => {
  const containerRef = useRef(null);
  const initialized = useRef(false);
  const scriptRef = useRef(null);
  
  const onAuthRef = useRef(onAuth);

  // Обновляем ref без ре-рендера
  useEffect(() => {
    onAuthRef.current = onAuth;
  }, [onAuth]);

  useEffect(() => {
    // ========== МАКСИМАЛЬНАЯ ЗАЩИТА #0: Глобальный singleton ==========
    if (window.__TELEGRAM_WIDGET_INITIALIZED__) {
      console.log('🛑 ГЛОБАЛЬНАЯ ЗАЩИТА: Виджет уже инициализирован на уровне window, БЛОКИРУЮ');
      initialized.current = true;
      return;
    }

    // ========== БЕТОННАЯ ЗАЩИТА #1: Проверка initialized ref ==========
    if (initialized.current) {
      console.log('⚠️ TelegramWidget уже инициализирован (initialized.current = true), пропускаю');
      return;
    }

    // ========== БЕТОННАЯ ЗАЩИТА #2: Проверка существования скрипта в DOM ==========
    const existingScript = document.getElementById('tg-login-script');
    if (existingScript) {
      console.log('⚠️ Скрипт Telegram Widget уже существует в DOM (id="tg-login-script"), пропускаю');
      initialized.current = true;
      return;
    }

    // ========== БЕТОННАЯ ЗАЩИТА #3: Проверка глобального callback ==========
    if (!window.onTelegramAuth) {
      window.onTelegramAuth = (user) => {
        console.log('🔐 Telegram Widget callback вызван:', user);
        if (onAuthRef.current) {
          onAuthRef.current(user);
        }
      };
      console.log('✅ Глобальный window.onTelegramAuth установлен');
    } else {
      console.log('⚠️ window.onTelegramAuth уже существует');
    }

    // ========== БЕТОННАЯ ЗАЩИТА #4: Проверка контейнера и отсутствия дочерних скриптов ==========
    if (!containerRef.current) {
      console.warn('⚠️ containerRef.current не существует, пропускаю инициализацию');
      return;
    }

    if (containerRef.current.querySelector('script')) {
      console.log('⚠️ Контейнер уже содержит script, пропускаю');
      initialized.current = true;
      return;
    }

    // ========== ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ: Создаем виджет ==========
    const script = document.createElement('script');
    script.id = 'tg-login-script'; // ВАЖНО: Уникальный ID для поиска в DOM
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute('data-telegram-login', 'AstraAnalyzerPro_bot');
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    // NOTE: data-request-access удален - не обязателен для базовой авторизации
    script.async = true;
    
    // Обработка успешной загрузки скрипта
    script.onload = () => {
      console.log('✅ Telegram Widget успешно загружен');
    };
    
    script.onerror = () => {
      console.error('❌ Ошибка загрузки Telegram Widget');
      initialized.current = false; // Позволяем повторную попытку при ошибке
    };

    containerRef.current.appendChild(script);
    scriptRef.current = script;
    initialized.current = true;
    window.__TELEGRAM_WIDGET_INITIALIZED__ = true; // Устанавливаем глобальный флаг
    
    console.log('📱 Telegram Widget инициализирован (ОДИН РАЗ) с ID="tg-login-script"');
    console.log('🔒 Глобальный флаг window.__TELEGRAM_WIDGET_INITIALIZED__ установлен в true');

    // Cleanup при unmount компонента
    return () => {
      // НЕ сбрасываем initialized.current чтобы предотвратить повторную загрузку
      // НЕ удаляем window.onTelegramAuth чтобы избежать ошибок от уже загруженного виджета
    };
  }, []); // Пустой массив зависимостей - выполнится строго ОДИН раз

  return (
    <div 
      ref={containerRef} 
      style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        minHeight: '44px',
        margin: '15px 0' 
      }}
    />
  );
});

// Устанавливаем displayName для React DevTools
TelegramWidget.displayName = 'TelegramWidget';

// Обернут в memo чтобы предотвратить ре-рендер при обновлении Header каждую секунду
export const LoginModal = memo(({ onClose, onLoginSuccess }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const authInProgress = useRef(false); // Защита от множественных вызовов
  
  // Состояние для авторизации через бота
  const [botAuthLink, setBotAuthLink] = useState(null);
  const pollingInterval = useRef(null);

  // Мемоизируем функцию чтобы она не пересоздавалась и не вызывала ре-рендер TelegramWidget
  const handleTelegramAuth = useCallback(async (tgUser) => {
    // ========== ГЛОБАЛЬНАЯ ЗАЩИТА ОТ МНОЖЕСТВЕННЫХ ВЫЗОВОВ ==========
    if (window.__TELEGRAM_AUTH_IN_PROGRESS__) {
      console.warn('🛑 ГЛОБАЛЬНАЯ ЗАЩИТА: Авторизация уже идет (window.__TELEGRAM_AUTH_IN_PROGRESS__), БЛОКИРУЮ');
      return;
    }

    // Защита от спама: если авторизация уже идет - игнорируем повторные вызовы
    if (authInProgress.current) {
      console.warn('⚠️ Авторизация уже в процессе (authInProgress.current), игнорирую');
      return;
    }

    console.log('🔐 Начинаем авторизацию через Telegram:', tgUser.id);
    authInProgress.current = true;
    window.__TELEGRAM_AUTH_IN_PROGRESS__ = true; // Глобальный флаг
    setIsLoading(true);
    
    try {
      const response = await axios.post(
        'https://astraanalyzerpro-q6up.onrender.com/api/auth/telegram', 
        tgUser,
        { timeout: 20000 } // 20 секунд таймаут для холодного старта Render
      );
      
      if (response.data.success) {
        onLoginSuccess(response.data.user);
        onClose();
      } else {
        // Сервер вернул success: false
        alert('❌ Ошибка авторизации Telegram.\n\nПопробуйте снова или обратитесь в поддержку.');
        console.error('Telegram auth failed:', response.data);
      }
    } catch (error) {
      console.error("❌ Ошибка авторизации на сервере:", error);
      
      // Определяем тип ошибки и показываем соответствующее сообщение
      if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
        alert(
          '⏱️ Сервер не отвечает (холодный старт Render).\n\n' +
          'Подождите 30-60 секунд и попробуйте снова.\n' +
          'Сервер пробуждается после периода неактивности.'
        );
      } else if (error.response?.status === 401) {
        alert(
          '🔐 Ошибка проверки подписи Telegram.\n\n' +
          'Попробуйте авторизоваться заново.\n' +
          'Если ошибка повторяется - обратитесь в поддержку.'
        );
      } else if (error.response?.status === 500) {
        alert(
          '🛠️ Внутренняя ошибка сервера.\n\n' +
          'Попробуйте через несколько минут.\n' +
          'Если проблема сохраняется - сообщите нам.'
        );
      } else if (!error.response) {
        alert(
          '🌐 Ошибка соединения с сервером.\n\n' +
          'Проверьте:\n' +
          '• Подключение к интернету\n' +
          '• Не блокирует ли VPN/Firewall\n' +
          '• Доступен ли сайт astraanalyzerpro-q6up.onrender.com'
        );
      } else {
        alert(`❌ Неизвестная ошибка авторизации.\n\nКод: ${error.response?.status || 'unknown'}`);
      }
      
      // НЕ закрываем модальное окно при ошибке - даём пользователю попробовать снова
    } finally {
      setIsLoading(false);
      authInProgress.current = false; // Разрешаем новую попытку
      window.__TELEGRAM_AUTH_IN_PROGRESS__ = false; // Сбрасываем глобальный флаг
      console.log('✅ Процесс авторизации завершен, флаги сброшены');
    }
  }, [onLoginSuccess, onClose]); // Зависимости от props

  const handleSubmit = (e) => {
    e.preventDefault();
    setIsLoading(true);
    setTimeout(() => {
      onLoginSuccess({ 
        name: email.split('@')[0], 
        photo: 'https://avatar.iran.liara.run/public/32' 
      });
      setIsLoading(false);
      onClose();
    }, 1000);
  };

  // ========== АВТОРИЗАЦИЯ ЧЕРЕЗ БОТА ==========
  
  const startPolling = useCallback((token) => {
    console.log('🔄 Запуск polling авторизации через бота...');
    
    // Останавливаем предыдущий polling если был
    if (pollingInterval.current) {
      clearInterval(pollingInterval.current);
    }
    
    let attempts = 0;
    const maxAttempts = 60; // 2 минуты (60 * 2 секунды)
    
    pollingInterval.current = setInterval(async () => {
      attempts++;
      
      if (attempts > maxAttempts) {
        clearInterval(pollingInterval.current);
        console.log('⏱️ Polling остановлен: таймаут');
        alert('⏱️ Время ожидания истекло.\n\nПопробуйте снова.');
        setBotAuthLink(null);
        return;
      }
      
      try {
        const response = await axios.get(
          `https://astraanalyzerpro-q6up.onrender.com/api/auth/check-session/${token}`,
          { timeout: 5000 }
        );
        
        if (response.data.success && response.data.status === 'completed') {
          // Авторизация завершена!
          clearInterval(pollingInterval.current);
          console.log('✅ Авторизация через бота завершена успешно');
          
          const userData = response.data.user;
          onLoginSuccess(userData);
          onClose();
        }
      } catch (error) {
        // Игнорируем ошибки - просто продолжаем polling
        if (error.response?.status === 404) {
          console.warn('⚠️ Сессия не найдена');
          clearInterval(pollingInterval.current);
          alert('❌ Сессия авторизации не найдена.\n\nПопробуйте снова.');
          setBotAuthLink(null);
        }
      }
    }, 2000); // Проверяем каждые 2 секунды
  }, [onLoginSuccess, onClose]);

  const handleBotAuth = useCallback(async () => {
    setIsLoading(true);
    
    try {
      // Генерируем токен
      const response = await axios.get(
        'https://astraanalyzerpro-q6up.onrender.com/api/auth/gen-token',
        { timeout: 10000 }
      );
      
      if (response.data.success) {
        const { token, link } = response.data;
        setBotAuthLink(link);
        
        console.log('🔑 Токен авторизации сгенерирован:', token.substring(0, 8) + '...');
        
        // Открываем ссылку в новом окне
        window.open(link, '_blank');
        
        // Запускаем polling
        startPolling(token);
      } else {
        alert('❌ Ошибка генерации токена.\n\nПопробуйте позже.');
      }
    } catch (error) {
      console.error('❌ Ошибка генерации токена:', error);
      alert('❌ Не удалось создать ссылку для входа.\n\nПроверьте соединение с сервером.');
    } finally {
      setIsLoading(false);
    }
  }, [startPolling]);

  // Очистка polling при размонтировании
  useEffect(() => {
    return () => {
      if (pollingInterval.current) {
        clearInterval(pollingInterval.current);
      }
    };
  }, []);

  return (
    <div className="login-modal-overlay">
      <div className="login-modal" style={{ position: 'relative', overflow: 'hidden' }}> 
        <img 
          src="/logo.svg" 
          alt="bg" 
          style={{ 
            position: 'absolute', 
            top: '50%', 
            left: '50%', 
            transform: 'translate(-50%, -50%) rotate(-15deg)', 
            width: '120%', 
            opacity: 0.04, 
            pointerEvents: 'none', 
            zIndex: 0 
          }} 
        />

        <div style={{ position: 'relative', zIndex: 1 }}>
          <button 
            onClick={onClose} 
            className="close-btn" 
            style={{ 
              position: 'absolute', 
              top: '0px', 
              right: '0px', 
              background: 'transparent', 
              border: 'none', 
              color: '#b0b0b0', 
              cursor: 'pointer' 
            }}
          >
            <X size={20}/>
          </button>
          
          <h2 style={{ marginBottom: '20px' }}>Авторизация</h2>
          
          <form onSubmit={handleSubmit}>
            <div className="login-input-group">
              <label>Email</label>
              <div style={{ position: 'relative' }}>
                <User 
                  size={16} 
                  style={{ 
                    position: 'absolute', 
                    left: '12px', 
                    top: '50%', 
                    transform: 'translateY(-50%)', 
                    color: '#b0b0b0' 
                  }} 
                />
                <input 
                  type="email" 
                  className="login-input" 
                  value={email} 
                  onChange={(e) => setEmail(e.target.value)} 
                  placeholder="your@email.com" 
                  required 
                  style={{ paddingLeft: '40px' }} 
                />
              </div>
            </div>
            
            <div className="login-input-group">
              <label>Пароль</label>
              <div style={{ position: 'relative' }}>
                <Lock 
                  size={16} 
                  style={{ 
                    position: 'absolute', 
                    left: '12px', 
                    top: '50%', 
                    transform: 'translateY(-50%)', 
                    color: '#b0b0b0' 
                  }} 
                />
                <input 
                  type="password" 
                  className="login-input" 
                  value={password} 
                  onChange={(e) => setPassword(e.target.value)} 
                  placeholder="••••••••" 
                  required 
                  style={{ paddingLeft: '40px' }} 
                />
              </div>
            </div>
            
            <button 
              type="submit" 
              className="login-btn" 
              disabled={isLoading}
            >
              {isLoading ? 'Вход...' : 'Войти'}
            </button>
          </form>

          {/* АЛЬТЕРНАТИВНЫЙ ВХОД ЧЕРЕЗ БОТА */}
          <div style={{ margin: '20px 0', textAlign: 'center' }}>
            <div style={{ 
              display: 'flex', 
              alignItems: 'center', 
              margin: '15px 0',
              gap: '10px'
            }}>
              <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.1)' }}></div>
              <span style={{ color: '#666', fontSize: '12px' }}>или</span>
              <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.1)' }}></div>
            </div>

            <button
              type="button"
              onClick={handleBotAuth}
              disabled={isLoading || botAuthLink}
              style={{
                width: '100%',
                padding: '12px 20px',
                background: botAuthLink ? '#26a69a' : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                border: 'none',
                borderRadius: '8px',
                color: 'white',
                fontSize: '14px',
                fontWeight: '500',
                cursor: botAuthLink ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px',
                transition: 'all 0.2s',
                opacity: (isLoading || botAuthLink) ? 0.7 : 1
              }}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M2 17L12 22L22 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M2 12L12 17L22 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {botAuthLink ? '⏳ Ожидаем подтверждения...' : '🤖 Войти через Telegram Bot'}
            </button>

            {botAuthLink && (
              <div style={{ 
                marginTop: '15px', 
                padding: '12px', 
                background: 'rgba(38, 166, 154, 0.1)',
                borderRadius: '8px',
                border: '1px solid rgba(38, 166, 154, 0.3)'
              }}>
                <p style={{ color: '#26a69a', fontSize: '13px', margin: '0 0 8px 0' }}>
                  ✅ Ссылка открыта в новой вкладке
                </p>
                <p style={{ color: '#888', fontSize: '12px', margin: 0 }}>
                  Подтвердите вход в боте. Авторизация завершится автоматически.
                </p>
                <button
                  onClick={() => window.open(botAuthLink, '_blank')}
                  style={{
                    marginTop: '8px',
                    padding: '6px 12px',
                    background: 'transparent',
                    border: '1px solid rgba(38, 166, 154, 0.5)',
                    borderRadius: '6px',
                    color: '#26a69a',
                    fontSize: '12px',
                    cursor: 'pointer'
                  }}
                >
                  Открыть ссылку снова
                </button>
              </div>
            )}
          </div>

          <div style={{ margin: '20px 0', textAlign: 'center' }}>
            <p style={{ color: '#888', fontSize: '12px', marginBottom: '10px' }}>
              Вход через виджет:
            </p>
            <TelegramWidget onAuth={handleTelegramAuth} />
          </div>
          
          <div className="login-footer">
            Нет аккаунта? <a href="/#">Зарегистрироваться</a>
          </div>
        </div>
      </div>
    </div>
  );
});

// Устанавливаем displayName для React DevTools
LoginModal.displayName = 'LoginModal';