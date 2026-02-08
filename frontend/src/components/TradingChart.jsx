import { useEffect, useRef, useCallback, useState } from 'react';
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
const TradingChart = ({ history, analysis, levels, setLevels, activeMode, setActiveMode, serverConnected, projections = [], setProjections, placementMode, onPlaceProjection, tf, source }) => {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const canvasRef = useRef(null);
  const linesRef = useRef({ entry: null, sl: null, tp: null });
  const smcPriceLinesRef = useRef({ rangeHigh: null, rangeLow: null, equilibrium: null });
  const projectionPriceLinesRef = useRef({}); // { [projId]: { entry, sl, tp } }
  const draggingRef = useRef(null);
  const levelsRef = useRef(levels);
  const userInteractedRef = useRef(false); // Флаг взаимодействия пользователя
  const hasInitialFitRef = useRef(false); // Первая загрузка — fitContent, далее всегда сохраняем позицию
  const lastTfSourceRef = useRef(null); // tf+source для сброса при смене таймфрейма/источника
  const lastVisibleLogicalRangeRef = useRef(null);
  const tfRef = useRef(tf);
  const historyRef = useRef(history);
  const drawSMCOverlayRef = useRef(null);
  const projectionRectsRef = useRef({}); // Hit-test rects для проекций
  const selectedProjectionRef = useRef(null); // Выбранная проекция для контекстного меню
  const [contextMenu, setContextMenu] = useState(null); // { x, y, projectionId }
  const projectionDragRef = useRef(null); // { id, startX, startY, startEntry, startSl, startTp, startTime }
  const projectionResizeRef = useRef(null); // { id, handle: 'sl'|'tp', startY, startPrice }
  const projectionsRef = useRef(projections);
  const placementModeRef = useRef(placementMode);
  const onPlaceProjectionRef = useRef(onPlaceProjection);
  useEffect(() => { projectionsRef.current = projections; }, [projections]);
  useEffect(() => { placementModeRef.current = placementMode; }, [placementMode]);
  useEffect(() => { onPlaceProjectionRef.current = onPlaceProjection; }, [onPlaceProjection]);
  useEffect(() => { tfRef.current = tf; }, [tf]);
  useEffect(() => { historyRef.current = history; }, [history]);
  
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

    // TASK 3.1: Safety check - clear and return if data is missing (projections draw without analysis)
    if (!history || !history.length) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }

    // Очищаем canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Если SMC выключен - не рисуем SMC (проекции рисуем в конце)
    const drawSMC = smcVisible && analysis;

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
    let eqTopY = null;
    let eqBottomY = null;
    let eqHeight = null;
    if (drawSMC) {
    const advancedZones = analysis.advanced?.zones;
    if (advancedZones && advancedZones.range_high > 0 && advancedZones.range_low > 0) {
      const rangeHigh = advancedZones.range_high;
      const rangeLow = advancedZones.range_low;
      const rangeSize = rangeHigh - rangeLow;
      const clamp = (v) => Math.max(rangeLow, Math.min(rangeHigh, v));

      // Зоны в пределах [range_low, range_high]
      const premiumTop = clamp(advancedZones.premium?.top ?? rangeHigh);
      const premiumBottom = clamp(advancedZones.premium?.bottom ?? (rangeLow + rangeSize * 0.618));
      const discountTop = clamp(advancedZones.discount?.top ?? (rangeLow + rangeSize * 0.382));
      const discountBottom = clamp(advancedZones.discount?.bottom ?? rangeLow);

      const equilibriumTop = clamp(advancedZones.equilibrium?.top ?? (rangeLow + rangeSize * 0.618));
      const equilibriumBottom = clamp(advancedZones.equilibrium?.bottom ?? (rangeLow + rangeSize * 0.382));
      
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
        
        ctx.font = '9px Inter, Arial';
        const premText = 'Premium';
        const premW = ctx.measureText(premText).width;
        const premX = chartRightEdge - premW - 12;
        const premY = Math.min(premiumTopY, premiumBottomY) + 4;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(premX - 4, premY - 9, premW + 8, 12);
        ctx.fillStyle = 'rgba(239, 83, 80, 0.9)';
        ctx.fillText(premText, premX, premY + 2);
      }
      
      // EQUILIBRIUM ZONE (фиолетовая, посередине между Premium и Discount)
      if (equilibriumTop && equilibriumBottom && isValidCoord(equilibriumTopY) && isValidCoord(equilibriumBottomY)) {
        eqTopY = Math.min(equilibriumTopY, equilibriumBottomY);
        eqBottomY = Math.max(equilibriumTopY, equilibriumBottomY);
        eqHeight = Math.abs(equilibriumBottomY - equilibriumTopY);
      }
      
      // DISCOUNT ZONE (зеленоватая снизу)
      if (discountTopY !== null && discountBottomY !== null) {
        const discHeight = Math.abs(discountBottomY - discountTopY);
        ctx.fillStyle = 'rgba(38, 166, 154, 0.06)';
        ctx.fillRect(0, Math.min(discountTopY, discountBottomY), chartRightEdge, discHeight);
        
        ctx.font = '9px Inter, Arial';
        const discText = 'Discount';
        const discW = ctx.measureText(discText).width;
        const discX = chartRightEdge - discW - 12;
        const discY = Math.max(discountTopY, discountBottomY) - 18;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(discX - 4, discY - 9, discW + 8, 12);
        ctx.fillStyle = 'rgba(38, 166, 154, 0.9)';
        ctx.fillText(discText, discX, discY + 2);
      }
      
      // v16.0: Приоритет — backend (H4 инверсия), иначе по trend
      const kl = analysis.advanced?.key_levels;
      const highLabel = (kl?.High_Type === 'Strong High' || kl?.High_Type === 'Weak High') ? kl.High_Type : (analysis.trend === 'DOWNTREND' ? 'Strong High' : 'Weak High');
      const lowLabel = (kl?.Low_Type === 'Strong Low' || kl?.Low_Type === 'Weak Low') ? kl.Low_Type : (analysis.trend === 'DOWNTREND' ? 'Weak Low' : 'Strong Low');

      const drawRangeLabel = (label, price, color, yOffset) => {
        const y = series.priceToCoordinate(price);
        if (y === null || y === undefined || y < 0 || y > canvas.height) return;
        ctx.font = '9px Inter, Arial';
        const textWidth = ctx.measureText(label).width;
        const x = chartRightEdge - textWidth - 8;
        const textY = Math.max(10, Math.min(canvas.height - 6, y + yOffset));
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(x - 4, textY - 9, textWidth + 8, 12);
        ctx.fillStyle = color;
        ctx.fillText(label, x, textY);
      };

      drawRangeLabel(highLabel, rangeHigh, 'rgba(239, 83, 80, 0.9)', -6);
      drawRangeLabel(lowLabel, rangeLow, 'rgba(38, 166, 154, 0.9)', 12);

      // Диапазон рисуется через PriceLines (см. updateSMCPriceLines)
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

    if (eqTopY != null && eqBottomY != null && eqHeight != null) {
      ctx.fillStyle = 'rgba(156, 39, 176, 0.12)';
      ctx.fillRect(0, eqTopY, chartRightEdge, eqHeight);
      // Метка Equilibrium — рисуем позже, поверх OB/FVG/BOS/EQH/EQL (см. конец SMC блока)
    }

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
        
        const lineMinX = Math.min(pivotX, breakX);
        const lineMaxX = Math.max(pivotX, breakX);
        if (lineMaxX < 0 || lineMinX > chartRightEdge) return;

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

        const isLabelVisible = breakX >= 0 && breakX <= chartRightEdge && priceY >= 0 && priceY <= canvas.height;
        if (!isLabelVisible) return;

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
      const resolveX = (lvl, side) => {
        const timeKey = side === 'left' ? 'left_time' : 'right_time';
        const indexKey = side === 'left' ? 'left_index' : 'right_index';
        const t = lvl?.[timeKey];
        if (t != null) {
          const x = timeScale.timeToCoordinate(t);
          if (x !== null && x !== undefined) return x;
        }
        const idx = lvl?.[indexKey];
        if (idx != null && history?.[idx]?.time != null) {
          const x = timeScale.timeToCoordinate(history[idx].time);
          if (x !== null && x !== undefined) return x;
        }
        return null;
      };

      eqLevels.forEach(lvl => {
        const priceY = series.priceToCoordinate(lvl.price);
        if (priceY === null) return;

        const leftX = resolveX(lvl, 'left') ?? 0;
        const rightX = resolveX(lvl, 'right') ?? chartRightEdge;
        const x1 = Math.max(0, Math.min(leftX, rightX));
        const x2 = Math.min(chartRightEdge, Math.max(leftX, rightX));
        if (x2 <= 0 || x1 >= chartRightEdge) return;

        ctx.beginPath();
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.moveTo(x1, priceY);
        ctx.lineTo(x2, priceY);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = color;
        ctx.font = '9px Arial';
        const labelX = Math.max(0, Math.min(chartRightEdge - 35, x2 - 35));
        ctx.fillText(label, labelX, priceY - 3);
      });
    };

    drawEqualLevels(analysis.eqh || [], SMC_COLORS.EQH, 'EQH');
    drawEqualLevels(analysis.eql || [], SMC_COLORS.EQL, 'EQL');

    // Equilibrium — метка поверх OB, FVG, BOS/CHoCH, EQH/EQL
    if (eqTopY != null && eqBottomY != null && eqHeight != null) {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
      ctx.fillRect(chartRightEdge - 78, eqTopY + eqHeight / 2 - 10, 74, 14);
      ctx.fillStyle = 'rgba(156, 39, 176, 0.95)';
      ctx.font = '9px Inter, Arial';
      ctx.fillText('Equilibrium', chartRightEdge - 74, eqTopY + eqHeight / 2 + 2);
    }
    } // конец if (drawSMC)

    // ============================================================
    // 5. LONG/SHORT ПРОЕКЦИИ (TradingView style) — рисуем всегда
    // Фиксированная ширина бара, позиция по времени (свече)
    // ============================================================
    const DEFAULT_PROJ_WIDTH = 90;
    const projList = projections || [];
    projectionRectsRef.current = {};
    const lastCandleTime = history?.length ? history[history.length - 1]?.time : null;
    const getTfSeconds = (value) => {
      const tfValue = (value || '').toString().toUpperCase();
      const match = tfValue.match(/^([MHDW])(\d+)$/);
      if (!match) return 60;
      const unit = match[1];
      const amount = parseInt(match[2], 10);
      if (!amount) return 60;
      if (unit === 'M') return amount * 60;
      if (unit === 'H') return amount * 3600;
      if (unit === 'D') return amount * 86400;
      if (unit === 'W') return amount * 604800;
      return 60;
    };

    const getXFromTime = (t) => {
      const directX = timeScale.timeToCoordinate(t);
      if (directX !== null && directX !== undefined) return directX;
      if (!history?.length) return null;
      const lastTime = history[history.length - 1]?.time;
      if (typeof lastTime !== 'number') return null;
      const lastX = timeScale.timeToCoordinate(lastTime);
      if (lastX === null || lastX === undefined) return null;
      const barSpacing = timeScale.barSpacing?.() ?? 8;
      const tfSeconds = getTfSeconds(tf);
      const barsOffset = (t - lastTime) / tfSeconds;
      return lastX + barsOffset * barSpacing;
    };

    projList.forEach((proj) => {
      const entry = parseFloat(proj.entry);
      const sl = parseFloat(proj.sl);
      const tp = parseFloat(proj.tp);
      if (isNaN(entry) || isNaN(sl) || isNaN(tp)) return;

      const t = proj.time ?? lastCandleTime;
      if (t == null) return;
      const leftX = getXFromTime(t);
      if (leftX === null || leftX === undefined) return;

      const topPrice = Math.max(entry, sl, tp);
      const bottomPrice = Math.min(entry, sl, tp);
      const topY = series.priceToCoordinate(topPrice);
      const bottomY = series.priceToCoordinate(bottomPrice);
      if (topY === null || bottomY === null) return;

      const width = Math.max(40, parseFloat(proj.widthPx ?? proj.width ?? DEFAULT_PROJ_WIDTH));
      const widthDraw = Math.max(0, Math.min(width, chartRightEdge - leftX));
      if (widthDraw <= 0) return;
      const rightX = leftX + widthDraw;
      const height = Math.abs(bottomY - topY) || 20;
      const rectTop = Math.min(topY, bottomY);
      const rectBottom = Math.max(topY, bottomY);
      const entryY = series.priceToCoordinate(entry);
      const slY = series.priceToCoordinate(sl);
      const tpY = series.priceToCoordinate(tp);
      if (entryY === null || slY === null || tpY === null) return;

      projectionRectsRef.current[proj.id] = {
        left: leftX, top: rectTop, right: rightX, bottom: rectBottom,
        entryY, slY, tpY, entry, sl, tp,
      };

      const isLong = proj.type === 'long';

      // Long: green (TP) above entry, red (SL) below. Short: red above, green below
      if (isLong) {
        ctx.fillStyle = 'rgba(20, 90, 70, 0.25)';
        ctx.fillRect(leftX, Math.min(entryY, tpY), widthDraw, Math.abs(tpY - entryY));
        ctx.fillStyle = 'rgba(90, 25, 25, 0.25)';
        ctx.fillRect(leftX, Math.min(entryY, slY), widthDraw, Math.abs(slY - entryY));
      } else {
        ctx.fillStyle = 'rgba(90, 25, 25, 0.25)';
        ctx.fillRect(leftX, Math.min(entryY, slY), widthDraw, Math.abs(slY - entryY));
        ctx.fillStyle = 'rgba(20, 90, 70, 0.25)';
        ctx.fillRect(leftX, Math.min(entryY, tpY), widthDraw, Math.abs(tpY - entryY));
      }

      ctx.strokeStyle = 'rgba(255,255,255,0.4)';
      ctx.lineWidth = 1;
      ctx.strokeRect(leftX, rectTop, widthDraw, height);

      ctx.strokeStyle = 'rgba(255,255,255,0.6)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(leftX, entryY);
      ctx.lineTo(rightX, entryY);
      ctx.stroke();

      ctx.fillStyle = '#fff';
      ctx.font = '9px Inter, Arial';
      ctx.fillText('E', leftX + 4, entryY + 3);

      const risk = Math.abs(entry - sl);
      const reward = Math.abs(tp - entry);
      if (risk > 0 && reward >= 0) {
        const ratio = reward / risk;
        const ratioText = ratio
          .toFixed(2)
          .replace(/\.00$/, '')
          .replace(/(\.\d)0$/, '$1');
        const rrText = `R:R 1:${ratioText}`;
        ctx.font = '11px Inter, Arial';
        const textWidth = ctx.measureText(rrText).width;
        const textX = leftX + widthDraw / 2 - textWidth / 2;
        const textY = rectTop + height / 2 + 4;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
        ctx.fillRect(textX - 6, textY - 12, textWidth + 12, 16);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
        ctx.fillText(rrText, textX, textY);
      }
    });

  }, [analysis, history, smcVisible, projections, tf]);
  useEffect(() => { drawSMCOverlayRef.current = drawSMCOverlay; }, [drawSMCOverlay]);

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
        drawSMCOverlayRef.current?.();
      }
    };
    resizeCanvas();

    chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawSMCOverlayRef.current?.());
    chart.subscribeCrosshairMove(() => drawSMCOverlayRef.current?.());
    
    // Отслеживаем взаимодействие пользователя (zoom/scroll)
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      userInteractedRef.current = true;
      if (range) lastVisibleLogicalRangeRef.current = range;
    });

    // Непрерывная перерисовка оверлея: уровни остаются привязаны к ценам при растяжении графика (вертикальный зум)
    let rafId = null;
    const tick = () => {
      drawSMCOverlayRef.current?.();
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
    
    const RESIZE_THRESHOLD = 8;
    const getTfSeconds = (value) => {
      const tfValue = (value || '').toString().toUpperCase();
      const match = tfValue.match(/^([MHDW])(\d+)$/);
      if (!match) return 60;
      const unit = match[1];
      const amount = parseInt(match[2], 10);
      if (!amount) return 60;
      if (unit === 'M') return amount * 60;
      if (unit === 'H') return amount * 3600;
      if (unit === 'D') return amount * 86400;
      if (unit === 'W') return amount * 604800;
      return 60;
    };

    const getTimeFromX = (x) => {
      const ts = chart.timeScale();
      const time = ts.coordinateToTime ? ts.coordinateToTime(x) : null;
      if (time != null) return time;
      const data = historyRef.current;
      if (!data || !data.length) return null;
      const lastTime = data[data.length - 1]?.time;
      if (typeof lastTime !== 'number') return null;
      const lastX = ts.timeToCoordinate ? ts.timeToCoordinate(lastTime) : null;
      if (lastX == null) return lastTime;
      const barSpacing = ts.barSpacing?.() ?? 8;
      const barsOffset = (x - lastX) / barSpacing;
      const offsetSeconds = Math.round(barsOffset) * getTfSeconds(tfRef.current);
      return lastTime + offsetSeconds;
    };

    const getChartRightEdge = () => {
      const ts = chart.timeScale();
      if (ts.width) return ts.width();
      return container.clientWidth;
    };

    const getProjectionAtPoint = (x, y) => {
      const rects = projectionRectsRef.current;
      for (const [id, r] of Object.entries(rects)) {
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return id;
      }
      return null;
    };

    const getProjectionResizeHandle = (x, y) => {
      const rects = projectionRectsRef.current;
      for (const [id, r] of Object.entries(rects)) {
        if (x < r.left || x > r.right) continue;
        if (r.slY != null && Math.abs(y - r.slY) <= RESIZE_THRESHOLD) return { id, handle: 'sl' };
        if (r.tpY != null && Math.abs(y - r.tpY) <= RESIZE_THRESHOLD) return { id, handle: 'tp' };
      }
      return null;
    };

    const updateProjectionPriceLines = (proj) => {
      const s = seriesRef.current;
      const pl = projectionPriceLinesRef.current;
      if (!s || !proj || !pl[proj.id]) return;
      const entry = parseFloat(proj.entry);
      const sl = parseFloat(proj.sl);
      const tp = parseFloat(proj.tp);
      if (isNaN(entry) || isNaN(sl) || isNaN(tp)) return;
      const lines = pl[proj.id];
      try {
        if (lines.entry) lines.entry.applyOptions({ price: entry });
        if (lines.sl) lines.sl.applyOptions({ price: sl });
        if (lines.tp) lines.tp.applyOptions({ price: tp });
      } catch { /* ignore */ }
    };

    const onMouseDown = (e) => {
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Режим размещения: клик на графике создаёт проекцию
      const placeCb = onPlaceProjectionRef.current;
      if (placementModeRef.current && placeCb && !getProjectionAtPoint(x, y)) {
        const time = getTimeFromX(x);
        const price = series.coordinateToPrice(y);
        if (time != null && price != null) {
          e.preventDefault();
          e.stopPropagation();
          placeCb(time, price, placementModeRef.current.type);
          return;
        }
      }

      const resizeHandle = getProjectionResizeHandle(x, y);
      if (resizeHandle && setProjections) {
        const proj = (projectionsRef.current || []).find(p => p.id === resizeHandle.id);
        if (proj) {
          e.preventDefault();
          e.stopPropagation();
          selectedProjectionRef.current = resizeHandle.id;
          projectionResizeRef.current = {
            id: resizeHandle.id,
            handle: resizeHandle.handle,
            startY: y,
            startPrice: parseFloat(proj[resizeHandle.handle]),
          };
          chart.applyOptions({ handleScroll: false, handleScale: false });
          return;
        }
      }

      const projId = getProjectionAtPoint(x, y);
      if (projId && setProjections) {
        const proj = (projectionsRef.current || []).find(p => p.id === projId);
        if (proj) {
          const rects = projectionRectsRef.current;
          const rect = rects ? rects[projId] : null;
          const dragOffsetX = rect ? x - rect.left : 0;
          e.preventDefault();
          e.stopPropagation();
          selectedProjectionRef.current = projId;
          projectionDragRef.current = {
            id: projId,
            startX: x,
            startY: y,
            dragOffsetX,
            startEntry: parseFloat(proj.entry),
            startSl: parseFloat(proj.sl),
            startTp: parseFloat(proj.tp),
            startTime: proj.time,
          };
          chart.applyOptions({ handleScroll: false, handleScale: false });
          return;
        }
      }

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
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Ресайз SL или TP (точка входа статична)
      if (projectionResizeRef.current && setProjections) {
        const resize = projectionResizeRef.current;
        const priceNow = series.coordinateToPrice(y);
        if (priceNow != null) {
          const updated = (projectionsRef.current || []).map(p => {
            if (p.id !== resize.id) return p;
            const entryPrice = parseFloat(p.entry);
            const isLong = p.type === 'long';
            const minStep = 0.01;
            const constrainedPrice = isLong
              ? (resize.handle === 'sl' ? Math.min(priceNow, entryPrice - minStep) : Math.max(priceNow, entryPrice + minStep))
              : (resize.handle === 'sl' ? Math.max(priceNow, entryPrice + minStep) : Math.min(priceNow, entryPrice - minStep));
            return { ...p, [resize.handle]: constrainedPrice.toFixed(2) };
          });
          setProjections(updated);
          const proj = updated.find(p => p.id === resize.id);
          if (proj) updateProjectionPriceLines(proj);
        }
        return;
      }
      
      if (projectionDragRef.current && setProjections) {
        const drag = projectionDragRef.current;
        const priceAtStart = series.coordinateToPrice(drag.startY);
        const priceAtNow = series.coordinateToPrice(y);
        const targetX = drag.dragOffsetX != null ? x - drag.dragOffsetX : x;
        const projForDrag = (projectionsRef.current || []).find(p => p.id === drag.id);
        const projWidth = Math.max(40, parseFloat(projForDrag?.widthPx ?? projForDrag?.width ?? 90));
        const maxLeftX = Math.max(0, getChartRightEdge() - projWidth);
        const clampedX = Math.min(targetX, maxLeftX);
        const newTime = getTimeFromX(clampedX);
        if (priceAtStart != null && priceAtNow != null) {
          const deltaPrice = priceAtNow - priceAtStart;
          const updated = (projectionsRef.current || []).map(p => {
            if (p.id !== drag.id) return p;
            return {
              ...p,
              entry: (drag.startEntry + deltaPrice).toFixed(2),
              sl: (drag.startSl + deltaPrice).toFixed(2),
              tp: (drag.startTp + deltaPrice).toFixed(2),
              time: newTime != null ? newTime : p.time,
            };
          });
          setProjections(updated);
          const proj = updated.find(p => p.id === drag.id);
          if (proj) updateProjectionPriceLines(proj);
        }
        return;
      }

      if (draggingRef.current) {
        const price = series.coordinateToPrice(y);
        if (price) {
          setLevels(prev => ({ ...prev, [draggingRef.current]: price.toFixed(2) }));
        }
        return;
      }

      const resizeHandle = getProjectionResizeHandle(x, y);
      if (resizeHandle) {
        container.style.cursor = 'ns-resize';
        return;
      }

      const projId = getProjectionAtPoint(x, y);
      if (projId) {
        container.style.cursor = 'move';
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
      container.style.cursor = placementModeRef.current ? 'crosshair' : (isNearLine ? 'ns-resize' : 'crosshair');
    };

    const onMouseUp = () => {
      if (projectionDragRef.current || projectionResizeRef.current) {
        projectionDragRef.current = null;
        projectionResizeRef.current = null;
        chart.applyOptions({ handleScroll: true, handleScale: true });
      }
      if (draggingRef.current) {
        draggingRef.current = null;
        chart.applyOptions({ handleScroll: true, handleScale: true });
      }
    };

    const onContextMenu = (e) => {
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const projId = getProjectionAtPoint(x, y);
      if (projId && setProjections) {
        e.preventDefault();
        selectedProjectionRef.current = projId;
        setContextMenu({ x, y, projectionId: projId });
      }
    };

    const onKeyDown = (e) => {
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const sel = selectedProjectionRef.current;
        if (sel && setProjections) {
          e.preventDefault();
          setProjections(prev => prev.filter(p => p.id !== sel));
          selectedProjectionRef.current = null;
          setContextMenu(null);
        }
      }
    };

    container.addEventListener('mousedown', onMouseDown, true);
    container.addEventListener('contextmenu', onContextMenu, true);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('keydown', onKeyDown);

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
      container.removeEventListener('mousedown', onMouseDown, true);
      container.removeEventListener('contextmenu', onContextMenu, true);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('keydown', onKeyDown);
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
  }, [setLevels, setProjections]);

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

    const updateLine = (key, price, color, lineStyle, title, axisLabelVisible = true) => {
      const parsedPrice = parseFloat(price);
      if (isNaN(parsedPrice) || parsedPrice <= 0) return;
      
      if (smcPriceLinesRef.current[key]) {
        try {
          smcPriceLinesRef.current[key].applyOptions({ price: parsedPrice, color, lineStyle, title, axisLabelVisible });
        } catch (e) { console.warn(e); }
      } else {
        try {
          smcPriceLinesRef.current[key] = series.createPriceLine({
            price: parsedPrice, color, lineWidth: 1, lineStyle, title, axisLabelVisible
          });
        } catch (e) { console.warn(e); }
      }
    };

    // v16.0: Приоритет — backend (H4 инверсия), иначе по trend
    const kl = analysis.advanced?.key_levels;
    const highLabel = (kl?.High_Type === 'Strong High' || kl?.High_Type === 'Weak High') ? kl.High_Type : (analysis.trend === 'DOWNTREND' ? 'Strong High' : 'Weak High');
    const lowLabel = (kl?.Low_Type === 'Strong Low' || kl?.Low_Type === 'Weak Low') ? kl.Low_Type : (analysis.trend === 'DOWNTREND' ? 'Weak Low' : 'Strong Low');

    updateLine('rangeHigh', advancedZones.range_high, 'rgba(239, 83, 80, 0.6)', 1, highLabel, false);
    updateLine('rangeLow', advancedZones.range_low, 'rgba(38, 166, 154, 0.6)', 1, lowLabel, false);
    
    // 2. Equilibrium (Штрих-пунктир 2)
    // Берем цену из key_levels ИЛИ считаем среднее сами
    const eqPrice = keyLevels?.Equilibrium_Price || ((advancedZones.range_high + advancedZones.range_low) / 2);
    
    if (eqPrice > 0) {
      updateLine('equilibrium', eqPrice, 'rgba(156, 39, 176, 0.8)', 2, '', false);  // метка на canvas поверх
    }
  }, [smcVisible, analysis]);

  useEffect(() => {
    if (!seriesRef.current || !history?.length) return;
    const chart = chartRef.current;
    if (!chart) return;

    // КРИТИЧНО: Удаляем старые PriceLines перед установкой новых данных
    clearSMCPriceLines();

    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext('2d');
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
    }

    const timeScale = chart.timeScale();

    // При смене таймфрейма или источника — сбрасываем, чтобы fitContent выполнился заново
    const tfSourceKey = `${tf || ''}_${source || ''}`;
    if (lastTfSourceRef.current !== null && lastTfSourceRef.current !== tfSourceKey) {
      hasInitialFitRef.current = false;
    }
    lastTfSourceRef.current = tfSourceKey;

    let savedLogicalRange = null;
    if (hasInitialFitRef.current) {
      savedLogicalRange = lastVisibleLogicalRangeRef.current ?? timeScale.getVisibleLogicalRange?.();
    }

    // Устанавливаем данные свечей
    seriesRef.current.setData(history);

    if (!hasInitialFitRef.current) {
      try {
        chart.timeScale().fitContent();
        hasInitialFitRef.current = true;
      } catch (e) {
        console.warn('Could not fit content:', e.message);
      }
    } else if (savedLogicalRange && timeScale.setVisibleLogicalRange) {
      // Восстанавливаем позицию пользователя (зум, скролл)
      setTimeout(() => {
        try {
          if (chartRef.current?.timeScale) {
            chartRef.current.timeScale().setVisibleLogicalRange(savedLogicalRange);
          }
        } catch (e) {
          console.warn('Could not restore visible range:', e.message);
        }
      }, 50);
    }

    const stabilityTimeout = setTimeout(() => {
      if (analysis) updateSMCPriceLines();
      drawSMCOverlayRef.current?.();
    }, 150);

    return () => clearTimeout(stabilityTimeout);
  }, [history, analysis, updateSMCPriceLines, clearSMCPriceLines, tf, source]);

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

  // PriceLines для проекций Long/Short (Entry, SL, TP на шкале цен)
  useEffect(() => {
    const s = seriesRef.current;
    if (!s) return;
    if (!projections?.length) {
      const pl = projectionPriceLinesRef.current;
      Object.keys(pl).forEach(projId => {
        const lines = pl[projId];
        if (lines) {
          ['entry', 'sl', 'tp'].forEach(k => {
            if (lines[k]) { try { s.removePriceLine(lines[k]); } catch { /* ignore */ } }
          });
        }
      });
      projectionPriceLinesRef.current = {};
      return;
    }

    const currentIds = new Set(projections.map(p => p.id));
    const pl = projectionPriceLinesRef.current;

    // Удаляем линии для проекций, которых больше нет
    Object.keys(pl).forEach(projId => {
      if (!currentIds.has(projId)) {
        const lines = pl[projId];
        if (lines) {
          ['entry', 'sl', 'tp'].forEach(k => {
            if (lines[k]) { try { s.removePriceLine(lines[k]); } catch { /* ignore */ } }
          });
        }
        delete pl[projId];
      }
    });

    projections.forEach(proj => {
      const entry = parseFloat(proj.entry);
      const sl = parseFloat(proj.sl);
      const tp = parseFloat(proj.tp);
      if (isNaN(entry) || isNaN(sl) || isNaN(tp)) return;

      if (!pl[proj.id]) pl[proj.id] = { entry: null, sl: null, tp: null };
      const lines = pl[proj.id];

      const upsert = (key, price, color, title) => {
        if (lines[key]) {
          try { lines[key].applyOptions({ price }); } catch { /* ignore */ }
        } else {
          try {
            lines[key] = s.createPriceLine({
              price, color, lineWidth: 1, lineStyle: 2, title, axisLabelVisible: true
            });
          } catch { /* ignore */ }
        }
      };
      upsert('entry', entry, 'rgba(41, 98, 255, 0.8)', 'E');
      upsert('sl', sl, 'rgba(239, 83, 80, 0.8)', 'SL');
      upsert('tp', tp, 'rgba(38, 166, 154, 0.8)', 'TP');
    });
  }, [projections]);

  // Закрытие контекстного меню при клике вне
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [contextMenu]);

  const handleFlipProjection = () => {
    if (!contextMenu || !setProjections) return;
    setProjections(prev => prev.map(p => {
      if (p.id !== contextMenu.projectionId) return p;
      return { ...p, sl: p.tp, tp: p.sl };
    }));
    setContextMenu(null);
  };

  const handleDeleteProjection = () => {
    if (!contextMenu || !setProjections) return;
    setProjections(prev => prev.filter(p => p.id !== contextMenu.projectionId));
    selectedProjectionRef.current = null;
    setContextMenu(null);
  };

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={chartContainerRef} style={{ width: '100%', height: '100%', position: 'relative' }} />
      {!serverConnected && (
        <div className="server-status-indicator">
          Соединение с сервером отсутствует
        </div>
      )}

      {/* Контекстное меню для проекций Long/Short */}
      {contextMenu && (
        <div
          className="projection-context-menu glass-panel"
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 9999,
            minWidth: '160px',
            padding: '6px 0',
            borderRadius: '8px',
            boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="context-menu-item"
            onClick={handleFlipProjection}
          >
            Переворот (SL↔TP)
          </button>
          <button
            type="button"
            className="context-menu-item delete"
            onClick={handleDeleteProjection}
          >
            Удалить
          </button>
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
