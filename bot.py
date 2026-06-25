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

# ─── .env loader (только для локальной разработки) ────────────────AAAAAAAAAAAA
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
ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"  # ИСПРАВЛЕНО: Указано корректное имя модели

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
    # ИСПРАВЛЕНО: Добавлен вывод подробной ошибки от Anthropic в логи
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        logger.error(f"Anthropic API Error: {e.code} - {error_body}")
        raise e

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
            {"he":
