import sqlite3
import os
from datetime import datetime

class DBService:
    def __init__(self, db_path="astra.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        # Позволяет работать с базой как со словарем (row['name'])
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Создает таблицы, если их еще нет"""
        with self.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    photo_url TEXT,
                    created_at DATETIME,
                    last_login DATETIME,
                    is_active INTEGER DEFAULT 1
                )
            ''')
            conn.commit()

    def save_user(self, user_data):
        """Сохраняет или обновляет данные пользователя после входа"""
        tg_id = user_data.get('id')
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        username = user_data.get('username', '')
        photo_url = user_data.get('photo_url', '')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self.get_connection() as conn:
            # Используем INSERT OR REPLACE для обновления существующих данных
            conn.execute('''
                INSERT INTO users (telegram_id, first_name, last_name, username, photo_url, created_at, last_login)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    username=excluded.username,
                    photo_url=excluded.photo_url,
                    last_login=excluded.last_login
            ''', (tg_id, first_name, last_name, username, photo_url, now, now))
            conn.commit()

    def get_all_active_users(self):
        """Для будущей рассылки сигналов всем юзерам"""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT telegram_id FROM users WHERE is_active = 1')
            return [row['telegram_id'] for row in cursor.fetchall()]

# Создаем экземпляр сервиса
db_service = DBService()