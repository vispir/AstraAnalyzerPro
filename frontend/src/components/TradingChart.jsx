import React, { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, CrosshairMode } from 'lightweight-charts';

const TradingChart = ({ history, levels, setLevels, activeMode, setActiveMode, serverConnected }) => {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const linesRef = useRef({ entry: null, sl: null, tp: null });
  const draggingRef = useRef(null);

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

    // Обработка перетаскивания
    const container = chartContainerRef.current;
    
    const onMouseDown = (e) => {
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      const price = series.coordinateToPrice(y);
      if (!price) return;

      // Проверяем близость к существующим линиям (в пикселях)
      const threshold = 15;
      let closest = null;
      let minDiff = threshold;

      ['entry', 'sl', 'tp'].forEach(mode => {
        const levelPrice = parseFloat(levelsRef.current[mode]);
        if (!isNaN(levelPrice)) {
          const levelY = series.priceToCoordinate(levelPrice);
          const diff = Math.abs(levelY - y);
          if (diff < minDiff) {
            minDiff = diff;
            closest = mode;
          }
        }
      });

      if (closest) {
        draggingRef.current = closest;
        chart.applyOptions({ handleScroll: false, handleScale: false }); // Отключаем скролл при перетаскивании
      }
    };

    const onMouseMove = (e) => {
      const rect = container.getBoundingClientRect();
      const y = e.clientY - rect.top;
      
      if (draggingRef.current) {
        const price = series.coordinateToPrice(y);
        if (price) {
          setLevels(prev => ({ ...prev, [draggingRef.current]: price.toFixed(2) }));
        }
        return;
      }

      // Подсветка при наведении
      const threshold = 15;
      let isNearLine = false;
      ['entry', 'sl', 'tp'].forEach(mode => {
        const levelPrice = parseFloat(levelsRef.current[mode]);
        if (!isNaN(levelPrice)) {
          const levelY = series.priceToCoordinate(levelPrice);
          if (Math.abs(levelY - y) < threshold) {
            isNearLine = true;
          }
        }
      });
      container.style.cursor = isNearLine ? 'ns-resize' : 'crosshair';
    };

    const onMouseUp = () => {
      if (draggingRef.current) {
        draggingRef.current = null;
        chart.applyOptions({ handleScroll: true, handleScale: true });
      }
    };

    container.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);

    chart.subscribeClick((param) => {
      if (!param.point || !window.currentActiveMode || draggingRef.current) return;
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
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  // Используем ref для уровней, чтобы иметь к ним доступ в обработчиках без пересоздания useEffect
  const levelsRef = useRef(levels);
  useEffect(() => {
    levelsRef.current = levels;
  }, [levels]);

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
      const parsedPrice = parseFloat(price);
      const isValid = price && !isNaN(parsedPrice);

      if (linesRef.current[key]) {
        if (isValid) {
          // Если линия уже есть и цена валидна — просто обновляем её
          linesRef.current[key].applyOptions({ price: parsedPrice });
        } else {
          // Если цена стала невалидной (сброс) — удаляем
          try { 
            s.removePriceLine(linesRef.current[key]); 
            linesRef.current[key] = null;
          } catch (err) { console.log(err.message); }
        }
      } else if (isValid) {
        // Если линии нет, но цена валидна — создаем
        linesRef.current[key] = s.createPriceLine({
          price: parsedPrice, 
          color, 
          lineWidth: 2, 
          lineStyle: 2, 
          title, 
          axisLabelVisible: true
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