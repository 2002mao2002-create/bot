#!/usr/bin/env python3
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

# ─── Config ───────────────────────────────────────────────────────────────────
DATA_FILE = Path("user_data.json")
WORDS_FILE = Path("words.json")

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

if not TELEGRAM_TOKEN:
    raise RuntimeError("Токен бота не найден. Задайте BOT_TOKEN.")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY не найден в переменных окружения.")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"

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
    encoded_text = urllib.parse.quote(text)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=he&client=tw-ob&q={encoded_text}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()

# ─── Groq Whisper STT ────────────────────────────────────────────────────────
def transcribe_audio_whisper(audio_bytes: bytes) -> str:
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
    system = """Ты — преподаватель иврита. Оцени произношение студента от 1 до 5 звёзд.
Дай краткий фидбек на русском. Формат ответа строго:
ОЦЕНКА: ⭐⭐⭐
ФИДБЕК: (1-2 предложения)"""
    user_text = (
        f"Студент произнёс: «{transcribed}»\n"
        f"Правильно: {correct_he} ({correct_translit}) — {correct_ru}"
    )
    return call_claude(system, user_text, max_tokens=300)

# ─── База уроков (Авто-генерация файла слов для надежности) ────────────────────
DEFAULT_LESSONS = {
    "greetings": {
        "title": "👋 Приветствия",
        "phrases": [
            {"he": "שָׁלוֹם", "translit": "Шалом", "ru": "Привет / Мир"},
            {"he": "בֹּקֶר טוֹב", "translit": "Бокер тов", "ru": "Доброе утро"},
            {"he": "עֶרֶב טוֹב", "translit": "Эрев тов", "ru": "Добрый вечер"},
            {"he": "לַיְלָה טוֹב", "translit": "Лайла тов", "ru": "Спокойной ночи"},
            {"he": "מַה שְׁלוֹมְךָ?", "translit": "Ма шломха? (м) / Ма шломех? (ж)", "ru": "Как дела?"},
            {"he": "בְּסֵדֶר", "translit": "Бесэдэр", "ru": "Хорошо / Нормально"},
            {"he": "תּוֹדָה", "translit": "Тода", "ru": "Спасибо"},
            {"he": "בְּבַקָּשָׁה", "translit": "Бевакаша", "ru": "Пожалуйста"},
            {"he": "סְלִיחָה", "translit": "Слиха", "ru": "Извините / Простите"},
            {"he": "לְהִתְרָאוֹת", "translit": "Лехитраот", "ru": "До свидания"}
        ]
    },
    "numbers": {
        "title": "🔢 Числа",
        "phrases": [
            {"he": "אֶחָד", "translit": "Эхад", "ru": "Один (1)"},
            {"he": "שְׁתัּיִם", "translit": "Шtaим", "ru": "Два (2)"},
            {"he": "שָׁלוֹשׁ", "translit": "Шалош", "ru": "Три (3)"},
            {"he": "אַרְבַּع", "translit": "Арба", "ru": "Четыре (4)"},
            {"he": "חָמֵשׁ", "translit": "Хамеш", "ru": "Пять (5)"},
            {"he": "שֵׁשׁ", "translit": "Шеш", "ru": "Шесть (6)"},
            {"he": "שֶׁבַע", "translit": "Шева", "ru": "Семь (7)"},
            {"he": "שְׁמוֹנֶה", "translit": "Шмоне", "ru": "Восемь (8)"},
            {"he": "תֵּשַׁע", "translit": "Теша", "ru": "Девять (9)"},
            {"he": "עֶשֶׂר", "translit": "Эсэр", "ru": "Десять (10)"}
        ]
    },
    "verbs": {
        "title": "🔥 10 главных глаголов",
        "phrases": [
            {"he": "לִהְיוֹת", "translit": "Лихйот", "ru": "Быть / являться"},
            {"he": "לַעֲשׂוֹת", "translit": "Лаасот", "ru": "Делать"},
            {"he": "לוֹմַר", "translit": "Ломар", "ru": "Говорить / сказать"},
            {"he": "לָלֶכֶת", "translit": "Лалехет", "ru": "Идти / ходить"},
            {"he": "לָדַעַת", "translit": "Лада'ат", "ru": "Знать"},
            {"he": "לִרְאוֹת", "translit": "Лиръот", "ru": "Видеть"},
            {"he": "לָבוֹא", "translit": "Лаво", "ru": "Приходить / прийти"},
            {"he": "לָתֵת", "translit": "Латет", "ru": "Давать / дать"},
            {"he": "לְדַבֵּר", "translit": "Ледабер", "ru": "Разговаривать"},
            {"he": "לִרְצוֹת", "translit": "Лирцот", "ru": "Хотеть"}
        ]
    },
    "phrases": {
        "title": "📖 Важные базовые слова",
        "phrases": [
            {"he": "אֲנִי", "translit": "Ани", "ru": "Я"},
            {"he": "אַתָּה / אַתְּ", "translit": "Ата / Ат", "ru": "Ты (м/ж)"},
            {"he": "הוּא", "translit": "Ху", "ru": "Он"},
            {"he": "הִיא", "translit": "Хи", "ru": "Она"},
            {"he": "אֲנַחְנוּ", "translit": "Анахну", "ru": "Мы"},
            {"he": "כֵּן", "translit": "Кен", "ru": "Да"},
            {"he": "לֹא", "translit": "Ло", "ru": "Нет"},
            {"he": "מָה", "translit": "Ма", "ru": "Что"},
            {"he": "מִי", "translit": "Ми", "ru": "Кто"},
            {"he": "אֵיפֹה", "translit": "Эйфо", "ru": "Где"},
            {"he": "בַּיִת", "translit": "Байт", "ru": "Дом"},
            {"he": "עִיר", "translit": "Ир", "ru": "Город"},
            {"he": "כֶּסֶף", "translit": "Кесеф", "ru": "Деньги"},
            {"he": "יָד", "translit": "Яд", "ru": "Рука"},
            {"he": "יוֹם", "translit": "Йом", "ru": "День"}
        ]
    }
}

def load_lessons() -> dict:
    if not WORDS_FILE.exists():
        with open(WORDS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_LESSONS, f, ensure_ascii=False, indent=2)
        return DEFAULT_LESSONS
    with open(WORDS_FILE, encoding="utf-8") as f:
        return json.load(f)

LESSONS = load_lessons()

DAILY_TIPS = [
    "💡 Иврит читается справа налево! Это одна из первых вещей, которую нужно запомнить.",
    "💡 В иврите нет заглавных букв — все буквы одного размера.",
    "💡 Слово «шалом» (שָׁלוֹם) означает одновременно «привет», «пока» и «мир».",
    "💡 В иврите глаголы меняются в зависимости от пола говорящего (мужской/женский).",
    "💡 Буква «алеф» (א) — первая в ивритском алфавите, как «А» в русском.",
    "💡 «Тода раба» (תּוֹדָה רַבָּה) означает «большое спасибо»."
]

# ─── User Data ────────────────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(user_id: int) -> dict:
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "learned": [], "streak": 0, "last_active": None,
            "total_phrases": 0, "reminders": True, "quiz_score": 0, "quiz_total": 0
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
        streak = streak + 1 if last == yesterday else 1
        update_user(user_id, {"streak": streak, "last_active": today})

# ─── Keyboards ────────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📚 Уроки", "🎯 Тест"],
        ["📊 Мой прогресс", "💡 Совет дня"],
        ["⚙️ Настройки"],
    ], resize_keyboard=True)

def lessons_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(l["title"], callback_data=f"lesson_{k}")] for k, l in LESSONS.items()])

def phrase_keyboard(lesson_key: str, phrase_idx: int, total: int):
    row1 = []
    if phrase_idx > 0:
        row1.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"phrase_{lesson_key}_{phrase_idx-1}"))
    if phrase_idx < total - 1:
        row1.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"phrase_{lesson_key}_{phrase_idx+1}"))
    return InlineKeyboardMarkup([
        row1,
        [InlineKeyboardButton("🔊 Произношение", callback_data=f"audio_{lesson_key}_{phrase_idx}"),
         InlineKeyboardButton("✅ Выучил!", callback_data=f"learned_{lesson_key}_{phrase_idx}")],
        [InlineKeyboardButton("🎤 Проверить произношение", callback_data=f"checkpron_{lesson_key}_{phrase_idx}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ])

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    update_streak(user.id)
    await update.message.reply_text(
        f"שָׁלוֹם, {user.first_name}! 👋\nДобро пожаловать в бот изучения иврита!\nВыбирайте раздел:",
        reply_markup=main_menu_keyboard()
    )

async def show_phrase(query, lesson_key: str, phrase_idx: int):
    lesson = LESSONS[lesson_key]
    phrase = lesson["phrases"][phrase_idx]
    total = len(lesson["phrases"])
    text = (
        f"<b>{lesson['title']}</b> — {phrase_idx + 1}/{total}\n\n"
        f"🇮🇱 <b>Иврит:</b>\n<code>{phrase['he']}</code>\n\n"
        f"🔤 <b>Транслитерация:</b>\n<i>{phrase['translit']}</i>\n\n"
        f"🇷🇺 <b>Перевод:</b>\n{phrase['ru']}"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=phrase_keyboard(lesson_key, phrase_idx, total))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("lesson_"):
        await show_phrase(query, data[7:], 0)
    elif data.startswith("phrase_"):
        _, l_key, idx = data.split("_", 2)
        await show_phrase(query, l_key, int(idx))
    elif data.startswith("audio_"):
        _, l_key, idx = data.split("_", 2)
        phrase = LESSONS[l_key]["phrases"][int(idx)]
        try:
            audio = get_hebrew_tts(phrase["he"])
            await query.message.reply_voice(voice=io.BytesIO(audio), caption=f"🗣 {phrase['translit']}")
        except Exception:
            await query.message.reply_text(f"❌ Ошибка аудио. Транслитерация: {phrase['translit']}")
    elif data.startswith("learned_"):
        _, l_key, idx = data.split("_", 2)
        key = f"{l_key}_{idx}"
        user = get_user(user_id)
        learned = user.get("learned", [])
        if key not in learned:
            learned.append(key)
            update_user(user_id, {"learned": learned, "total_phrases": len(learned)})
            await query.answer("✅ Добавлено в выученные!")
    elif data.startswith("checkpron_"):
        _, l_key, idx = data.split("_", 2)
        phrase = LESSONS[l_key]["phrases"][int(idx)]
        context.user_data["pron_check"] = {"lesson_key": l_key, "phrase_idx": int(idx), **phrase}
        await query.message.reply_text(f"🎤 Запишите и отправьте голосовое сообщение для слова:\n<b>{phrase['he']}</b>", parse_mode="HTML")
    elif data == "main_menu":
        await query.edit_message_text("Выберите тему уроков:", reply_markup=lessons_keyboard())
    elif data.startswith("quiz_"):
        await handle_quiz_answer(query, user_id, data, context)

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    total_avail = sum(len(l["phrases"]) for l in LESSONS.values())
    learned = len(user.get("learned", []))
    await update.message.reply_text(f"📊 *Прогресс*\n🔥 Серия: {user.get('streak')} дней\n📚 Выучено фраз: {learned}/{total_avail}", parse_mode="Markdown")

async def daily_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(random.choice(DAILY_TIPS))

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_phrases = [(k, i, p) for k, l in LESSONS.items() for i, p in enumerate(l["phrases"])]
    if len(all_phrases) < 4:
        await update.message.reply_text("Недостаточно слов для теста.")
        return
    correct = random.choice(all_phrases)
    wrong = random.sample([p for k, i, p in all_phrases if p["he"] != correct[2]["he"]], 3)
    opts = [correct[2]] + wrong
    random.shuffle(opts)
    context.user_data["quiz_correct"] = next(i for i, p in enumerate(opts) if p["he"] == correct[2]["he"])
    context.user_data["quiz_phrase"] = correct[2]
    buttons = [[InlineKeyboardButton(o["ru"], callback_data=f"quiz_{i}")] for i, o in enumerate(opts)]
    await update.message.reply_text(f"🎯 Как переводится:\n<b>{correct[2]['he']}</b>?", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_quiz_answer(query, user_id, data, context):
    chosen = int(data.split("_")[1])
    correct_pos = context.user_data.get("quiz_correct")
    phrase = context.user_data.get("quiz_phrase", {})
    user = get_user(user_id)
    score, total = user.get("quiz_score", 0), user.get("quiz_total", 0) + 1
    if chosen == correct_pos:
        score += 1
        res = "✅ <b>Правильно!</b>"
    else:
        res = "❌ <b>Неверно.</b>"
    res += f"\n\n🇮🇱 {phrase.get('he')} = {phrase.get('ru')}"
    update_user(user_id, {"quiz_score": score, "quiz_total": total})
    await query.edit_message_text(res, parse_mode="HTML")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pron_data = context.user_data.get("pron_check")
    if not pron_data:
        await update.message.reply_text("Сначала выберите фразу в уроках.")
        return
    await update.message.reply_text("⏳ Проверяю произношение...")
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        audio_bytes = await file.download_as_bytearray()
        loop = asyncio.get_event_loop()
        transcribed = await loop.run_in_executor(None, lambda: transcribe_audio_whisper(bytes(audio_bytes)))
        if not transcribed:
            await update.message.reply_text("❌ Речь не распознана.")
            return
        feedback = await loop.run_in_executor(None, lambda: evaluate_pronunciation(transcribed, pron_data["he"], pron_data["translit"], pron_data["ru"]))
        await update.message.reply_text(f"🗣 Распознано: {transcribed}\n\n{feedback}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📚 Уроки": await update.message.reply_text("Выберите тему:", reply_markup=lessons_keyboard())
    elif text == "🎯 Тест": await quiz(update, context)
    elif text == "📊 Мой прогресс": await progress(update, context)
    elif text == "💡 Совет дня": await daily_tip(update, context)
    else: await update.message.reply_text("Используйте меню ниже:", reply_markup=main_menu_keyboard())

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
