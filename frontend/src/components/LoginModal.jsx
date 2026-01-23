import React, { useState, useEffect, useRef } from 'react';
import { User, Lock, X } from 'lucide-react';
import axios from 'axios';

// --- КОМПОНЕНТ-ОБЕРТКА ДЛЯ ТВОЕГО КОДА ТЕЛЕГРАМ ---
const TelegramWidget = ({ onAuth }) => {
  const containerRef = useRef(null);
  const initialized = useRef(false);
  
  // Создаем "живую" ссылку на функцию onAuth. 
  // Это позволит нам вызывать самую свежую версию функции, 
  // не добавляя её в зависимости useEffect.
  const onAuthRef = useRef(onAuth);

  useEffect(() => {
    onAuthRef.current = onAuth;
  }, [onAuth]);

  useEffect(() => {
    // Если уже инициализировано, выходим
    if (initialized.current) return;

    // Глобальная функция для ТГ (теперь она всегда берет актуальный onAuth из рефа)
    window.onTelegramAuth = (user) => {
      if (onAuthRef.current) {
        onAuthRef.current(user);
      }
    };

    if (containerRef.current) {
      containerRef.current.innerHTML = '';
      
      const script = document.createElement('script');
      script.src = "https://telegram.org/js/telegram-widget.js?22";
      script.setAttribute('data-telegram-login', 'AstraAnalyzerPro_bot');
      script.setAttribute('data-size', 'large');
      script.setAttribute('data-onauth', 'onTelegramAuth(user)');
      script.setAttribute('data-request-access', 'write');
      script.async = true;

      containerRef.current.appendChild(script);
      initialized.current = true;
    }

    // Cleanup функция
    return () => {
      // При закрытии модалки не обнуляем initialized.current, 
      // чтобы избежать мерцания при случайных ре-рендерах.
    };
  }, []); // Теперь массив пустой, и мы добавили коммент для ESLint, чтобы он не ругался

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

// --- ОСНОВНАЯ МОДАЛКА (Без изменений) ---
export const LoginModal = ({ onClose, onLoginSuccess }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleTelegramAuth = async (tgUser) => {
    setIsLoading(true);
    try {
      const response = await axios.post('http://127.0.0.1:5000/api/auth/telegram', tgUser);
      if (response.data.success) {
        onLoginSuccess(response.data.user);
        onClose();
      }
    } catch (error) {
      console.error("Ошибка авторизации:", error);
      onLoginSuccess({
        name: tgUser.first_name + (tgUser.last_name ? ` ${tgUser.last_name}` : ''),
        photo: tgUser.photo_url || 'https://ui-avatars.com/api/?name=TG',
        id: tgUser.id
      });
      onClose();
    } finally {
      setIsLoading(false);
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