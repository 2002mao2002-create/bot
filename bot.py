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

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

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

# ─── TTS для произношения (Google Translate) ─────────────────────────────────
def get_hebrew_tts(text: str) -> bytes:
    """Генерирует аудио (ogg) через Google Translate TTS"""
    encoded_text = urllib.parse.quote(text)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=he&client=tw-ob&q={encoded_text}"
    
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()

# ─── Groq Whisper STT для проверки произношения (бесплатно) ──────────────────
def transcribe_audio_whisper(audio_bytes: bytes) -> str:
    """Транскрибирует аудио через Groq SDK"""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY не задан")

    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)

    transcription = client.audio.transcriptions.create(
        file=("voice.ogg", audio_bytes, "audio/ogg"),
        model="whisper-large-v3-turbo",
        language="he",
        response_format="json",
    )
    return (transcription.text or "").strip()


def evaluate_pronunciation(transcribed: str, correct_he: str, correct_translit: str, correct_ru: str) -> str:
    """Оценивает произношение через Claude"""
    system = """Ты — преподаватель иврита. Твоя задача — оценить произношение студента.
Тебе дадут: что студент произнёс (транскрипция Whisper на иврите), и правильное слово/фразу.
Оцени точность произношения по шкале от 1 до 5 звёзд.
Дай краткий, добрый и конструктивный фидбек на русском языке.
Ответь строго в формате:
ОЦЕНКА: ⭐⭐⭐ (от 1 до 5 звёзд)
ФИДБЕК: (1-2 предложения)
СОВЕТ: (короткий совет по улучшению, если нужно)"""

    user_text = (
        f"Студент произнёс (распознано Whisper): «{transcribed}»\n"
        f"Правильное слово на иврите: {correct_he}\n"
        f"Правильная транслитерация: {correct_translit}\n"
        f"Перевод: {correct_ru}"
    )
    return call_claude(system, user_text, max_tokens=300)

LESSONS = {
    "greetings": {
        "title": "👋 Приветствия",
        "phrases": [
            {"he": "שָׁלוֹם", "translit": "Шалом", "ru": "Привет / Мир"},
            {"he": "בֹּקֶר טוֹב", "translit": "Бокер тов", "ru": "Доброе утро"},
            {"he": "עֶרֶב טוֹב", "translit": "Эрев тов", "ru": "Добрый вечер"},
            {"he": "לַיְלָה טוֹב", "translit": "Лайла тов", "ru": "Спокойной ночи"},
            {"he": "מַה שְׁלוֹמְךָ?", "translit": "Ма шломха? (м) / Ма шломех? (ж)", "ru": "Как дела?"},
            {"he": "בְּסֵדֶר", "translit": "Бесэдэр", "ru": "Хорошо / Нормально"},
            {"he": "תּוֹדָה", "translit": "Тода", "ru": "Спасибо"},
            {"he": "בְּבַקָּשָׁה", "translit": "Бевакаша", "ru": "Пожалуйста"},
            {"he": "סְלִיחָה", "translit": "Слиха", "ru": "Извините / Простите"},
            {"he": "לְהִתְרָאוֹת", "translit": "Лехитраот", "ru": "До свидания"},
        ]
    },
    "numbers": {
        "title": "🔢 Числа",
        "phrases": [
            {"he": "אֶחָד", "translit": "Эхад", "ru": "Один (1)"},
            {"he": "שְׁתַּיִם", "translit": "Штаим", "ru": "Два (2)"},
            {"he": "שָׁלוֹשׁ", "translit": "Шалош", "ru": "Три (3)"},
            {"he": "אַרְבַּע", "translit": "Арба", "ru": "Четыре (4)"},
            {"he": "חָמֵשׁ", "translit": "Хамеш", "ru": "Пять (5)"},
            {"he": "שֵׁשׁ", "translit": "Шеш", "ru": "Шесть (6)"},
            {"he": "שֶׁבַע", "translit": "Шева", "ru": "Семь (7)"},
            {"he": "שְׁמוֹנֶה", "translit": "Шмоне", "ru": "Восемь (8)"},
            {"he": "תֵּשַׁע", "translit": "Теша", "ru": "Девять (9)"},
            {"he": "עֶשֶׂר", "translit": "Эсэр", "ru": "Десять (10)"},
        ]
    },
    "food": {
        "title": "🔥 10 главных глаголов",
        "phrases": [
            {"he": "לִהְיוֹת", "translit": "Лихйот", "ru": "Быть / являться"},
            {"he": "לַעֲשׂוֹת", "translit": "Лаасот", "ru": "Делать"},
            {"he": "לוֹמַר", "translit": "Ломар", "ru": "Говорить / сказать"},
            {"he": "לָלֶכֶת", "translit": "Лалехет", "ru": "Идти / ходить"},
            {"he": "לָדַעַת", "translit": "Лада'ат", "ru": "Знать"},
            {"he": "לִרְאוֹת", "translit": "Лиръот", "ru": "Видеть"},
            {"he": "לָבוֹא", "translit": "Лаво", "ru": "Приходить / прийти"},
            {"he": "לָתֵת", "translit": "Латет", "ru": "Давать / дать"},
            {"he": "לְדַבֵּר", "translit": "Ледабер", "ru": "Разговаривать"},
            {"he": "לִרְצוֹת", "translit": "Лирцот", "ru": "Хотеть"},
        ]
    },
    "phrases": {
        "title": "📖 100 главных слов",
        "phrases": [
            {"he": "אֲנִי", "translit": "Ани", "ru": "Я"},
            {"he": "אַתָּה / אַתְּ", "translit": "Ата / Ат", "ru": "Ты (м/ж)"},
            {"he": "הוּא", "translit": "Ху", "ru": "Он"},
            {"he": "הִיא", "translit": "Хи", "ru": "Она"},
            {"he": "אֲנַחְנוּ", "translit": "Анахну", "ru": "Мы"},
            {"he": "אַתֶּם / אַתֶּן", "translit": "Атем / Атен", "ru": "Вы (м/ж)"},
            {"he": "הֵם / הֵן", "translit": "Хем / Хен", "ru": "Они (м/ж)"},
            {"he": "כֵּן", "translit": "Кен", "ru": "Да"},
            {"he": "לֹא", "translit": "Ло", "ru": "Нет"},
            {"he": "מָה", "translit": "Ма", "ru": "Что"},
            {"he": "מִי", "translit": "Ми", "ru": "Кто"},
            {"he": "אֵיפֹה", "translit": "Эйфо", "ru": "Где"},
            {"he": "מָתַי", "translit": "Матай", "ru": "Когда"},
            {"he": "לָמָּה", "translit": "Лама", "ru": "Почему"},
            {"he": "אֵיךְ", "translit": "Эйх", "ru": "Как"},
            {"he": "כַּמָּה", "translit": "Кама", "ru": "Сколько"},
            {"he": "זֶה / זֹאת", "translit": "Зе / Зот", "ru": "Это (м/ж)"},
            {"he": "כָּל", "translit": "Коль", "ru": "Всё / каждый"},
            {"he": "עִם", "translit": "Им", "ru": "С (предлог)"},
            {"he": "בְּ", "translit": "Бе", "ru": "В / на (предлог)"},
            {"he": "לְ", "translit": "Ле", "ru": "К / для (предлог)"},
            {"he": "מִן / מִ", "translit": "Мин / Ми", "ru": "Из / от (предлог)"},
            {"he": "עַל", "translit": "Аль", "ru": "На / о (предлог)"},
            {"he": "שֶׁל", "translit": "Шель", "ru": "Из / принадлежащий"},
            {"he": "אֶת", "translit": "Эт", "ru": "Знак прямого дополнения"},
            {"he": "גַּם", "translit": "Гам", "ru": "Тоже / также"},
            {"he": "רַק", "translit": "Рак", "ru": "Только / лишь"},
            {"he": "כְּבָר", "translit": "Квар", "ru": "Уже"},
            {"he": "עֲדַיִן", "translit": "Адаин", "ru": "Ещё / пока что"},
            {"he": "אוּלַי", "translit": "Улай", "ru": "Может быть"},
            {"he": "אַף פַּעַם", "translit": "Аф паам", "ru": "Никогда"},
            {"he": "תָּמִיד", "translit": "Тамид", "ru": "Всегда"},
            {"he": "עַכְשָׁו", "translit": "Ахшав", "ru": "Сейчас"},
            {"he": "אַחַר כָּךְ", "translit": "Ахар ках", "ru": "Потом / после"},
            {"he": "לִפְנֵי", "translit": "Лифней", "ru": "Перед / до"},
            {"he": "טוֹב", "translit": "Тов", "ru": "Хорошо / хороший"},
            {"he": "רַע", "translit": "Ра", "ru": "Плохо / плохой"},
            {"he": "גָּדוֹל", "translit": "Гадоль", "ru": "Большой"},
            {"he": "קָטָן", "translit": "Катан", "ru": "Маленький"},
            {"he": "חָדָשׁ", "translit": "Хадаш", "ru": "Новый"},
            {"he": "יָשָׁן", "translit": "Яшан", "ru": "Старый"},
            {"he": "יָפֶה", "translit": "Яфе", "ru": "Красивый"},
            {"he": "מָהִיר", "translit": "Махир", "ru": "Быстрый"},
            {"he": "אִטִּי", "translit": "Ити", "ru": "Медленный"},
            {"he": "קַר", "translit": "Кар", "ru": "Холодный"},
            {"he": "חַם", "translit": "Хам", "ru": "Горячий"},
            {"he": "יוֹם", "translit": "Йом", "ru": "День"},
            {"he": "לַיְלָה", "translit": "Лайла", "ru": "Ночь"},
            {"he": "שָׁעָה", "translit": "Шаа", "ru": "Час"},
            {"he": "שָׁבוּעַ", "translit": "Шавуа", "ru": "Неделя"},
            {"he": "חֹדֶשׁ", "translit": "Ходеш", "ru": "Месяц"},
            {"he": "שָׁנָה", "translit": "Шана", "ru": "Год"},
            {"he": "בַּיִת", "translit": "Байт", "ru": "Дом"},
            {"he": "עִיר", "translit": "Ир", "ru": "Город"},
            {"he": "רְחוֹב", "translit": "Рехов", "ru": "Улица"},
            {"he": "מְדִינָה", "translit": "Медина", "ru": "Страна / государство"},
            {"he": "אֶרֶץ", "translit": "Эрец", "ru": "Земля / страна"},
            {"he": "אֲוִיר", "translit": "Авир", "ru": "Воздух"},
            {"he": "מַיִם", "translit": "Маим", "ru": "Вода"},
            {"he": "אֵשׁ", "translit": "Эш", "ru": "Огонь"},
            {"he": "אָדָם", "translit": "Адам", "ru": "Человек / люди"},
            {"he": "אִישׁ", "translit": "Иш", "ru": "Мужчина / муж"},
            {"he": "אִשָּׁה", "translit": "Иша", "ru": "Женщина / жена"},
            {"he": "יֶלֶד", "translit": "Йелед", "ru": "Ребёнок / мальчик"},
            {"he": "יַלְדָּה", "translit": "Ялда", "ru": "Девочка"},
            {"he": "חָבֵר", "translit": "Хавер", "ru": "Друг"},
            {"he": "עֲבוֹדָה", "translit": "Авода", "ru": "Работа"},
            {"he": "כֶּסֶף", "translit": "Кесеф", "ru": "Деньги / серебро"},
            {"he": "זְמַן", "translit": "Зман", "ru": "Время"},
            {"he": "מָקוֹם", "translit": "Маком", "ru": "Место"},
            {"he": "דֶּרֶךְ", "translit": "Дерех", "ru": "Путь / дорога"},
            {"he": "חַיִּים", "translit": "Хаим", "ru": "Жизнь"},
            {"he": "שֵׁם", "translit": "Шем", "ru": "Имя"},
            {"he": "יָד", "translit": "Яд", "ru": "Рука"},
            {"he": "עַיִן", "translit": "Аин", "ru": "Глаз"},
            {"he": "לֵב", "translit": "Лев", "ru": "Сердце"},
            {"he": "רֹאשׁ", "translit": "Рош", "ru": "Голова"},
            {"he": "פֶּה", "translit": "Пе", "ru": "Рот"},
            {"he": "אֹכֶל", "translit": "Охель", "ru": "Еда"},
            {"he": "לֶחֶם", "translit": "Лехем", "ru": "Хлеб"},
            {"he": "מִלָּה", "translit": "Мила", "ru": "Слово"},
            {"he": "שָׂפָה", "translit": "Сафа", "ru": "Язык / губа"},
            {"he": "שְׁאֵלָה", "translit": "Шеэла", "ru": "Вопрос"},
            {"he": "תְּשׁוּבָה", "translit": "Тшува", "ru": "Ответ"},
            {"he": "בְּעָיָה", "translit": "Беая", "ru": "Проблема"},
            {"he": "רַעְיוֹן", "translit": "Раайон", "ru": "Идея"},
            {"he": "סֵפֶר", "translit": "Сефер", "ru": "Книга"},
            {"he": "מִכְתָּב", "translit": "Михтав", "ru": "Письмо"},
            {"he": "אִי-מֵיְל", "translit": "И-мейл", "ru": "Электронная почта"},
            {"he": "טֶלֶפוֹן", "translit": "Телефон", "ru": "Телефон"},
            {"he": "מְכוֹנִית", "translit": "Мехонит", "ru": "Машина / автомобиль"},
            {"he": "אוֹטוֹבּוּס", "translit": "Отобус", "ru": "Автобус"},
            {"he": "בֵּית סֵפֶר", "translit": "Бейт сефер", "ru": "Школа"},
            {"he": "בֵּית חוֹלִים", "translit": "Бейт холим", "ru": "Больница"},
            {"he": "חָדָר", "translit": "Хадар", "ru": "Комната"},
            {"he": "דֶּלֶת", "translit": "Делет", "ru": "Дверь"},
            {"he": "חַלּוֹן", "translit": "Халон", "ru": "Окно"},
            {"he": "שֻׁלְחָן", "translit": "Шульхан", "ru": "Стол"},
            {"he": "כִּסֵּא", "translit": "Кисэ", "ru": "Стул"},
            {"he": "בֶּגֶד", "translit": "Бегед", "ru": "Одежда"},
        ]
    },
}

# ─── Уроки версии 2: глаголы в трёх временах с примерами ─────────────────────────────────
LESSONS_V2_VERBS = {
    "title": "🔥 Глаголы: 3 времени",
    "phrases": [
        {"he": "הָיִיתִי / אֶהְיֶה", "translit": "Хайити / Эхье", "ru": "Быть — я: был(а) / буду", "example": "אני הייתי מורה — Я был учителем"},
        {"he": "הוּא הָיָה / יִהְיֶה", "translit": "Ху hая / Йихье", "ru": "Быть — он: был / будет", "example": "הוא היה תלמיד — Он был учеником"},
        {"he": "עָשִׂיתִי / אֶעֱשֶׂה", "translit": "Асити / Ээсэ", "ru": "Делать — я: сделал(а) / сделаю", "example": "עשיתי את העבודה — Я сделал работу"},
        {"he": "הוּא עָשָׂה / יַעֲשֶׂה", "translit": "Ху аса / Яасэ", "ru": "Делать — он: сделал / сделает", "example": "הוא עשה את זה — Он сделал это"},
        {"he": "אָמַרְתִּי / אֹמַר", "translit": "Амарти / Омар", "ru": "Говорить — я: сказал(а) / скажу", "example": "אמרתי לו שלום — Я сказал ему привет"},
        {"he": "הוּא אָמַר / יֹאמַר", "translit": "Ху амар / Йомар", "ru": "Говорить — он: сказал / скажет", "example": "הוא אמר את האמת — Он сказал правду"},
        {"he": "הָלַכְתִּי / אֵלֵךְ", "translit": "hалахти / Элех", "ru": "Идти — я: пошёл(ла) / пойду", "example": "הלכתי הביתה — Я пошёл домой"},
        {"he": "הוּא הָלַךְ / יֵלֵךְ", "translit": "Ху hалах / Йелех", "ru": "Идти — он: пошёл / пойдёт", "example": "הוא הלך לעבודה — Он пошёл на работу"},
        {"he": "יָדַעְתִּי / אֵדַע", "translit": "Ядати / Эда", "ru": "Знать — я: знал(а) / узнаю", "example": "ידעתי את התשובה — Я знал ответ"},
        {"he": "הוּא יָדַע / יֵדַע", "translit": "Ху яда / Йеда", "ru": "Знать — он: знал / узнает", "example": "הוא ידע את הדרך — Он знал дорогу"},
        {"he": "רָאִיתִי / אֶרְאֶה", "translit": "Раити / Эрэ", "ru": "Видеть — я: видел(а) / увижу", "example": "ראיתי סרט — Я посмотрел фильм"},
        {"he": "הוּא רָאָה / יִרְאֶה", "translit": "Ху раа / Йирэ", "ru": "Видеть — он: видел / увидит", "example": "הוא ראה את הים — Он увидел море"},
        {"he": "בָּאתִי / אָבוֹא", "translit": "Бати / Аво", "ru": "Прийти — я: пришёл(ла) / приду", "example": "באתי מוקדם — Я пришёл рано"},
        {"he": "הוּא בָּא / יָבוֹא", "translit": "Ху ба / Яво", "ru": "Прийти — он: пришёл / придёт", "example": "הוא בא לבית — Он пришёл домой"},
        {"he": "נָתַתִּי / אֶתֵּן", "translit": "Натати / Этен", "ru": "Давать — я: дал(а) / дам", "example": "נתתי לו מתנה — Я дал ему подарок"},
        {"he": "הוּא נָתַן / יִתֵּן", "translit": "Ху натан / Йитен", "ru": "Давать — он: дал / даст", "example": "הוא נתן עצה — Он дал совет"},
        {"he": "דִּבַּרְתִּי / אֲדַבֵּר", "translit": "Дибарти / Адабер", "ru": "Разговаривать — я: говорил(а) / буду говорить", "example": "דיברתי עם חבר — Я поговорил с другом"},
        {"he": "הוּא דִּיבֵּר / יְדַבֵּר", "translit": "Ху дибер / Йедабер", "ru": "Разговаривать — он: говорил / будет говорить", "example": "הוא דיבר אתמול — Он говорил вчера"},
        {"he": "רָצִיתִי / אֶרְצֶה", "translit": "Рацити / Эрцэ", "ru": "Хотеть — я: хотел(а) / захочу", "example": "רציתי לאכול — Я хотел есть"},
        {"he": "הוּא רָצָה / יִרְצֶה", "translit": "Ху раца / Йирцэ", "ru": "Хотеть — он: хотел / захочет", "example": "הוא רצה לשתות — Он хотел пить"},
    ]
}

# ─── Числа до 1000 для версии 2 ───────────────────────────────────────────────
NUMBERS_V2 = [
    {"he": "אֶחָד", "translit": "Эхад", "ru": "1 — Один"},
    {"he": "שְׁתַּיִם", "translit": "Штаим", "ru": "2 — Два"},
    {"he": "שָׁלוֹשׁ", "translit": "Шалош", "ru": "3 — Три"},
    {"he": "אַרְבַּע", "translit": "Арба", "ru": "4 — Четыре"},
    {"he": "חָמֵשׁ", "translit": "Хамеш", "ru": "5 — Пять"},
    {"he": "שֵׁשׁ", "translit": "Шеш", "ru": "6 — Шесть"},
    {"he": "שֶׁבַע", "translit": "Шева", "ru": "7 — Семь"},
    {"he": "שְׁמוֹנֶה", "translit": "Шмоне", "ru": "8 — Восемь"},
    {"he": "תֵּשַׁע", "translit": "Теша", "ru": "9 — Девять"},
    {"he": "עֶשֶׂר", "translit": "Эсэр", "ru": "10 — Десять"},
    {"he": "אַחַד עָשָׂר", "translit": "Ахад асар", "ru": "11 — Одиннадцать"},
    {"he": "שְׁנֵים עָשָׂר", "translit": "Шнейм асар", "ru": "12 — Двенадцать"},
    {"he": "שְׁלוֹשָׁה עָשָׂר", "translit": "Шлоша асар", "ru": "13 — Тринадцать"},
    {"he": "אַרְבָּעָה עָשָׂר", "translit": "Арбаа асар", "ru": "14 — Четырнадцать"},
    {"he": "חֲמִשָּׁה עָשָׂר", "translit": "Хамиша асар", "ru": "15 — Пятнадцать"},
    {"he": "שִׁשָּׁה עָשָׂר", "translit": "Шиша асар", "ru": "16 — Шестнадцать"},
    {"he": "שִׁבְעָה עָשָׂר", "translit": "Шивъа асар", "ru": "17 — Семнадцать"},
    {"he": "שְׁמוֹנָה עָשָׂר", "translit": "Шмона асар", "ru": "18 — Восемнадцать"},
    {"he": "תִּשְׁעָה עָשָׂר", "translit": "Тишъа асар", "ru": "19 — Девятнадцать"},
    {"he": "עֶשְׂרִים", "translit": "Эсрим", "ru": "20 — Двадцать"},
    {"he": "שְׁלוֹשִׁים", "translit": "Шлошим", "ru": "30 — Тридцать"},
    {"he": "אַרְבָּעִים", "translit": "Арбаим", "ru": "40 — Сорок"},
    {"he": "חֲמִשִּׁים", "translit": "Хамишим", "ru": "50 — Пятьдесят"},
    {"he": "שִׁשִּׁים", "translit": "Шишим", "ru": "60 — Шестьдесят"},
    {"he": "שִׁבְעִים", "translit": "Шивъим", "ru": "70 — Семьдесят"},
    {"he": "שְׁמוֹנִים", "translit": "Шмоним", "ru": "80 — Восемьдесят"},
    {"he": "תִּשְׁעִים", "translit": "Тишъим", "ru": "90 — Девяносто"},
    {"he": "מֵאָה", "translit": "Меа", "ru": "100 — Сто"},
    {"he": "מָאתַיִם", "translit": "Матаим", "ru": "200 — Двести"},
    {"he": "שְׁלוֹשׁ מֵאוֹת", "translit": "Шлош меот", "ru": "300 — Триста"},
    {"he": "אַרְבַּע מֵאוֹת", "translit": "Арба меот", "ru": "400 — Четыреста"},
    {"he": "חֲמֵשׁ מֵאוֹת", "translit": "Хамеш меот", "ru": "500 — Пятьсот"},
    {"he": "שֵׁשׁ מֵאוֹת", "translit": "Шеш меот", "ru": "600 — Шестьсот"},
    {"he": "שְׁבַע מֵאוֹת", "translit": "Шва меот", "ru": "700 — Семьсот"},
    {"he": "שְׁמוֹנֶה מֵאוֹת", "translit": "Шмоне меот", "ru": "800 — Восемьсот"},
    {"he": "תְּשַׁע מֵאוֹת", "translit": "Тша меот", "ru": "900 — Девятьсот"},
    {"he": "אֶלֶף", "translit": "Элеф", "ru": "1000 — Тысяча"},
]

# ─── Пулы для тестов ─────────────────────────────────────────────────────────
def get_quiz_pool_verbs(version: int):
    """Пул для теста глаголов"""
    src = LESSONS_V2_VERBS["phrases"] if version == 2 else LESSONS["food"]["phrases"]
    return src

def get_quiz_pool_words(version: int):
    """Пул для теста слов (приветствия + 100 слов)"""
    pool = []
    for key in ("greetings", "phrases"):
        pool.extend(LESSONS[key]["phrases"])
    return pool

DAILY_TIPS = [
    "💡 Иврит читается справа налево! Это одна из первых вещей, которую нужно запомнить.",
    "💡 В иврите нет заглавных букв — все буквы одного размера.",
    "💡 Слово «шалом» (שָׁלוֹם) означает одновременно «привет», «пока» и «мир».",
    "💡 В иврите глаголы меняются в зависимости от пола говорящего (мужской/женский).",
    "💡 Буква «алеф» (א) — первая в ивритском алфавите, как «А» в русском.",
    "💡 «Тода раба» (תּוֹדָה רַבָּה) означает «большое спасибо».",
    "💡 Число 7 считается счастливым в иудейской традиции — на иврите שֶׁבַע (шева).",
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
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📚 Уроки", "📊 Мой прогресс"],
        ["🎯 Тест глаголы", "🎯 Тест слова"],
        ["💡 Совет дня", "⚙️ Настройки"],
    ], resize_keyboard=True)

def main_menu_keyboard_v2():
    return ReplyKeyboardMarkup([
        ["📚 Уроки", "📊 Мой прогресс"],
        ["🎯 Тест глаголы", "🎯 Тест слова"],
        ["🔢 Тест числа", "💡 Совет дня"],
        ["⚙️ Настройки"],
    ], resize_keyboard=True)

def get_menu_keyboard(user_id: int):
    version = get_user(user_id).get("version", 1)
    return main_menu_keyboard_v2() if version == 2 else main_menu_keyboard()

def lessons_keyboard(version: int = 1):
    buttons = []
    for key, lesson in LESSONS.items():
        title = lesson["title"]
        # В версии 2 для глаголов показываем расширенный урок
        if version == 2 and key == "food":
            title = LESSONS_V2_VERBS["title"]
        buttons.append([InlineKeyboardButton(title, callback_data=f"lesson_{key}")])
    return InlineKeyboardMarkup(buttons)

def phrase_keyboard(lesson_key: str, phrase_idx: int, total: int):
    row1 = []
    if phrase_idx > 0:
        row1.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"phrase_{lesson_key}_{phrase_idx-1}"))
    if phrase_idx < total - 1:
        row1.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"phrase_{lesson_key}_{phrase_idx+1}"))

    row2 = [
        InlineKeyboardButton("🔊 Произношение", callback_data=f"audio_{lesson_key}_{phrase_idx}"),
        InlineKeyboardButton("✅ Выучил!", callback_data=f"learned_{lesson_key}_{phrase_idx}"),
    ]
    row3 = [
        InlineKeyboardButton("🎤 Проверить произношение", callback_data=f"checkpron_{lesson_key}_{phrase_idx}"),
    ]
    row4 = [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]

    rows = []
    if row1:
        rows.append(row1)
    rows.append(row2)
    rows.append(row3)
    rows.append(row4)
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
        f"שָׁלוֹם, {user.first_name}! 👋\n\n"
        "Добро пожаловать в бот для изучения иврита!\n\n"
        "Выберите версию:\n"
        "• *Версия 1* — транслитерация на русском\n"
        "• *Версия 2* — транслитерация на русском, глаголы в трёх временах с примерами употребления",
        parse_mode="Markdown",
        reply_markup=buttons
    )

async def lessons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    version = get_user(user_id).get("version", 1)
    await update.message.reply_text(
        "📚 *Выберите тему урока:*",
        parse_mode="Markdown",
        reply_markup=lessons_keyboard(version)
    )

async def show_phrase(query, lesson_key: str, phrase_idx: int, version: int = 1):
    # В версии 2 для глаголов используем расширенный урок
    if version == 2 and lesson_key == "food":
        lesson = LESSONS_V2_VERBS
    else:
        lesson = LESSONS[lesson_key]

    phrase = lesson["phrases"][phrase_idx]
    total = len(lesson["phrases"])

    # Базовая информация
    text = (
        f"{lesson['title']} — фраза {phrase_idx + 1}/{total}\n\n"
        f"🇮🇱 *Иврит:*\n"
        f"```\n{phrase['he']}\n```\n\n"
        f"🔤 *Транслитерация:*\n_{phrase['translit']}_\n\n"
        f"🇷🇺 *По-русски:*\n{phrase['ru']}"
    )

    # Добавляем пример в версии 2
    if version == 2 and "example" in phrase and phrase["example"]:
        text += f"\n\n📝 *Пример:*\n_{phrase['example']}_"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=phrase_keyboard(lesson_key, phrase_idx, total)
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    version = get_user(user_id).get("version", 1)

    if data.startswith("set_version_"):
        version = int(data.split("_")[-1])
        update_user(user_id, {"version": version})
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

    elif data.startswith("lesson_"):
        lesson_key = data[7:]
        context.user_data["lesson"] = lesson_key
        await show_phrase(query, lesson_key, 0, version)

    elif data.startswith("phrase_"):
        _, lesson_key, idx = data.split("_", 2)
        await show_phrase(query, lesson_key, int(idx), version)

    elif data.startswith("audio_"):
        _, lesson_key, idx = data.split("_", 2)
        if version == 2 and lesson_key == "food":
            phrase = LESSONS_V2_VERBS["phrases"][int(idx)]
        else:
            phrase = LESSONS[lesson_key]["phrases"][int(idx)]
        he_text = phrase["he"]
        
        await query.answer("🔊 Генерирую произношение...", show_alert=False)
        
        try:
            audio_bytes = get_hebrew_tts(he_text)
            caption = f"🇮🇱 {he_text}\n👄 {phrase['translit']}\n🇷🇺 {phrase['ru']}"
            if "example" in phrase and phrase["example"]:
                caption += f"\n📝 {phrase['example']}"
            await query.message.reply_voice(
                voice=io.BytesIO(audio_bytes),
                caption=caption,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"TTS error: {e}")
            await query.message.reply_text(
                "❌ Не удалось загрузить аудио.\n\n"
                f"👄 *Читается:* {phrase['translit']}\n"
                f"Повторите вслух несколько раз!",
                parse_mode="Markdown"
            )

    elif data.startswith("learned_"):
        _, lesson_key, idx = data.split("_", 2)
        key = f"{lesson_key}_{idx}"
        user = get_user(user_id)
        learned = user.get("learned", [])
        total_phrases = user.get("total_phrases", 0)
        
        # Получаем общее количество фраз в уроке
        if version == 2 and lesson_key == "food":
            lesson = LESSONS_V2_VERBS
        else:
            lesson = LESSONS[lesson_key]
        total = len(lesson["phrases"])
        next_idx = int(idx) + 1
        
        if key not in learned:
            learned.append(key)
            total_phrases += 1
            update_user(user_id, {"learned": learned, "total_phrases": total_phrases})
            await query.answer("✅ Отлично! Фраза добавлена в выученные!", show_alert=False)
            
            # Автоматически переходим к следующей фразе
            if next_idx < total:
                await show_phrase(query, lesson_key, next_idx, version)
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
            # Всё равно переходим к следующей
            if next_idx < total:
                await show_phrase(query, lesson_key, next_idx, version)

    elif data.startswith("checkpron_"):
        _, lesson_key, idx = data.split("_", 2)
        if version == 2 and lesson_key == "food":
            phrase = LESSONS_V2_VERBS["phrases"][int(idx)]
        else:
            phrase = LESSONS[lesson_key]["phrases"][int(idx)]
        context.user_data["pron_check"] = {
            "lesson_key": lesson_key,
            "phrase_idx": int(idx),
            "he": phrase["he"],
            "translit": phrase["translit"],
            "ru": phrase["ru"],
        }
        if not GROQ_API_KEY:
            await query.message.reply_text(
                "⚠️ Для проверки произношения нужен *GROQ\\_API\\_KEY*\\.\n"
                "Получите бесплатно на console\\.groq\\.com",
                parse_mode="MarkdownV2"
            )
            return
        await query.message.reply_text(
            f"🎤 *Проверка произношения*\n\n"
            f"Произнесите это слово/фразу голосовым сообщением:\n\n"
            f"🇮🇱 *{phrase['he']}*\n"
            f"🔤 _{phrase['translit']}_\n"
            f"🇷🇺 {phrase['ru']}\n\n"
            f"_Запишите голосовое сообщение и отправьте его сюда_ 👇",
            parse_mode="Markdown"
        )

    elif data == "main_menu":
        await query.edit_message_text(
            "Выберите раздел 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Уроки", callback_data="go_lessons")],
                [InlineKeyboardButton("🎯 Тест", callback_data="go_quiz")],
            ])
        )

    elif data == "go_lessons":
        await query.edit_message_text("📚 Выберите тему:", reply_markup=lessons_keyboard(version))

    elif data == "go_quiz":
        await query.edit_message_text(
            "🎯 *Выберите тест:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Тест глаголы", callback_data="quiz_type_verbs")],
                [InlineKeyboardButton("🎯 Тест слова", callback_data="quiz_type_words")],
            ] + ([ [InlineKeyboardButton("🔢 Тест числа", callback_data="quiz_type_numbers")] ] if version == 2 else []))
        )

    elif data.startswith("quiz_type_"):
        quiz_type = data[len("quiz_type_"):]
        await send_quiz_question(query, user_id, context, quiz_type)

    elif data.startswith("quiz_"):
        await handle_quiz_answer(query, user_id, data, context)

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_streak(user_id)
    user = get_user(user_id)

    total_available = sum(len(l["phrases"]) for l in LESSONS.values())
    learned = len(user.get("learned", []))
    streak = user.get("streak", 0)
    quiz_score = user.get("quiz_score", 0)
    quiz_total = user.get("quiz_total", 0)
    accuracy = round(quiz_score / quiz_total * 100) if quiz_total > 0 else 0

    bar_len = 10
    filled = round(learned / total_available * bar_len) if total_available > 0 else 0
    bar = "🟩" * filled + "⬜" * (bar_len - filled)

    text = (
        f"📊 *Ваш прогресс*\n\n"
        f"🔥 Серия дней: *{streak}* {'🏆' if streak >= 7 else ''}\n\n"
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
    version = get_user(user_id).get("version", 1)
    buttons = [
        [InlineKeyboardButton("🎯 Тест глаголы", callback_data="quiz_type_verbs")],
        [InlineKeyboardButton("🎯 Тест слова", callback_data="quiz_type_words")],
    ]
    if version == 2:
        buttons.append([InlineKeyboardButton("🔢 Тест числа", callback_data="quiz_type_numbers")])
    await update.message.reply_text(
        "🎯 *Выберите тест:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def send_quiz_question(query, user_id: int, context, quiz_type: str = "words"):
    version = get_user(user_id).get("version", 1)

    if quiz_type == "verbs":
        pool = get_quiz_pool_verbs(version)
        title = "🔥 Тест: глаголы"
    elif quiz_type == "numbers":
        pool = NUMBERS_V2
        title = "🔢 Тест: числа"
    else:
        pool = get_quiz_pool_words(version)
        title = "📖 Тест: слова"

    if len(pool) < 4:
        await query.edit_message_text("Недостаточно фраз для теста.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], 3)
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct
    context.user_data["quiz_type"] = quiz_type
    context.user_data["quiz_pool"] = pool  # Сохраняем пул для следующих вопросов

    text = f"{title}\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
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

    version = get_user(user_id).get("version", 1)
    user = get_user(user_id)
    quiz_total = user.get("quiz_total", 0) + 1
    quiz_score = user.get("quiz_score", 0)

    is_correct = (chosen == correct_pos)
    if is_correct:
        quiz_score += 1

    update_user(user_id, {"quiz_score": quiz_score, "quiz_total": quiz_total})

    # Показываем результат и сразу следующий вопрос
    result = "✅ *Правильно!*" if is_correct else "❌ *Неправильно.*"
    result += f"\n\n🇮🇱 {correct_phrase.get('he', '')}\n"
    result += f"🔤 {correct_phrase.get('translit', '')}\n"
    result += f"🇷🇺 {correct_phrase.get('ru', '')}"

    if version == 2 and "example" in correct_phrase and correct_phrase["example"]:
        result += f"\n\n📝 {correct_phrase['example']}"

    # Сохраняем результат для показа, затем задаём следующий вопрос
    context.user_data["last_result"] = result
    
    # Генерируем следующий вопрос
    if pool and len(pool) >= 4:
        # Удаляем использованную фразу из пула
        remaining = [p for p in pool if p["he"] != correct_phrase["he"]]
        if len(remaining) >= 4:
            context.user_data["quiz_pool"] = remaining
            # Показываем результат, затем новый вопрос
            await query.edit_message_text(result, parse_mode="Markdown")
            # Отправляем новый вопрос отдельным сообщением
            await send_next_quiz_question(query.message, user_id, context, quiz_type, remaining)
            return

    # Если фраз мало или это был последний вопрос
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
    """Отправляет следующий вопрос теста"""
    version = get_user(user_id).get("version", 1)

    if len(pool) < 4:
        await message.reply_text("✅ Тест завершён! Вы ответили на все вопросы.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], 3)
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

    text = f"{title}\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
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
        user = get_user(query.from_user.id)
        score = user.get("quiz_score", 0)
        total = user.get("quiz_total", 0)
        pct = round(score / total * 100) if total else 0
        await query.edit_message_text(
            f"📊 *Ваш счёт в тестах*\n\n"
            f"✅ Правильных: {score}/{total} ({pct}%)\n\n"
            f"{'🏆 Отличный результат!' if pct >= 80 else '💪 Продолжайте практиковаться!'}",
            parse_mode="Markdown"
        )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает голосовые сообщения для проверки произношения"""
    user_id = update.effective_user.id
    pron_data = context.user_data.get("pron_check")

    if not pron_data:
        await update.message.reply_text(
            "Чтобы проверить произношение, сначала выберите слово в уроке и нажмите *🎤 Проверить произношение*.",
            parse_mode="Markdown"
        )
        return

    await update.message.chat.send_action("typing")
    await update.message.reply_text("⏳ Анализирую произношение...")

    try:
        # Скачиваем голосовое сообщение
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()

        loop = asyncio.get_event_loop()

        # Транскрибируем через Whisper
        transcribed = await loop.run_in_executor(
            None, lambda: transcribe_audio_whisper(bytes(audio_bytes))
        )

        if not transcribed:
            await update.message.reply_text(
                "😕 Не удалось распознать речь. Попробуйте говорить чётче и ближе к микрофону."
            )
            return

        # Оцениваем через Claude
        feedback = await loop.run_in_executor(
            None, lambda: evaluate_pronunciation(
                transcribed,
                pron_data["he"],
                pron_data["translit"],
                pron_data["ru"],
            )
        )

        result_text = (
            f"🎤 *Результат проверки произношения*\n\n"
            f"🇮🇱 Слово: *{pron_data['he']}*\n"
            f"🔤 Правильно: _{pron_data['translit']}_\n"
            f"🗣 Распознано: _{transcribed}_\n\n"
            f"{feedback}"
        )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Попробовать ещё раз", callback_data=f"checkpron_{pron_data['lesson_key']}_{pron_data['phrase_idx']}")],
            [InlineKeyboardButton("▶️ Продолжить урок", callback_data=f"phrase_{pron_data['lesson_key']}_{pron_data['phrase_idx']}")],
        ])

        await update.message.reply_text(result_text, parse_mode="Markdown", reply_markup=buttons)
        context.user_data.pop("pron_check", None)

    except Exception as e:
        logger.error(f"Voice check error: {type(e).__name__}: {e}")
        user_id = update.effective_user.id
        await update.message.reply_text(
            f"❌ Ошибка: <code>{type(e).__name__}: {e}</code>",
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(user_id)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    update_streak(user_id)

    if text == "📚 Уроки":
        await lessons_menu(update, context)
    elif text == "🎯 Тест глаголы":
        # Создаём временное сообщение для теста
        await update.message.reply_text("🔄 Загрузка теста...")
        # Используем последнее сообщение для отправки вопроса
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
    """Отправляет вопрос теста в обычное сообщение (не callback)"""
    version = get_user(user_id).get("version", 1)

    if quiz_type == "verbs":
        pool = get_quiz_pool_verbs(version)
        title = "🔥 Тест: глаголы"
    elif quiz_type == "numbers":
        pool = NUMBERS_V2
        title = "🔢 Тест: числа"
    else:
        pool = get_quiz_pool_words(version)
        title = "📖 Тест: слова"

    if len(pool) < 4:
        await message.reply_text("Недостаточно фраз для теста.")
        return

    correct = random.choice(pool)
    wrong_options = random.sample([p for p in pool if p["he"] != correct["he"]], 3)
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct
    context.user_data["quiz_type"] = quiz_type
    context.user_data["quiz_pool"] = pool

    text = f"{title}\n\nКак переводится:\n\n🇮🇱 _{correct['he']}_\n🔤 ({correct['translit']})\n\n"
    
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
    user = get_user(update.effective_user.id)
    reminders = user.get("reminders", True)
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
        user = get_user(user_id)
        new_val = not user.get("reminders", True)
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
    """Фоновый поток: каждый день в 09:00 шлёт напоминания"""
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
        for uid, user in data.items():
            if not user.get("reminders", True):
                continue
            text = (
                f"🌅 *Доброе утро! Время учить иврит!*\n\n"
                f"{tip}\n\n"
                f"Ваша серия: 🔥 {user.get('streak', 0)} дней\n\n"
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
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Запускаем напоминания в фоновом потоке
    t = threading.Thread(target=_reminder_loop, args=(TELEGRAM_TOKEN,), daemon=True)
    t.start()

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
