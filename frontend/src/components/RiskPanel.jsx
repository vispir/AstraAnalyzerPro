import React, { useState, useEffect } from 'react';
import { Target, ShieldAlert, BadgeCheck, Wallet, Timer, RefreshCcw, MoveVertical } from 'lucide-react';

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
        {/* Теперь шрифт всегда будет четким, так как символов меньше */}
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
    const balance = parseFloat(account.balance) || 0;
    const riskPercent = parseFloat(account.riskPercent) || 0.5;
    
    if (!e || !s || !t || e === s || balance === 0) return "0.00";

    const stopPoints = Math.abs(e - s);
    const profitPoints = Math.abs(t - e);
    const rr = profitPoints / stopPoints;

    if (rr < 2.0) return "0.00";

    const riskTargetUSD = balance * (riskPercent / 100); 
    let rawLot = riskTargetUSD / (stopPoints * 100);
    
    if (rawLot < 0.01) {
        if (stopPoints <= (balance * 0.01)) return "0.01";
        return "0.00";
    }
    return (Math.floor(rawLot * 100) / 100).toFixed(2);
  };

  const lotValue = calculateLot();
  const dailyLossLimit = parseFloat(account.dailyLossLimit) || 250;
  const currentBalance = parseFloat(account.balance) || 5000;
  const startBalance = 5000; // Начальный баланс дня (можно сохранять в localStorage)
  const dailyLoss = Math.max(0, startBalance - currentBalance); 
  const lossPercent = Math.min(100, (dailyLoss / dailyLossLimit) * 100);

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
        <label>Daily Loss Limit ($)</label>
        <input 
          type="number" 
          className="custom-input small" 
          value={account.dailyLossLimit} 
          onChange={(e) => setAccount({...account, dailyLossLimit: e.target.value})} 
        />
        <label>Risk Percent (%)</label>
        <input 
          type="number" 
          step="0.1"
          className="custom-input small" 
          value={account.riskPercent} 
          onChange={(e) => setAccount({...account, riskPercent: e.target.value})} 
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
        <div className="input-with-action">
          <label>Entry Price</label>
          <div className="input-row">
            <input type="number" className="custom-input" value={levels.entry} onChange={(e) => setLevels({...levels, entry: e.target.value})} />
            <button className="icon-btn" onClick={() => setLevels({...levels, entry: ''})} title="Reset Entry">
              <RefreshCcw size={14} />
            </button>
          </div>
        </div>

        <div className="input-with-action">
          <label>Stop Loss</label>
          <div className="input-row">
            <input type="number" className="custom-input" value={levels.sl} onChange={(e) => setLevels({...levels, sl: e.target.value})} />
            <button className="icon-btn" onClick={() => setLevels({...levels, sl: ''})} title="Reset SL">
              <RefreshCcw size={14} />
            </button>
          </div>
        </div>

        <div className="input-with-action">
          <label>Take Profit</label>
          <div className="input-row">
            <input type="number" className="custom-input" value={levels.tp} onChange={(e) => setLevels({...levels, tp: e.target.value})} />
            <button className="icon-btn" onClick={() => setLevels({...levels, tp: ''})} title="Reset TP">
              <RefreshCcw size={14} />
            </button>
          </div>
        </div>
        
        <button className="reset-all-btn" onClick={() => setLevels({entry: '', sl: '', tp: ''})}>
          <RefreshCcw size={14} /> Reset All Levels
        </button>
        
        <div className="drag-hint">
          <MoveVertical size={12} /> Двигайте линии на графике
        </div>
      </div>

      {/* ТАЙМЕР ПЕРЕД RECOMMENDED LOT */}
      <div style={{display: 'flex', justifyContent: 'center', margin: '12px 0 8px 0'}}>
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