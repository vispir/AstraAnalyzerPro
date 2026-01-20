import React, { useState, useEffect } from 'react';
import { Target, ShieldAlert, BadgeCheck, Wallet, Timer } from 'lucide-react';

// Вспомогательный компонент для кругового таймера
const CandleTimer = ({ tf }) => {
  const [timeLeft, setTimeLeft] = useState(0);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const updateTimer = () => {
      const now = new Date();
      const mins = now.getMinutes();
      const secs = now.getSeconds();
      let total, remaining;

      if (tf === 'M15') {
        total = 15 * 60;
        remaining = total - ((mins % 15) * 60 + secs);
      } else if (tf === 'H1') {
        total = 60 * 60;
        remaining = total - (mins * 60 + secs);
      } else if (tf === 'H4') {
        total = 4 * 60 * 60; // 14400 секунд
        // Расчет: сколько секунд прошло с начала текущего 4-часового блока
        const currentHour = now.getHours();
        const blockStartHour = Math.floor(currentHour / 4) * 4;
        const secondsPassedInBlock = ((currentHour - blockStartHour) * 3600) + (mins * 60) + secs;
        remaining = total - secondsPassedInBlock;
      } else {
        total = 60; remaining = 0;
      }
      setTimeLeft(remaining);
      setProgress((remaining / total) * 100);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [tf]);

  // УМНЫЙ ФОРМАТ: Убираем секунды, если есть часы
  const format = (s) => {
    const hrs = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rs = s % 60;

    if (hrs > 0) {
      // Если есть часы, показываем только H:MM (Секунды не нужны)
      return `${hrs}:${m.toString().padStart(2, '0')}`;
    }
    // Если меньше часа (М15 или финал H1/H4), показываем M:SS
    return `${m}:${rs.toString().padStart(2, '0')}`;
  };

  const isUrgent = timeLeft < 60;

  return (
    <div className={`candle-timer-mini ${isUrgent ? 'urgent' : ''}`}>
      <svg viewBox="0 0 36 36" className="timer-circle">
        <path className="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
        <path className="circle-fill" 
          strokeDasharray={`${progress}, 100`} 
          d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" 
        />
      </svg>
      <div className="timer-info">
        <span className="t-time">
          {format(timeLeft)}
        </span>
        <span className="t-label">{tf}</span>
      </div>
    </div>
  );
};

// ТВОЙ ОСНОВНОЙ КОМПОНЕНТ (Добавлен tf в пропсы)
const RiskPanel = ({ account, setAccount, levels, setLevels, activeMode, setActiveMode, tf = "M15" }) => {
  
  const calculateLot = () => {
    const e = parseFloat(levels.entry);
    const s = parseFloat(levels.sl);
    const t = parseFloat(levels.tp);
    if (!e || !s || !t || e === s) return "0.00";

    const stopPoints = Math.abs(e - s);
    const profitPoints = Math.abs(t - e);
    const rr = profitPoints / stopPoints;

    if (rr < 2.0) return "0.00";

    const riskTargetUSD = account.balance * 0.005; 
    let rawLot = riskTargetUSD / (stopPoints * 100);
    
    if (rawLot < 0.01) {
        if (stopPoints <= (account.balance * 0.01)) return "0.01";
        return "0.00";
    }
    return (Math.floor(rawLot * 100) / 100).toFixed(2);
  };

  const lotValue = calculateLot();
  const dailyLoss = Math.max(0, 5000 - account.equity); 
  const lossPercent = Math.min(100, (dailyLoss / 250) * 100);

  return (
    <aside className="glass-panel sidebar-left">
      <h3>Account & Risk</h3>
      
      <div className="account-inputs">
        <label><Wallet size={12} /> Account Balance ($)</label>
        <input 
          type="number" 
          className="custom-input small" 
          value={account.balance} 
          onChange={(e) => setAccount({...account, balance: e.target.value})} 
        />
        <label>Current Equity ($)</label>
        <input 
          type="number" 
          className="custom-input small" 
          value={account.equity} 
          onChange={(e) => setAccount({...account, equity: e.target.value})} 
        />
        <div className="daily-bar-container">
           <div className="daily-bar-fill" style={{
               width: `${lossPercent}%`, 
               background: lossPercent > 80 ? '#ef5350' : '#26a69a'
           }}></div>
        </div>
      </div>

      <div className="mode-selector-group">
        <button className={`mode-btn ${activeMode === 'entry' ? 'active' : ''}`} onClick={() => setActiveMode('entry')}>
          <Target size={16} /> Set Entry
        </button>
        <button className={`mode-btn ${activeMode === 'sl' ? 'active' : ''}`} onClick={() => setActiveMode('sl')}>
          <ShieldAlert size={16} /> Set SL
        </button>
        <button className={`mode-btn ${activeMode === 'tp' ? 'active' : ''}`} onClick={() => setActiveMode('tp')}>
          <BadgeCheck size={16} /> Set TP
        </button>
      </div>

      <div className="inputs-group">
        <label>Entry Price</label>
        <input type="number" className="custom-input" value={levels.entry} onChange={(e) => setLevels({...levels, entry: e.target.value})} />
        <label>Stop Loss</label>
        <input type="number" className="custom-input" value={levels.sl} onChange={(e) => setLevels({...levels, sl: e.target.value})} />
        <label>Take Profit</label>
        <input type="number" className="custom-input" value={levels.tp} onChange={(e) => setLevels({...levels, tp: e.target.value})} />
      </div>

      {/* ТАЙМЕР ПЕРЕД RECOMMENDED LOT */}
      <div style={{display: 'flex', justifyContent: 'center', margin: '20px 0 16px 0'}}>
        <CandleTimer tf={tf} />
      </div>

      <div className="lot-result-box">
        <span className="lot-label">Recommended Lot</span>
        <span className="lot-separator"> - </span>
        <span className={`lot-value ${lotValue === "0.00" ? "danger" : "success"}`}>{lotValue}</span>
      </div>
    </aside>
  );
};

export default RiskPanel;