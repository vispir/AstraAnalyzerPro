import React, { useState, useEffect, useRef } from 'react';
import { User, LogOut, ChevronDown, TrendingUp, TrendingDown } from 'lucide-react';

// Теперь Header - "тупой" компонент, получающий все как пропсы
const Header = ({ tf, setTf, source, setSource, user, onLoginClick, onLogout, onAddProjection, onClearProjections, placementMode }) => {
  const [time, setTime] = useState(new Date());
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showProjectionMenu, setShowProjectionMenu] = useState(false);
  const menuRef = useRef(null);
  const projectionMenuRef = useRef(null);

  // ТАЙМЕР И КЛИК ВНЕ МЕНЮ
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);

    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setShowUserMenu(false);
      }
      if (projectionMenuRef.current && !projectionMenuRef.current.contains(event.target)) {
        setShowProjectionMenu(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      clearInterval(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
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

        {/* ЦЕНТР: ЧАСЫ (премиум стиль) */}
        <div className="header-section clock-area">
          <div className="clock-display">
            <span className="clock-time">{time.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            <span className="utc-badge">UTC+4</span>
          </div>
        </div>

        {/* ПРАВО: ТАЙМФРЕЙМ КНОПКИ + СЕЛЕКТ ИСТОЧНИКА + ЛОГИН */}
        <div className="header-section controls-area">
          {onAddProjection && (
            <div className="projection-menu-wrapper" ref={projectionMenuRef}>
              <button
                type="button"
                className={`projection-trigger ${showProjectionMenu ? 'active' : ''}`}
                onClick={() => setShowProjectionMenu(prev => !prev)}
              >
                <span>Projection</span>
                <ChevronDown size={14} className={`arrow-icon ${showProjectionMenu ? 'rotate' : ''}`} />
              </button>
              {showProjectionMenu && (
                <div className="projection-dropdown glass-panel animate-fade-in">
                  <div className="projection-buttons" title={placementMode ? 'Кликните на графике для размещения' : ''}>
                    <button
                      type="button"
                      className={`projection-btn long ${placementMode?.type === 'long' ? 'active' : ''}`}
                      onClick={() => onAddProjection('long')}
                      title={placementMode?.type === 'long' ? 'Кликните на графике' : 'Добавить Long позицию'}
                    >
                      <TrendingUp size={14} />
                      <span>Long</span>
                    </button>
                    <button
                      type="button"
                      className={`projection-btn short ${placementMode?.type === 'short' ? 'active' : ''}`}
                      onClick={() => onAddProjection('short')}
                      title={placementMode?.type === 'short' ? 'Кликните на графике' : 'Добавить Short позицию'}
                    >
                      <TrendingDown size={14} />
                      <span>Short</span>
                    </button>
                  </div>
                  <div className="projection-menu-actions">
                    <button
                      type="button"
                      className="projection-clear-btn"
                      onClick={() => {
                        if (onClearProjections) onClearProjections();
                        setShowProjectionMenu(false);
                      }}
                    >
                      Clear
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="tf-buttons">
            {['M15', 'H1', 'H4'].map((t) => (
              <button
                key={t}
                type="button"
                className={`tf-btn ${tf === t ? 'active' : ''}`}
                onClick={() => setTf(t)}
              >
                {t}
              </button>
            ))}
          </div>

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
                  <button className="logout-button" onClick={() => { onLogout(); setShowUserMenu(false); }}>
                    <LogOut size={14} />
                    <span>Log out</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button 
              className="login-badge" 
              onClick={onLoginClick}
            >
              <span>Log in</span>
              <User size={14} />
            </button>
          )}
        </div>
      </header>
    </>
  );
};

export default Header;
