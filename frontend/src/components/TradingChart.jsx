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
  const smcPriceLinesRef = useRef({ rangeHigh: null, rangeLow: null, equilibrium: null });
  const draggingRef = useRef(null);
  const levelsRef = useRef(levels);
  const userInteractedRef = useRef(false); // Флаг взаимодействия пользователя
  
  // Состояние видимости SMC (сохраняем в localStorage)
  const [smcVisible, setSmcVisible] = useState(() => {
    const saved = localStorage.getItem('astra_smc_visible');
    return saved !== null ? JSON.parse(saved) : true;
  });
  
  // State для tooltip с данными свечи
  const [candleData, setCandleData] = useState(null);
  
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

    // TASK 3.1: Safety check - clear and return if data is missing
    if (!history || !history.length || !analysis) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }

    // Очищаем canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Если SMC выключен - не рисуем
    if (!smcVisible) return;

    // Проверяем, что серия имеет данные (пробуем получить координату для последней свечи)
    // Если серия еще не готова, priceToCoordinate вернет null
    try {
      const lastCandle = history[history.length - 1];
      if (!lastCandle || !lastCandle.close) return;
      const testCoord = series.priceToCoordinate(lastCandle.close);
      if (testCoord === null || testCoord === undefined) return; // Серия еще не готова
    } catch {
      return; // Серия не готова или произошла ошибка
    }

    // Получаем видимый диапазон
    const visibleRange = timeScale.getVisibleLogicalRange();
    if (!visibleRange) return;

    // Вычисляем ширину области графика (без шкалы цен справа)
    // timeScale.width() возвращает ширину области свечей
    const chartContentWidth = timeScale.width ? timeScale.width() : (canvas.width - 60);
    const priceScaleWidth = canvas.width - chartContentWidth;
    const chartRightEdge = canvas.width - priceScaleWidth;

    // ============================================================
    // 0. PREMIUM/DISCOUNT/EQUILIBRIUM ZONES (ФОН - рисуем первым)
    // ============================================================
    const advancedZones = analysis.advanced?.zones;
    if (advancedZones && advancedZones.range_high > 0 && advancedZones.range_low > 0) {
      // Используем значения из zones, если они есть, иначе fallback к range
      const premiumTop = advancedZones.premium?.top ?? advancedZones.range_high;
      const premiumBottom = advancedZones.premium?.bottom ?? (advancedZones.range_high * 0.95); // 95% от high как fallback
      const discountTop = advancedZones.discount?.top ?? (advancedZones.range_low * 1.05); // 5% от low как fallback
      const discountBottom = advancedZones.discount?.bottom ?? advancedZones.range_low;
      
      // Equilibrium зона (диапазон между Premium и Discount)
      const equilibriumTop = advancedZones.equilibrium?.top;
      const equilibriumBottom = advancedZones.equilibrium?.bottom;
      
      // Конвертируем цены в Y координаты и проверяем валидность
      const premiumTopY = series.priceToCoordinate(premiumTop);
      const premiumBottomY = series.priceToCoordinate(premiumBottom);
      const discountTopY = series.priceToCoordinate(discountTop);
      const discountBottomY = series.priceToCoordinate(discountBottom);
      const equilibriumTopY = equilibriumTop ? series.priceToCoordinate(equilibriumTop) : null;
      const equilibriumBottomY = equilibriumBottom ? series.priceToCoordinate(equilibriumBottom) : null;
      
      // Проверяем, что координаты валидны (не null и в пределах canvas)
      const isValidCoord = (y) => y !== null && y !== undefined && y >= 0 && y <= canvas.height;
      
      // PREMIUM ZONE (красноватая сверху)
      if (isValidCoord(premiumTopY) && isValidCoord(premiumBottomY)) {
        const premHeight = Math.abs(premiumBottomY - premiumTopY);
        ctx.fillStyle = 'rgba(239, 83, 80, 0.06)';
        ctx.fillRect(0, Math.min(premiumTopY, premiumBottomY), chartRightEdge, premHeight);
        
        ctx.fillStyle = 'rgba(239, 83, 80, 0.5)';
        ctx.font = '9px Inter, Arial';
        ctx.fillText('Premium', chartRightEdge - 50, Math.min(premiumTopY, premiumBottomY) + 12);
      }
      
      // EQUILIBRIUM ZONE (фиолетовая, посередине между Premium и Discount)
      if (equilibriumTop && equilibriumBottom && isValidCoord(equilibriumTopY) && isValidCoord(equilibriumBottomY)) {
        const eqHeight = Math.abs(equilibriumBottomY - equilibriumTopY);
        ctx.fillStyle = 'rgba(156, 39, 176, 0.08)';
        ctx.fillRect(0, Math.min(equilibriumTopY, equilibriumBottomY), chartRightEdge, eqHeight);
        
        ctx.fillStyle = 'rgba(156, 39, 176, 0.5)';
        ctx.font = '9px Inter, Arial';
        ctx.fillText('Equilibrium', chartRightEdge - 70, (Math.min(equilibriumTopY, equilibriumBottomY) + eqHeight / 2));
      }
      
      // DISCOUNT ZONE (зеленоватая снизу)
      if (discountTopY !== null && discountBottomY !== null) {
        const discHeight = Math.abs(discountBottomY - discountTopY);
        ctx.fillStyle = 'rgba(38, 166, 154, 0.06)';
        ctx.fillRect(0, Math.min(discountTopY, discountBottomY), chartRightEdge, discHeight);
        
        ctx.fillStyle = 'rgba(38, 166, 154, 0.5)';
        ctx.font = '9px Inter, Arial';
        ctx.fillText('Discount', chartRightEdge - 50, Math.max(discountTopY, discountBottomY) - 5);
      }
      
      // Swing High/Low теперь рисуются через PriceLines (см. updateSMCPriceLines)
      // Equilibrium также рисуется как линия через PriceLines для точности, но зона на canvas для визуализации
    }

    // ============================================================
    // 1. ORDER BLOCKS (Прямоугольники) - Ограничено последние 3
    // ============================================================
    const orderBlocks = (analysis.order_blocks || []).slice(-3); // Показываем только последние 3
    orderBlocks.forEach(ob => {
      if (ob.bar_index === undefined) return;
      
      // Проверяем границы индекса
      if (ob.bar_index < 0 || ob.bar_index >= history.length) return;
      
      const time = history[ob.bar_index]?.time;
      if (!time) return;

      const topY = series.priceToCoordinate(ob.top);
      const bottomY = series.priceToCoordinate(ob.bottom);
      const leftX = timeScale.timeToCoordinate(time);
      
      if (topY === null || bottomY === null || leftX === null) return;

      const rightX = chartRightEdge;
      const height = Math.abs(bottomY - topY);
      const y = Math.min(topY, bottomY);

      const isBull = ob.type?.includes('BULL');
      
      // Заливка с прозрачностью
      ctx.fillStyle = isBull ? SMC_COLORS.BULL_OB : SMC_COLORS.BEAR_OB;
      ctx.fillRect(leftX, y, rightX - leftX, height);

      // Граница слева (как в LuxAlgo)
      ctx.strokeStyle = isBull ? SMC_COLORS.BULL_OB_BORDER : SMC_COLORS.BEAR_OB_BORDER;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(leftX, y + height);
      ctx.stroke();
      
      // Горизонтальные пунктирные линии сверху и снизу
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.moveTo(leftX, y + height);
      ctx.lineTo(rightX, y + height);
      ctx.stroke();
      ctx.setLineDash([]);

      // Метка OB с тёмным фоном для контраста
      const labelText = isBull ? 'BULL OB' : 'BEAR OB';
      ctx.font = 'bold 9px Inter, Arial';
      const labelWidth = ctx.measureText(labelText).width;
      
      // Тёмный фон под текстом
      ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
      ctx.fillRect(leftX + 2, y + 2, labelWidth + 4, 12);
      
      // Сам текст - более яркий цвет
      ctx.fillStyle = isBull ? '#1de9b6' : '#ff5252';
      ctx.fillText(labelText, leftX + 4, y + 11);
    });

    // ============================================================
    // 2. FAIR VALUE GAPS (Прямоугольники с пунктиром) - Последние 3
    // ============================================================
    const fvgList = (analysis.fvg || []).slice(-3); // Показываем только последние 3
    fvgList.forEach(fvg => {
      if (fvg.bar_index === undefined) return;
      
      // Проверяем границы индекса
      if (fvg.bar_index < 0 || fvg.bar_index >= history.length) return;
      
      const time = history[fvg.bar_index]?.time;
      if (!time) return;

      const topY = series.priceToCoordinate(fvg.top);
      const bottomY = series.priceToCoordinate(fvg.bottom);
      const leftX = timeScale.timeToCoordinate(time);
      
      if (topY === null || bottomY === null || leftX === null) return;

      const rightX = chartRightEdge;
      const height = Math.abs(bottomY - topY);
      const y = Math.min(topY, bottomY);

      const isBull = fvg.type?.includes('BULL');
      
      // Заливка с меньшей прозрачностью
      ctx.fillStyle = isBull ? SMC_COLORS.BULL_FVG : SMC_COLORS.BEAR_FVG;
      ctx.fillRect(leftX, y, rightX - leftX, height);

      // Пунктирные границы
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = isBull ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
      ctx.lineWidth = 1;
      ctx.strokeRect(leftX, y, rightX - leftX, height);
      ctx.setLineDash([]);

      // Метка FVG с тёмным фоном для контраста
      ctx.font = '8px Inter, Arial';
      const fvgLabelWidth = ctx.measureText('FVG').width;
      
      // Тёмный фон
      ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
      ctx.fillRect(leftX + 1, y + 1, fvgLabelWidth + 4, 10);
      
      // Текст - яркий
      ctx.fillStyle = isBull ? '#1de9b6' : '#ff5252';
      ctx.fillText('FVG', leftX + 3, y + 9);
    });

    // ============================================================
    // 3. BOS/CHoCH LINES (Internal + Swing) - LuxAlgo Style
    // ============================================================
    const drawBreakLine = (breaks, isInternal = false) => {
      if (!breaks || !Array.isArray(breaks) || breaks.length === 0) return;
      
      breaks.forEach(brk => {
        // Проверяем наличие необходимых полей
        if (brk.bar_index === undefined || brk.pivot_bar_index === undefined) {
          console.warn('BOS/CHoCH missing bar_index or pivot_bar_index:', brk);
          return;
        }
        
        // Проверяем границы индексов (с учетом что индексы могут быть 0-based)
        if (brk.bar_index >= history.length || brk.pivot_bar_index >= history.length) {
          console.warn(`BOS/CHoCH index out of bounds: bar=${brk.bar_index}, pivot=${brk.pivot_bar_index}, history=${history.length}`);
          return;
        }
        if (brk.bar_index < 0 || brk.pivot_bar_index < 0) {
          return;
        }
        
        const pivotCandle = history[brk.pivot_bar_index];
        const breakCandle = history[brk.bar_index];
        
        if (!pivotCandle?.time || !breakCandle?.time) {
          console.warn('BOS/CHoCH missing candle time:', { pivotCandle, breakCandle });
          return;
        }

        const priceY = series.priceToCoordinate(brk.price);
        const pivotX = timeScale.timeToCoordinate(pivotCandle.time);
        const breakX = timeScale.timeToCoordinate(breakCandle.time);
        
        if (priceY === null || pivotX === null || breakX === null) {
          return;
        }

        const isBullish = brk.type?.includes('BULLISH');
        const isChoch = brk.is_choch === true || brk.type?.includes('CHOCH');
        
        // ============================================================
        // СТИЛЬ ЛИНИЙ (как на TradingView/LuxAlgo)
        // BOS: сплошная линия (—)
        // CHoCH: пунктирная линия (┄)
        // ============================================================
        ctx.beginPath();
        ctx.moveTo(pivotX, priceY);
        ctx.lineTo(Math.min(breakX, chartRightEdge), priceY);
        
        if (isChoch) {
          // CHoCH: пунктирная линия ┄
          ctx.setLineDash([6, 4]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_CHOCH : SMC_COLORS.BEARISH_CHOCH;
          ctx.lineWidth = isInternal ? 1.5 : 2;
        } else {
          // BOS: сплошная линия —
          ctx.setLineDash([]);
          ctx.strokeStyle = isBullish ? SMC_COLORS.BULLISH_BOS : SMC_COLORS.BEARISH_BOS;
          ctx.lineWidth = isInternal ? 1 : 1.5;
        }
        ctx.stroke();
        ctx.setLineDash([]);

        // ============================================================
        //  // МЕТКА с тёмным фоном для контраста (как на TradingView)
        // ============================================================
        const labelText = isChoch ? 'CHoCH' : 'BOS';
        ctx.font = isInternal ? '9px Inter, Arial' : 'bold 10px Inter, Arial';
        const bosLabelWidth = ctx.measureText(labelText).width;
        
        // Позиционируем метку ближе к точке пробоя
        const labelX = Math.max(5, Math.min(breakX - 25, chartRightEdge - 35));
        const labelY = isBullish ? priceY + 12 : priceY - 5;
        // Тёмный фон под текстом
        ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
        ctx.fillRect(labelX - 2, labelY - 9, bosLabelWidth + 4, 12);
        
        // Яркий текст
        if (isChoch) {
          ctx.fillStyle = isBullish ? '#64b5f6' : '#ff8a80'; // Голубой / Светло-красный для CHoCH
        } else {
          ctx.fillStyle = isBullish ? '#1de9b6' : '#ff5252'; // Зелёный / Красный для BOS
        }
        ctx.fillText(labelText, labelX, labelY);
      });
    };

    // ============================================================
    // ОТРИСОВКА СТРУКТУРЫ (ВСЕ BOS/CHoCH без фильтрации)
    // Для визуализации используем all_* массивы
    // ============================================================
    const allInternalBos = analysis.all_internal_bos || [];
    const allInternalChoch = analysis.all_internal_choch || [];
    const allSwingBos = analysis.all_swing_bos || [];
    const allSwingChoch = analysis.all_swing_choch || [];
    
    // Рисуем Internal структуру (более частые, тонкие линии) - ВСЕ без фильтрации
    drawBreakLine(allInternalBos, true);
    drawBreakLine(allInternalChoch, true);
    
    // Рисуем Swing структуру (реже, толще линии) - ВСЕ без фильтрации
    drawBreakLine(allSwingBos, false);
    drawBreakLine(allSwingChoch, false);
    
    // Debug: логируем количество элементов
    const totalBosChoch = allInternalBos.length + allInternalChoch.length + 
                          allSwingBos.length + allSwingChoch.length;
    if (totalBosChoch > 0) {
      console.log(`SMC Structure v6: Internal BOS=${allInternalBos.length}, CHoCH=${allInternalChoch.length}, ` +
        `Swing BOS=${allSwingBos.length}, CHoCH=${allSwingChoch.length}, ` +
                  `Total drawn=${totalBosChoch}, History=${history.length}`);
    }

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

    // Сохраняем текущие значения ref для cleanup функции
    const currentSmcPriceLines = smcPriceLinesRef.current;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
      layout: { background: { color: '#0b0e14' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: 'rgba(42, 46, 57, 0.05)' }, horzLines: { color: 'rgba(42, 46, 57, 0.05)' } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        borderColor: 'rgba(255, 255, 255, 0.1)',
        shiftVisibleRangeOnNewBar: false,
      },
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
    
    // Отслеживаем взаимодействие пользователя (zoom/scroll)
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      userInteractedRef.current = true;
    });

    // Непрерывная перерисовка оверлея: уровни остаются привязаны к ценам при растяжении графика (вертикальный зум)
    let rafId = null;
    const tick = () => {
      drawSMCOverlay();
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    
    // Обработчик для tooltip с данными свечи
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData || !param.seriesData.get(series)) {
        setCandleData(null);
        return;
      }
      
      const data = param.seriesData.get(series);
      if (data && data.open !== undefined) {
        const change = data.close - data.open;
        const changePercent = ((change / data.open) * 100);
        
        setCandleData({
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
          change: change,
          changePercent: changePercent,
          isBullish: data.close >= data.open
        });
      }
    });

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
      if (rafId !== null) cancelAnimationFrame(rafId);
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('resize', handleResize);
      
      // Очищаем SMC PriceLines (используем значения, сохраненные в начале useEffect)
      const seriesForCleanup = seriesRef.current;
      if (seriesForCleanup && currentSmcPriceLines) {
        Object.keys(currentSmcPriceLines).forEach(key => {
          if (currentSmcPriceLines[key]) {
            try {
              seriesForCleanup.removePriceLine(currentSmcPriceLines[key]);
            } catch {
              // Игнорируем ошибки при очистке
            }
          }
        });
      }
      
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

  // Функция для очистки всех SMC PriceLines
  const clearSMCPriceLines = useCallback(() => {
    if (!seriesRef.current) return;
    const series = seriesRef.current;
    const smcLines = smcPriceLinesRef.current;
    Object.keys(smcLines).forEach(key => {
      if (smcLines[key]) {
        try {
          series.removePriceLine(smcLines[key]);
          smcLines[key] = null;
        } catch (err) {
          console.warn(`Failed to remove ${key} line:`, err);
        }
      }
    });
  }, []);

  // Функция для управления SMC PriceLines (range_high, range_low, equilibrium)
  const updateSMCPriceLines = useCallback(() => {
    if (!seriesRef.current || !smcVisible || !analysis) {
      console.log('updateSMCPriceLines skipped:', { 
        hasSeries: !!seriesRef.current, 
        smcVisible, 
        hasAnalysis: !!analysis 
      });
      return;
    }
    
    const series = seriesRef.current;
    const advancedZones = analysis.advanced?.zones;
    const keyLevels = analysis.advanced?.key_levels;
    
    if (!advancedZones || advancedZones.range_high <= 0) {
      console.warn('updateSMCPriceLines: invalid zones', advancedZones);
      return;
    }
    
    console.log('updateSMCPriceLines: creating/updating lines', {
      range_high: advancedZones.range_high,
      range_low: advancedZones.range_low,
      equilibrium: keyLevels?.Equilibrium_Price || ((advancedZones.range_high + advancedZones.range_low) / 2)
    });

    const updateLine = (key, price, color, lineStyle, title) => {
      const parsedPrice = parseFloat(price);
      if (isNaN(parsedPrice) || parsedPrice <= 0) return;
      
      if (smcPriceLinesRef.current[key]) {
        try {
          smcPriceLinesRef.current[key].applyOptions({ price: parsedPrice, color, lineStyle, title });
        } catch (e) { console.warn(e); }
      } else {
        try {
          smcPriceLinesRef.current[key] = series.createPriceLine({
            price: parsedPrice, color, lineWidth: 1, lineStyle, title, axisLabelVisible: true
          });
        } catch (e) { console.warn(e); }
      }
    };

    // Swing High
    updateLine('rangeHigh', advancedZones.range_high, 'rgba(239, 83, 80, 0.6)', 1, 'Swing High');
    
    // Swing Low
    updateLine('rangeLow', advancedZones.range_low, 'rgba(38, 166, 154, 0.6)', 1, 'Swing Low');
    
    // 2. Equilibrium (Штрих-пунктир 2)
    // Берем цену из key_levels ИЛИ считаем среднее сами
    const eqPrice = keyLevels?.Equilibrium_Price || ((advancedZones.range_high + advancedZones.range_low) / 2);
    
    if (eqPrice > 0) {
      updateLine('equilibrium', eqPrice, 'rgba(156, 39, 176, 0.8)', 2, 'Equilibrium');
    }
  }, [smcVisible, analysis]);

  useEffect(() => {
    if (!seriesRef.current || !history?.length) return;
    const chart = chartRef.current;
    if (!chart) return;

    // КРИТИЧНО: Удаляем старые PriceLines перед установкой новых данных
    // Это предотвращает "призраков" старых линий при переключении таймфрейма
    clearSMCPriceLines();

    // TASK 3.2.1: Immediately clear canvas to prevent old zones "ghosting"
    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext('2d');
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
    }

    const timeScale = chart.timeScale();
    
    // Сохраняем текущий видимый диапазон ТОЛЬКО если пользователь взаимодействовал
    let savedRange = null;
    if (userInteractedRef.current) {
      const currentRange = timeScale.getVisibleLogicalRange && timeScale.getVisibleLogicalRange();
      if (currentRange && currentRange.from !== undefined && currentRange.to !== undefined) {
        savedRange = { from: currentRange.from, to: currentRange.to };
      }
    }

    // КРИТИЧНО: Устанавливаем данные свечей
    seriesRef.current.setData(history);
    
    // Пересчитываем координаты видимой области
    try {
      if (!userInteractedRef.current) {
        // Только при первой загрузке - подгоняем под данные
        chart.timeScale().fitContent();
      }
    } catch (e) {
      console.warn('Could not fit content:', e.message);
    }

    // Восстанавливаем диапазон после небольшой задержки
    if (savedRange && timeScale.setVisibleLogicalRange) {
      setTimeout(() => {
        try {
          if (chartRef.current) {
            chartRef.current.timeScale().setVisibleLogicalRange(savedRange);
          }
        } catch (e) {
          console.warn('Could not restore visible range:', e.message);
        }
      }, 50);
    }
    
    // TASK 3.2.2: Increased timeout to 150ms for price scale stabilization
    // Обновляем PriceLines только если есть analysis (иначе они будут созданы когда придет analysis)
    const stabilityTimeout = setTimeout(() => {
      if (analysis) {
        updateSMCPriceLines();
      }
      drawSMCOverlay();
    }, 150);

    if (analysis) {
      console.log('SMC Analysis received:', {
        history_length: history.length,
        order_blocks: analysis.order_blocks?.length || 0,
        fvg: analysis.fvg?.length || 0,
        all_internal_bos: analysis.all_internal_bos?.length || 0,
        all_internal_choch: analysis.all_internal_choch?.length || 0,
        all_swing_bos: analysis.all_swing_bos?.length || 0,
        all_swing_choch: analysis.all_swing_choch?.length || 0,
        eqh: analysis.eqh?.length || 0,
        eql: analysis.eql?.length || 0,
        sample_bos: analysis.all_internal_bos?.[0] || 'none'
      });
    }

    return () => clearTimeout(stabilityTimeout);
  }, [history, drawSMCOverlay, analysis, updateSMCPriceLines, clearSMCPriceLines]);

  useEffect(() => {
    // Обновляем SMC PriceLines при изменении analysis
    updateSMCPriceLines();
    drawSMCOverlay();
  }, [analysis, drawSMCOverlay, updateSMCPriceLines]);

  // Перерисовываем при переключении SMC
  useEffect(() => {
    // Удаляем или создаём PriceLines в зависимости от видимости
    if (!smcVisible) {
      // Удаляем все SMC PriceLines
      const series = seriesRef.current;
      const smcLines = smcPriceLinesRef.current;
      if (series) {
        Object.keys(smcLines).forEach(key => {
          if (smcLines[key]) {
            try {
              series.removePriceLine(smcLines[key]);
              smcLines[key] = null;
            } catch (err) {
              console.warn(`Failed to remove ${key} line:`, err);
            }
          }
        });
      }
    } else {
      // Создаём PriceLines если SMC включен
      updateSMCPriceLines();
    }
    drawSMCOverlay();
  }, [smcVisible, drawSMCOverlay, updateSMCPriceLines, clearSMCPriceLines]);

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
      
      {/* SMC Toggle Button + Legend | OHLC Tooltip справа */}
      <div style={{
        position: 'absolute',
        top: '10px',
        left: '10px',
        zIndex: 20,
        display: 'flex',
        flexDirection: 'row',
        alignItems: 'flex-start',
        gap: '10px',
        pointerEvents: 'none' // НЕ блокируем crosshair
      }}>
        {/* Левая колонка: SMC переключатель + легенда */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
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
            pointerEvents: 'auto', // Кнопка кликабельна
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
            background: 'rgba(11, 14, 20, 0.9)',
            padding: '8px 12px',
            borderRadius: '6px',
            fontSize: '9px',
            color: '#d1d4dc',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            backdropFilter: 'blur(10px)',
            border: '1px solid rgba(255, 255, 255, 0.05)',
            maxWidth: '180px'
          }}>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: '#26a69a' }}>● OB Bull</span>
              <span style={{ color: '#ef5350' }}>● OB Bear</span>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: '#26a69a' }}>◇ FVG</span>
              <span style={{ color: '#ff9800' }}>⋯ EQH/EQL</span>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: '#26a69a' }}>— BOS</span>
              <span style={{ color: '#2962ff' }}>┄ CHoCH</span>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '4px', marginTop: '2px' }}>
              <span style={{ color: 'rgba(239, 83, 80, 0.7)' }}>▓ Premium</span>
              <span style={{ color: 'rgba(38, 166, 154, 0.7)' }}>▓ Discount</span>
            </div>
          </div>
        )}
        </div>
        
        {/* Candle Data Tooltip — справа от SMC, одна строка */}
        {candleData && (
          <div style={{
            background: 'rgba(11, 14, 20, 0.95)',
            padding: '8px 14px',
            borderRadius: '8px',
            fontSize: '11px',
            fontWeight: '500',
            fontFamily: 'Inter, sans-serif',
            color: candleData.isBullish ? '#26a69a' : '#ef5350',
            backdropFilter: 'blur(10px)',
            border: `1px solid ${candleData.isBullish ? 'rgba(38, 166, 154, 0.3)' : 'rgba(239, 83, 80, 0.3)'}`,
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.4)',
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'center',
            gap: '14px',
            whiteSpace: 'nowrap'
          }}>
            <span style={{ color: '#848e9c', fontSize: '9px', fontWeight: '600' }}>ОТКР</span>
            <span>{candleData.open.toFixed(3)}</span>
            <span style={{ color: 'rgba(255,255,255,0.2)', margin: '0 2px' }}>|</span>
            <span style={{ color: '#848e9c', fontSize: '9px', fontWeight: '600' }}>МАКС</span>
            <span>{candleData.high.toFixed(3)}</span>
            <span style={{ color: 'rgba(255,255,255,0.2)', margin: '0 2px' }}>|</span>
            <span style={{ color: '#848e9c', fontSize: '9px', fontWeight: '600' }}>МИН</span>
            <span>{candleData.low.toFixed(3)}</span>
            <span style={{ color: 'rgba(255,255,255,0.2)', margin: '0 2px' }}>|</span>
            <span style={{ color: '#848e9c', fontSize: '9px', fontWeight: '600' }}>ЗАКР</span>
            <span>{candleData.close.toFixed(3)}</span>
            <span style={{ color: 'rgba(255,255,255,0.2)', margin: '0 2px' }}>|</span>
            <span style={{ fontWeight: '700', fontSize: '11px' }}>
              {candleData.change >= 0 ? '+' : ''}{candleData.change.toFixed(3)}
            </span>
            <span style={{ fontSize: '10px', opacity: 0.8 }}>
              ({candleData.changePercent >= 0 ? '+' : ''}{candleData.changePercent.toFixed(2)}%)
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

export default TradingChart;