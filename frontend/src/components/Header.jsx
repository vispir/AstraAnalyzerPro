import React, { useState, useEffect } from 'react';
import { User, Clock } from 'lucide-react';
import { LoginModal } from './LoginModal';

const Header = ({ tf, setTf, source, setSource }) => {
  const [time, setTime] = useState(new Date());
  const [showLoginModal, setShowLoginModal] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <>
      <header className="header-container glass-panel">
        {/* ЛЕВО: ЛОГОТИП (ВСТРОЕННЫЙ SVG) */}
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
            <option value="twelvedata">Twelve Data</option>
            <option value="yfinance">Yahoo Finance</option>
          </select>

          <button 
            className="login-badge" 
            onClick={() => setShowLoginModal(true)}
          >
            <span>Log in</span>
            <User size={14} />
          </button>
        </div>
      </header>

      {showLoginModal && (
        <LoginModal onClose={() => setShowLoginModal(false)} />
      )}
    </>
  );
};

export default Header;