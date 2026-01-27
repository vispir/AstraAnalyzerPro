import React, { useState, useEffect, useRef, useCallback } from 'react';
import { User, Clock, LogOut, ChevronDown } from 'lucide-react'; // Добавили новые иконки
import { LoginModal } from './LoginModal';

const Header = ({ tf, setTf, source, setSource }) => {
  const [time, setTime] = useState(new Date());
  const [showLoginModal, setShowLoginModal] = useState(false);
  
  // 1. СОСТОЯНИЕ АВТОРИЗАЦИИ (с проверкой памяти при загрузке)
  const [user, setUser] = useState(() => {
    const savedUser = localStorage.getItem('astra_user');
    return savedUser ? JSON.parse(savedUser) : null;
  });

  const [showUserMenu, setShowUserMenu] = useState(false);
  const menuRef = useRef(null); // Нужно, чтобы закрывать меню при клике мимо

  // 2. ТАЙМЕР И КЛИК ВНЕ МЕНЮ
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);

    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setShowUserMenu(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      clearInterval(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  // 3. ФУНКЦИИ ВХОДА И ВЫХОДА
  const handleLoginSuccess = useCallback((userData) => {
    setUser(userData);
    localStorage.setItem('astra_user', JSON.stringify(userData));
    setShowLoginModal(false);
  }, []);

  const handleCloseModal = useCallback(() => {
    setShowLoginModal(false);
  }, []);

  const handleLogout = useCallback(() => {
    setUser(null);
    localStorage.removeItem('astra_user'); // Удаляем из браузера
    setShowUserMenu(false);
  }, []);

  return (
    <>
      <header className="header-container glass-panel">
        {/* ЛЕВО: ЛОГОТИП */}
        <div className="header-section logo-area">
          <svg width="32" height="32" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect width="100" height="100" rx="22" fill="#0B0E14"/>
              <path d="M50 15L82 33V67L50 85L18 67V33L50 15Z" stroke="#2962ff" strokeWidth="2" opacity="0.4"/>
              <path d="M32 35V75" stroke="#26a69a" strokeWidth="2.5" strokeLinecap="round"/>
              <rect x="25" y="45" width="14" height="20" rx="2" fill="#26a69a"/>
              <path d="M50 40V70" stroke="#ef5350" strokeWidth="2.5" strokeLinecap="round"/>
              <rect x="43" y="45" width="14" height="15" rx="2" fill="#ef5350"/>
              <path d="M68 25V65" stroke="#26a69a" strokeWidth="2.5" strokeLinecap="round"/>
              <rect x="61" y="35" width="14" height="25" rx="2" fill="#26a69a"/>
          </svg>
          <span className="brand-text">ASTRA ANALYZER</span>
        </div>

        {/* ЦЕНТР: ЧАСЫ */}
        <div className="header-section clock-area">
          <div className="clock-display">
            <Clock size={16} />
            <span>{time.toLocaleTimeString('ru-RU')}</span>
            <span className="utc-badge">UTC+4</span>
          </div>
        </div>

        {/* ПРАВО: СЕЛЕКТОРЫ И ЛОГИН */}
        <div className="header-section controls-area">
          <select value={tf} onChange={(e) => setTf(e.target.value)} className="select-modern tf-select">
            <option value="M15">M15</option>
            <option value="H1">H1</option>
            <option value="H4">H4</option>
          </select>
          
          <select value={source} onChange={(e) => setSource(e.target.value)} className="select-modern src-select">
            <option value="oanda">OANDA</option>
            <option value="twelvedata">Twelve Data</option>
            <option value="yfinance">Yahoo Finance</option>
          </select>

          {/* ЛОГИКА АВТОРИЗАЦИИ: ПОКАЗЫВАЕМ АВУ ИЛИ КНОПКУ */}
          {user ? (
            <div className="user-profile-wrapper" ref={menuRef} style={{ position: 'relative' }}>
              <div 
                className={`user-avatar-badge ${showUserMenu ? 'active' : ''}`}
                onClick={() => setShowUserMenu(!showUserMenu)}
              >
                <img src={user.photo} alt="User" className="header-avatar" />
                <ChevronDown size={14} className={`arrow-icon ${showUserMenu ? 'rotate' : ''}`} />
              </div>

              {/* ВЫПАДАЮЩЕЕ МЕНЮ ПРИ КЛИКЕ НА АВУ */}
              {showUserMenu && (
                <div className="user-dropdown-menu glass-panel animate-fade-in">
                  <div className="dropdown-info">
                    <span className="dropdown-name">{user.name}</span>
                    <span className="dropdown-status">Active Trader</span>
                  </div>
                  <div className="dropdown-divider"></div>
                  <button className="logout-button" onClick={handleLogout}>
                    <LogOut size={14} />
                    <span>Log out</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button 
              className="login-badge" 
              onClick={() => setShowLoginModal(true)}
            >
              <span>Log in</span>
              <User size={14} />
            </button>
          )}
        </div>
      </header>

      {showLoginModal && (
        <LoginModal 
          onClose={handleCloseModal} 
          onLoginSuccess={handleLoginSuccess} 
        />
      )}
    </>
  );
};

export default Header;