"""
Telegram Bot Service
Полный функционал бота с использованием pyTelegramBotAPI
"""
import os
import logging
import telebot
import html
from telebot import types
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        self.use_polling = os.getenv("USE_TELEGRAM_POLLING", "false").lower() == "true"

        # Admin chat IDs для статусов (может быть несколько)
        admin_ids = []
        if os.getenv("TELEGRAM_ADMIN_CHAT_ID"):
            admin_ids.append(os.getenv("TELEGRAM_ADMIN_CHAT_ID"))
        if os.getenv("TELEGRAM_ADMIN_CHAT_ID_2"):
            admin_ids.append(os.getenv("TELEGRAM_ADMIN_CHAT_ID_2"))
        self.admin_chat_ids = admin_ids

        # Astra Signal Bot — только BUY/SELL (без анализа и WAIT)
        self.bot_signals_token = os.getenv("TELEGRAM_BOT_TOKEN_SIGNALS")
        self.bot_signals = None
        if self.bot_signals_token:
            self.bot_signals = telebot.TeleBot(self.bot_signals_token, threaded=False)
            logger.info("✅ Astra Signal Bot (@AstraSignal_Bot) инициализирован")
        else:
            logger.info("📱 TELEGRAM_BOT_TOKEN_SIGNALS: не установлен (Signal Bot отключен)")

        # Логируем конфигурацию
        logger.info(f"📱 TELEGRAM_BOT_TOKEN: {'установлен ✅' if self.bot_token else '❌ не установлен'}")
        logger.info(f"📱 Режим работы: {'POLLING' if self.use_polling else 'WEBHOOK'}")
        logger.info(f"📱 Admin Chat IDs: {len(self.admin_chat_ids)} админов")

        if not self.use_polling and self.webhook_url:
            logger.info(f"📱 TELEGRAM_WEBHOOK_URL: {self.webhook_url}")

        if not self.bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
            self.bot = None
            return

        # Инициализация бота
        self.bot = telebot.TeleBot(self.bot_token, threaded=False)
        # Ожидание ввода диапазона (chat_id -> True)
        self._pending_range_chat_ids = {}
        
        # Регистрация всех обработчиков команд
        self._register_handlers()
        # Команды в меню (кнопка «Меню» в TG)
        self._set_bot_commands()
        
        logger.info("✅ Telegram Bot инициализирован")
    
    def _register_handlers(self):
        """Регистрация всех обработчиков команд и callback-ов"""
        if not self.bot:
            return
        
        # Команды
        @self.bot.message_handler(commands=['start'])
        def handle_start(message):
            self._cmd_start(message)
        
        @self.bot.message_handler(commands=['price'])
        def handle_price(message):
            self._cmd_price(message)
        
        @self.bot.message_handler(commands=['trend'])
        def handle_trend(message):
            self._cmd_trend(message)
        
        @self.bot.message_handler(commands=['status'])
        def handle_status(message):
            self._cmd_status(message)
        
        @self.bot.message_handler(commands=['help'])
        def handle_help(message):
            self._cmd_help(message)
        
        @self.bot.message_handler(commands=['stats'])
        def handle_stats(message):
            self._cmd_stats(message)

        @self.bot.message_handler(commands=['setrange'])
        def handle_setrange(message):
            self._handle_set_range_cmd(message)
        
        @self.bot.message_handler(commands=['autorange'])
        def handle_autorange(message):
            self._handle_auto_range_cmd(message)
        
        # Обработка callback кнопок (inline)
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            self._handle_callback_query(call)

        # Signal Bot: отдельный обработчик inline-кнопок (callback'ов)
        if self.bot_signals:
            @self.bot_signals.callback_query_handler(func=lambda call: True)
            def handle_signal_callback(call):
                self._handle_signal_bot_callback_query(call)
        
        # Текстовые сообщения (кнопки внизу экрана)
        @self.bot.message_handler(func=lambda message: True)
        def handle_text(message):
            self._handle_text_message(message)

    def _set_bot_commands(self):
        """Регистрация команд в меню бота (кнопка «Меню» в Telegram)."""
        if not self.bot:
            return
        try:
            from telebot.types import BotCommand
            self.bot.set_my_commands([
                BotCommand("start", "🚀 Запустить терминал Astra"),
                BotCommand("price", "💰 Актуальный курс Gold (OANDA)"),
                BotCommand("trend", "📈 Технический SMC анализ М15"),
                BotCommand("status", "🛡️ Проверить статус системы"),
                BotCommand("help", "❓ Справка по командам"),
                BotCommand("stats", "📊 Статистика сделок"),
                BotCommand("setrange", "📐 Задать диапазон вручную"),
                BotCommand("autorange", "🔄 Переключить в авто режим"),
            ])
        except Exception as e:
            logger.warning(f"⚠️ set_my_commands: {e}")
    
    # ==================== КОМАНДЫ ====================
    
    def _cmd_start(self, message):
        """Обработчик команды /start"""
        # Проверяем есть ли аргумент (токен авторизации)
        text_parts = message.text.split()
        
        if len(text_parts) > 1:
            # Есть аргумент - это токен авторизации
            token = text_parts[1]
            logger.info(f"🔑 Получен токен авторизации от user {message.from_user.id}: {token[:8]}...")
            
            try:
                from services.db_service import db_service
                
                # Проверяем существует ли такая сессия
                session = db_service.get_auth_session(token)
                
                if not session:
                    self.bot.send_message(
                        message.chat.id,
                        "❌ Неверный токен авторизации.\n\nПопробуйте еще раз или свяжитесь с поддержкой.",
                        parse_mode='HTML'
                    )
                    return
                
                if session['status'] == 'completed':
                    self.bot.send_message(
                        message.chat.id,
                        "⚠️ Этот токен уже был использован.\n\nСгенерируйте новый токен на сайте.",
                        parse_mode='HTML'
                    )
                    return
                
                # Получаем данные пользователя
                user = message.from_user
                user_id = user.id
                username = user.username or ''
                first_name = user.first_name or ''
                last_name = user.last_name or ''
                
                # Получаем фото профиля
                photo_url = None
                try:
                    photos = self.bot.get_user_profile_photos(user_id, limit=1)
                    if photos.total_count > 0:
                        # Берем самое большое фото
                        photo = photos.photos[0][-1]
                        file_info = self.bot.get_file(photo.file_id)
                        photo_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_info.file_path}"
                        logger.info(f"📸 Получено фото профиля для user {user_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось получить фото профиля: {e}")
                    # Используем дефолтное фото
                    photo_url = f"https://avatar.iran.liara.run/public/{user_id % 100}"
                
                # Сохраняем пользователя в БД с фото
                user_data = {
                    'id': user_id,
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'photo_url': photo_url,
                    'auth_date': int(message.date),
                    'is_active': True
                }
                
                db_service.save_user(user_data)
                
                # Закрываем сессию авторизации
                db_service.complete_auth_session(token, user_id)
                
                # Отправляем подтверждение
                user_name = html.escape(f"{first_name} {last_name}".strip())
                self.bot.send_message(
                    message.chat.id,
                    f"<b>✅ Авторизация успешна!</b>\n\n"
                    f"Привет, <b>{user_name}</b>! 🎉\n\n"
                    f"Ты успешно авторизовался на сайте <b>Astra Analyzer Pro</b>.\n\n"
                    f"Теперь ты будешь получать торговые сигналы здесь в Telegram! 📱\n\n"
                    f"Можешь вернуться на сайт — авторизация завершена.",
                    parse_mode='HTML'
                )
                
                logger.info(f"✅ Авторизация через бота завершена: user {user_id} (@{username})")
                return
                
            except Exception as e:
                logger.error(f"❌ Ошибка обработки токена авторизации: {e}")
                self.bot.send_message(
                    message.chat.id,
                    "❌ Произошла ошибка при авторизации.\n\nПопробуйте еще раз позже.",
                    parse_mode='HTML'
                )
                return
        
        # Обычный запуск бота (без токена)
        self._register_user(message.from_user)
        
        welcome = (
            "<b>💎 ASTRA ANALYZER PRO</b>\n"
            "————————————————\n"
            "Привет, Трейдер! 🚀\n\n"
            "Я твой автономный терминал для торговли золотом. "
            "Я использую <b>SMC структуру</b> и <b>AI</b> для поиска снайперских входов.\n\n"
            "<b>📋 Доступные команды:</b>\n"
            "🔹 <code>/price</code> — Живая цена OANDA\n"
            "🔹 <code>/trend</code> — Тренд и уровни M15\n"
            "🔹 <code>/status</code> — Статус системы\n"
            "🔹 <code>/help</code> — Показать это меню\n\n"
            "————————————————\n"
            "<b>Выбери действие:</b>"
        )
        
        # Отправляем с inline кнопками
        markup = self._get_inline_menu()
        self.bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=markup)
    
    def _cmd_price(self, message):
        """Обработчик команды /price"""
        try:
            from services.oanda_service import oanda_service
            
            data = oanda_service.get_candles(timeframe='M15', limit=1)
            if "candles" in data and len(data["candles"]) > 0:
                price = data["candles"][-1]["close"]
                msg = f"<b>💰 Текущая цена XAU/USD (Live):</b>\n<code>{price:.2f}</code>"
            else:
                msg = "❌ Ошибка получения котировок из OANDA."
        except Exception as e:
            logger.error(f"Ошибка в /price: {e}")
            msg = "❌ Временная ошибка при получении цены."
        
        self.bot.send_message(message.chat.id, msg, parse_mode='HTML')
    
    def _cmd_trend(self, message):
        """Обработчик команды /trend"""
        try:
            from services.oanda_service import oanda_service
            from services.smc_detector import smc_detector
            
            data = oanda_service.get_candles(timeframe='M15', limit=100)
            if "candles" in data and smc_detector:
                analysis = smc_detector.analyze(data["candles"])
                trend = analysis.get('trend', 'N/A')
                emoji = "🐂 BULLISH" if "UP" in trend.upper() else "🐻 BEARISH" if "DOWN" in trend.upper() else "↔️ RANGING"
                
                signals_count = analysis.get('signals_count', 0)
                current_zone = analysis.get('advanced', {}).get('key_levels', {}).get('Current_Zone', 'N/A')
                
                msg = (
                    f"<b>📈 Структура рынка (M15):</b>\n\n"
                    f"Тренд: <b>{emoji}</b>\n"
                    f"Сигналов SMC: <code>{signals_count}</code>\n"
                    f"Зона: <code>{current_zone}</code>"
                )
            else:
                msg = "❌ SMC детектор временно недоступен."
        except Exception as e:
            logger.error(f"Ошибка в /trend: {e}")
            msg = "❌ Временная ошибка при анализе тренда."
        
        self.bot.send_message(message.chat.id, msg, parse_mode='HTML')
    
    def _cmd_status(self, message):
        """Обработчик команды /status"""
        try:
            from services.db_service import db_service
            
            last_sig = db_service.get_last_signal_time()
            local_now = datetime.now(timezone.utc) + timedelta(hours=4)
            last_sig_local = last_sig + timedelta(hours=4)
            
            time_diff = datetime.now(timezone.utc) - last_sig
            minutes_ago = int(time_diff.total_seconds() // 60)
            
            msg = (
                f"<b>🛡️ Статус Astra Analyzer:</b>\n\n"
                f"✅ Система: <b>ONLINE</b>\n"
                f"🛰️ Наблюдатель: <b>ACTIVE</b>\n"
                f"🔔 Последний сигнал: <code>{minutes_ago} мин. назад</code>\n"
                f"⏰ Время сигнала: <code>{last_sig_local.strftime('%H:%M:%S')}</code>\n"
                f"📍 Время сервера: <code>{local_now.strftime('%H:%M:%S')} (UTC+4)</code>"
            )
        except Exception as e:
            logger.error(f"Ошибка в /status: {e}")
            msg = (
                f"<b>🛡️ Статус Astra Analyzer:</b>\n\n"
                f"✅ Система: <b>ONLINE</b>\n"
                f"⚠️ База данных: <b>Проверка...</b>"
            )
        
        self.bot.send_message(message.chat.id, msg, parse_mode='HTML')
    
    def _cmd_help(self, message):
        """Обработчик команды /help"""
        help_text = (
            "<b>📚 Справка по командам:</b>\n\n"
            "<code>/start</code> — Главное меню\n"
            "<code>/price</code> — Текущая цена золота\n"
            "<code>/trend</code> — Анализ тренда M15\n"
            "<code>/status</code> — Статус системы\n"
            "<code>/stats</code> — Статистика сделок\n"
            "<code>/help</code> — Эта справка\n\n"
            "💡 <i>Вы также можете использовать кнопки в меню</i>"
        )
        self.bot.send_message(message.chat.id, help_text, parse_mode='HTML')
    
    def _cmd_stats(self, message):
        """Обработчик команды /stats — статистика сделок"""
        try:
            from services.db_service import db_service
            stats = db_service.get_stats()
            if not stats:
                self.bot.send_message(
                    message.chat.id,
                    "❌ Не удалось получить статистику. Попробуйте позже.",
                    parse_mode='HTML'
                )
                return

            def fmt_period(p):
                total_pnl = p['total_pnl']
                if total_pnl > 0:
                    color = '🟢'
                    sign = '+'
                elif total_pnl < 0:
                    color = '🔴'
                    sign = ''
                else:
                    color = '⚪'
                    sign = ''
                return (
                    f"├ Сделок: {p['total']}\n"
                    f"├ ✅ В плюс: {p['wins']} (+{p['wins_pnl']:.2f} пунктов)\n"
                    f"├ ❌ В минус: {p['losses']} (-{p['losses_pnl']:.2f} пунктов)\n"
                    f"└ 💰 Итого: {color} {sign}{total_pnl:.2f} пунктов"
                )

            msg = (
                f"<b>📊 СТАТИСТИКА ASTRA</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>📅 Сегодня:</b>\n"
                f"{fmt_period(stats['day'])}\n\n"
                f"<b>📅 Неделя (пн–вс):</b>\n"
                f"{fmt_period(stats['week'])}\n\n"
                f"<b>📅 Месяц ({stats['month_name']}):</b>\n"
                f"{fmt_period(stats['month'])}"
            )
            self.bot.send_message(message.chat.id, msg, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Ошибка в /stats: {e}", exc_info=True)
            self.bot.send_message(
                message.chat.id,
                "❌ Ошибка получения статистики.",
                parse_mode='HTML'
            )

    # ==================== CALLBACK ОБРАБОТЧИКИ ====================

    def _handle_callback_query(self, call):
        """Обработка нажатий на inline кнопки"""
        try:
            logger.info(f"🔘 Callback query received: data={call.data}, from_user={call.from_user.id}")

            # Кнопки Astra Signal Bot (close_manual_trade) обрабатывает только второй бот.
            # Если сюда попал такой callback — не вызывать answer_callback_query основного бота
            # (Telegram вернёт ошибку по чужому call.id).
            if call.data and str(call.data).startswith("close_manual_trade:"):
                return

            # Отвечаем на callback чтобы убрать "крутилку"
            self.bot.answer_callback_query(call.id)
            
            # Проверяем что call.message существует
            if not call.message:
                logger.error(f"❌ call.message is None for callback {call.data}")
                chat_id = call.from_user.id
                self.bot.send_message(
                    chat_id,
                    "❌ Ошибка: сообщение не найдено. Попробуйте использовать команды напрямую.",
                    parse_mode='HTML'
                )
                return
            
            # Маршрутизация по callback_data
            if call.data == "price":
                logger.debug("📊 Handling price callback")
                self._cmd_price(call.message)
            elif call.data == "trend":
                logger.debug("📈 Handling trend callback")
                self._cmd_trend(call.message)
            elif call.data == "status":
                logger.debug("🛡️ Handling status callback")
                self._cmd_status(call.message)
            elif call.data == "help":
                logger.debug("❓ Handling help callback")
                self._cmd_help(call.message)
            elif call.data == "set_range":
                self._handle_set_range(call)
            elif call.data == "auto_range":
                self._handle_auto_range(call)
            elif call.data == "stats":
                self._cmd_stats(call.message)
            elif call.data == "cancel_range_input":
                self._handle_cancel_range_input(call)
            elif call.data == "approve_login":
                self._handle_approve_login(call)
            elif call.data == "deny_login":
                self._handle_deny_login(call)
            else:
                logger.warning(f"⚠️ Unknown callback_data: {call.data}")
                self.bot.send_message(
                    call.message.chat.id,
                    f"❓ Неизвестная команда: {call.data}",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки callback_query: {e}", exc_info=True)
            try:
                # Пытаемся отправить сообщение об ошибке
                chat_id = call.message.chat.id if call.message else call.from_user.id
                self.bot.send_message(
                    chat_id,
                    "❌ Произошла ошибка при обработке запроса. Попробуйте позже или используйте команды напрямую.",
                    parse_mode='HTML'
                )
            except Exception as e2:
                logger.error(f"❌ Не удалось отправить сообщение об ошибке: {e2}")

    def _handle_signal_bot_callback_query(self, call):
        """Обработка callback'ов в Astra Signal Bot (TELEGRAM_BOT_TOKEN_SIGNALS)."""
        try:
            if not self.bot_signals:
                return
            self.bot_signals.answer_callback_query(call.id)
            if not call.data:
                return

            if call.data.startswith("close_manual_trade:"):
                parts = call.data.split(":", 1)
                if len(parts) != 2:
                    return
                signal_id = int(parts[1])
                chat_id = call.from_user.id

                # Убираем кнопку сразу, чтобы она "пропадала" после нажатия.
                if call.message:
                    try:
                        self.bot_signals.edit_message_reply_markup(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=None
                        )
                    except Exception:
                        pass

                # Проверяем авторизацию пользователя
                if not self._is_authorized_user(chat_id):
                    self.bot_signals.send_message(chat_id, "⚠️ Недоступно: вы не авторизованы.", parse_mode='HTML')
                    return

                from services.db_service import db_service, safe_float
                from services.oanda_service import oanda_service

                signal = db_service.get_signal_by_id(signal_id)
                if not signal:
                    self.bot_signals.send_message(chat_id, "❌ Сделка не найдена в БД.", parse_mode='HTML')
                    return

                status = (signal.get("status") or "").lower()
                if status not in ("active", "be_set", "tp1_reached"):
                    self.bot_signals.send_message(chat_id, "ℹ️ Сделка уже закрыта.", parse_mode='HTML')
                    return

                entry_price = safe_float(signal.get("entry_price"), 0.0)
                signal_type = (signal.get("signal_type") or "").upper()
                if entry_price <= 0 or signal_type not in ("BUY", "SELL"):
                    self.bot_signals.send_message(chat_id, "❌ Некорректные параметры сделки.", parse_mode='HTML')
                    return

                # Текущая цена
                price_data = oanda_service.get_candles(timeframe="S5", limit=1)
                candles = price_data.get("candles") if isinstance(price_data, dict) else None
                if not candles:
                    self.bot_signals.send_message(chat_id, "❌ Не удалось получить текущую цену.", parse_mode='HTML')
                    return

                last_c = candles[-1]
                close_price = safe_float(last_c.get("close"), 0.0)
                if close_price <= 0:
                    self.bot_signals.send_message(chat_id, "❌ Некорректная текущая цена.", parse_mode='HTML')
                    return

                # PnL (как в Price Monitor): SELL = entry - close, BUY = close - entry
                result_pnl = entry_price - close_price if signal_type == "SELL" else close_price - entry_price

                ok = db_service.update_signal_result(signal_id, result_pnl, close_price, status="closed_manual")
                if not ok:
                    self.bot_signals.send_message(chat_id, "❌ Ошибка закрытия сделки в БД.", parse_mode='HTML')
                    return

                report = (
                    "🛑 <b>Закрыто вручную</b>\n\n"
                    f"id={signal_id} | {signal_type}\n"
                    f"Вход: <b>{entry_price:.2f}</b>\n"
                    f"Выход: <b>{close_price:.2f}</b>\n"
                    f"PnL: <b>{result_pnl:.2f}</b>"
                )
                self.bot_signals.send_message(chat_id, report, parse_mode='HTML')
        except Exception as e:
            logger.error(f"❌ Signal Bot callback error: {e}", exc_info=True)
            try:
                if self.bot_signals and call:
                    self.bot_signals.send_message(call.from_user.id, "❌ Ошибка обработки кнопки.", parse_mode='HTML')
            except Exception:
                pass
    
    def _handle_approve_login(self, call):
        """Обработка подтверждения входа"""
        try:
            # Редактируем сообщение, убирая кнопки
            self.bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=(
                    f"<b>✅ Вход подтвержден</b>\n\n"
                    f"Отлично! Авторизация успешно завершена.\n"
                    f"Добро пожаловать в <b>Astra Analyzer Pro</b>! 🚀"
                ),
                parse_mode='HTML'
            )
            logger.info(f"✅ Пользователь {call.from_user.id} подтвердил вход")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки approve_login: {e}")
    
    def _handle_deny_login(self, call):
        """Обработка отклонения входа (потенциальная угроза безопасности)"""
        try:
            # Редактируем сообщение
            self.bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=(
                    f"<b>⚠️ Попытка несанкционированного входа</b>\n\n"
                    f"Если это были не вы, немедленно:\n"
                    f"1️⃣ Смените пароль Telegram\n"
                    f"2️⃣ Проверьте активные сеансы\n"
                    f"3️⃣ Свяжитесь с поддержкой\n\n"
                    f"Ваш аккаунт в безопасности! 🛡️"
                ),
                parse_mode='HTML'
            )
            logger.warning(f"⚠️ Пользователь {call.from_user.id} ОТКЛОНИЛ вход! Возможная угроза безопасности.")
            
            # Опционально: можно добавить деактивацию сессии или уведомление админа
        except Exception as e:
            logger.error(f"❌ Ошибка обработки deny_login: {e}")

    def _is_authorized_user(self, chat_id: int) -> bool:
        """Проверка: пользователь авторизован в боте (есть в БД)."""
        try:
            from services.db_service import db_service
            user = db_service.get_user_by_id(chat_id)
            return user is not None
        except Exception:
            return False

    def _handle_set_range_cmd(self, message):
        """Команда /setrange — та же логика, что кнопка «Задать диапазон»."""
        chat_id = message.chat.id
        if not self._is_authorized_user(chat_id):
            self.bot.send_message(
                chat_id,
                "⚠️ Сначала авторизуйтесь на сайте Astra Analyzer Pro, затем нажмите /start в боте.",
                parse_mode='HTML'
            )
            return
        self._pending_range_chat_ids[chat_id] = True
        self.bot.send_message(
            chat_id,
            "Введите диапазон в формате: <b>HIGH LOW</b>\n\nНапример: <code>5192 5175</code>",
            parse_mode='HTML',
            reply_markup=self._get_cancel_range_markup()
        )

    def _get_cancel_range_markup(self):
        """Кнопка «Отмена» под сообщением «Введите диапазон»."""
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_range_input"))
        return markup

    def _handle_cancel_range_input(self, call):
        """Отмена ожидания ввода диапазона — система снова ищет диапазон сама."""
        chat_id = call.message.chat.id
        self._pending_range_chat_ids.pop(chat_id, None)
        self.bot.send_message(
            chat_id,
            "✅ Ожидание ввода отменено",
            parse_mode='HTML'
        )

    def _handle_auto_range_cmd(self, message):
        """Команда /autorange — та же логика, что кнопка «Авто режим»."""
        chat_id = message.chat.id
        if not self._is_authorized_user(chat_id):
            self.bot.send_message(
                chat_id,
                "⚠️ Сначала авторизуйтесь на сайте Astra Analyzer Pro, затем нажмите /start в боте.",
                parse_mode='HTML'
            )
            return
        try:
            from services.db_service import db_service
            db_service.deactivate_manual_ranges(symbol='XAUUSD')
            self.bot.send_message(
                chat_id,
                "✅ Ручной диапазон деактивирован.\nСистема переходит в авто режим.",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ deactivate_manual_ranges: {e}")
            self.bot.send_message(chat_id, "❌ Ошибка при переключении в авто режим.", parse_mode='HTML')

    def _handle_set_range(self, call):
        """Кнопка «Задать диапазон» — только для авторизованных."""
        chat_id = call.message.chat.id
        if not self._is_authorized_user(chat_id):
            self.bot.send_message(
                chat_id,
                "⚠️ Сначала авторизуйтесь на сайте Astra Analyzer Pro, затем нажмите /start в боте.",
                parse_mode='HTML'
            )
            return
        self._pending_range_chat_ids[chat_id] = True
        self.bot.send_message(
            chat_id,
            "Введите диапазон в формате: <b>HIGH LOW</b>\n\nНапример: <code>5192 5175</code>",
            parse_mode='HTML',
            reply_markup=self._get_cancel_range_markup()
        )

    def _handle_auto_range(self, call):
        """Кнопка «Авто режим» — деактивировать ручной диапазон."""
        chat_id = call.message.chat.id
        if not self._is_authorized_user(chat_id):
            self.bot.send_message(
                chat_id,
                "⚠️ Сначала авторизуйтесь на сайте Astra Analyzer Pro, затем нажмите /start в боте.",
                parse_mode='HTML'
            )
            return
        try:
            from services.db_service import db_service
            db_service.deactivate_manual_ranges(symbol='XAUUSD')
            self.bot.send_message(
                chat_id,
                "✅ Ручной диапазон деактивирован.\nСистема переходит в авто режим.",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ deactivate_manual_ranges: {e}")
            self.bot.send_message(chat_id, "❌ Ошибка при переключении в авто режим.", parse_mode='HTML')

    def _handle_range_input(self, message, text: str):
        """Парсинг HIGH LOW и сохранение ручного диапазона."""
        chat_id = message.chat.id
        parts = text.replace(",", ".").split()
        if len(parts) < 2:
            self.bot.send_message(
                chat_id,
                "❌ Введите два числа: HIGH и LOW через пробел.\nНапример: 5192 5175",
                parse_mode='HTML'
            )
            self._pending_range_chat_ids[chat_id] = True
            return
        try:
            high = float(parts[0])
            low = float(parts[1])
        except ValueError:
            self.bot.send_message(chat_id, "❌ Оба значения должны быть числами.", parse_mode='HTML')
            self._pending_range_chat_ids[chat_id] = True
            return
        if high <= 0 or low <= 0:
            self.bot.send_message(chat_id, "❌ HIGH и LOW должны быть больше 0.", parse_mode='HTML')
            self._pending_range_chat_ids[chat_id] = True
            return
        if high <= low:
            self.bot.send_message(chat_id, "❌ HIGH должен быть больше LOW.", parse_mode='HTML')
            self._pending_range_chat_ids[chat_id] = True
            return
        try:
            from services.db_service import db_service
            saved = db_service.save_manual_range(
                symbol='XAUUSD',
                timeframe='M15',
                range_high=high,
                range_low=low,
            )
            if saved:
                self.bot.send_message(
                    chat_id,
                    f"✅ Диапазон задан: [{low:.2f} - {high:.2f}]\nСистема будет торговать по нему.",
                    parse_mode='HTML'
                )
            else:
                self.bot.send_message(chat_id, "❌ Не удалось сохранить диапазон (проверьте БД).", parse_mode='HTML')
        except Exception as e:
            logger.error(f"❌ save_manual_range: {e}")
            self.bot.send_message(chat_id, "❌ Ошибка сохранения диапазона.", parse_mode='HTML')
    
    def _handle_text_message(self, message):
        """Обработка текстовых сообщений (для кнопок внизу и ввод диапазона)"""
        text = message.text
        chat_id = message.chat.id

        # Ожидание ввода диапазона HIGH LOW (только для авторизованных)
        if self._pending_range_chat_ids.pop(chat_id, None):
            self._handle_range_input(message, text)
            return

        if text == "📊 Курс Gold":
            self._cmd_price(message)
        elif text == "📈 Тренд M15":
            self._cmd_trend(message)
        elif text == "🛡️ Статус системы":
            self._cmd_status(message)
        elif text == "🔔 Последний сигнал":
            self._cmd_status(message)
        else:
            msg = (
                "❓ Неизвестная команда.\n"
                "Используйте /help для списка команд."
            )
            self.bot.send_message(chat_id, msg)
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    def _get_inline_menu(self):
        """Создает inline клавиатуру (кнопки под сообщением)"""
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn_price = types.InlineKeyboardButton("💰 Курс", callback_data="price")
        btn_trend = types.InlineKeyboardButton("📈 Анализ", callback_data="trend")
        btn_status = types.InlineKeyboardButton("🛡️ Статус", callback_data="status")
        btn_help = types.InlineKeyboardButton("❓ Помощь", callback_data="help")
        btn_stats = types.InlineKeyboardButton("📊 Статистика", callback_data="stats")
        btn_set_range = types.InlineKeyboardButton("📐 Задать диапазон", callback_data="set_range")
        btn_auto_range = types.InlineKeyboardButton("🔄 Авто режим", callback_data="auto_range")

        # Кнопка открытия терминала
        frontend_url = os.getenv("FRONTEND_URL", "https://astra-analyzer-pro.vercel.app/")
        btn_terminal = types.InlineKeyboardButton("🌐 Открыть Терминал", url=frontend_url)

        markup.add(btn_terminal)
        markup.add(btn_price, btn_trend)
        markup.add(btn_status, btn_help)
        markup.add(btn_stats)        # отдельная строка
        markup.add(btn_set_range)
        markup.add(btn_auto_range)
        
        return markup
    
    def _register_user(self, user):
        """
        Регистрирует пользователя в базе данных
        Использует partial_update=True чтобы НЕ затереть photo_url и другие поля
        """
        try:
            from services.db_service import db_service
            
            user_data = {
                'id': user.id,
                'first_name': user.first_name or '',
                'last_name': user.last_name or '',
                'username': user.username or '',
                'is_active': True
            }
            
            # ВАЖНО: partial_update=True чтобы не затереть photo_url, auth_date и другие поля
            db_service.save_user(user_data, partial_update=True)
            logger.info(f"✅ Пользователь {user.id} (@{user.username or 'no_username'}) обновлен (partial)")
        except Exception as e:
            logger.error(f"❌ Ошибка регистрации пользователя: {e}")
    
    def _auto_register_user(self, user):
        """Автоматически регистрирует пользователя при любом взаимодействии"""
        if user:
            self._register_user(user)
    
    # ==================== ПУБЛИЧНЫЕ МЕТОДЫ ====================
    
    def send_message(self, chat_id, text, reply_markup=None):
        """
        Отправка сообщения пользователю
        
        Args:
            chat_id: ID чата
            text: Текст сообщения (поддерживает HTML)
            reply_markup: Клавиатура (опционально)
        
        Returns:
            bool: Успешность отправки
        """
        if not self.bot:
            return False
        
        try:
            self.bot.send_message(
                chat_id, 
                text, 
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
            return True
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                # Пользователь заблокировал бота
                logger.warning(f"⚠️ Пользователь {chat_id} заблокировал бота")
                self._deactivate_user(chat_id)
            else:
                logger.error(f"❌ Ошибка отправки в TG (chat_id={chat_id}): {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка отправки: {e}")
            return False
    
    def broadcast_signal(self, user_ids, message):
        """
        Массовая рассылка сигналов
        
        Args:
            user_ids: Список ID пользователей
            message: Текст сообщения
        
        Returns:
            int: Количество успешно доставленных сообщений
        """
        if not self.bot or not user_ids:
            return 0
        
        success_count = 0
        
        for i, user_id in enumerate(user_ids):
            if self.send_message(user_id, message):
                success_count += 1
            
            # Rate limiting: Telegram ограничивает 30 сообщений/сек
            # Добавляем задержку каждые 25 сообщений
            if (i + 1) % 25 == 0:
                import time
                time.sleep(1)
        
        logger.info(f"📤 Рассылка завершена: {success_count}/{len(user_ids)} доставлено")
        return success_count
    
    def broadcast_deals_only(self, user_ids, message, reply_markup=None):
        """
        Рассылка ТОЛЬКО в Astra Signal Bot (@AstraSignal_Bot).
        Используется для BUY/SELL — без анализа каждые 15 мин и без WAIT.
        """
        if not self.bot_signals or not user_ids:
            return 0
        success_count = 0
        for i, user_id in enumerate(user_ids):
            try:
                self.bot_signals.send_message(
                    user_id,
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                success_count += 1
            except Exception as e:
                logger.warning(f"Signal Bot: не удалось отправить user {user_id}: {e}")
            if (i + 1) % 25 == 0:
                import time
                time.sleep(1)
        logger.info(f"📤 Astra Signal Bot: {success_count}/{len(user_ids)} доставлено")
        return success_count

    def _get_manual_close_trade_markup(self, signal_id: int):
        """Inline-кнопка ручного закрытия сделки (Signal Bot)."""
        markup = types.InlineKeyboardMarkup()
        btn_close = types.InlineKeyboardButton(
            "🛑 Закрыть сделку вручную",
            callback_data=f"close_manual_trade:{signal_id}"
        )
        markup.add(btn_close)
        return markup
    
    def send_approval_notification(self, user_id, user_name):
        """
        Отправляет пользователю уведомление о входе на сайт с кнопкой подтверждения
        
        Args:
            user_id: Telegram ID пользователя
            user_name: Имя пользователя
        
        Returns:
            bool: Успешность отправки
        """
        if not self.bot:
            logger.error("❌ Бот не инициализирован")
            return False
        
        safe_name = html.escape(str(user_name))
        message = (
            f"<b>🔔 Новый вход на сайт</b>\n\n"
            f"Привет, <b>{safe_name}</b>! 👋\n\n"
            f"Ты только что авторизовался на сайте <b>Astra Analyzer Pro</b>.\n\n"
            f"Подтверди, что это был ты:"
        )
        
        # Создаем inline кнопки
        markup = types.InlineKeyboardMarkup()
        btn_approve = types.InlineKeyboardButton("✅ Да, это я", callback_data="approve_login")
        btn_deny = types.InlineKeyboardButton("❌ Нет, не я", callback_data="deny_login")
        markup.add(btn_approve, btn_deny)
        
        logger.info(f"📱 Отправка уведомления о входе для user_id={user_id}")
        return self.send_message(user_id, message, reply_markup=markup)
    
    def _deactivate_user(self, user_id):
        """Деактивирует пользователя в БД (заблокировал бота)"""
        try:
            from services.db_service import db_service
            db_service.deactivate_user(user_id)
        except Exception as e:
            logger.error(f"❌ Ошибка деактивации пользователя {user_id}: {e}")
    
    def process_webhook_update(self, update_dict):
        """
        Обработка входящего webhook update от Telegram
        
        Args:
            update_dict: Словарь с данными update от Telegram
        
        Важно: один и тот же URL webhook может быть зарегистрирован у обоих ботов.
        Каждый update относится только к одному боту. Нельзя вызывать
        process_new_updates() у обоих — иначе answer_callback_query() падает
        (чужой callback_id), и основной бот шлёт пользователю «Произошла ошибка…».
        Callback «Закрыть сделку вручную» (close_manual_trade:) — только Signal Bot.
        """
        if not self.bot:
            logger.error("❌ Бот не инициализирован")
            return
        
        try:
            update = telebot.types.Update.de_json(update_dict)
            cq = getattr(update, "callback_query", None)
            data = getattr(cq, "data", None) if cq else None

            is_signal_manual_close = (
                data is not None and str(data).startswith("close_manual_trade:")
            )

            if is_signal_manual_close:
                if self.bot_signals:
                    self.bot_signals.process_new_updates([update])
                else:
                    logger.warning("⚠️ close_manual_trade callback, но TELEGRAM_BOT_TOKEN_SIGNALS не задан")
            else:
                self.bot.process_new_updates([update])

            logger.debug(f"✅ Webhook update обработан: {update.update_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки webhook update: {e}")
    
    def start_polling(self):
        """
        Запуск бота в polling режиме (для локальной разработки)
        Блокирующий вызов!
        """
        if not self.bot:
            logger.error("❌ Бот не инициализирован")
            return
        
        logger.info("🤖 Запуск Telegram бота в POLLING режиме...")
        logger.info("   Webhook не требуется!")
        
        try:
            # Удаляем webhook если был
            self.bot.remove_webhook()
            
            # Запускаем polling
            self.bot.infinity_polling(
                timeout=10,
                long_polling_timeout=5
            )
        except KeyboardInterrupt:
            logger.info("🛑 Polling остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка polling: {e}")
    
    def reload_config(self):
        """Перезагружает конфигурацию из .env"""
        logger.info("🔄 Перезагрузка конфигурации Telegram бота...")
        
        from dotenv import load_dotenv
        load_dotenv(override=True)
        
        old_webhook = self.webhook_url
        self.webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        self.use_polling = os.getenv("USE_TELEGRAM_POLLING", "false").lower() == "true"
        
        if old_webhook != self.webhook_url:
            logger.info(f"   Webhook URL изменен:")
            logger.info(f"   Старый: {old_webhook}")
            logger.info(f"   Новый:  {self.webhook_url}")
        
        logger.info(f"   Режим: {'POLLING' if self.use_polling else 'WEBHOOK'}")

        return True

    # ========================================================================
    # SESSION BREAKOUT v2.1 - УВЕДОМЛЕНИЯ
    # ========================================================================

    def send_session_breakout_signal(self, signal_data, test_mode=False):
        """
        Отправка сигнала AstraH4Trend v1.0 в Astra Signal Bot (при открытии позиции EA)

        Args:
            signal_data: dict из Supabase mt5_signals (direction, entry, sl, tp, risk_usd)
            test_mode: bool - если True, добавляет метку [TEST MODE]
        """
        if not self.bot_signals:
            logger.warning("Signal Bot не инициализирован")
            return

        try:
            direction = signal_data.get('direction', 'LONG')
            entry     = float(signal_data.get('entry', 0))
            sl        = float(signal_data.get('sl', 0))
            tp        = float(signal_data.get('tp', 0))
            risk_usd  = float(signal_data.get('risk_usd', 0))

            risk_pts   = abs(entry - sl)
            reward_pts = abs(tp - entry)
            rr_ratio   = reward_pts / risk_pts if risk_pts > 0 else 0

            dir_emoji  = "🟢" if direction == "LONG" else "🔴"
            trend_txt  = "🟢 UP (H4 slope ↑)" if direction == "LONG" else "🔴 DN (H4 slope ↓)"
            mode_label = "⚠️ [TEST MODE] " if test_mode else ""

            message = (
                f"{mode_label}<b>{dir_emoji} {direction} — AstraH4Trend v1.0</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>H4 Trend:</b>    {trend_txt}\n"
                f"<b>Entry:</b>       {entry:.2f}\n"
                f"<b>Stop Loss:</b>   {sl:.2f}  (1.2×ATR)\n"
                f"<b>Take Profit:</b> {tp:.2f}  (4R)\n"
                f"<b>Risk:</b>        ${risk_usd:.0f}\n"
                f"<b>R:R:</b>         1:{rr_ratio:.1f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Max 1 LONG + 1 SHORT per day | LONG trail@1.2R/0.1R | SHORT trail@0.8R/0.1R</i>"
            )

            sent_count = 0
            for chat_id in self.admin_chat_ids:
                if chat_id:
                    try:
                        self.bot_signals.send_message(chat_id, message, parse_mode='HTML')
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Ошибка отправки сигнала в {chat_id}: {e}")

            logger.info(f"H4Trend signal sent to {sent_count}: {direction} @ {entry:.2f}")

        except Exception as e:
            logger.error(f"Ошибка send_session_breakout_signal: {e}")

    def send_session_breakout_status(self, status_data):
        """
        Статус AstraH4Trend v1.0 каждые 15 мин → Astra Analyzer Pro (Main Bot)
        """
        if not self.bot or not self.admin_chat_ids:
            return

        try:
            timestamp     = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            current_price = status_data.get('current_price', 'N/A')
            h4_trend      = status_data.get('h4_trend')
            current_hour  = status_data.get('current_hour', 0)
            next_cron     = status_data.get('next_cron', '')
            long_done     = status_data.get('long_done', False)
            short_done    = status_data.get('short_done', False)
            active_long   = status_data.get('active_long')
            active_short  = status_data.get('active_short')

            # H4 trend
            if h4_trend:
                trend = h4_trend.get('trend', 'FLAT')
                if trend == 'UP':
                    trend_emoji, cmp, slope_arrow = '🟢', '&gt;', '↑'
                elif trend == 'DN':
                    trend_emoji, cmp, slope_arrow = '🔴', '&lt;', '↓'
                else:
                    trend_emoji, cmp, slope_arrow = '⚪', '~', '→'
                trend_line = (
                    f"H4 Trend: {trend_emoji} {trend}  "
                    f"({h4_trend['close']:.0f} {cmp} EMA20 {h4_trend['ema20']:.0f}, slope {slope_arrow})"
                )
            else:
                trend_line = "H4 Trend: нет данных"

            # Entry windows for current hour
            long_win  = (5 <= current_hour <= 9) or (13 <= current_hour <= 16)
            short_win = (8 <= current_hour <= 9) or (13 <= current_hour <= 16)
            lw = "✅ OPEN" if long_win  else "⏸ closed"
            sw = "✅ OPEN" if short_win else "⏸ closed"

            # Today trade status
            ls = "✅ Done" if long_done  else "⏳ Open"
            ss = "✅ Done" if short_done else "⏳ Open"

            # Active positions block
            pos_lines = []
            if active_long:
                e = float(active_long.get('entry', 0))
                s = float(active_long.get('sl', 0))
                pos_lines.append(f"  🟢 LONG  @ {e:.2f}  SL {s:.2f}")
            if active_short:
                e = float(active_short.get('entry', 0))
                s = float(active_short.get('sl', 0))
                pos_lines.append(f"  🔴 SHORT @ {e:.2f}  SL {s:.2f}")

            if pos_lines:
                pos_block = "Active:\n" + "\n".join(pos_lines)
            else:
                pos_block = "⏳ No active positions"

            message = (
                f"<b>📊 AstraH4Trend v1.0</b>\n"
                f"<i>{timestamp} | ${current_price}</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{trend_line}\n"
                f"Hour: {current_hour:02d} UTC  │  LONG win: {lw}  │  SHORT win: {sw}\n\n"
                f"Today:\n"
                f"  <b>LONG:</b>  {ls}   │   <b>SHORT:</b> {ss}\n\n"
                f"{pos_block}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Следующий крон: {next_cron}"
            )

            for chat_id in self.admin_chat_ids:
                if chat_id:
                    try:
                        self.bot.send_message(chat_id, message, parse_mode='HTML')
                    except Exception as e:
                        logger.error(f"Ошибка отправки статуса в {chat_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка send_session_breakout_status: {e}")

    def send_session_breakout_close(self, signal_data, test_mode=False):
        """
        Уведомление о закрытии позиции → Astra Signal Bot
        Вызывается из session_breakout_trader.py при EA-закрытии (SL/TP/trail/timeout).

        Args:
            signal_data: dict из Supabase mt5_signals (status=closed, exit_price, pnl заполнены EA)
            test_mode: bool
        """
        if not self.bot_signals:
            logger.warning("Signal Bot не инициализирован — close notification не отправлено")
            return

        try:
            direction  = signal_data.get('direction', 'LONG')
            entry      = float(signal_data.get('entry', 0))
            sl         = float(signal_data.get('sl', 0))
            exit_price = float(signal_data.get('exit_price', 0))
            pnl        = float(signal_data.get('pnl', 0))

            sl_dist = abs(entry - sl)
            if sl_dist > 0:
                r_val = (exit_price - entry) / sl_dist if direction == 'LONG' else (entry - exit_price) / sl_dist
            else:
                r_val = 0.0

            pnl_emoji  = "💰" if pnl > 0 else "❌"
            dir_emoji  = "🟢" if direction == "LONG" else "🔴"
            close_type = "TP HIT ✅" if pnl > 0 else "SL HIT ❌"
            pnl_sign   = "+" if pnl >= 0 else ""
            r_sign     = "+" if r_val >= 0 else ""
            mode_label = "⚠️ [TEST MODE] " if test_mode else ""

            message = (
                f"{mode_label}<b>{pnl_emoji} CLOSED — {close_type}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{dir_emoji} <b>{direction}</b> | AstraH4Trend v1.0\n"
                f"<b>Entry:</b>  {entry:.2f}\n"
                f"<b>Exit:</b>   {exit_price:.2f}\n"
                f"<b>Result:</b> {r_sign}{r_val:.2f}R\n"
                f"<b>PnL:</b>    {pnl_sign}{pnl:.0f}$\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )

            sent_count = 0
            for chat_id in self.admin_chat_ids:
                if chat_id:
                    try:
                        self.bot_signals.send_message(chat_id, message, parse_mode='HTML')
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Ошибка отправки close в {chat_id}: {e}")

            logger.info(f"Close sent to {sent_count}: {direction} {r_sign}{r_val:.2f}R  PnL={pnl_sign}{pnl:.0f}$")

        except Exception as e:
            logger.error(f"Ошибка send_session_breakout_close: {e}")

    def send_trade_update(self, trade_data, test_mode=False):
        """
        Отправка обновления по сделке (открытие, закрытие, trailing)

        Args:
            trade_data: dict с данными сделки
            test_mode: bool - если True, добавляет метку [TEST MODE]
        """
        if not self.bot_signals:
            return

        try:
            update_type = trade_data.get('type', 'unknown')  # 'opened', 'closed', 'trailing'
            mode_label = "⚠️ [TEST] " if test_mode else ""

            if update_type == 'opened':
                message = (
                    f"{mode_label}<b>✅ Trade Opened</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Ticket: #{trade_data.get('ticket', 'N/A')}\n"
                    f"Entry: {trade_data.get('entry', 0):.2f}\n"
                    f"Lot: {trade_data.get('lot', 0):.2f}"
                )
            elif update_type == 'closed':
                pnl = trade_data.get('pnl', 0)
                pnl_emoji = "💰" if pnl > 0 else "❌"
                message = (
                    f"{mode_label}<b>{pnl_emoji} Trade Closed</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Ticket: #{trade_data.get('ticket', 'N/A')}\n"
                    f"Exit: {trade_data.get('exit', 0):.2f}\n"
                    f"PnL: ${pnl:.2f}"
                )
            elif update_type == 'trailing':
                message = (
                    f"{mode_label}<b>📈 Trailing Stop Updated</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"New SL: {trade_data.get('new_sl', 0):.2f}\n"
                    f"Profit: +{trade_data.get('profit_r', 0):.1f}R"
                )
            else:
                return

            subscribers = self._get_all_subscribers()
            for chat_id in subscribers:
                try:
                    self.bot_signals.send_message(chat_id, message, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Ошибка отправки обновления в {chat_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка send_trade_update: {e}")



# Singleton instance
telegram_service = TelegramService()
