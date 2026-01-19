"""
Сервис для генерации графиков с SMC уровнями используя Plotly
"""
import plotly.graph_objects as go
import pandas as pd
import base64
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


class ChartService:
    """Сервис для генерации графиков"""
    
    def __init__(self):
        self.default_height = 600
        self.default_width = 1000
        
    def generate_chart_image(
        self,
        df: pd.DataFrame,
        smc_data: Optional[Dict] = None,
        title: str = "XAUUSD Market Structure",
        height: int = None,
        width: int = None
    ) -> str:
        """
        Генерация графика с SMC уровнями
        
        Args:
            df: DataFrame с колонками Open, High, Low, Close (index - timestamp)
            smc_data: Данные SMC уровней (OB, FVG, Liquidity)
            title: Заголовок графика
            height: Высота изображения
            width: Ширина изображения
            
        Returns:
            Base64 строка с изображением PNG
        """
        try:
            if df is None or df.empty:
                raise ValueError("DataFrame is empty")
            
            height = height or self.default_height
            width = width or self.default_width
            
            # 1. Создаем свечной график
            fig = go.Figure(data=[go.Candlestick(
                x=df.index,
                open=df['Open'],
                high=df['High'],
                low=df['Low'],
                close=df['Close'],
                name='Price',
                increasing_line_color='#089981',  # TradingView Green
                decreasing_line_color='#F23645'   # TradingView Red
            )])
            
            # 2. Настраиваем внешний вид (Темная тема TradingView)
            fig.update_layout(
                xaxis_rangeslider_visible=False,  # Убираем слайдер
                template="plotly_dark",
                paper_bgcolor="#131722",  # TradingView background
                plot_bgcolor="#131722",
                margin=dict(l=50, r=50, t=50, b=50),
                height=height,
                width=width,
                title=dict(
                    text=title,
                    font=dict(family="Monospace", size=16, color="white"),
                    x=0.5,
                    xanchor='center'
                ),
                xaxis=dict(
                    showgrid=True,
                    gridcolor='#1E222D',
                    showline=True,
                    linecolor='#2A2E39'
                ),
                yaxis=dict(
                    showgrid=True,
                    gridcolor='#1E222D',
                    showline=True,
                    linecolor='#2A2E39',
                    side='right'  # Цена справа как в TV
                ),
                font=dict(family="Monospace", size=11, color="#787B86")
            )
            
            # 3. Визуализируем SMC уровни (если есть)
            if smc_data:
                self._add_smc_levels(fig, df, smc_data)
            
            # 4. Конвертация в PNG (В память)
            logger.info(f"Generating chart image: {width}x{height}")
            img_bytes = fig.to_image(format="png", engine="kaleido")
            
            # 5. Кодируем в Base64
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            logger.info(f"Chart generated successfully, size: {len(img_bytes)} bytes")
            return base64_image
            
        except Exception as e:
            logger.error(f"Error generating chart: {str(e)}")
            raise
    
    def _add_smc_levels(self, fig: go.Figure, df: pd.DataFrame, smc_data: Dict):
        """
        Добавление SMC уровней на график
        
        Args:
            fig: Plotly Figure
            df: DataFrame с данными
            smc_data: Данные уровней
        """
        # --- ORDER BLOCKS (Прямоугольники) ---
        if 'order_blocks' in smc_data and smc_data['order_blocks']:
            for idx, ob in enumerate(smc_data['order_blocks']):
                ob_type = ob.get('type', '').upper()
                
                # Цвет: Зеленый для Bull, Красный для Bear
                if 'BULL' in ob_type:
                    color = "rgba(8, 153, 129, 0.25)"  # TradingView green с прозрачностью
                    border_color = "#089981"
                else:
                    color = "rgba(242, 54, 69, 0.25)"  # TradingView red с прозрачностью
                    border_color = "#F23645"
                
                # Определяем x0 и x1 (начало и конец зоны)
                x0 = ob.get('start_index', 0)
                x1 = ob.get('end_index', len(df) - 1)
                
                # Если передан timestamp
                if 'start_time' in ob:
                    x0 = pd.to_datetime(ob['start_time'], unit='s')
                else:
                    x0 = df.index[min(x0, len(df) - 1)]
                
                if 'end_time' in ob:
                    x1 = pd.to_datetime(ob['end_time'], unit='s')
                else:
                    x1 = df.index[-1]
                
                # Рисуем прямоугольник
                fig.add_shape(
                    type="rect",
                    x0=x0, x1=x1,
                    y0=ob['bottom'], y1=ob['top'],
                    fillcolor=color,
                    line=dict(color=border_color, width=1, dash="dot"),
                    layer='below'
                )
                
                # Подпись с силой импульса
                mid_y = (ob['top'] + ob['bottom']) / 2
                strength_pct = ob.get('strength', 0) * 100
                label = f"{ob_type} ({strength_pct:.1f}%)" if strength_pct > 0 else ob_type
                
                fig.add_annotation(
                    x=x1,
                    y=mid_y,
                    text=label,
                    showarrow=False,
                    font=dict(color="white", size=9),
                    bgcolor=border_color,
                    opacity=0.8,
                    xanchor='left'
                )
        
        # --- FVG (Fair Value Gaps) ---
        if 'fvg' in smc_data and smc_data['fvg']:
            for gap in smc_data['fvg']:
                gap_type = gap.get('type', '').upper()
                
                # FVG обычно оранжевые/желтые
                if 'BULL' in gap_type:
                    color = "rgba(255, 193, 7, 0.2)"  # Amber
                    border_color = "#FFC107"
                else:
                    color = "rgba(255, 152, 0, 0.2)"  # Orange
                    border_color = "#FF9800"
                
                # Определяем координаты
                x0 = gap.get('start_time', df.index[0])
                x1 = gap.get('end_time', df.index[-1])
                
                if isinstance(x0, (int, float)):
                    x0 = pd.to_datetime(x0, unit='s')
                if isinstance(x1, (int, float)):
                    x1 = pd.to_datetime(x1, unit='s')
                
                price_top = gap.get('top', gap.get('price', 0) + 1.0)
                price_bottom = gap.get('bottom', gap.get('price', 0) - 1.0)
                
                # Рисуем FVG
                fig.add_shape(
                    type="rect",
                    x0=x0, x1=x1,
                    y0=price_bottom, y1=price_top,
                    fillcolor=color,
                    line=dict(color=border_color, width=1, dash="dash"),
                    layer='below'
                )
                
                # Подпись с размером гэпа
                gap_pct = gap.get('gap_percent', 0)
                label = f"FVG: {gap_type} ({gap_pct:.2f}%)" if gap_pct > 0 else f"FVG: {gap_type}"
                
                fig.add_annotation(
                    x=x1,
                    y=(price_top + price_bottom) / 2,
                    text=label,
                    showarrow=False,
                    font=dict(color="white", size=9),
                    bgcolor=border_color,
                    opacity=0.8,
                    xanchor='left'
                )
        
        # --- LIQUIDITY ZONES (Support/Resistance) ---
        if 'liquidity' in smc_data and smc_data['liquidity']:
            for liq in smc_data['liquidity']:
                # Liquidity рисуем как горизонтальные линии
                price = liq.get('price', 0)
                liq_type = liq.get('type', '').upper()
                strength = liq.get('strength', 1)
                
                # Цвет линии
                if 'HIGH' in liq_type or 'RESISTANCE' in liq_type:
                    line_color = "#2962FF"  # Синий
                else:
                    line_color = "#9C27B0"  # Фиолетовый
                
                # Толщина линии зависит от силы
                line_width = min(2 + strength, 5)
                
                # Подпись с силой уровня
                label = f"{liq_type} (x{strength})" if strength > 1 else liq_type
                
                fig.add_hline(
                    y=price,
                    line_color=line_color,
                    line_width=line_width,
                    line_dash="dash",
                    annotation_text=label,
                    annotation_position="right",
                    annotation=dict(
                        font=dict(size=10, color="white"),
                        bgcolor=line_color,
                        opacity=0.8
                    )
                )
        
        # --- ENTRY/SL/TP LEVELS (если есть) ---
        if 'entry' in smc_data:
            fig.add_hline(
                y=smc_data['entry'],
                line_color="#2196F3",
                line_width=2,
                annotation_text="ENTRY",
                annotation_position="left"
            )
        
        if 'stop_loss' in smc_data:
            fig.add_hline(
                y=smc_data['stop_loss'],
                line_color="#F44336",
                line_width=2,
                annotation_text="SL",
                annotation_position="left"
            )
        
        if 'take_profit' in smc_data:
            fig.add_hline(
                y=smc_data['take_profit'],
                line_color="#4CAF50",
                line_width=2,
                annotation_text="TP",
                annotation_position="left"
            )
        
        # --- CHOCH (Change of Character) ---
        if 'choch' in smc_data and smc_data['choch']:
            for choch in smc_data['choch']:
                choch_type = choch.get('type', '').upper()
                price = choch.get('price', 0)
                
                # Цвет: оранжевый для смены характера
                color = "#FF6D00" if 'BULLISH' in choch_type else "#D32F2F"
                
                fig.add_hline(
                    y=price,
                    line_color=color,
                    line_width=2,
                    line_dash="dot",
                    annotation_text=f"CHOCH",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=10, color="white"),
                        bgcolor=color,
                        opacity=0.9
                    )
                )
        
        # --- BOS (Break of Structure) ---
        if 'bos' in smc_data and smc_data['bos']:
            for bos in smc_data['bos']:
                bos_type = bos.get('type', '').upper()
                price = bos.get('price', 0)
                
                # Цвет: зеленый/красный для BOS
                color = "#00C853" if 'BULLISH' in bos_type else "#D50000"
                
                fig.add_hline(
                    y=price,
                    line_color=color,
                    line_width=3,
                    line_dash="solid",
                    annotation_text=f"BOS",
                    annotation_position="left",
                    annotation=dict(
                        font=dict(size=10, color="white"),
                        bgcolor=color,
                        opacity=0.9
                    )
                )
        
        # --- EQH (Equal Highs) ---
        if 'eqh' in smc_data and smc_data['eqh']:
            for eqh in smc_data['eqh']:
                price = eqh.get('price', 0)
                touches = eqh.get('touches', 2)
                
                fig.add_hline(
                    y=price,
                    line_color="#FFC107",
                    line_width=2,
                    line_dash="dashdot",
                    annotation_text=f"EQH (x{touches})",
                    annotation_position="right",
                    annotation=dict(
                        font=dict(size=9, color="white"),
                        bgcolor="#FFC107",
                        opacity=0.8
                    )
                )
        
        # --- EQL (Equal Lows) ---
        if 'eql' in smc_data and smc_data['eql']:
            for eql in smc_data['eql']:
                price = eql.get('price', 0)
                touches = eql.get('touches', 2)
                
                fig.add_hline(
                    y=price,
                    line_color="#FF9800",
                    line_width=2,
                    line_dash="dashdot",
                    annotation_text=f"EQL (x{touches})",
                    annotation_position="right",
                    annotation=dict(
                        font=dict(size=9, color="white"),
                        bgcolor="#FF9800",
                        opacity=0.8
                    )
                )
    
    def save_chart_to_file(
        self,
        df: pd.DataFrame,
        filename: str,
        smc_data: Optional[Dict] = None,
        title: str = "XAUUSD Market Structure"
    ):
        """
        Сохранение графика в файл (для отладки)
        
        Args:
            df: DataFrame с данными
            filename: Имя файла для сохранения
            smc_data: SMC данные
            title: Заголовок
        """
        try:
            base64_image = self.generate_chart_image(df, smc_data, title)
            img_bytes = base64.b64decode(base64_image)
            
            with open(filename, 'wb') as f:
                f.write(img_bytes)
            
            logger.info(f"Chart saved to {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving chart: {str(e)}")
            return False


# Глобальный экземпляр
chart_service = ChartService()
