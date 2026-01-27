import React, { useState, useEffect, useRef } from 'react';
import { User, Lock, X } from 'lucide-react';
import axios from 'axios';

// --- КОМПОНЕНТ-ОБЕРТКА ДЛЯ ТВОЕГО КОДА ТЕЛЕГРАМ ---
const TelegramWidget = ({ onAuth }) => {
  const containerRef = useRef(null);
  const initialized = useRef(false);
  const scriptRef = useRef(null);
  
  const onAuthRef = useRef(onAuth);

  // Обновляем ref без ре-рендера
  useEffect(() => {
    onAuthRef.current = onAuth;
  }, [onAuth]);

  useEffect(() => {
    // КРИТИЧНО: Проверяем initialized ДО любых манипуляций с DOM
    if (initialized.current) {
      return;
    }

    // Устанавливаем глобальный callback один раз
    if (!window.onTelegramAuth) {
      window.onTelegramAuth = (user) => {
        console.log('🔐 Telegram Widget callback вызван:', user);
        if (onAuthRef.current) {
          onAuthRef.current(user);
        }
      };
    }

    // Проверяем что контейнер существует И еще не содержит скрипт
    if (containerRef.current && !containerRef.current.querySelector('script')) {
      const script = document.createElement('script');
      script.src = "https://telegram.org/js/telegram-widget.js?22";
      script.setAttribute('data-telegram-login', 'AstraAnalyzerPro_bot');
      script.setAttribute('data-size', 'large');
      script.setAttribute('data-onauth', 'onTelegramAuth(user)');
      script.setAttribute('data-request-access', 'write');
      script.async = true;
      
      // Обработка успешной загрузки скрипта
      script.onload = () => {
        console.log('✅ Telegram Widget успешно загружен');
      };
      
      script.onerror = () => {
        console.error('❌ Ошибка загрузки Telegram Widget');
        initialized.current = false; // Позволяем повторную попытку
      };

      containerRef.current.appendChild(script);
      scriptRef.current = script;
      initialized.current = true;
      
      console.log('📱 Telegram Widget инициализирован (один раз)');
    }

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
};

export const LoginModal = ({ onClose, onLoginSuccess }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const authInProgress = useRef(false); // Защита от множественных вызовов

  const handleTelegramAuth = async (tgUser) => {
    // Защита от спама: если авторизация уже идет - игнорируем повторные вызовы
    if (authInProgress.current) {
      console.warn('⚠️ Авторизация уже в процессе, игнорирую повторный вызов');
      return;
    }

    console.log('🔐 Начинаем авторизацию через Telegram:', tgUser.id);
    authInProgress.current = true;
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
      console.log('✅ Процесс авторизации завершен');
    }
  };

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

          <div style={{ margin: '20px 0', textAlign: 'center' }}>
            <p style={{ color: '#888', fontSize: '12px', marginBottom: '10px' }}>
              Вариант регистрации:
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
};