[20:40, 20.06.2026] Sergei: #!/usr/bin/env python3
"""
Hebrew Learning Telegram Bot for Russian Speakers
Бот для изучения иврита русскоговорящими

Переменные окружения:
  TELEGRAM_TOKEN      — токен бота
  ANTHROPIC_API_KEY   — ключ API Anthropic
"""

import asyncio
import json
import logging
import os
import random
import urllib.request
import urllib.error
import urllib.parse
import io
import threading
from datetime import datetime
from pathlib import Path

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
logger = logging.getLogger(_name_)

# ─── .env loader ─────────────────────────────────────────────────────────────
env_file = Path(file_).parent / ".env"
if _env_file.exists():
    logger.info(".env файл найден, загружаем...")
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# ─── Config ───────────────────────────────────────────────────────────────────
DATA_FILE = Path("user_data.json")

TELEGRAM_TOKEN = (
    os.getenv("BOT_TOKEN") or
    os.getenv("TELEGRAM_BOT_TOKEN") or
    os.getenv("TELEGRAM_TOKEN") or
    os.getenv("TOKEN") or
    os.getenv("BOT_API_TOKEN") or
    ""
).strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("Токен бота не найден!")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY не найден!")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ─── Anthropic API ────────────────────────────────────────────────────────────
def call_claude(system: str, user_text: str, max_tokens: int = 600) -> str:
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

# ─── TTS Произношение (Google Translate) ─────────────────────────────────────
def get_hebrew_tts(text: str) -> bytes:
    """Генерирует голосовое сообщение с произношением иврита"""
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

# ─── Lessons Database ─────────────────────────────────────────────────────────
LESSONS = { ... }  # (оставил без изменений, чтобы не делать сообщение слишком длинным)

# Остальная часть LESSONS, DAILY_TIPS и функций load_data/save_data/get_user и т.д. 
# полностью такая же, как в твоём оригинальном файле.

# (Я оставил их как есть, чтобы не плодить огромный текст. Если нужно — скажи, пришлю полностью)

# ─── Keyboards (без изменений) ────────────────────────────────────────────────
# ... (main_menu_keyboard, lessons_keyboard, phrase_keyboard)

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (без изменений)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query
[20:43, 20.06.2026] Sergei: #!/usr/bin/env python3
"""
Hebrew Learning Telegram Bot for Russian Speakers
Бот для изучения иврита русскоговорящими

Переменные окружения:
  TELEGRAM_TOKEN      — токен бота
  ANTHROPIC_API_KEY   — ключ API Anthropic
"""

import asyncio
import json
import logging
import os
import random
import urllib.request
import urllib.error
import urllib.parse
import io
import threading
from datetime import datetime
from pathlib import Path

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
logger = logging.getLogger(_name_)

# ─── .env loader ─────────────────────────────────────────────────────────────
env_file = Path(file_).parent / ".env"
if _env_file.exists():
    logger.info(".env файл найден, загружаем...")
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# ─── Config ───────────────────────────────────────────────────────────────────
DATA_FILE = Path("user_data.json")

TELEGRAM_TOKEN = (
    os.getenv("BOT_TOKEN") or
    os.getenv("TELEGRAM_BOT_TOKEN") or
    os.getenv("TELEGRAM_TOKEN") or
    os.getenv("TOKEN") or
    os.getenv("BOT_API_TOKEN") or
    ""
).strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("Токен бота не найден!")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY не найден!")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ─── Anthropic API ────────────────────────────────────────────────────────────
def call_claude(system: str, user_text: str, max_tokens: int = 600) -> str:
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

# ─── TTS Произношение (Google Translate) ─────────────────────────────────────
def get_hebrew_tts(text: str) -> bytes:
    """Генерирует голосовое сообщение с произношением иврита"""
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

# ─── Lessons Database ─────────────────────────────────────────────────────────
LESSONS = { ... }  # (оставил без изменений, чтобы не делать сообщение слишком длинным)

# Остальная часть LESSONS, DAILY_TIPS и функций load_data/save_data/get_user и т.д. 
# полностью такая же, как в твоём оригинальном файле.

# (Я оставил их как есть, чтобы не плодить огромный текст. Если нужно — скажи, пришлю полностью)

# ─── Keyboards (без изменений) ────────────────────────────────────────────────
# ... (main_menu_keyboard, lessons_keyboard, phrase_keyboard)

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (без изменений)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query
