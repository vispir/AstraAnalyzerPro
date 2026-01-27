"""
Telegram Bot Service
Полный функционал бота с использованием pyTelegramBotAPI
"""
import os
import logging
import telebot
from telebot import types
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        self.use_polling = os.getenv("USE_TELEGRAM_POLLING", "false").lower() == "true"
        
        # Логируем конфигурацию
        logger.info(f"📱 TELEGRAM_BOT_TOKEN: {'установлен ✅' if self.bot_token else '❌ не установлен'}")
        logger.info(f"📱 Режим работы: {'POLLING' if self.use_polling else 'WEBHOOK'}")
        
        if not self.use_polling and self.webhook_url:
            logger.info(f"📱 TELEGRAM_WEBHOOK_URL: {self.webhook_url}")
        
        if not self.bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
            self.bot = None
            return
            
        # Инициализация бота
        self.bot = telebot.TeleBot(self.bot_token, threaded=False)
        
        # Регистрация всех обработчиков команд
        self._register_handlers()
        
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
        
        # Обработка callback кнопок (inline)
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            self._handle_callback_query(call)
        
        # Текстовые сообщения (кнопки внизу экрана)
        @self.bot.message_handler(func=lambda message: True)
        def handle_text(message):
            self._handle_text_message(message)
    
    # ==================== КОМАНДЫ ====================
    
    def _cmd_start(self, message):
        """Обработчик команды /start"""
        # Регистрируем пользователя в БД (доступно всем)
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
            "✅ <i>Вы подписаны на сигналы!</i>\n"
            "<b>Выбери действие:</b>"
        )
        
        # Отправляем с inline кнопками
        markup = self._get_inline_menu()
        self.bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=markup)
    
    def _cmd_price(self, message):
        """Обработчик команды /price"""
        self._auto_register_user(message.from_user)
        
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
        self._auto_register_user(message.from_user)
        
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
        self._auto_register_user(message.from_user)
        
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
            "<code>/help</code> — Эта справка\n\n"
            "💡 <i>Вы также можете использовать кнопки в меню</i>"
        )
        self.bot.send_message(message.chat.id, help_text, parse_mode='HTML')
    
    # ==================== CALLBACK ОБРАБОТЧИКИ ====================
    
    def _handle_callback_query(self, call):
        """Обработка нажатий на inline кнопки"""
        # Отвечаем на callback чтобы убрать "крутилку"
        self.bot.answer_callback_query(call.id)
        
        # Маршрутизация по callback_data
        if call.data == "price":
            self._cmd_price(call.message)
        elif call.data == "trend":
            self._cmd_trend(call.message)
        elif call.data == "status":
            self._cmd_status(call.message)
        elif call.data == "help":
            self._cmd_help(call.message)
        elif call.data == "approve_login":
            self._handle_approve_login(call)
        elif call.data == "deny_login":
            self._handle_deny_login(call)
    
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
    
    def _handle_text_message(self, message):
        """Обработка текстовых сообщений (для кнопок внизу)"""
        text = message.text
        
        if text == "📊 Курс Gold":
            self._cmd_price(message)
        elif text == "📈 Тренд M15":
            self._cmd_trend(message)
        elif text == "🛡️ Статус системы":
            self._cmd_status(message)
        elif text == "🔔 Последний сигнал":
            self._cmd_status(message)
        else:
            # Неизвестная команда
            msg = (
                "❓ Неизвестная команда.\n"
                "Используйте /help для списка команд."
            )
            self.bot.send_message(message.chat.id, msg)
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    def _get_inline_menu(self):
        """Создает inline клавиатуру (кнопки под сообщением)"""
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn_price = types.InlineKeyboardButton("💰 Курс", callback_data="price")
        btn_trend = types.InlineKeyboardButton("📈 Анализ", callback_data="trend")
        btn_status = types.InlineKeyboardButton("🛡️ Статус", callback_data="status")
        btn_help = types.InlineKeyboardButton("❓ Помощь", callback_data="help")
        
        # Кнопка открытия терминала
        frontend_url = os.getenv("FRONTEND_URL", "https://astra-analyzer-pro.vercel.app/")
        btn_terminal = types.InlineKeyboardButton("🌐 Открыть Терминал", url=frontend_url)
        
        markup.add(btn_terminal)
        markup.add(btn_price, btn_trend)
        markup.add(btn_status, btn_help)
        
        return markup
    
    def _register_user(self, user):
        """
        Регистрирует пользователя в базе данных
        Доступно всем - без проверки авторизации
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
            
            db_service.save_user(user_data)
            logger.info(f"✅ Пользователь {user.id} (@{user.username or 'no_username'}) зарегистрирован")
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
        
        message = (
            f"<b>🔔 Новый вход на сайт</b>\n\n"
            f"Привет, <b>{user_name}</b>! 👋\n\n"
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
        """
        if not self.bot:
            logger.error("❌ Бот не инициализирован")
            return
        
        try:
            update = telebot.types.Update.de_json(update_dict)
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


# Singleton instance
telegram_service = TelegramService()
