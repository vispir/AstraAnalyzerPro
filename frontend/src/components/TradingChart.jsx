import React, { useEffect, useRef, useCallback } from 'react';
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

  // ============================================================
  // DRAW SMC ON CANVAS
  // ============================================================
  const drawSMCOverlay = useCallback(() => {
    if (!canvasRef.current || !chartRef.current || !seriesRef.current || !analysis) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const chart = chartRef.current;
    const series = seriesRef.current;
    const timeScale = chart.timeScale();

    // Очищаем canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Получаем видимый диапазон
    const visibleRange = timeScale.getVisibleLogicalRange();
    if (!visibleRange) return;

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

      const rightX = canvas.width;
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

      const rightX = canvas.width;
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
    // 3. BOS/CHoCH LINES
    // ============================================================
    const drawBreakLine = (breaks) => {
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
        ctx.lineTo(breakX, priceY);
        
        if (isChoch) {
          ctx.setLineDash([6, 3]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_CHOCH : SMC_COLORS.BEARISH_CHOCH;
          ctx.lineWidth = 2;
        } else {
          ctx.setLineDash([]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
          ctx.lineWidth = 1.5;
        }
        ctx.stroke();
        ctx.setLineDash([]);

        const labelText = isChoch ? 'CHoCH' : 'BOS';
        ctx.fillStyle = ctx.strokeStyle;
        ctx.font = 'bold 10px Arial';
        ctx.fillText(labelText, (pivotX + breakX) / 2 - 15, priceY - 5);
      });
    };

    drawBreakLine(analysis.all_swing_bos || []);
    drawBreakLine(analysis.all_swing_choch || []);

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
        ctx.lineTo(canvas.width, priceY);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = color;
        ctx.font = '9px Arial';
        ctx.fillText(label, canvas.width - 30, priceY - 3);
      });
    };

    drawEqualLevels(analysis.eqh || [], SMC_COLORS.EQH, 'EQH');
    drawEqualLevels(analysis.eql || [], SMC_COLORS.EQL, 'EQL');

  }, [analysis, history]);

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
      {analysis && (
        <div style={{
          position: 'absolute',
          top: '10px',
          left: '10px',
          background: 'rgba(11, 14, 20, 0.85)',
          padding: '8px 12px',
          borderRadius: '6px',
          fontSize: '11px',
          color: '#d1d4dc',
          zIndex: 20,
          display: 'flex',
          gap: '12px',
          flexWrap: 'wrap'
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
  );
};

export default TradingChart;
