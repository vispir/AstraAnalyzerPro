import plotly.graph_objects as go
import pandas as pd
import base64
import hashlib
import json
import logging
import kaleido
from datetime import timedelta
from typing import Dict, Optional

from services.cache_service import cache_service

# Настройка логгера
logger = logging.getLogger(__name__)

class ChartService:
    """
    Сервис для генерации графиков с SMC уровнями, оптимизированный для компьютерного зрения LLM.
    """
    
    def __init__(self):
        # Размеры по умолчанию (HD aspect ratio)
        self.default_height = 800
        self.default_width = 1200

        # Автоматическая установка Chrome для Kaleido 1.0+
        try:
            logger.info("Checking/Installing Kaleido Chrome...")
            kaleido.get_chrome_sync()
            logger.info("Kaleido Chrome is ready.")
        except Exception as e:
            logger.warning(f"Kaleido Chrome setup warning: {str(e)}")
        
        # TTL для кэша графиков (без TTL, т.к. привязано к данным свечей)
        self.chart_cache_ttl = None  # Бессрочно, инвалидация по изменению данных
        
        # Палитра цветов (TradingView Dark Theme)
        self.colors = {
            'bg': "#131722",          # Темный фон
            'grid': "#1E222D",        # Сетка
            'candle_up': "#089981",   # Зеленая свеча
            'candle_down': "#F23645", # Красная свеча
            
            # SMC Зоны (с альфа-каналом для прозрачности)
            'bull_ob': "rgba(8, 153, 129, 0.25)",
            'bear_ob': "rgba(242, 54, 69, 0.25)",
            'bull_fvg': "rgba(255, 193, 7, 0.15)",   # Желтый
            'bear_fvg': "rgba(255, 152, 0, 0.15)",   # Оранжевый
            
            # Линии
            'structure_bull': "#00E676",
            'structure_bear': "#FF1744",
            'liquidity': "#FFA000",
            'text': "#B2B5BE"
        }

    def _generate_data_hash(self, df: pd.DataFrame, smc_data: Optional[Dict] = None) -> str:
        """
        Генерирует хеш для данных графика (свечи + SMC данные)
        
        Args:
            df: DataFrame со свечами
            smc_data: SMC данные
            
        Returns:
            MD5 хеш данных
        """
        # Берем последние 200 свечей для хеширования
        limit_candles = 200
        if len(df) > limit_candles:
            df_hash = df.tail(limit_candles).copy()
        else:
            df_hash = df.copy()
        
        # Создаем строку для хеширования
        hash_parts = []
        
        # Добавляем данные свечей (только OHLC, без Volume для стабильности)
        for col in ['Open', 'High', 'Low', 'Close']:
            if col in df_hash.columns:
                hash_parts.append(df_hash[col].to_json())
        
        # Добавляем индекс (времена)
        hash_parts.append(df_hash.index.astype(str).to_list().__str__())
        
        # Добавляем SMC данные (если есть)
        if smc_data:
            # Используем default=str для обработки pandas Timestamp и других нестандартных типов
            hash_parts.append(json.dumps(smc_data, sort_keys=True, default=str))
        
        # Вычисляем хеш
        hash_string = "|".join(hash_parts)
        return hashlib.md5(hash_string.encode()).hexdigest()
    
    def generate_chart_image(
        self,
        df: pd.DataFrame,
        smc_data: Optional[Dict] = None,
        title: str = "XAUUSD SMC Analysis",
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> str:
        try:
            if df is None or df.empty:
                raise ValueError("DataFrame is empty")
            
            # Используем переданные размеры или дефолтные
            chart_width = width or self.default_width
            chart_height = height or self.default_height
            
            # Проверяем кэш
            data_hash = self._generate_data_hash(df, smc_data)
            cache_key = cache_service._generate_key(
                'chart_image',
                data_hash=data_hash,
                title=title,
                width=chart_width,
                height=chart_height
            )
            
            cached_image = cache_service.get(cache_key)
            if cached_image is not None:
                logger.info(f"Returning cached chart image (hash: {data_hash[:8]}...)")
                return cached_image
            
            # --- ЛОГИКА 200 СВЕЧЕЙ ---
            # Берем только последние 200 свечей для отрисовки
            limit_candles = 200
            if len(df) > limit_candles:
                df_plot = df.tail(limit_candles).copy()
            else:
                df_plot = df.copy()

            if not isinstance(df_plot.index, pd.DatetimeIndex):
                df_plot.index = pd.to_datetime(df_plot.index)

            # 1. Основной свечной график
            fig = go.Figure(data=[go.Candlestick(
                x=df_plot.index,
                open=df_plot['Open'],
                high=df_plot['High'],
                low=df_plot['Low'],
                close=df_plot['Close'],
                name='Price',
                increasing_line_color=self.colors['candle_up'],
                decreasing_line_color=self.colors['candle_down'],
                increasing_fillcolor=self.colors['candle_up'], 
                decreasing_fillcolor=self.colors['candle_down'],
                showlegend=False
            )])
            
            # 2. Настройка внешнего вида
            fig.update_layout(
                xaxis_rangeslider_visible=False,
                template="plotly_dark",
                paper_bgcolor=self.colors['bg'],
                plot_bgcolor=self.colors['bg'],
                margin=dict(l=20, r=60, t=50, b=30),
                height=chart_height,
                width=chart_width,
                title=dict(
                    text=title, 
                    x=0.05, 
                    font=dict(size=20, color="white", family="Monospace")
                ),
                xaxis=dict(
                    showgrid=True, 
                    gridcolor=self.colors['grid'],
                    
                    # !!! ВАЖНО: Скрываем выходные (Sat, Sun) !!!
                    rangebreaks=[
                        dict(bounds=["sat", "mon"]), 
                    ]
                ),
                yaxis=dict(
                    showgrid=True, 
                    gridcolor=self.colors['grid'], 
                    side='right',
                    showline=True
                ),
                font=dict(family="Monospace", size=11, color=self.colors['text'])
            )
            
            # 3. Отрисовка SMC слоев (передаем полный df для правильного расчета линий, 
            # но Plotly сам обрежет то, что не влазит в экран)
            if smc_data:
                self._draw_zones(fig, df_plot, smc_data)     
                self._draw_structure(fig, df_plot, smc_data) 
                self._draw_liquidity(fig, df_plot, smc_data) 

            # 4. Экспорт
            img_bytes = fig.to_image(format="png", engine="kaleido", scale=2)
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            # Сохраняем в кэш (бессрочно, т.к. привязано к хешу данных)
            cache_service.set(cache_key, base64_image, self.chart_cache_ttl)
            
            logger.info(f"Chart generated successfully. Size: {len(base64_image)} chars (cached with hash: {data_hash[:8]}...)")
            return base64_image
            
        except Exception as e:
            logger.error(f"Error generating chart: {str(e)}")
            raise
    
    def _draw_zones(self, fig, df, data):
        """Рисует прямоугольные зоны (Order Blocks, FVG)"""
        last_time = df.index[-1]
        
        # --- ORDER BLOCKS ---
        for ob in data.get('order_blocks', []):
            is_bull = 'BULL' in ob['type']
            color = self.colors['bull_ob'] if is_bull else self.colors['bear_ob']
            border = self.colors['candle_up'] if is_bull else self.colors['candle_down']
            
            # Начало блока
            start_t = pd.to_datetime(ob.get('time', df.index[0]))
            
            # Рисуем прямоугольник от момента создания до текущей цены
            fig.add_shape(
                type="rect",
                x0=start_t, x1=last_time, 
                y0=ob['bottom'], y1=ob['top'],
                fillcolor=color,
                line=dict(color=border, width=1), # Тонкая рамка
                layer="below"
            )

        # --- FVG ---
        for fvg in data.get('fvg', []):
            is_bull = 'BULL' in fvg['type']
            color = self.colors['bull_fvg'] if is_bull else self.colors['bear_fvg']
            
            start_t = pd.to_datetime(fvg.get('start_time', df.index[0]))
            
            fig.add_shape(
                type="rect",
                x0=start_t, x1=last_time,
                y0=fvg['bottom'], y1=fvg['top'],
                fillcolor=color,
                line=dict(width=0), # Без границ
                layer="below"
            )

    def _draw_structure(self, fig, df, data):
        """
        Рисует структуру (BOS, CHOCH) с ограничением длины линий (Clamping).
        """
        last_time = df.index[-1]
        
        # Определяем "горизонт видимости" (последние 40 свечей)
        lookback_idx = max(0, len(df) - 40) 
        visible_start_time = df.index[lookback_idx]

        def draw_segment(item, label, color, style):
            price = item['price']
            
            # Получаем время события
            event_time = pd.to_datetime(item.get('time', df.index[0]))
            
            # ОБРЕЗКА: Рисуем линию только если она попадает в видимую зону (или чуть раньше)
            start_t = max(event_time, visible_start_time)
            
            # Защита от сбоев дат (если start > end, рисуем маленькую черточку)
            if start_t > last_time:
                start_t = df.index[-2]

            fig.add_shape(
                type="line",
                x0=start_t, x1=last_time,
                y0=price, y1=price,
                line=dict(color=color, width=2, dash=style)
            )
            # Подпись
            fig.add_annotation(
                x=last_time, y=price,
                text=label,
                showarrow=False,
                xanchor="left",
                font=dict(color=color, size=10, weight="bold"),
                bgcolor=self.colors['bg'],
                opacity=0.8
            )

        # BOS
        for bos in data.get('bos', []):
            is_bull = 'BULL' in bos.get('type', '')
            color = self.colors['structure_bull'] if is_bull else self.colors['structure_bear']
            draw_segment(bos, "BOS", color, "solid")

        # CHOCH
        for choch in data.get('choch', []):
            # Оранжевый/Желтый для CHOCH
            color = "#FFAB00" 
            draw_segment(choch, "CHoCH", color, "dash")

    def _draw_liquidity(self, fig, df, data):
        """Рисует уровни ликвидности (EQH/EQL) с ограничением длины"""
        last_time = df.index[-1]
        
        # Ограничиваем длину линий (последние 30 свечей)
        lookback_idx = max(0, len(df) - 30)
        start_t = df.index[lookback_idx]
        
        levels = data.get('eqh', []) + data.get('eql', [])
        
        for lvl in levels:
            price = lvl['price']
            is_eqh = 'HIGHS' in lvl.get('type', '')
            label = "EQH ($)" if is_eqh else "EQL ($)"
            
            # Цвет: Золотой/Оранжевый
            color = "#FFD700" if is_eqh else "#FF9100"
            
            fig.add_shape(
                type="line",
                x0=start_t, x1=last_time,
                y0=price, y1=price,
                line=dict(color=color, width=2, dash="dot")
            )
            
            fig.add_annotation(
                x=last_time, y=price,
                text=label,
                showarrow=False,
                xanchor="left",
                font=dict(color=color, size=9),
                bgcolor=self.colors['bg'],
                opacity=0.8
            )
        
        # --- ADVANCED SMC LEVELS ---
        if 'advanced' in data:
            advanced = data['advanced']
            key_levels = advanced.get('key_levels', {})
            structure_points = advanced.get('structure_points', {})
            
            # DH (Daily High)
            dh = key_levels.get('DH')
            if dh and dh > 0:
                fig.add_hline(
                    y=dh,
                    line_color="#E91E63",  # Розовый
                    line_width=2.5,
                    line_dash="solid",
                    annotation_text="DH",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=11, color="white", family="Arial Black"),
                        bgcolor="#E91E63",
                        opacity=0.9
                    )
                )
            
            # DL (Daily Low)
            dl = key_levels.get('DL')
            if dl and dl > 0:
                fig.add_hline(
                    y=dl,
                    line_color="#C2185B",  # Темно-розовый
                    line_width=2.5,
                    line_dash="solid",
                    annotation_text="DL",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=11, color="white", family="Arial Black"),
                        bgcolor="#C2185B",
                        opacity=0.9
                    )
                )
            
            # PDH (Previous Day High)
            pdh = key_levels.get('PDH')
            if pdh and pdh > 0:
                fig.add_hline(
                    y=pdh,
                    line_color="#9C27B0",  # Фиолетовый
                    line_width=2,
                    line_dash="longdash",
                    annotation_text="PDH",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=10, color="white"),
                        bgcolor="#9C27B0",
                        opacity=0.85
                    )
                )
            
            # PDL (Previous Day Low)
            pdl = key_levels.get('PDL')
            if pdl and pdl > 0:
                fig.add_hline(
                    y=pdl,
                    line_color="#673AB7",  # Темно-фиолетовый
                    line_width=2,
                    line_dash="longdash",
                    annotation_text="PDL",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=10, color="white"),
                        bgcolor="#673AB7",
                        opacity=0.85
                    )
                )
            
            # Equilibrium (50% уровень)
            eq_price = key_levels.get('Equilibrium_Price')
            eq_zone = key_levels.get('Current_Zone', 'UNKNOWN')
            if eq_price and eq_price > 0:
                # Цвет зависит от зоны
                if eq_zone == 'PREMIUM':
                    eq_color = "#FF5252"  # Красный (дорого)
                elif eq_zone == 'DISCOUNT':
                    eq_color = "#00E676"  # Зеленый (дешево)
                else:
                    eq_color = "#FFD600"  # Желтый (равновесие)
                
                fig.add_hline(
                    y=eq_price,
                    line_color=eq_color,
                    line_width=3,
                    line_dash="dashdot",
                    annotation_text=f"EQ ({eq_zone})",
                    annotation_position="right",
                    annotation=dict(
                        font=dict(size=11, color="white", family="Arial Black"),
                        bgcolor=eq_color,
                        opacity=0.9
                    )
                )
            
            # Nearest Swing High
            swing_high = structure_points.get('nearest_swing_high')
            if swing_high and swing_high > 0:
                fig.add_hline(
                    y=swing_high,
                    line_color="#00BCD4",  # Cyan
                    line_width=1.5,
                    line_dash="dot",
                    annotation_text="Last Swing High",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=8, color="white"),
                        bgcolor="#00BCD4",
                        opacity=0.7
                    )
                )
            
            # Nearest Swing Low
            swing_low = structure_points.get('nearest_swing_low')
            if swing_low and swing_low > 0:
                fig.add_hline(
                    y=swing_low,
                    line_color="#FF6E40",  # Оранжево-красный
                    line_width=1.5,
                    line_dash="dot",
                    annotation_text="Last Swing Low",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=8, color="white"),
                        bgcolor="#FF6E40",
                        opacity=0.7
                    )
                )

    def save_to_file(self, df, filename, smc_data=None):
        """Утилита для отладки: сохраняет PNG на диск"""
        try:
            b64_str = self.generate_chart_image(df, smc_data)
            with open(filename, "wb") as f:
                f.write(base64.b64decode(b64_str))
            logger.info(f"Saved debug chart to {filename}")
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
    
    def clear_cache(self):
        """Очистка кэша изображений графиков"""
        count = cache_service.clear('chart_image')
        logger.info(f"Chart images cache cleared ({count} entries)")

# Глобальный экземпляр для импорта
chart_service = ChartService()