import React, { useState, useEffect } from 'react';
import axios from 'axios';
import Header from './components/Header';
import RiskPanel from './components/RiskPanel';
import TradingChart from './components/TradingChart';
import AIPanel from './components/AIPanel';
import './App.css';

const API_BASE = "http://127.0.0.1:5000/api";

function App() {
  const [marketData, setMarketData] = useState({ candles: [], analysis: null });
  
  // 1. Инициализация баланса из localStorage или дефолт
  const [account, setAccount] = useState(() => {
    const saved = localStorage.getItem('astra_account');
    return saved ? JSON.parse(saved) : { balance: 5000.00, equity: 5000.00 };
  });

  const [tf, setTf] = useState('M15');
  const [source, setSource] = useState('twelvedata');

  // 2. Инициализация уровней из localStorage или пусто
  const [levels, setLevels] = useState(() => {
    const saved = localStorage.getItem('astra_levels');
    return saved ? JSON.parse(saved) : { entry: '', sl: '', tp: '' };
  });

  const [activeMode, setActiveMode] = useState(null);
  const [serverConnected, setServerConnected] = useState(true);

  // Сохраняем баланс при каждом изменении
  useEffect(() => {
    localStorage.setItem('astra_account', JSON.stringify(account));
  }, [account]);

  // Сохраняем уровни при каждом изменении
  useEffect(() => {
    localStorage.setItem('astra_levels', JSON.stringify(levels));
  }, [levels]);

  useEffect(() => {
    let isMounted = true;
    const controller = new AbortController();

    // Создаем внутреннюю асинхронную функцию
    const loadData = async () => {
      try {
        const res = await axios.get(`${API_BASE}/market/candles`, { 
          params: { tf, source, limit: 100 },
          signal: controller.signal
        });

        if (isMounted && res.data && res.data.candles) {
          setMarketData({ 
            candles: res.data.candles, 
            analysis: res.data.analysis || null 
          });
          setServerConnected(true);
        }
      } catch (error) {
        if (!axios.isCancel(error)) {
          console.error("Ошибка:", error.message);
          if (isMounted) {
            setServerConnected(false);
          }
        }
      }
    };

    // Запускаем через setTimeout 0, чтобы вынести из синхронного потока
    // Это на 100% убирает ошибку "Calling setState synchronously"
    const startTimeout = setTimeout(() => {
      loadData();
    }, 0);

    const interval = setInterval(loadData, 60000);

    return () => {
      isMounted = false;
      controller.abort();
      clearInterval(interval);
      clearTimeout(startTimeout);
    };
  }, [tf, source]);

  return (
    <div className="app-wrapper">
      <Header tf={tf} setTf={setTf} source={source} setSource={setSource} />
      <main className="main-layout">
        {/* ВОТ ТУТ: Добавили tf={tf} */}
        <RiskPanel 
          account={account} setAccount={setAccount}
          levels={levels} setLevels={setLevels} 
          activeMode={activeMode} setActiveMode={setActiveMode} 
          tf={tf} 
        />
        <div className="chart-wrapper glass-panel">
          <TradingChart 
            history={marketData.candles} 
            levels={levels} setLevels={setLevels} 
            activeMode={activeMode} setActiveMode={setActiveMode}
            serverConnected={serverConnected}
          />
        </div>
        <AIPanel analysis={marketData.analysis} levels={levels} account={account} />
      </main>
    </div>
  );
}

export default App;