import React, { useState } from 'react';
import { User, Lock, X } from 'lucide-react';

export const LoginModal = ({ onClose }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    setIsLoading(true);
    // Здесь будет логика авторизации
    setTimeout(() => {
      setIsLoading(false);
      onClose();
    }, 1500);
  };

  return (
    <div className="login-modal-overlay">
      {/* ДОБАВИЛ position: 'relative' СЮДА */}
      <div className="login-modal" style={{ position: 'relative' }}> 
        <button 
          onClick={onClose}
          className="close-btn"
          style={{
            position: 'absolute',
            top: '15px',
            right: '15px',
            background: 'transparent',
            border: 'none',
            color: '#b0b0b0',
            cursor: 'pointer'
          }}
        >
          <X size={20}/>
        </button>
        
        <h2>Авторизация</h2>
        
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
        
        <div className="login-footer">
          Нет аккаунта? <a href="#">Зарегистрироваться</a>
        </div>
      </div>
    </div>
  );
};