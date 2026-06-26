#!/usr/bin/env python3
"""
Hebrew Learning Telegram Bot for Russian Speakers
Бот для изучения иврита русскоговорящими

Переменные окружения (задаются в панели BotHost или в файле .env):
  TELEGRAM_TOKEN      — токен бота
  ANTHROPIC_API_KEY   — ключ API Anthropic
"""

import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import urllib.request
import urllib.parse
import urllib.error
import io
import threading
import requests as _requests
from datetime import datetime, time, timedelta
from pathlib import Path

# ─── Авто-установка зависимостей ─────────────────────────────────────────────
def _ensure_package(package: str):
    try:
        __import__(package)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

_ensure_package("groq")
_ensure_package("gtts")

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from gtts import gTTS
import tempfile

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── .env loader (только для локальной разработки) ────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    logger.info(".env файл найден, загружаем...")
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())
else:
    logger.info(".env файл не найден — используем переменные окружения системы")

# ─── Config ───────────────────────────────────────────────────────────────────
DATA_FILE = Path("user_data.json")

# BotHost автоматически задаёт токен бота в переменной BOT_TOKEN.
TELEGRAM_TOKEN = (
    os.getenv("BOT_TOKEN") or
    os.getenv("TELEGRAM_BOT_TOKEN") or
    os.getenv("TELEGRAM_TOKEN") or
    os.getenv("TOKEN") or
    os.getenv("BOT_API_TOKEN") or
    ""
).strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

logger.info(f"TELEGRAM_TOKEN найден: {'ДА' if TELEGRAM_TOKEN else 'НЕТ'}")
logger.info(f"ANTHROPIC_API_KEY найден: {'ДА' if ANTHROPIC_API_KEY else 'НЕТ'}")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Токен бота не найден. Ожидались переменные: BOT_TOKEN, TELEGRAM_TOKEN, TOKEN. "
    )
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY не найден в переменных окружения.")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ─── Anthropic API ────────────────────────────────────────────────────────────
def call_claude(system: str, user_text: str, max_tokens: int = 600) -> str:
    """Call Anthropic API using only stdlib urllib"""
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]

# ─── TTS для произношения (Google Translate через gTTS) ─────────────────────
def get_hebrew_tts(text: str) -> bytes:
    """
    Генерирует аудио (mp3) через gTTS библиотеку
    """
    try:
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_path = tmp_file.name
        
        # Генерируем речь через gTTS
        tts = gTTS(text=text, lang='he', slow=False)
        tts.save(tmp_path)
        
        # Читаем файл в bytes
        with open(tmp_path, 'rb') as f:
            audio_bytes = f.read()
        
        # Удаляем временный файл
        os.unlink(tmp_path)
        
        return audio_bytes
        
    except Exception as e:
        logger.error(f"gTTS error: {e}")
        # Пробуем альтернативный метод через Google Translate TTS
        return get_hebrew_tts_google(text)

def get_hebrew_tts_google(text: str) -> bytes:
    """Резервный метод через Google Translate TTS (urllib)"""
    encoded_text = urllib.parse.quote(text)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=he&client=tw-ob&q={encoded_text}"
    
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()

# ─── Функция для удаления огласовок ──────────────────────────────────────────
def remove_niqqud(text: str) -> str:
    """Удаляет огласовки (никуд) из ивритского текста"""
    import re
    # Диапазон Unicode для огласовок: \u0591-\u05C7
    return re.sub(r'[\u0591-\u05C7]', '', text)

# ─── УРОВЕНЬ 1 ────────────────────────────────────────────────────────────────

# Уроки уровня 1 (без огласовок)
LESSONS_L1 = {
    "greetings": {
        "title": "👋 Приветствия",
        "phrases": [
            {"he": "שלום", "translit": "Шалом", "ru": "Привет / Мир"},
            {"he": "בוקר טוב", "translit": "Бокер тов", "ru": "Доброе утро"},
            {"he": "ערב טוב", "translit": "Эрев тов", "ru": "Добрый вечер"},
            {"he": "לילה טוב", "translit": "Лайла тов", "ru": "Спокойной ночи"},
            {"he": "מה שלומך?", "translit": "Ма шломха? (м) / Ма шломех? (ж)", "ru": "Как дела?"},
            {"he": "בסדר", "translit": "Бесэдэр", "ru": "Хорошо / Нормально"},
            {"he": "תודה", "translit": "Тода", "ru": "Спасибо"},
            {"he": "בבקשה", "translit": "Бевакаша", "ru": "Пожалуйста"},
            {"he": "סליחה", "translit": "Слиха", "ru": "Извините / Простите"},
            {"he": "להתראות", "translit": "Лехитраот", "ru": "До свидания"},
        ]
    },
    "numbers": {
        "title": "🔢 Числа 1-10",
        "phrases": [
            {"he": "אחד", "translit": "Эхад", "ru": "Один (1)"},
            {"he": "שתיים", "translit": "Штаим", "ru": "Два (2)"},
            {"he": "שלוש", "translit": "Шалош", "ru": "Три (3)"},
            {"he": "ארבע", "translit": "Арба", "ru": "Четыре (4)"},
            {"he": "חמש", "translit": "Хамеш", "ru": "Пять (5)"},
            {"he": "שש", "translit": "Шеш", "ru": "Шесть (6)"},
            {"he": "שבע", "translit": "Шева", "ru": "Семь (7)"},
            {"he": "שמונה", "translit": "Шмоне", "ru": "Восемь (8)"},
            {"he": "תשע", "translit": "Теша", "ru": "Девять (9)"},
            {"he": "עשר", "translit": "Эсэр", "ru": "Десять (10)"},
        ]
    },
    "verbs_l1": {
        "title": "🔥 10 главных глаголов",
        "phrases": [
            {"he": "להיות", "translit": "Лихйот", "ru": "Быть / являться"},
            {"he": "לעשות", "translit": "Лаасот", "ru": "Делать"},
            {"he": "לומר", "translit": "Ломар", "ru": "Говорить / сказать"},
            {"he": "ללכת", "translit": "Лалехет", "ru": "Идти / ходить"},
            {"he": "לדעת", "translit": "Лада'ат", "ru": "Знать"},
            {"he": "לראות", "translit": "Лиръот", "ru": "Видеть"},
            {"he": "לבוא", "translit": "Лаво", "ru": "Приходить / прийти"},
            {"he": "לתת", "translit": "Латет", "ru": "Давать / дать"},
            {"he": "לדבר", "translit": "Ледабер", "ru": "Разговаривать"},
            {"he": "לרצות", "translit": "Лирцот", "ru": "Хотеть"},
        ]
    },
    "words_l1": {
        "title": "📖 100 главных слов",
        "phrases": [
            {"he": "אני", "translit": "Ани", "ru": "Я"},
            {"he": "אתה / את", "translit": "Ата / Ат", "ru": "Ты (м/ж)"},
            {"he": "הוא", "translit": "Ху", "ru": "Он"},
            {"he": "היא", "translit": "Хи", "ru": "Она"},
            {"he": "אנחנו", "translit": "Анахну", "ru": "Мы"},
            {"he": "אתם / אתן", "translit": "Атем / Атен", "ru": "Вы (м/ж)"},
            {"he": "הם / הן", "translit": "Хем / Хен", "ru": "Они (м/ж)"},
            {"he": "כן", "translit": "Кен", "ru": "Да"},
            {"he": "לא", "translit": "Ло", "ru": "Нет"},
            {"he": "מה", "translit": "Ма", "ru": "Что"},
            {"he": "מי", "translit": "Ми", "ru": "Кто"},
            {"he": "איפה", "translit": "Эйфо", "ru": "Где"},
            {"he": "מתי", "translit": "Матай", "ru": "Когда"},
            {"he": "למה", "translit": "Лама", "ru": "Почему"},
            {"he": "איך", "translit": "Эйх", "ru": "Как"},
            {"he": "כמה", "translit": "Кама", "ru": "Сколько"},
            {"he": "זה / זאת", "translit": "Зе / Зот", "ru": "Это (м/ж)"},
            {"he": "כל", "translit": "Коль", "ru": "Всё / каждый"},
            {"he": "עם", "translit": "Им", "ru": "С (предлог)"},
            {"he": "ב", "translit": "Бе", "ru": "В / на (предлог)"},
            {"he": "ל", "translit": "Ле", "ru": "К / для (предлог)"},
            {"he": "מן / מ", "translit": "Мин / Ми", "ru": "Из / от (предлог)"},
            {"he": "על", "translit": "Аль", "ru": "На / о (предлог)"},
            {"he": "של", "translit": "Шель", "ru": "Из / принадлежащий"},
            {"he": "את", "translit": "Эт", "ru": "Знак прямого дополнения"},
            {"he": "גם", "translit": "Гам", "ru": "Тоже / также"},
            {"he": "רק", "translit": "Рак", "ru": "Только / лишь"},
            {"he": "כבר", "translit": "Квар", "ru": "Уже"},
            {"he": "עדין", "translit": "Адаин", "ru": "Ещё / пока что"},
            {"he": "אולי", "translit": "Улай", "ru": "Может быть"},
            {"he": "אף פעם", "translit": "Аф паам", "ru": "Никогда"},
            {"he": "תמיד", "translit": "Тамид", "ru": "Всегда"},
            {"he": "עכשיו", "translit": "Ахшав", "ru": "Сейчас"},
            {"he": "אחר כך", "translit": "Ахар ках", "ru": "Потом / после"},
            {"he": "לפני", "translit": "Лифней", "ru": "Перед / до"},
            {"he": "טוב", "translit": "Тов", "ru": "Хорошо / хороший"},
            {"he": "רע", "translit": "Ра", "ru": "Плохо / плохой"},
            {"he": "גדול", "translit": "Гадоль", "ru": "Большой"},
            {"he": "קטן", "translit": "Катан", "ru": "Маленький"},
            {"he": "חדש", "translit": "Хадаш", "ru": "Новый"},
            {"he": "ישן", "translit": "Яшан", "ru": "Старый"},
            {"he": "יפה", "translit": "Яфе", "ru": "Красивый"},
            {"he": "מהיר", "translit": "Махир", "ru": "Быстрый"},
            {"he": "איטי", "translit": "Ити", "ru": "Медленный"},
            {"he": "קר", "translit": "Кар", "ru": "Холодный"},
            {"he": "חם", "translit": "Хам", "ru": "Горячий"},
            {"he": "יום", "translit": "Йом", "ru": "День"},
            {"he": "לילה", "translit": "Лайла", "ru": "Ночь"},
            {"he": "שעה", "translit": "Шаа", "ru": "Час"},
            {"he": "שבוע", "translit": "Шавуа", "ru": "Неделя"},
            {"he": "חודש", "translit": "Ходеш", "ru": "Месяц"},
            {"he": "שנה", "translit": "Шана", "ru": "Год"},
            {"he": "בית", "translit": "Байт", "ru": "Дом"},
            {"he": "עיר", "translit": "Ир", "ru": "Город"},
            {"he": "רחוב", "translit": "Рехов", "ru": "Улица"},
            {"he": "מדינה", "translit": "Медина", "ru": "Страна / государство"},
            {"he": "ארץ", "translit": "Эрец", "ru": "Земля / страна"},
            {"he": "אויר", "translit": "Авир", "ru": "Воздух"},
            {"he": "מים", "translit": "Маим", "ru": "Вода"},
            {"he": "אש", "translit": "Эш", "ru": "Огонь"},
            {"he": "אדם", "translit": "Адам", "ru": "Человек / люди"},
            {"he": "איש", "translit": "Иш", "ru": "Мужчина / муж"},
            {"he": "אישה", "translit": "Иша", "ru": "Женщина / жена"},
            {"he": "ילד", "translit": "Йелед", "ru": "Ребёнок / мальчик"},
            {"he": "ילדה", "translit": "Ялда", "ru": "Девочка"},
            {"he": "חבר", "translit": "Хавер", "ru": "Друг"},
            {"he": "עבודה", "translit": "Авода", "ru": "Работа"},
            {"he": "כסף", "translit": "Кесеф", "ru": "Деньги / серебро"},
            {"he": "זמן", "translit": "Зман", "ru": "Время"},
            {"he": "מקום", "translit": "Маком", "ru": "Место"},
            {"he": "דרך", "translit": "Дерех", "ru": "Путь / дорога"},
            {"he": "חיים", "translit": "Хаим", "ru": "Жизнь"},
            {"he": "שם", "translit": "Шем", "ru": "Имя"},
            {"he": "יד", "translit": "Яд", "ru": "Рука"},
            {"he": "עין", "translit": "Аин", "ru": "Глаз"},
            {"he": "לב", "translit": "Лев", "ru": "Сердце"},
            {"he": "ראש", "translit": "Рош", "ru": "Голова"},
            {"he": "פה", "translit": "Пе", "ru": "Рот"},
            {"he": "אוכל", "translit": "Охель", "ru": "Еда"},
            {"he": "לחם", "translit": "Лехем", "ru": "Хлеб"},
            {"he": "מילה", "translit": "Мила", "ru": "Слово"},
            {"he": "שפה", "translit": "Сафа", "ru": "Язык / губа"},
            {"he": "שאלה", "translit": "Шеэла", "ru": "Вопрос"},
            {"he": "תשובה", "translit": "Тшува", "ru": "Ответ"},
            {"he": "בעיה", "translit": "Беая", "ru": "Проблема"},
            {"he": "רעיון", "translit": "Раайон", "ru": "Идея"},
            {"he": "ספר", "translit": "Сефер", "ru": "Книга"},
            {"he": "מכתב", "translit": "Михтав", "ru": "Письмо"},
            {"he": "דואר אלקטרוני", "translit": "Доар электрони", "ru": "Электронная почта"},
            {"he": "טלפון", "translit": "Телефон", "ru": "Телефон"},
            {"he": "מכונית", "translit": "Мехонит", "ru": "Машина / автомобиль"},
            {"he": "אוטובוס", "translit": "Отобус", "ru": "Автобус"},
            {"he": "בית ספר", "translit": "Бейт сефер", "ru": "Школа"},
            {"he": "בית חולים", "translit": "Бейт холим", "ru": "Больница"},
            {"he": "חדר", "translit": "Хадар", "ru": "Комната"},
            {"he": "דלת", "translit": "Делет", "ru": "Дверь"},
            {"he": "חלון", "translit": "Халон", "ru": "Окно"},
            {"he": "שולחן", "translit": "Шульхан", "ru": "Стол"},
            {"he": "כיסא", "translit": "Кисэ", "ru": "Стул"},
            {"he": "בגד", "translit": "Бегед", "ru": "Одежда"},
        ]
    }
}

# ─── УРОВЕНЬ 2 ────────────────────────────────────────────────────────────────

# Глаголы уровня 2 (версия 1: 50 глаголов, не включая первые 10)
VERBS_L2_V1 = [
    {"he": "לאכול", "translit": "Леэхоль", "ru": "Есть / кушать"},
    {"he": "לשתות", "translit": "Лиштот", "ru": "Пить"},
    {"he": "לישון", "translit": "Лишон", "ru": "Спать"},
    {"he": "לעבוד", "translit": "Ла'авод", "ru": "Работать"},
    {"he": "ללמוד", "translit": "Лилмод", "ru": "Учиться / изучать"},
    {"he": "לקרוא", "translit": "Ликро", "ru": "Читать"},
    {"he": "לכתוב", "translit": "Лихтов", "ru": "Писать"},
    {"he": "לשמוע", "translit": "Лишмоа", "ru": "Слышать / слушать"},
    {"he": "להבין", "translit": "Лехавин", "ru": "Понимать"},
    {"he": "לחשוב", "translit": "Лахшов", "ru": "Думать"},
    {"he": "להרגיש", "translit": "Лехаргиш", "ru": "Чувствовать"},
    {"he": "לזכור", "translit": "Лизкор", "ru": "Помнить"},
    {"he": "לשכוח", "translit": "Лишкоах", "ru": "Забывать"},
    {"he": "לנסות", "translit": "Ленасот", "ru": "Пробовать / пытаться"},
    {"he": "להצליח", "translit": "Лехацлиах", "ru": "Преуспевать"},
    {"he": "לבקש", "translit": "Левакеш", "ru": "Просить"},
    {"he": "לענות", "translit": "Ла'анот", "ru": "Отвечать"},
    {"he": "לשאול", "translit": "Лиш'оль", "ru": "Спрашивать"},
    {"he": "לפתוח", "translit": "Лифтоах", "ru": "Открывать"},
    {"he": "לסגור", "translit": "Лисгор", "ru": "Закрывать"},
    {"he": "לבוא", "translit": "Лаво", "ru": "Приходить"},
    {"he": "לצאת", "translit": "Лацет", "ru": "Выходить"},
    {"he": "לחזור", "translit": "Лахзор", "ru": "Возвращаться"},
    {"he": "להגיע", "translit": "Лехагиа", "ru": "Прибывать / достигать"},
    {"he": "לעזוב", "translit": "Ла'азов", "ru": "Покидать / оставлять"},
    {"he": "לשבת", "translit": "Лашевет", "ru": "Сидеть"},
    {"he": "לעמוד", "translit": "Ла'амод", "ru": "Стоять"},
    {"he": "לרוץ", "translit": "Ларуц", "ru": "Бежать"},
    {"he": "לשיר", "translit": "Лашир", "ru": "Петь"},
    {"he": "לרקוד", "translit": "Лиркод", "ru": "Танцевать"},
    {"he": "לצפות", "translit": "Лицпот", "ru": "Смотреть / наблюдать"},
    {"he": "להתבונן", "translit": "Лехитбонен", "ru": "Наблюдать / всматриваться"},
    {"he": "להתחיל", "translit": "Лехатхиль", "ru": "Начинать"},
    {"he": "לסיים", "translit": "Лесайем", "ru": "Заканчивать"},
    {"he": "להמשיך", "translit": "Лехамших", "ru": "Продолжать"},
    {"he": "לחכות", "translit": "Лехако", "ru": "Ждать"},
    {"he": "להתעורר", "translit": "Лехиторер", "ru": "Просыпаться"},
    {"he": "להתלבש", "translit": "Лехитлабеш", "ru": "Одеваться"},
    {"he": "להתקלח", "translit": "Лехиткалеах", "ru": "Принимать душ"},
    {"he": "להסתדר", "translit": "Лехистадер", "ru": "Справляться / обходиться"},
    {"he": "להנות", "translit": "Леханот", "ru": "Наслаждаться"},
    {"he": "לפחד", "translit": "Лифход", "ru": "Бояться"},
    {"he": "לאהוב", "translit": "Леэхов", "ru": "Любить"},
    {"he": "לשנוא", "translit": "Лисно", "ru": "Ненавидеть"},
    {"he": "לכעוס", "translit": "Лик'ос", "ru": "Злиться"},
    {"he": "לשמוח", "translit": "Лисмоах", "ru": "Радоваться"},
    {"he": "להצטער", "translit": "Лехицатер", "ru": "Сожалеть / грустить"},
    {"he": "להתנצל", "translit": "Лехитнацель", "ru": "Извиняться"},
    {"he": "להזמין", "translit": "Лехазмин", "ru": "Приглашать / заказывать"},
    {"he": "לשלם", "translit": "Лешалем", "ru": "Платить"},
]

# Глаголы уровня 2 (версия 2: 100 глаголов, не включая первые 20)
VERBS_L2_V2 = VERBS_L2_V1 + [
    {"he": "להשאיר", "translit": "Лехашир", "ru": "Оставлять"},
    {"he": "להסיר", "translit": "Лехасир", "ru": "Убирать / снимать"},
    {"he": "להכניס", "translit": "Лехахнис", "ru": "Вносить / впускать"},
    {"he": "להוציא", "translit": "Лехоци", "ru": "Вынимать / выводить"},
    {"he": "להעלות", "translit": "Лехаалот", "ru": "Поднимать"},
    {"he": "להוריד", "translit": "Лехорид", "ru": "Опускать / спускать"},
    {"he": "להביא", "translit": "Лехави", "ru": "Приносить"},
    {"he": "לקחת", "translit": "Лакахат", "ru": "Брать"},
    {"he": "למכור", "translit": "Лимкор", "ru": "Продавать"},
    {"he": "לקנות", "translit": "Ликнот", "ru": "Покупать"},
    {"he": "לבשל", "translit": "Левашель", "ru": "Готовить (еду)"},
    {"he": "לטגן", "translit": "Летаген", "ru": "Жарить"},
    {"he": "לאפות", "translit": "Леэфот", "ru": "Печь"},
    {"he": "לשפוך", "translit": "Лишпох", "ru": "Наливать / проливать"},
    {"he": "לערבב", "translit": "Леарбэв", "ru": "Смешивать"},
    {"he": "לחתוך", "translit": "Лахтох", "ru": "Резать"},
    {"he": "לשבור", "translit": "Лишбор", "ru": "Ломать"},
    {"he": "לתקן", "translit": "Летакан", "ru": "Исправлять / чинить"},
    {"he": "לבנות", "translit": "Ливнот", "ru": "Строить"},
    {"he": "להרוס", "translit": "Лехорэс", "ru": "Разрушать"},
    {"he": "לצייר", "translit": "Лецайер", "ru": "Рисовать"},
    {"he": "לצלם", "translit": "Лецалем", "ru": "Фотографировать / снимать"},
    {"he": "לשחק", "translit": "Лесахэк", "ru": "Играть"},
    {"he": "לנצח", "translit": "Ленацеах", "ru": "Побеждать"},
    {"he": "להפסיד", "translit": "Лехафсид", "ru": "Проигрывать / терять"},
    {"he": "לספור", "translit": "Лиспор", "ru": "Считать"},
    {"he": "למדוד", "translit": "Лимдод", "ru": "Измерять"},
    {"he": "לשקול", "translit": "Лишколь", "ru": "Взвешивать"},
    {"he": "להחליט", "translit": "Лехахлит", "ru": "Решать"},
    {"he": "לבחור", "translit": "Ливхор", "ru": "Выбирать"},
    {"he": "להתגבר", "translit": "Лехитгабер", "ru": "Преодолевать"},
    {"he": "להסתכל", "translit": "Лехистакель", "ru": "Смотреть"},
    {"he": "להקשיב", "translit": "Лехакшив", "ru": "Слушать (внимательно)"},
    {"he": "להתייחס", "translit": "Лехитъяхас", "ru": "Относиться"},
    {"he": "להתעניין", "translit": "Лехитъаньен", "ru": "Интересоваться"},
    {"he": "להתאמן", "translit": "Лехитамен", "ru": "Тренироваться"},
    {"he": "להשתפר", "translit": "Лехиштапер", "ru": "Улучшаться"},
    {"he": "להתפתח", "translit": "Лехитпатеах", "ru": "Развиваться"},
    {"he": "לגדול", "translit": "Лигдоль", "ru": "Расти"},
    {"he": "להתכווץ", "translit": "Лехиткавец", "ru": "Сжиматься"},
    {"he": "להרחיב", "translit": "Лехархив", "ru": "Расширять"},
    {"he": "לצמצם", "translit": "Лецамцем", "ru": "Сокращать"},
    {"he": "לחלק", "translit": "Лехалек", "ru": "Делить / раздавать"},
    {"he": "להחליף", "translit": "Лехахлиф", "ru": "Менять / обменивать"},
    {"he": "להזיז", "translit": "Лехазиз", "ru": "Двигать"},
    {"he": "לעצור", "translit": "Ла'ацор", "ru": "Останавливать"},
    {"he": "להמשיך", "translit": "Лехамших", "ru": "Продолжать"},
    {"he": "להתקדם", "translit": "Лехиткадек", "ru": "Продвигаться"},
    {"he": "להתרחק", "translit": "Лехитрахек", "ru": "Отдаляться"},
    {"he": "להתקרב", "translit": "Лехиткарев", "ru": "Приближаться"},
    {"he": "להתחבר", "translit": "Лехиткабер", "ru": "Подключаться / соединяться"},
]

# Слова уровня 2 (версия 1: 500 слов, не включая первые 100)
WORDS_L2_V1 = [
    {"he": "שולחן", "translit": "Шульхан", "ru": "Стол"},
    {"he": "כיסא", "translit": "Кисэ", "ru": "Стул"},
    {"he": "ספר", "translit": "Сефер", "ru": "Книга"},
    {"he": "עט", "translit": "Эт", "ru": "Ручка"},
    {"he": "מחשב", "translit": "Махшев", "ru": "Компьютер"},
    {"he": "טלפון", "translit": "Телефон", "ru": "Телефон"},
    {"he": "אוכל", "translit": "Охель", "ru": "Еда"},
    {"he": "מים", "translit": "Маим", "ru": "Вода"},
    {"he": "חלב", "translit": "Халав", "ru": "Молоко"},
    {"he": "ביצה", "translit": "Бейца", "ru": "Яйцо"},
    {"he": "לחם", "translit": "Лехем", "ru": "Хлеб"},
    {"he": "פירות", "translit": "Пейрот", "ru": "Фрукты"},
    {"he": "ירקות", "translit": "Ерако", "ru": "Овощи"},
    {"he": "בשר", "translit": "Басар", "ru": "Мясо"},
    {"he": "דג", "translit": "Даг", "ru": "Рыба"},
    {"he": "גבינה", "translit": "Гвина", "ru": "Сыр"},
    {"he": "שמן", "translit": "Шемен", "ru": "Масло"},
    {"he": "סוכר", "translit": "Сукар", "ru": "Сахар"},
    {"he": "מלח", "translit": "Мелах", "ru": "Соль"},
    {"he": "פלפל", "translit": "Пильпель", "ru": "Перец"},
    {"he": "קפה", "translit": "Кафе", "ru": "Кофе"},
    {"he": "תה", "translit": "Те", "ru": "Чай"},
    {"he": "יין", "translit": "Яин", "ru": "Вино"},
    {"he": "בירה", "translit": "Бира", "ru": "Пиво"},
    {"he": "הר", "translit": "Хар", "ru": "Гора"},
    {"he": "עמק", "translit": "Эмек", "ru": "Долина"},
    {"he": "ים", "translit": "Ям", "ru": "Море"},
    {"he": "נהר", "translit": "Нахар", "ru": "Река"},
    {"he": "אגם", "translit": "Агам", "ru": "Озеро"},
    {"he": "עץ", "translit": "Эц", "ru": "Дерево"},
    {"he": "פרח", "translit": "Перах", "ru": "Цветок"},
    {"he": "דשא", "translit": "Деше", "ru": "Трава"},
    {"he": "אבן", "translit": "Эвен", "ru": "Камень"},
    {"he": "חול", "translit": "Холь", "ru": "Песок"},
    {"he": "שמיים", "translit": "Шамаим", "ru": "Небо"},
    {"he": "ענן", "translit": "Анан", "ru": "Облако"},
    {"he": "גשם", "translit": "Гешем", "ru": "Дождь"},
    {"he": "שלג", "translit": "Шелег", "ru": "Снег"},
    {"he": "רוח", "translit": "Руах", "ru": "Ветер"},
    {"he": "קרח", "translit": "Керах", "ru": "Лёд"},
    {"he": "אש", "translit": "Эш", "ru": "Огонь"},
    {"he": "עשן", "translit": "Ашан", "ru": "Дым"},
    {"he": "חלום", "translit": "Халом", "ru": "Сон / мечта"},
    {"he": "אמת", "translit": "Эмет", "ru": "Правда"},
    {"he": "שקר", "translit": "Шекер", "ru": "Ложь"},
    {"he": "חיים", "translit": "Хаим", "ru": "Жизнь"},
    {"he": "מות", "translit": "Мавет", "ru": "Смерть"},
    {"he": "לידה", "translit": "Лейда", "ru": "Рождение"},
    {"he": "חתונה", "translit": "Хатуна", "ru": "Свадьба"},
    {"he": "חג", "translit": "Хаг", "ru": "Праздник"},
    {"he": "שבת", "translit": "Шабат", "ru": "Суббота"},
    {"he": "יום שישי", "translit": "Йом шиши", "ru": "Пятница"},
    {"he": "יום שבת", "translit": "Йом шабат", "ru": "Суббота"},
    {"he": "יום ראשון", "translit": "Йом ришон", "ru": "Воскресенье"},
    {"he": "יום שני", "translit": "Йом шени", "ru": "Понедельник"},
    {"he": "יום שלישי", "translit": "Йом шлиши", "ru": "Вторник"},
    {"he": "יום רביעי", "translit": "Йом ревии", "ru": "Среда"},
    {"he": "יום חמישי", "translit": "Йон хамиши", "ru": "Четверг"},
    {"he": "שעה", "translit": "Шаа", "ru": "Час"},
    {"he": "דקה", "translit": "Дака", "ru": "Минута"},
    {"he": "שנייה", "translit": "Шния", "ru": "Секунда"},
    {"he": "בוקר", "translit": "Бокер", "ru": "Утро"},
    {"he": "צהריים", "translit": "Цаараим", "ru": "Полдень"},
    {"he": "ערב", "translit": "Эрев", "ru": "Вечер"},
    {"he": "לילה", "translit": "Лайла", "ru": "Ночь"},
    {"he": "שלום", "translit": "Шалом", "ru": "Мир / привет"},
    {"he": "מלחמה", "translit": "Милхама", "ru": "Война"},
    {"he": "שלטון", "translit": "Шилтон", "ru": "Правление"},
    {"he": "חופש", "translit": "Хофеш", "ru": "Свобода"},
    {"he": "שוויון", "translit": "Шивеон", "ru": "Равенство"},
    {"he": "צדק", "translit": "Цедек", "ru": "Справедливость"},
    {"he": "אהבה", "translit": "Ахава", "ru": "Любовь"},
    {"he": "שנאה", "translit": "Сина", "ru": "Ненависть"},
    {"he": "רחמים", "translit": "Рхамим", "ru": "Сострадание"},
    {"he": "חמלה", "translit": "Хемла", "ru": "Жалость"},
    {"he": "טוב", "translit": "Тов", "ru": "Хороший"},
    {"he": "רע", "translit": "Ра", "ru": "Плохой"},
    {"he": "חדש", "translit": "Хадаш", "ru": "Новый"},
    {"he": "ישן", "translit": "Яшан", "ru": "Старый"},
    {"he": "חם", "translit": "Хам", "ru": "Горячий"},
    {"he": "קר", "translit": "Кар", "ru": "Холодный"},
    {"he": "טרי", "translit": "Тари", "ru": "Свежий"},
    {"he": "רקוב", "translit": "Ракув", "ru": "Гнилой"},
    {"he": "נקי", "translit": "Наки", "ru": "Чистый"},
    {"he": "מלוכלך", "translit": "Мелухлах", "ru": "Грязный"},
    {"he": "קשה", "translit": "Каше", "ru": "Тяжёлый / трудный"},
    {"he": "רך", "translit": "Рак", "ru": "Мягкий"},
    {"he": "חזק", "translit": "Хазак", "ru": "Сильный"},
    {"he": "חלש", "translit": "Халаш", "ru": "Слабый"},
    {"he": "עשיר", "translit": "Ашир", "ru": "Богатый"},
    {"he": "עני", "translit": "Ани", "ru": "Бедный"},
    {"he": "יפה", "translit": "Яфе", "ru": "Красивый"},
    {"he": "מכוער", "translit": "Махоар", "ru": "Уродливый"},
    {"he": "אמיץ", "translit": "Амиц", "ru": "Смелый"},
    {"he": "פחדן", "translit": "Пахдан", "ru": "Трусливый"},
    {"he": "חכם", "translit": "Хахам", "ru": "Умный"},
    {"he": "טיפש", "translit": "Типеш", "ru": "Глупый"},
    {"he": "ידיד", "translit": "Ядид", "ru": "Приятель"},
    {"he": "אויב", "translit": "Ойев", "ru": "Враг"},
    {"he": "שכן", "translit": "Шахен", "ru": "Сосед"},
    {"he": "אורח", "translit": "Ореах", "ru": "Гость"},
    {"he": "מארח", "translit": "Марах", "ru": "Хозяин"},
]

# Числа уровня 2 (версия 1: 11-1000, не включая 1-10)
NUMBERS_L2_V1 = [
    {"he": "אחת עשרה", "translit": "Ахат эсре", "ru": "11"},
    {"he": "שתים עשרה", "translit": "Штейм эсре", "ru": "12"},
    {"he": "שלוש עשרה", "translit": "Шлош эсре", "ru": "13"},
    {"he": "ארבע עשרה", "translit": "Арба эсре", "ru": "14"},
    {"he": "חמש עשרה", "translit": "Хамеш эсре", "ru": "15"},
    {"he": "שש עשרה", "translit": "Шеш эсре", "ru": "16"},
    {"he": "שבע עשרה", "translit": "Шва эсре", "ru": "17"},
    {"he": "שמונה עשרה", "translit": "Шмоне эсре", "ru": "18"},
    {"he": "תשע עשרה", "translit": "Тша эсре", "ru": "19"},
    {"he": "עשרים", "translit": "Эсрим", "ru": "20"},
    {"he": "שלושים", "translit": "Шлошим", "ru": "30"},
    {"he": "ארבעים", "translit": "Арбаим", "ru": "40"},
    {"he": "חמישים", "translit": "Хамишим", "ru": "50"},
    {"he": "שישים", "translit": "Шишим", "ru": "60"},
    {"he": "שבעים", "translit": "Шивъим", "ru": "70"},
    {"he": "שמונים", "translit": "Шмоним", "ru": "80"},
    {"he": "תשעים", "translit": "Тишъим", "ru": "90"},
    {"he": "מאה", "translit": "Меа", "ru": "100"},
    {"he": "מאתיים", "translit": "Матаим", "ru": "200"},
    {"he": "שלוש מאות", "translit": "Шлош меот", "ru": "300"},
    {"he": "ארבע מאות", "translit": "Арба меот", "ru": "400"},
    {"he": "חמש מאות", "translit": "Хамеш меот", "ru": "500"},
    {"he": "שש מאות", "translit": "Шеш меот", "ru": "600"},
    {"he": "שבע מאות", "translit": "Шва меот", "ru": "700"},
    {"he": "שמונה מאות", "translit": "Шмоне меот", "ru": "800"},
    {"he": "תשע מאות", "translit": "Тша меот", "ru": "900"},
    {"he": "אלף", "translit": "Элеф", "ru": "1000"},
]

# Числа уровня 2 (версия 2: 1001-1000000 с шагом 1000)
NUMBERS_L2_V2 = [
    {"he": "אלף ואחת", "translit": "Элеф ве-ахат", "ru": "1001"},
    {"he": "אלפיים", "translit": "Альпаим", "ru": "2000"},
    {"he": "אלפיים ואחת", "translit": "Альпаим ве-ахат", "ru": "2001"},
    {"he": "שלושת אלפים", "translit": "Шлошет алафим", "ru": "3000"},
    {"he": "ארבעת אלפים", "translit": "Арбаат алафим", "ru": "4000"},
    {"he": "חמשת אלפים", "translit": "Хамешет алафим", "ru": "5000"},
    {"he": "ששת אלפים", "translit": "Шешет алафим", "ru": "6000"},
    {"he": "שבעת אלפים", "translit": "Шивъат алафим", "ru": "7000"},
    {"he": "שמונת אלפים", "translit": "Шмонат алафим", "ru": "8000"},
    {"he": "תשעת אלפים", "translit": "Тишъат алафим", "ru": "9000"},
    {"he": "עשרת אלפים", "translit": "Асерет алафим", "ru": "10000"},
    {"he": "עשרים אלף", "translit": "Эсрим элеф", "ru": "20000"},
    {"he": "שלושים אלף", "translit": "Шлошим элеф", "ru": "30000"},
    {"he": "ארבעים אלף", "translit": "Арбаим элеф", "ru": "40000"},
    {"he": "חמישים אלף", "translit": "Хамишим элеф", "ru": "50000"},
    {"he": "שישים אלף", "translit": "Шишим элеф", "ru": "60000"},
    {"he": "שבעים אלף", "translit": "Шивъим элеф", "ru": "70000"},
    {"he": "שמונים אלף", "translit": "Шмоним элеф", "ru": "80000"},
    {"he": "תשעים אלף", "translit": "Тишъим элеф", "ru": "90000"},
    {"he": "מאה אלף", "translit": "Меа элеф", "ru": "100000"},
    {"he": "מאתיים אלף", "translit": "Матаим элеф", "ru": "200000"},
    {"he": "שלוש מאות אלף", "translit": "Шлош меот элеф", "ru": "300000"},
    {"he": "ארבע מאות אלף", "translit": "Арба меот элеф", "ru": "400000"},
    {"he": "חמש מאות אלף", "translit": "Хамеш меот элеф", "ru": "500000"},
    {"he": "שש מאות אלף", "translit": "Шеш меот элеф", "ru": "600000"},
    {"he": "שבע מאות אלף", "translit": "Шва меот элеф", "ru": "700000"},
    {"he": "שמונה מאות אלף", "translit": "Шмоне меот элеф", "ru": "800000"},
    {"he": "תשע מאות אלף", "translit": "Тша меот элеф", "ru": "900000"},
    {"he": "מיליון", "translit": "Милйон", "ru": "1,000,000"},
]

# ─── Функции для получения уроков по уровню и версии ──────────────────────────

def get_lessons(version: int, level: int) -> dict:
    """Возвращает словарь уроков для заданной версии и уровня"""
    if level == 1:
        return LESSONS_L1.copy()
    elif level == 2:
        # Для уровня 2 создаём динамические уроки
        lessons = {
            "verbs_l2": {
                "title": "🔥 Глаголы уровня 2",
                "phrases": VERBS_L2_V1 if version == 1 else VERBS_L2_V2
            },
            "words_l2": {
                "title": "📖 Слова уровня 2",
                "phrases": WORDS_L2_V1 if version == 1 else WORDS_L2_V1 + []
            },
            "numbers_l2": {
                "title": "🔢 Числа уровня 2",
                "phrases": NUMBERS_L2_V1 if version == 1 else NUMBERS_L2_V2
            }
        }
        return lessons
    return {}

def get_all_lessons(version: int, level: int) -> dict:
    """Возвращает все уроки для заданной версии и уровня"""
    if level == 1:
        return LESSONS_L1.copy()
    else:
        return get_lessons(version, 2)

def get_lesson_by_key(version: int, level: int, lesson_key: str) -> dict:
    """Возвращает урок по ключу"""
    # Сначала пробуем получить уроки для уровня
    lessons = get_all_lessons(version, level)
    if lesson_key in lessons:
        return lessons[lesson_key]
    
    # Для уровня 2 пробуем найти в динамических уроках
    if level == 2:
        if lesson_key == "verbs_l2":
            return {
                "title": "🔥 Глаголы уровня 2",
                "phrases": VERBS_L2_V1 if version == 1 else VERBS_L2_V2
            }
        elif lesson_key == "words_l2":
            return {
                "title": "📖 Слова уровня 2",
                "phrases": WORDS_L2_V1 if version == 1 else WORDS_L2_V1 + []
            }
        elif lesson_key == "numbers_l2":
            return {
                "title": "🔢 Числа уровня 2",
                "phrases": NUMBERS_L2_V1 if version == 1 else NUMBERS_L2_V2
            }
    
    # Проверяем уровень 1 (для обратной совместимости)
    if lesson_key in LESSONS_L1:
        return LESSONS_L1[lesson_key]
    
    logger.warning(f"Урок не найден: {lesson_key}, уровень: {level}, версия: {version}")
    return None

def get_phrase_by_key(version: int, level: int, lesson_key: str, phrase_idx: int) -> dict:
    """Возвращает фразу по ключу урока и индексу"""
    lesson = get_lesson_by_key(version, level, lesson_key)
    if not lesson:
        logger.error(f"Урок не найден: {lesson_key}")
        return None
    try:
        phrases = lesson.get("phrases", [])
        if phrase_idx < len(phrases):
            return phrases[phrase_idx]
        else:
            logger.error(f"Индекс {phrase_idx} вне диапазона (всего {len(phrases)})")
            return None
    except (IndexError, KeyError) as e:
        logger.error(f"Ошибка получения фразы: {e}")
        return None

def get_quiz_pool(version: int, level: int, quiz_type: str) -> list:
    """Возвращает пул фраз для теста"""
    if quiz_type == "verbs":
        if level == 1:
            return LESSONS_L1["verbs_l1"]["phrases"]
        else:
            return VERBS_L2_V1 if version == 1 else VERBS_L2_V2
    elif quiz_type == "words":
        if level == 1:
            return LESSONS_L1["words_l1"]["phrases"]
        else:
            return WORDS_L2_V1 if version == 1 else WORDS_L2_V1 + []
    elif quiz_type == "numbers":
        if level == 1:
            return LESSONS_L1["numbers"]["phrases"]
        else:
            return NUMBERS_L2_V1 if version == 1 else NUMBERS_L2_V2
    return []

DAILY_TIPS = [
    "💡 Иврит читается справа налево! Это одна из первых вещей, которую нужно запомнить.",
    "💡 В иврите нет заглавных букв — все буквы одного размера.",
    "💡 Слово «шалом» (שלום) означает одновременно «привет», «пока» и «мир».",
    "💡 В иврите глаголы меняются в зависимости от пола говорящего (мужской/женский).",
    "💡 Буква «алеф» (א) — первая в ивритском алфавите, как «А» в русском.",
    "💡 «Тода раба» (תודה רבה) означает «большое спасибо».",
    "💡 Число 7 считается счастливым в иудейской традиции — на иврите שבע (шева).",
    "💡 Слово «сабра» — так называют уроженцев Израиля. Это тоже вид кактуса!",
]

# ─── User Data ────────────────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(user_id: int) -> dict:
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "learned": [],
            "streak": 0,
            "last_active": None,
            "total_phrases": 0,
            "reminders": True,
            "current_lesson": None,
            "quiz_score": 0,
            "quiz_total": 0,
            "version": 1,
            "level": 1,
        }
        save_data(data)
    return data[uid]

def update_user(user_id: int, updates: dict):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid].update(updates)
    save_data(data)

def update_streak(user_id: int):
    user = get_user(user_id)
    today = datetime.now().date().isoformat()
    last = user.get("last_active")
    streak = user.get("streak", 0)
    if last != today:
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        if last == yesterday:
            streak += 1
        else:
            streak = 1
        update_user(user_id, {"streak": streak, "last_active": today})

# ─── Keyboards ────────────────────────────────────────────────────────────────
def main_menu_keyboard(version: int = 1, level: int = 1):
    buttons = [
        ["📚 Уроки", "📊 Мой прогресс"],
        ["🎯 Тест глаголы", "🎯 Тест слова"],
        ["💡 Совет дня", "⚙️ Настройки"],
    ]
    if level >= 2:
        buttons.insert(2, ["🔢 Тест числа"])
    else:
        buttons.insert(2, ["🔢 Тест числа"])
    
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_menu_keyboard(user_id: int):
    user = get_user(user_id)
    version = user.get("version", 1)
    level = user.get("level", 1)
    return main_menu_keyboard(version, level)

def lessons_keyboard(version: int = 1, level: int = 1):
    buttons = []
    lessons = get_all_lessons(version, level)
    for key, lesson in lessons.items():
        buttons.append([InlineKeyboardButton(lesson["title"], callback_data=f"lesson_{key}")])
    
    # Кнопка для смены уровня
    if level == 1:
        buttons.append([InlineKeyboardButton("⬆️ Уровень 2", callback_data="level_up")])
    else:
        buttons.append([InlineKeyboardButton("⬇️ Уровень 1", callback_data="level_down")])
    
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)

def phrase_keyboard(lesson_key: str, phrase_idx: int, total: int, version: int = 1, level: int = 1):
    row1 = []
    if phrase_idx > 0:
        row1.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"phrase_{lesson_key}_{phrase_idx-1}"))
    if phrase_idx < total - 1:
        row1.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"phrase_{lesson_key}_{phrase_idx+1}"))

    row2 = [
        InlineKeyboardButton("🔊 Произношение", callback_data=f"audio_{lesson_key}_{phrase_idx}"),
        InlineKeyboardButton("✅ Выучил!", callback_data=f"learned_{lesson_key}_{phrase_idx}"),
    ]
    row3 = [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]

    rows = []
    if row1:
        rows.append(row1)
    rows.append(row2)
    rows.append(row3)
    return InlineKeyboardMarkup(rows)

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    update_streak(user.id)
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Версия 1", callback_data="set_version_1"),
            InlineKeyboardButton("Версия 2", callback_data="set_version_2"),
        ],
    ])
    await update.message.reply_text(
        f"שלום, {user.first_name}! 👋\n\n"
        "Добро пожаловать в бот для изучения иврита!\n\n"
        "Выберите версию:\n"
        "• *Версия 1* — транслитерация на русском\n"
        "• *Версия 2* — транслитерация на русском, глаголы в трёх временах с примерами употребления",
        parse_mode="Markdown",
        reply_markup=buttons
    )

async def level_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню выбора уровня"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    current_level = user.get("level", 1)
    version = user.get("version", 1)
    
    text = f"📊 *Ваш текущий уровень: {current_level}*\n\n"
    if current_level == 1:
        text += "Уровень 1: базовые слова и фразы\n"
        text += "Уровень 2: расширенный словарь\n\n"
    else:
        text += "Уровень 2: расширенный словарь\n\n"
    
    text += "Выберите уровень для изучения:"
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Уровень 1", callback_data="set_level_1")],
        [InlineKeyboardButton("📚 Уровень 2", callback_data="set_level_2")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
    ])
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=buttons)

async def lessons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    version = user.get("version", 1)
    level = user.get("level", 1)
    await update.message.reply_text(
        f"📚 *Выберите тему урока (Уровень {level}):*",
        parse_mode="Markdown",
        reply_markup=lessons_keyboard(version, level)
    )

async def show_phrase(query, lesson_key: str, phrase_idx: int, version: int = 1, level: int = 1):
    lesson = get_lesson_by_key(version, level, lesson_key)
    if not lesson:
        await query.edit_message_text("❌ Урок не найден.")
        return
    
    phrase = lesson["phrases"][phrase_idx]
    total = len(lesson["phrases"])

    text = (
        f"{lesson['title']} — фраза {phrase_idx + 1}/{total}\n\n"
        f"🇮🇱 *Иврит:*\n"
        f"```\n{phrase['he']}\n```\n\n"
        f"🔤 *Транслитерация:*\n_{phrase['translit']}_\n\n"
        f"🇷🇺 *По-русски:*\n{phrase['ru']}"
    )

    # Добавляем пример для версии 2
    if version == 2 and "example" in phrase and phrase["example"]:
        text += f"\n\n📝 *Пример:*\n_{phrase['example']}_"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=phrase_keyboard(lesson_key, phrase_idx, total, version, level)
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    user = get_user(user_id)
    version = user.get("version", 1)
    level = user.get("level", 1)

    if data.startswith("set_version_"):
        version = int(data.split("_")[-1])
        update_user(user_id, {"version": version, "level": 1})
        if version == 1:
            desc = "транслитерация на *русском*"
        else:
            desc = "транслитерация на *русском*, глаголы в трёх временах с примерами употребления"
        await query.edit_message_text(
            f"✅ *Версия {version}* выбрана — {desc}!\n\n"
            "Здесь вы найдёте:\n"
            "📚 *Уроки* — фразы с транслитерацией\n"
            "🎯 *Тест* — проверь свои знания\n"
            "📊 *Прогресс* — следи за успехами\n"
            "💡 *Совет дня* — интересные факты\n\n"
            "Иврит читается *справа налево* — начнём! 🇮🇱",
            parse_mode="Markdown",
        )
        await query.message.reply_text("Выберите раздел:", reply_markup=get_menu_keyboard(user_id))

    elif data.startswith("set_level_"):
        level = int(data.split("_")[-1])
        update_user(user_id, {"level": level})
        await query.edit_message_text(
            f"✅ *Уровень {level}* выбран!\n\n"
            f"Теперь доступны уроки уровня {level}.",
            parse_mode="Markdown"
        )
        await query.message.reply_text("Выберите раздел:", reply_markup=get_menu_keyboard(user_id))

    elif data == "level_up":
        if level == 1:
            update_user(user_id, {"level": 2})
            await query.edit_message_text(
                "✅ *Переключено на уровень 2*\n\n"
                "Теперь доступны расширенные уроки!",
                parse_mode="Markdown"
            )
            await query.message.reply_text("Выберите раздел:", reply_markup=get_menu_keyboard(user_id))
        else:
            await query.answer("Вы уже на уровне 2!")

    elif data == "level_down":
        if level == 2:
            update_user(user_id, {"level": 1})
            await query.edit_message_text(
                "✅ *Переключено на уровень 1*\n\n"
                "Базовые уроки снова доступны.",
                parse_mode="Markdown"
            )
            await query.message.reply_text("Выберите раздел:", reply_markup=get_menu_keyboard(user_id))
        else:
            await query.answer("Вы уже на уровне 1!")

    elif data.startswith("lesson_"):
        lesson_key = data[7:]
        context.user_data["lesson"] = lesson_key
        await show_phrase(query, lesson_key, 0, version, level)

    elif data.startswith("phrase_"):
        # Парсим: phrase_lesson_key_idx
        parts = data.split("_")
        if len(parts) < 3:
            await query.answer("❌ Ошибка: неверный формат")
            return
        lesson_key = "_".join(parts[1:-1])
        idx_str = parts[-1]
        try:
            phrase_idx = int(idx_str)
        except ValueError:
            await query.answer("❌ Ошибка: неверный индекс")
            return
        await show_phrase(query, lesson_key, phrase_idx, version, level)

    elif data.startswith("audio_"):
        # Парсим: audio_lesson_key_idx
        parts = data.split("_")
        if len(parts) < 3:
            await query.answer("❌ Ошибка: неверный формат")
            return
        
        # Склеиваем части для lesson_key (между audio_ и последним _)
        lesson_key = "_".join(parts[1:-1])
        idx_str = parts[-1]
        
        try:
            phrase_idx = int(idx_str)
        except ValueError:
            await query.answer("❌ Ошибка: неверный индекс")
            return
        
        # Получаем фразу
        phrase = get_phrase_by_key(version, level, lesson_key, phrase_idx)
        if not phrase:
            await query.answer(f"❌ Фраза не найдена")
            logger.error(f"Фраза не найдена: lesson_key={lesson_key}, idx={phrase_idx}, level={level}, version={version}")
            return
        
        he_text = phrase["he"]
        
        await query.answer("🔊 Генерирую произношение...", show_alert=False)
        
        try:
            # Используем улучшенный TTS
            audio_bytes = get_hebrew_tts(he_text)
            
            # Отправляем как голосовое сообщение
            caption = f"🇮🇱 {he_text}\n👄 {phrase['translit']}\n🇷🇺 {phrase['ru']}"
            if version == 2 and "example" in phrase and phrase["example"]:
                caption += f"\n📝 {phrase['example']}"
            
            await query.message.reply_voice(
                voice=io.BytesIO(audio_bytes),
                caption=caption,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"TTS error for {he_text}: {e}")
            await query.message.reply_text(
                f"❌ Не удалось загрузить аудио.\n\n"
                f"👄 *Читается:* {phrase['translit']}\n"
                f"Повторите вслух несколько раз!\n\n"
                f"💡 *Совет:* Попробуйте произнести слово по слогам.",
                parse_mode="Markdown"
            )

    elif data.startswith("learned_"):
        # Парсим: learned_lesson_key_idx
        parts = data.split("_")
        if len(parts) < 3:
            await query.answer("❌ Ошибка: неверный формат")
            return
        lesson_key = "_".join(parts[1:-1])
        idx_str = parts[-1]
        try:
            phrase_idx = int(idx_str)
        except ValueError:
            await query.answer("❌ Ошибка: неверный индекс")
            return
        
        key = f"{lesson_key}_{phrase_idx}"
        user_data = get_user(user_id)
        learned = user_data.get("learned", [])
        total_phrases = user_data.get("total_phrases", 0)
        
        lesson = get_lesson_by_key(version, level, lesson_key)
        if not lesson:
            await query.answer("❌ Урок не найден")
            return
        total = len(lesson["phrases"])
        next_idx = phrase_idx + 1
        
        if key not in learned:
            learned.append(key)
            total_phrases += 1
            update_user(user_id, {"learned": learned, "total_phrases": total_phrases})
            await query.answer("✅ Отлично! Фраза добавлена в выученные!", show_alert=False)
            
            if next_idx < total:
                await show_phrase(query, lesson_key, next_idx, version, level)
            else:
                await query.edit_message_text(
                    f"🎉 *Поздравляю!* Вы выучили все фразы в этом уроке!\n\n"
                    f"📚 {lesson['title']} — завершён!\n\n"
                    f"Выберите другой урок или вернитесь в меню.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📚 Другой урок", callback_data="go_lessons")],
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
                    ])
                )
        else:
            await query.answer("Вы уже выучили эту фразу! 🌟", show_alert=False)
            if next_idx < total:
                await show_phrase(query, lesson_key, next_idx, version, level)

    elif data == "main_menu":
        await query.edit_message_text(
            "Выберите раздел 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Уроки", callback_data="go_lessons")],
                [InlineKeyboardButton("🎯 Тест", callback_data="go_quiz")],
                [InlineKeyboardButton("📊 Уровень", callback_data="go_level")],
            ])
        )

    elif data == "go_lessons":
        await query.edit_message_text(
            f"📚 *Выберите тему урока (Уровень {level}):*",
            parse_mode="Markdown",
            reply_markup=lessons_keyboard(version, level)
        )

    elif data == "go_quiz":
        await query.edit_message_text(
            "🎯 *Выберите тест:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Тест глаголы", callback_data="quiz_type_verbs")],
                [InlineKeyboardButton("🎯 Тест слова", callback_data="quiz_type_words")],
                [InlineKeyboardButton("🔢 Тест числа", callback_data="quiz_type_numbers")],
            ])
        )

    elif data == "go_level":
        await query.edit_message_text(
            f"📊 *Ваш текущий уровень: {level}*\n\n"
            "Выберите уровень для изучения:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Уровень 1", callback_data="set_level_1")],
                [InlineKeyboardButton("📚 Уровень 2", callback_data="set_level_2")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
            ])
        )

    elif data.startswith("quiz_type_"):
        quiz_type = data[len("quiz_type_"):]
        await send_quiz_question(query, user_id, context, quiz_type)

    elif data.startswith("quiz_"):
        await handle_quiz_answer(query, user_id, data, context)

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_streak(user_id)
    user_data = get_user(user_id)
    version = user_data.get("version", 1)
    level = user_data.get("level", 1)

    # Подсчёт общего количества фраз
    lessons = get_all_lessons(version, level)
    total_available = sum(len(l["phrases"]) for l in lessons.values())
    learned = len(user_data.get("learned", []))
    streak = user_data.get("streak", 0)
    quiz_score = user_data.get("quiz_score", 0)
    quiz_total = user_data.get("quiz_total", 0)
    accuracy = round(quiz_score / quiz_total * 100) if quiz_total > 0 else 0

    bar_len = 10
    filled = round(learned / total_available * bar_len) if total_available > 0 else 0
    bar = "🟩" * filled + "⬜" * (bar_len - filled)

    text = (
        f"📊 *Ваш прогресс*\n\n"
        f"🔥 Серия дней: *{streak}* {'🏆' if streak >= 7 else ''}\n"
        f"📚 Уровень: *{level}*\n"
        f"📱 Версия: *{version}*\n\n"
        f"📚 Выучено фраз:\n"
        f"{bar} {learned}/{total_available}\n\n"
        f"🎯 Тесты: *{quiz_score}/{quiz_total}* правильных ({accuracy}%)\n\n"
        f"{'🌟 Вы освоили все фразы! Попробуйте тест!' if learned == total_available else '👉 Продолжайте учиться!'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tip = random.choice(DAILY_TIPS)
    await update.message.reply_text(tip)

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора теста"""
    user_id = update.effective_user.id
    await update.message.reply_text(
        "🎯 *Выберите тест:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Тест глаголы", callback_data="quiz_type_verbs")],
            [InlineKeyboardButton("🎯 Тест слова", callback_data="quiz_type_words")],
            [InlineKeyboardButton("🔢 Тест числа", callback_data="quiz_type_numbers")],
        ])
    )

async def send_quiz_question(query, user_id: int, context, quiz_type: str = "words"):
    user_data = get_user(user_id)
    version = user_data.get("version", 1)
    level = user_data.get("level", 1)

    pool = get_quiz_pool(version, level, quiz_type)
    
    if quiz_type == "verbs":
        title = "🔥 Тест: глаголы"
    elif quiz_type == "numbers":
        title = "🔢 Тест: числа"
    else:
        title = "📖 Тест: слова"

    if len(pool) < 4:
        await query.edit_message_text("Недостаточно фраз для теста.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], min(3, len(pool)-1))
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct
    context.user_data["quiz_type"] = quiz_type
    context.user_data["quiz_pool"] = pool

    text = f"{title} (Уровень {level})\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
    # Добавляем пример для версии 2
    if version == 2 and "example" in correct and correct["example"]:
        text += f"📝 *Пример:* {correct['example']}\n\n"
    
    text += "Выберите правильный перевод:"

    buttons = [[InlineKeyboardButton(opt["ru"], callback_data=f"quiz_{i}")] for i, opt in enumerate(all_options)]

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_quiz_answer(query, user_id: int, data: str, context):
    chosen = int(data.split("_")[1])
    correct_pos = context.user_data.get("quiz_correct")
    correct_phrase = context.user_data.get("quiz_phrase", {})
    quiz_type = context.user_data.get("quiz_type", "words")
    pool = context.user_data.get("quiz_pool", [])

    if correct_pos is None:
        await query.edit_message_text("Начните тест заново — нажмите кнопку теста")
        return

    user_data = get_user(user_id)
    version = user_data.get("version", 1)
    quiz_total = user_data.get("quiz_total", 0) + 1
    quiz_score = user_data.get("quiz_score", 0)

    is_correct = (chosen == correct_pos)
    if is_correct:
        quiz_score += 1

    update_user(user_id, {"quiz_score": quiz_score, "quiz_total": quiz_total})

    result = "✅ *Правильно!*" if is_correct else "❌ *Неправильно.*"
    result += f"\n\n🇮🇱 {correct_phrase.get('he', '')}\n"
    result += f"🔤 {correct_phrase.get('translit', '')}\n"
    result += f"🇷🇺 {correct_phrase.get('ru', '')}"

    if version == 2 and "example" in correct_phrase and correct_phrase["example"]:
        result += f"\n\n📝 {correct_phrase['example']}"

    context.user_data["last_result"] = result
    
    if pool and len(pool) >= 4:
        remaining = [p for p in pool if p["he"] != correct_phrase["he"]]
        if len(remaining) >= 4:
            context.user_data["quiz_pool"] = remaining
            await query.edit_message_text(result, parse_mode="Markdown")
            await send_next_quiz_question(query.message, user_id, context, quiz_type, remaining)
            return

    context.user_data.pop("quiz_correct", None)
    context.user_data.pop("quiz_phrase", None)
    context.user_data.pop("quiz_pool", None)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Новый тест", callback_data=f"quiz_type_{quiz_type}")],
        [InlineKeyboardButton("📊 Мой счёт", callback_data="show_score")],
        [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")],
    ])
    await query.edit_message_text(
        f"{result}\n\n🎯 Тест завершён!",
        parse_mode="Markdown",
        reply_markup=buttons
    )

async def send_next_quiz_question(message, user_id: int, context, quiz_type: str, pool: list):
    user_data = get_user(user_id)
    version = user_data.get("version", 1)
    level = user_data.get("level", 1)

    if len(pool) < 4:
        await message.reply_text("✅ Тест завершён! Вы ответили на все вопросы.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], min(3, len(pool)-1))
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct
    context.user_data["quiz_type"] = quiz_type
    context.user_data["quiz_pool"] = pool

    if quiz_type == "verbs":
        title = "🔥 Тест: глаголы"
    elif quiz_type == "numbers":
        title = "🔢 Тест: числа"
    else:
        title = "📖 Тест: слова"

    text = f"{title} (Уровень {level})\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
    if version == 2 and "example" in correct and correct["example"]:
        text += f"📝 *Пример:* {correct['example']}\n\n"
    
    text += "Выберите правильный перевод:"

    buttons = [[InlineKeyboardButton(opt["ru"], callback_data=f"quiz_{i}")] for i, opt in enumerate(all_options)]

    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def next_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("next_quiz_"):
        quiz_type = data[len("next_quiz_"):]
        await send_quiz_question(query, query.from_user.id, context, quiz_type)
    elif data == "show_score":
        user_data = get_user(query.from_user.id)
        score = user_data.get("quiz_score", 0)
        total = user_data.get("quiz_total", 0)
        pct = round(score / total * 100) if total else 0
        await query.edit_message_text(
            f"📊 *Ваш счёт в тестах*\n\n"
            f"✅ Правильных: {score}/{total} ({pct}%)\n\n"
            f"{'🏆 Отличный результат!' if pct >= 80 else '💪 Продолжайте практиковаться!'}",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    update_streak(user_id)

    if text == "📚 Уроки":
        await lessons_menu(update, context)
    elif text == "🎯 Тест глаголы":
        await update.message.reply_text("🔄 Загрузка теста...")
        context.user_data["quiz_type"] = "verbs"
        await send_quiz_question_to_message(update.message, user_id, context, "verbs")
    elif text == "🎯 Тест слова":
        await update.message.reply_text("🔄 Загрузка теста...")
        context.user_data["quiz_type"] = "words"
        await send_quiz_question_to_message(update.message, user_id, context, "words")
    elif text == "🔢 Тест числа":
        await update.message.reply_text("🔄 Загрузка теста...")
        context.user_data["quiz_type"] = "numbers"
        await send_quiz_question_to_message(update.message, user_id, context, "numbers")
    elif text == "🎯 Тест":
        await quiz(update, context)
    elif text == "📊 Мой прогресс":
        await progress(update, context)
    elif text == "💡 Совет дня":
        await daily_tip(update, context)
    elif text == "⚙️ Настройки":
        await settings(update, context)
    else:
        await update.message.reply_text(
            "Выберите раздел в меню ниже 👇",
            reply_markup=get_menu_keyboard(user_id)
        )

async def send_quiz_question_to_message(message, user_id: int, context, quiz_type: str = "words"):
    user_data = get_user(user_id)
    version = user_data.get("version", 1)
    level = user_data.get("level", 1)

    pool = get_quiz_pool(version, level, quiz_type)
    
    if quiz_type == "verbs":
        title = "🔥 Тест: глаголы"
    elif quiz_type == "numbers":
        title = "🔢 Тест: числа"
    else:
        title = "📖 Тест: слова"

    if len(pool) < 4:
        await message.reply_text("Недостаточно фраз для теста.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], min(3, len(pool)-1))
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct
    context.user_data["quiz_type"] = quiz_type
    context.user_data["quiz_pool"] = pool

    text = f"{title} (Уровень {level})\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
    if version == 2 and "example" in correct and correct["example"]:
        text += f"📝 *Пример:* {correct['example']}\n\n"
    
    text += "Выберите правильный перевод:"

    buttons = [[InlineKeyboardButton(opt["ru"], callback_data=f"quiz_{i}")] for i, opt in enumerate(all_options)]

    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user(update.effective_user.id)
    reminders = user_data.get("reminders", True)
    status = "включены ✅" if reminders else "выключены ❌"
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Выключить напоминания" if reminders else "🔔 Включить напоминания",
                callback_data="toggle_reminders"
            )
        ],
        [InlineKeyboardButton("🗑 Сбросить прогресс", callback_data="reset_progress")],
    ])
    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n🔔 Ежедневные напоминания: {status}\n\n"
        f"Бот присылает напоминание каждый день в 9:00 🕘",
        parse_mode="Markdown",
        reply_markup=buttons
    )

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "toggle_reminders":
        user_data = get_user(user_id)
        new_val = not user_data.get("reminders", True)
        update_user(user_id, {"reminders": new_val})
        status = "включены ✅" if new_val else "выключены ❌"
        await query.edit_message_text(f"🔔 Напоминания теперь {status}")

    elif query.data == "reset_progress":
        update_user(user_id, {
            "learned": [], "streak": 0, "total_phrases": 0,
            "quiz_score": 0, "quiz_total": 0
        })
        await query.edit_message_text("🗑 Прогресс сброшен. Начинаем сначала!")

# ─── Daily Reminder ───────────────────────────────────────────────────────────
def _reminder_loop(bot_token: str):
    import time as _time
    while True:
        now = datetime.now()
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        sleep_sec = (next_run - now).total_seconds()
        logger.info(f"Следующее напоминание через {sleep_sec/3600:.1f} ч")
        _time.sleep(sleep_sec)

        data = load_data()
        tip = random.choice(DAILY_TIPS)
        for uid, user_data in data.items():
            if not user_data.get("reminders", True):
                continue
            text = (
                f"🌅 *Доброе утро! Время учить иврит!*\n\n"
                f"{tip}\n\n"
                f"Ваша серия: 🔥 {user_data.get('streak', 0)} дней\n"
                f"Уровень: {user_data.get('level', 1)}\n\n"
                f"Выберите урок и выучите 3-5 новых фраз! 💪"
            )
            payload = json.dumps({
                "chat_id": int(uid),
                "text": text,
                "parse_mode": "Markdown",
            }).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                logger.warning(f"Напоминание для {uid} не отправлено: {e}")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("progress", progress))

    app.add_handler(CallbackQueryHandler(next_quiz_callback, pattern=r"^(next_quiz_|show_score)"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(toggle_reminders|reset_progress)$"))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    t = threading.Thread(target=_reminder_loop, args=(TELEGRAM_TOKEN,), daemon=True)
    t.start()

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
