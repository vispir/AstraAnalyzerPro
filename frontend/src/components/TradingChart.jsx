import React, { useEffect, useRef, useCallback, useState } from 'react';
import { createChart, CandlestickSeries, CrosshairMode } from 'lightweight-charts';

// ============================================================
// SMC COLORS (LuxAlgo Style) - Вынесены за пределы компонента
// ============================================================
const SMC_COLORS = {
  BULL_OB: 'rgba(38, 166, 154, 0.25)',
  BEAR_OB: 'rgba(239, 83, 80, 0.25)',
  BULL_FVG: 'rgba(38, 166, 154, 0.15)',
  BEAR_FVG: 'rgba(239, 83, 80, 0.15)',
  BULL_OB_BORDER: 'rgba(38, 166, 154, 0.8)',
  BEAR_OB_BORDER: 'rgba(239, 83, 80, 0.8)',
  BULLISH_BOS: '#26a69a',
  BEARISH_BOS: '#ef5350',
  BULLISH_CHOCH: '#2962ff',
  BEARISH_CHOCH: '#f44336',
  EQH: '#ff9800',
  EQL: '#ff9800',
};

/**
 * TradingChart v2.0 с SMC Visualization
 * - Canvas overlay для Order Blocks, FVG
 * - Lines для BOS/CHoCH
 * - Price Lines для EQH/EQL
 */
const TradingChart = ({ history, analysis, levels, setLevels, activeMode, setActiveMode, serverConnected }) => {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const canvasRef = useRef(null);
  const linesRef = useRef({ entry: null, sl: null, tp: null });
  const draggingRef = useRef(null);
  const levelsRef = useRef(levels);
  
  // Состояние видимости SMC (сохраняем в localStorage)
  const [smcVisible, setSmcVisible] = useState(() => {
    const saved = localStorage.getItem('astra_smc_visible');
    return saved !== null ? JSON.parse(saved) : true;
  });
  
  // Сохраняем состояние SMC в localStorage
  useEffect(() => {
    localStorage.setItem('astra_smc_visible', JSON.stringify(smcVisible));
  }, [smcVisible]);

  // ============================================================
  // DRAW SMC ON CANVAS
  // ============================================================
  const drawSMCOverlay = useCallback(() => {
    if (!canvasRef.current || !chartRef.current || !seriesRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const chart = chartRef.current;
    const series = seriesRef.current;
    const timeScale = chart.timeScale();

    // Очищаем canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Если SMC выключен или нет данных анализа - не рисуем
    if (!smcVisible || !analysis) return;

    // Получаем видимый диапазон
    const visibleRange = timeScale.getVisibleLogicalRange();
    if (!visibleRange) return;

    // Вычисляем ширину области графика (без шкалы цен справа)
    // timeScale.width() возвращает ширину области свечей
    const chartContentWidth = timeScale.width ? timeScale.width() : (canvas.width - 60);
    const priceScaleWidth = canvas.width - chartContentWidth;
    const chartRightEdge = canvas.width - priceScaleWidth;

    // ============================================================
    // 1. ORDER BLOCKS (Прямоугольники)
    // ============================================================
    const orderBlocks = analysis.order_blocks || [];
    orderBlocks.forEach(ob => {
      if (ob.bar_index === undefined) return;
      
      const time = history[ob.bar_index]?.time;
      if (!time) return;

      const topY = series.priceToCoordinate(ob.top);
      const bottomY = series.priceToCoordinate(ob.bottom);
      const leftX = timeScale.timeToCoordinate(time);
      
      if (topY === null || bottomY === null || leftX === null) return;

      // Используем chartRightEdge вместо canvas.width
      const rightX = chartRightEdge;
      const height = Math.abs(bottomY - topY);
      const y = Math.min(topY, bottomY);

      const isBull = ob.type?.includes('BULL');
      ctx.fillStyle = isBull ? SMC_COLORS.BULL_OB : SMC_COLORS.BEAR_OB;
      ctx.fillRect(leftX, y, rightX - leftX, height);

      ctx.strokeStyle = isBull ? SMC_COLORS.BULL_OB_BORDER : SMC_COLORS.BEAR_OB_BORDER;
      ctx.lineWidth = 1;
      ctx.strokeRect(leftX, y, rightX - leftX, height);

      ctx.fillStyle = isBull ? SMC_COLORS.BULL_OB_BORDER : SMC_COLORS.BEAR_OB_BORDER;
      ctx.font = 'bold 10px Arial';
      ctx.fillText(isBull ? 'BULL OB' : 'BEAR OB', leftX + 5, y + 12);
    });

    // ============================================================
    // 2. FAIR VALUE GAPS (Прямоугольники с пунктиром)
    // ============================================================
    const fvgList = analysis.fvg || [];
    fvgList.forEach(fvg => {
      if (fvg.bar_index === undefined) return;
      
      const time = history[fvg.bar_index]?.time;
      if (!time) return;

      const topY = series.priceToCoordinate(fvg.top);
      const bottomY = series.priceToCoordinate(fvg.bottom);
      const leftX = timeScale.timeToCoordinate(time);
      
      if (topY === null || bottomY === null || leftX === null) return;

      // Используем chartRightEdge вместо canvas.width
      const rightX = chartRightEdge;
      const height = Math.abs(bottomY - topY);
      const y = Math.min(topY, bottomY);

      const isBull = fvg.type?.includes('BULL');
      ctx.fillStyle = isBull ? SMC_COLORS.BULL_FVG : SMC_COLORS.BEAR_FVG;
      ctx.fillRect(leftX, y, rightX - leftX, height);

      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = isBull ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
      ctx.lineWidth = 1;
      ctx.strokeRect(leftX, y, rightX - leftX, height);
      ctx.setLineDash([]);

      ctx.fillStyle = isBull ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
      ctx.font = '9px Arial';
      ctx.fillText('FVG', leftX + 5, y + 10);
    });

    // ============================================================
    // 3. BOS/CHoCH LINES (Internal + Swing)
    // ============================================================
    const drawBreakLine = (breaks, isInternal = false) => {
      breaks.forEach(brk => {
        if (brk.bar_index === undefined || brk.pivot_bar_index === undefined) return;
        
        const pivotTime = history[brk.pivot_bar_index]?.time;
        const breakTime = history[brk.bar_index]?.time;
        if (!pivotTime || !breakTime) return;

        const priceY = series.priceToCoordinate(brk.price);
        const pivotX = timeScale.timeToCoordinate(pivotTime);
        const breakX = timeScale.timeToCoordinate(breakTime);
        
        if (priceY === null || pivotX === null || breakX === null) return;

        const isBullish = brk.type?.includes('BULLISH');
        const isChoch = brk.is_choch;
        
        ctx.beginPath();
        ctx.moveTo(pivotX, priceY);
        // Ограничиваем линию правой границей графика
        ctx.lineTo(Math.min(breakX, chartRightEdge), priceY);
        
        if (isChoch) {
          ctx.setLineDash([6, 3]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_CHOCH : SMC_COLORS.BEARISH_CHOCH;
          ctx.lineWidth = isInternal ? 1.5 : 2;
        } else {
          ctx.setLineDash([]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
          ctx.lineWidth = isInternal ? 1 : 1.5;
        }
        ctx.stroke();
        ctx.setLineDash([]);

        const labelText = isChoch ? 'CHoCH' : 'BOS';
        ctx.fillStyle = ctx.strokeStyle;
        ctx.font = isInternal ? '9px Arial' : 'bold 10px Arial';
        const labelX = Math.min((pivotX + breakX) / 2 - 15, chartRightEdge - 40);
        ctx.fillText(labelText, labelX, priceY - 5);
      });
    };

    // Рисуем Internal структуру (более частые, тонкие линии)
    drawBreakLine(analysis.all_internal_bos || [], true);
    drawBreakLine(analysis.all_internal_choch || [], true);
    
    // Рисуем Swing структуру (реже, толще линии)
    drawBreakLine(analysis.all_swing_bos || [], false);
    drawBreakLine(analysis.all_swing_choch || [], false);

    // ============================================================
    // 4. EQUAL HIGHS/LOWS (Пунктирные линии)
    // ============================================================
    const drawEqualLevels = (eqLevels, color, label) => {
      eqLevels.forEach(lvl => {
        const priceY = series.priceToCoordinate(lvl.price);
        if (priceY === null) return;

        ctx.beginPath();
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.moveTo(0, priceY);
        // Ограничиваем линию правой границей графика
        ctx.lineTo(chartRightEdge, priceY);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = color;
        ctx.font = '9px Arial';
        ctx.fillText(label, chartRightEdge - 35, priceY - 3);
      });
    };

    drawEqualLevels(analysis.eqh || [], SMC_COLORS.EQH, 'EQH');
    drawEqualLevels(analysis.eql || [], SMC_COLORS.EQL, 'EQL');

  }, [analysis, history, smcVisible]);

  // ============================================================
  // CHART INITIALIZATION
  // ============================================================
  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
      layout: { background: { color: '#0b0e14' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: 'rgba(42, 46, 57, 0.05)' }, horzLines: { color: 'rgba(42, 46, 57, 0.05)' } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, borderColor: 'rgba(255, 255, 255, 0.1)' },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Создаём Canvas overlay
    const canvas = document.createElement('canvas');
    canvas.style.position = 'absolute';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '10';
    chartContainerRef.current.appendChild(canvas);
    canvasRef.current = canvas;

    const resizeCanvas = () => {
      if (canvas && chartContainerRef.current) {
        canvas.width = chartContainerRef.current.clientWidth;
        canvas.height = chartContainerRef.current.clientHeight;
        drawSMCOverlay();
      }
    };
    resizeCanvas();

    chart.timeScale().subscribeVisibleLogicalRangeChange(drawSMCOverlay);
    chart.subscribeCrosshairMove(drawSMCOverlay);

    const container = chartContainerRef.current;
    
    const onMouseDown = (e) => {
      const rect = container.getBoundingClientRect();
      const y = e.clientY - rect.top;
      
      const price = series.coordinateToPrice(y);
      if (!price) return;

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
        chart.applyOptions({ handleScroll: false, handleScale: false });
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
        resizeCanvas();
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('resize', handleResize);
      if (canvasRef.current && canvasRef.current.parentNode) {
        canvasRef.current.parentNode.removeChild(canvasRef.current);
      }
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [setLevels, drawSMCOverlay]);

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
      setTimeout(drawSMCOverlay, 100);
    }
  }, [history, drawSMCOverlay]);

  useEffect(() => {
    drawSMCOverlay();
  }, [analysis, drawSMCOverlay]);

  // Перерисовываем при переключении SMC
  useEffect(() => {
    drawSMCOverlay();
  }, [smcVisible, drawSMCOverlay]);

  useEffect(() => {
    const s = seriesRef.current;
    if (!s) return;

    const updateLine = (key, price, color, title) => {
      const parsedPrice = parseFloat(price);
      const isValid = price && !isNaN(parsedPrice);

      if (linesRef.current[key]) {
        if (isValid) {
          linesRef.current[key].applyOptions({ price: parsedPrice });
        } else {
          try { 
            s.removePriceLine(linesRef.current[key]); 
            linesRef.current[key] = null;
          } catch (err) { console.log(err.message); }
        }
      } else if (isValid) {
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
      <div ref={chartContainerRef} style={{ width: '100%', height: '100%', position: 'relative' }} />
      {!serverConnected && (
        <div className="server-status-indicator">
          Соединение с сервером отсутствует
        </div>
      )}
      
      {/* SMC Toggle Button + Legend */}
      <div style={{
        position: 'absolute',
        top: '10px',
        left: '10px',
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}>
        {/* Toggle Button */}
        <button
          onClick={() => setSmcVisible(prev => !prev)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 14px',
            background: smcVisible 
              ? 'linear-gradient(135deg, rgba(41, 98, 255, 0.2), rgba(0, 210, 255, 0.15))'
              : 'rgba(17, 22, 30, 0.85)',
            border: smcVisible 
              ? '1px solid rgba(41, 98, 255, 0.5)'
              : '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: '8px',
            color: smcVisible ? '#00d2ff' : '#848e9c',
            fontSize: '11px',
            fontWeight: '600',
            fontFamily: 'Inter, sans-serif',
            cursor: 'pointer',
            transition: 'all 0.2s ease',
            backdropFilter: 'blur(10px)',
            boxShadow: smcVisible 
              ? '0 0 20px rgba(41, 98, 255, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1)'
              : '0 2px 8px rgba(0, 0, 0, 0.3)',
          }}
          onMouseEnter={(e) => {
            e.target.style.transform = 'translateY(-1px)';
            e.target.style.boxShadow = smcVisible 
              ? '0 4px 24px rgba(41, 98, 255, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.1)'
              : '0 4px 12px rgba(0, 0, 0, 0.4)';
          }}
          onMouseLeave={(e) => {
            e.target.style.transform = 'translateY(0)';
            e.target.style.boxShadow = smcVisible 
              ? '0 0 20px rgba(41, 98, 255, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1)'
              : '0 2px 8px rgba(0, 0, 0, 0.3)';
          }}
        >
          {/* Toggle Icon */}
          <div style={{
            width: '32px',
            height: '16px',
            background: smcVisible 
              ? 'linear-gradient(90deg, #2962ff, #00d2ff)'
              : 'rgba(255, 255, 255, 0.1)',
            borderRadius: '8px',
            position: 'relative',
            transition: 'all 0.3s ease',
          }}>
            <div style={{
              width: '12px',
              height: '12px',
              background: smcVisible ? '#fff' : '#848e9c',
              borderRadius: '50%',
              position: 'absolute',
              top: '2px',
              left: smcVisible ? '18px' : '2px',
              transition: 'all 0.3s ease',
              boxShadow: smcVisible ? '0 0 8px rgba(0, 210, 255, 0.5)' : 'none',
            }} />
          </div>
          <span>SMC</span>
        </button>
        
        {/* Legend (only when SMC is visible and analysis exists) */}
        {smcVisible && analysis && (
          <div style={{
            background: 'rgba(11, 14, 20, 0.85)',
            padding: '8px 12px',
            borderRadius: '6px',
            fontSize: '10px',
            color: '#d1d4dc',
            display: 'flex',
            gap: '10px',
            flexWrap: 'wrap',
            backdropFilter: 'blur(10px)',
            border: '1px solid rgba(255, 255, 255, 0.05)',
            maxWidth: '320px'
          }}>
            <span style={{ color: '#26a69a' }}>● OB Bull</span>
            <span style={{ color: '#ef5350' }}>● OB Bear</span>
            <span style={{ color: '#26a69a' }}>◇ FVG</span>
            <span style={{ color: '#2962ff' }}>— BOS</span>
            <span style={{ color: '#f44336' }}>┄ CHoCH</span>
            <span style={{ color: '#ff9800' }}>⋯ EQH/EQL</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default TradingChart;
