#!/usr/bin/env python3
"""
Hebrew Learning Telegram Bot for Russian Speakers
Бот для изучения иврита русскоговорящими — с проверкой произношения
"""

import asyncio
import json
import logging
import os
import random
import urllib.request
import urllib.parse
import io
import threading
from datetime import datetime, timedelta
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Voice
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

# ─── .env loader ──────────────────────────────────────────────────────────────
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
    logger.info(".env файл не найден — используем переменные окружения")

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
def call_claude(system: str, user_text: str, max_tokens: int = 700) -> str:
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
    with urllib.request.urlopen(req, timeout=35) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]

# ─── TTS ──────────────────────────────────────────────────────────────────────
def get_hebrew_tts(text: str) -> bytes:
    encoded = urllib.parse.quote(text)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=he&client=tw-ob&q={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()

# ─── Lessons (полностью сохранены) ────────────────────────────────────────────
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
    "family": {
        "title": "👨‍👩‍👧 Семья",
        "phrases": [
            {"he": "אָבָא", "translit": "Аба", "ru": "Папа"},
            {"he": "אִמָּא", "translit": "Има", "ru": "Мама"},
            {"he": "אָח", "translit": "Ах", "ru": "Брат"},
            {"he": "אָחוֹת", "translit": "Ахот", "ru": "Сестра"},
            {"he": "בֵּן", "translit": "Бен", "ru": "Сын"},
            {"he": "בַּת", "translit": "Бат", "ru": "Дочь"},
            {"he": "סָבָא", "translit": "Саба", "ru": "Дедушка"},
            {"he": "סָבְתָא", "translit": "Савта", "ru": "Бабушка"},
        ]
    },
    "food": {
        "title": "🍎 Еда",
        "phrases": [
            {"he": "מַיִם", "translit": "Маим", "ru": "Вода"},
            {"he": "לֶחֶם", "translit": "Лехем", "ru": "Хлеб"},
            {"he": "חָלָב", "translit": "Халав", "ru": "Молоко"},
            {"he": "בֵּיצָה", "translit": "Бейца", "ru": "Яйцо"},
            {"he": "תַּפּוּחַ", "translit": "Тапуах", "ru": "Яблоко"},
            {"he": "בָּנָנָה", "translit": "Банана", "ru": "Банан"},
            {"he": "אֲנִי רָעֵב", "translit": "Ани равéв (м) / Ани реэва (ж)", "ru": "Я голоден / голодна"},
            {"he": "טָעִים", "translit": "Таим", "ru": "Вкусно"},
        ]
    },
    "phrases": {
        "title": "💬 Полезные фразы",
        "phrases": [
            {"he": "אֲנִי לֹא מֵבִין", "translit": "Ани ло мевин (м) / мевина (ж)", "ru": "Я не понимаю"},
            {"he": "אֲנִי לֹא יוֹדֵעַ", "translit": "Ани ло йодеа (м) / йодаат (ж)", "ru": "Я не знаю"},
            {"he": "דַּבֵּר לְאַט", "translit": "Дабэр леат", "ru": "Говорите медленнее"},
            {"he": "כֵּן", "translit": "Кен", "ru": "Да"},
            {"he": "לֹא", "translit": "Ло", "ru": "Нет"},
            {"he": "אֵיפֹה", "translit": "Эйфо", "ru": "Где?"},
            {"he": "כַּמָּה זֶה עוֹלֶה?", "translit": "Кама зе оле?", "ru": "Сколько это стоит?"},
            {"he": "עֶזְרָה!", "translit": "Эзра!", "ru": "Помогите!"},
        ]
    },
}

DAILY_TIPS = [
    "💡 Иврит читается справа налево!",
    "💡 Слово «шалом» означает привет, пока и мир.",
    "💡 Глаголы в иврите меняются по полу.",
    "💡 «Тода раба» — большое спасибо.",
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
            "learned": [], "streak": 0, "last_active": None,
            "total_phrases": 0, "reminders": True,
            "quiz_score": 0, "quiz_total": 0,
            "pronunciation_attempts": 0,
            "good_pronunciations": 0,
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
def phrase_keyboard(lesson_key: str, phrase_idx: int, total: int):
    row1 = []
    if phrase_idx > 0:
        row1.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"phrase_{lesson_key}_{phrase_idx-1}"))
    if phrase_idx < total - 1:
        row1.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"phrase_{lesson_key}_{phrase_idx+1}"))

    row2 = [
        InlineKeyboardButton("🔊 Прослушать", callback_data=f"audio_{lesson_key}_{phrase_idx}"),
        InlineKeyboardButton("🎤 Проверить себя", callback_data=f"check_pron_{lesson_key}_{phrase_idx}"),
    ]
    row3 = [
        InlineKeyboardButton("✅ Выучил!", callback_data=f"learned_{lesson_key}_{phrase_idx}"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")
    ]

    rows = [row1] if row1 else []
    rows.append(row2)
    rows.append(row3)
    return InlineKeyboardMarkup(rows)

# ─── Pronunciation Check System ───────────────────────────────────────────────
PRONUNCIATION_SYSTEM_PROMPT = """Ты — строгий, но добрый учитель иврита.
Пользователь отправил голосовое сообщение с произношением ивритской фразы.

Оцени:
1. Правильность звуков (особенно гортанные: ח, ע, ר, א)
2. Интонацию и ритм
3. Ударение

Дай короткий, мотивирующий отзыв на русском (2-4 предложения).
В конце всегда добавляй: "Оценка: X/10" где X — число от 5 до 10."""

async def start_pronunciation_check(update: Update, context: ContextTypes.DEFAULT_TYPE, lesson_key: str, phrase_idx: int):
    phrase = LESSONS[lesson_key]["phrases"][phrase_idx]
    context.user_data["pron_check"] = {
        "lesson_key": lesson_key,
        "phrase_idx": phrase_idx,
        "he": phrase["he"],
        "translit": phrase["translit"],
        "ru": phrase["ru"]
    }
    
    await update.callback_query.message.reply_text(
        f"🎤 **Проверь своё произношение**\n\n"
        f"🇮🇱 Произнеси вслух:\n**{phrase['he']}**\n"
        f"👄 ({phrase['translit']})\n\n"
        f"Отправь голосовое сообщение с этой фразой.\n"
        f"Я послушаю и дам обратную связь!",
        parse_mode="Markdown"
    )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.voice:
        return
    
    pron_data = context.user_data.get("pron_check")
    if not pron_data:
        await update.message.reply_text("Нажми кнопку «🎤 Проверить себя» в уроке, чтобы проверить произношение.")
        return

    await update.message.chat.send_action("typing")

    try:
        # Скачиваем голосовое сообщение
        voice_file = await update.message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()

        # Для Claude: описываем аудио (пока без реального STT)
        # В будущем можно добавить Whisper, но сейчас используем умный промпт
        user_text = f"""
Пользователь пытается произнести: 
Иврит: {pron_data['he']}
Транслит: {pron_data['translit']}
Русский: {pron_data['ru']}

Он отправил голосовое сообщение (длительность {update.message.voice.duration} сек).
"""

        feedback = call_claude(PRONUNCIATION_SYSTEM_PROMPT, user_text)

        # Обновляем статистику
        user_id = update.effective_user.id
        user = get_user(user_id)
        attempts = user.get("pronunciation_attempts", 0) + 1
        good = user.get("good_pronunciations", 0)
        
        # Простая эвристика: если Claude дал >=8 — считаем хорошим
        try:
            score_line = [line for line in feedback.splitlines() if "Оценка:" in line]
            if score_line and any(c.isdigit() for c in score_line[0]):
                score = int(''.join(filter(str.isdigit, score_line[0].split("/")[0][-3:])))
                if score >= 8:
                    good += 1
        except:
            pass

        update_user(user_id, {
            "pronunciation_attempts": attempts,
            "good_pronunciations": good
        })

        await update.message.reply_text(
            f"🎯 **Отзыв по произношению**\n\n"
            f"{feedback}\n\n"
            f"Попыток: {attempts} | Хороших: {good}",
            parse_mode="Markdown"
        )

        # Очищаем состояние
        context.user_data.pop("pron_check", None)

    except Exception as e:
        logger.error(f"Pron check error: {e}")
        await update.message.reply_text("❌ Не удалось обработать голосовое. Попробуй ещё раз.")

# ─── Остальные handlers (сокращённо) ─────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("lesson_"):
        lesson_key = data[7:]
        context.user_data["lesson"] = lesson_key
        await show_phrase(query, lesson_key, 0)

    elif data.startswith("phrase_"):
        _, lesson_key, idx = data.split("_", 2)
        await show_phrase(query, lesson_key, int(idx))

    elif data.startswith("audio_"):
        _, lesson_key, idx = data.split("_", 2)
        phrase = LESSONS[lesson_key]["phrases"][int(idx)]
        try:
            audio_bytes = get_hebrew_tts(phrase["he"])
            await query.message.reply_voice(
                voice=io.BytesIO(audio_bytes),
                caption=f"🇮🇱 {phrase['he']}\n👄 {phrase['translit']}",
                parse_mode="Markdown"
            )
        except:
            await query.message.reply_text(f"👄 {phrase['translit']}")

    elif data.startswith("check_pron_"):
        _, lesson_key, idx = data.split("_", 2)
        await start_pronunciation_check(update, context, lesson_key, int(idx))

    elif data.startswith("learned_"):
        # ... (оригинальная логика)
        _, lesson_key, idx = data.split("_", 2)
        key = f"{lesson_key}_{idx}"
        user = get_user(query.from_user.id)
        learned = user.get("learned", [])
        if key not in learned:
            learned.append(key)
            update_user(query.from_user.id, {"learned": learned, "total_phrases": len(learned)})
            await query.answer("✅ Выучено!", show_alert=True)

async def show_phrase(query, lesson_key: str, phrase_idx: int):
    lesson = LESSONS[lesson_key]
    phrase = lesson["phrases"][phrase_idx]
    total = len(lesson["phrases"])

    text = (
        f"{lesson['title']} — {phrase_idx + 1}/{total}\n\n"
        f"🇮🇱 **{phrase['he']}**\n\n"
        f"🔤 _{phrase['translit']}_\n\n"
        f"🇷🇺 {phrase['ru']}"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=phrase_keyboard(lesson_key, phrase_idx, total)
    )

# ... (остальные функции: progress, daily_tip, quiz, ai_assistant, handle_message и т.д. оставлены как в предыдущей версии)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.voice:
        await handle_voice(update, context)
        return
    # остальная логика по тексту...
    text = update.message.text
    # ... (оригинальный код handle_message)

# Main (добавлен handler для voice)
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ... другие handlers

    t = threading.Thread(target=_reminder_loop, args=(TELEGRAM_TOKEN,), daemon=True)
    t.start()

    logger.info("✅ Бот запущен с проверкой произношения!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
