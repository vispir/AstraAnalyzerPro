import React, { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, CrosshairMode } from 'lightweight-charts';

const TradingChart = ({ history, levels, setLevels, activeMode, setActiveMode, serverConnected }) => {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const linesRef = useRef({ entry: null, sl: null, tp: null });

  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return;

    // Создаем график через глобальный объект библиотеки
    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
      layout: { background: { color: '#0b0e14' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: 'rgba(42, 46, 57, 0.05)' }, horzLines: { color: 'rgba(42, 46, 57, 0.05)' } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, borderColor: 'rgba(255, 255, 255, 0.1)' },
    });

    // Создаем серию
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    chart.subscribeClick((param) => {
      if (!param.point || !window.currentActiveMode) return;
      const price = series.coordinateToPrice(param.point.y);
      if (price) window.updateLevel(window.currentActiveMode, price.toFixed(2));
    });

    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.applyOptions({ 
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight
        });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    window.currentActiveMode = activeMode;
    window.updateLevel = (mode, price) => {
      setLevels(prev => ({ ...prev, [mode]: price }));
      setActiveMode(null);
    };
  }, [activeMode, setLevels, setActiveMode]);

  useEffect(() => {
    if (seriesRef.current && history && history.length > 0) {
      seriesRef.current.setData(history);
    }
  }, [history]);

  useEffect(() => {
    const s = seriesRef.current;
    if (!s) return;

    const updateLine = (key, price, color, title) => {
      if (linesRef.current[key]) {
        try { s.removePriceLine(linesRef.current[key]); } catch (err) { console.log(err.message); }
      }
      if (price && !isNaN(parseFloat(price))) {
        linesRef.current[key] = s.createPriceLine({
          price: parseFloat(price), color, lineWidth: 2, lineStyle: 2, title, axisLabelVisible: true
        });
      }
    };
    updateLine('entry', levels.entry, '#2962ff', 'ENTRY');
    updateLine('sl', levels.sl, '#ef5350', 'SL');
    updateLine('tp', levels.tp, '#26a69a', 'TP');
  }, [levels]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={chartContainerRef} style={{ width: '100%', height: '100%' }} />
      {!serverConnected && (
        <div className="server-status-indicator">
          Соединение с сервером отсутствует
        </div>
      )}
    </div>
  );
};

export default TradingChart;