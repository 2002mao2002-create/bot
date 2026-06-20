#!/usr/bin/env python3
"""
Hebrew Learning Telegram Bot for Russian Speakers
Бот для изучения иврита русскоговорящими

Переменные окружения (задаются в панели BotHost или в файле .env локально):
  TELEGRAM_TOKEN      — токен бота от @BotFather
  ANTHROPIC_API_KEY   — ключ API от console.anthropic.com
"""

import asyncio
import json
import logging
import os
import random
import urllib.request
import urllib.error
import threading
from datetime import datetime, time
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

# Диагностика: показываем какие переменные видит процесс при старте
_all_keys = list(os.environ.keys())
logger.info(f"Все доступные переменные окружения: {_all_keys}")

# BotHost автоматически задаёт токен бота в переменной BOT_TOKEN.
# Также проверяем альтернативные названия на случай других хостингов.
TELEGRAM_TOKEN = (
    os.getenv("BOT_TOKEN") or
    os.getenv("TELEGRAM_BOT_TOKEN") or
    os.getenv("TELEGRAM_TOKEN") or
    os.getenv("TOKEN") or
    os.getenv("BOT_API_TOKEN") or
    ""
).strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

logger.info(f"TELEGRAM_TOKEN найден: {'ДА' if TELEGRAM_TOKEN else 'НЕТ'}")
logger.info(f"ANTHROPIC_API_KEY найден: {'ДА' if ANTHROPIC_API_KEY else 'НЕТ'}")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Токен бота не найден. Ожидались переменные: BOT_TOKEN, TELEGRAM_TOKEN, TOKEN. "
        f"Доступные переменные: {_all_keys}"
    )
if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY не найден в переменных окружения. "
        f"Доступные переменные: {_all_keys}"
    )

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ─── Anthropic API (pure stdlib, no sdk) ──────────────────────────────────────
def call_claude(system: str, user_text: str, max_tokens: int = 600) -> str:
    """Call Anthropic API using only stdlib urllib — no anthropic package needed."""
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

# ─── Hebrew Lessons Database ───────────────────────────────────────────────────
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
        from datetime import timedelta
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        if last == yesterday:
            streak += 1
        elif last != today:
            streak = 1
        update_user(user_id, {"streak": streak, "last_active": today})

# ─── Keyboards ────────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📚 Уроки", "🎯 Тест"],
        ["📊 Мой прогресс", "💡 Совет дня"],
        ["🤖 Спросить ИИ", "⚙️ Настройки"],
    ], resize_keyboard=True)

def lessons_keyboard():
    buttons = []
    for key, lesson in LESSONS.items():
        buttons.append([InlineKeyboardButton(lesson["title"], callback_data=f"lesson_{key}")])
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
    text = (
        f"שָׁלוֹם, {user.first_name}! 👋\n\n"
        "Добро пожаловать в бот для изучения иврита!\n\n"
        "Здесь вы найдёте:\n"
        "📚 *Уроки* — диалоги и фразы с транслитерацией\n"
        "🎯 *Тест* — проверь свои знания\n"
        "📊 *Прогресс* — следи за успехами\n"
        "💡 *Совет дня* — интересные факты\n"
        "🤖 *ИИ-помощник* — задай любой вопрос об иврите\n\n"
        "Иврит читается *справа налево* — начнём! 🇮🇱"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def lessons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *Выберите тему урока:*",
        parse_mode="Markdown",
        reply_markup=lessons_keyboard()
    )

async def show_phrase(query, lesson_key: str, phrase_idx: int):
    lesson = LESSONS[lesson_key]
    phrase = lesson["phrases"][phrase_idx]
    total = len(lesson["phrases"])

    text = (
        f"{lesson['title']} — фраза {phrase_idx + 1}/{total}\n\n"
        f"🇮🇱 *Иврит:*\n"
        f"```\n{phrase['he']}\n```\n\n"
        f"🔤 *Транслитерация:*\n_{phrase['translit']}_\n\n"
        f"🇷🇺 *По-русски:*\n{phrase['ru']}"
    )
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
        translit = phrase["translit"]
        he = phrase["he"]
        await query.answer(
            f"🔊 {he}\nПроизносится: {translit}",
            show_alert=True
        )

    elif data.startswith("learned_"):
        _, lesson_key, idx = data.split("_", 2)
        key = f"{lesson_key}_{idx}"
        user = get_user(user_id)
        learned = user.get("learned", [])
        total_phrases = user.get("total_phrases", 0)
        if key not in learned:
            learned.append(key)
            total_phrases += 1
            update_user(user_id, {"learned": learned, "total_phrases": total_phrases})
            await query.answer("✅ Отлично! Фраза добавлена в выученные!", show_alert=False)
        else:
            await query.answer("Вы уже выучили эту фразу! 🌟", show_alert=False)

    elif data == "main_menu":
        await query.edit_message_text(
            "Выберите раздел 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📚 Уроки", callback_data="go_lessons")],
                [InlineKeyboardButton("🎯 Тест", callback_data="go_quiz")],
            ])
        )

    elif data == "go_lessons":
        await query.edit_message_text("📚 Выберите тему:", reply_markup=lessons_keyboard())

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
    await send_quiz_question(update.message, update.effective_user.id, context)

async def send_quiz_question(message, user_id: int, context):
    all_phrases = []
    for key, lesson in LESSONS.items():
        for i, phrase in enumerate(lesson["phrases"]):
            all_phrases.append((key, i, phrase))

    if len(all_phrases) < 4:
        await message.reply_text("Недостаточно фраз для теста.")
        return

    correct_key, correct_idx, correct = random.choice(all_phrases)
    wrong_options = random.sample(
        [p for k, i, p in all_phrases if p["he"] != correct["he"]], 3
    )
    all_options = [correct] + wrong_options
    random.shuffle(all_options)
    correct_pos = next(i for i, p in enumerate(all_options) if p["he"] == correct["he"])

    context.user_data["quiz_correct"] = correct_pos
    context.user_data["quiz_phrase"] = correct

    buttons = []
    for i, opt in enumerate(all_options):
        buttons.append([InlineKeyboardButton(opt["ru"], callback_data=f"quiz_{i}")])

    await message.reply_text(
        f"🎯 *Тест*\n\nКак переводится:\n\n"
        f"🇮🇱 _{correct['he']}_\n"
        f"🔤 ({correct['translit']})\n\n"
        f"Выберите правильный перевод:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_quiz_answer(query, user_id: int, data: str, context):
    chosen = int(data.split("_")[1])
    correct_pos = context.user_data.get("quiz_correct")
    correct_phrase = context.user_data.get("quiz_phrase", {})

    if correct_pos is None:
        await query.edit_message_text("Начните тест заново — нажмите 🎯 Тест")
        return

    user = get_user(user_id)
    quiz_total = user.get("quiz_total", 0) + 1
    quiz_score = user.get("quiz_score", 0)

    if chosen == correct_pos:
        quiz_score += 1
        result = "✅ *Правильно!*\n\n"
    else:
        result = "❌ *Неправильно.*\n\n"

    result += (
        f"🇮🇱 {correct_phrase.get('he', '')}\n"
        f"🔤 {correct_phrase.get('translit', '')}\n"
        f"🇷🇺 {correct_phrase.get('ru', '')}"
    )

    update_user(user_id, {"quiz_score": quiz_score, "quiz_total": quiz_total})
    context.user_data.pop("quiz_correct", None)
    context.user_data.pop("quiz_phrase", None)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Ещё вопрос", callback_data="next_quiz")],
        [InlineKeyboardButton("📊 Мой счёт", callback_data="show_score")],
    ])
    await query.edit_message_text(result, parse_mode="Markdown", reply_markup=buttons)

async def next_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "next_quiz":
        await send_quiz_question(query.message, query.from_user.id, context)
    elif query.data == "show_score":
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

# ─── AI Assistant ──────────────────────────────────────────────────────────────
AI_SYSTEM = """Ты — дружелюбный учитель иврита для русскоговорящих студентов.
Ты помогаешь изучать иврит — объясняешь слова, грамматику, произношение.
Всегда давай транслитерацию ивритских слов русскими буквами.
Отвечай на русском языке. Будь позитивным и поддерживающим.
Когда пишешь ивритские слова, всегда добавляй транслитерацию в скобках.
Примеры: שָׁלוֹם (шалом), תּוֹדָה (тода), כֵּן (кен)."""

async def ai_assistant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *ИИ-помощник по ивриту*\n\n"
        "Задайте любой вопрос об иврите!\n"
        "Например:\n"
        "• «Как сказать 'я тебя люблю'?»\n"
        "• «Расскажи про ивритский алфавит»\n"
        "• «Как считать до 100?»",
        parse_mode="Markdown"
    )
    context.user_data["ai_mode"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    update_streak(user_id)

    if text == "📚 Уроки":
        await lessons_menu(update, context)
    elif text == "🎯 Тест":
        await quiz(update, context)
    elif text == "📊 Мой прогресс":
        await progress(update, context)
    elif text == "💡 Совет дня":
        await daily_tip(update, context)
    elif text == "🤖 Спросить ИИ":
        await ai_assistant(update, context)
    elif text == "⚙️ Настройки":
        await settings(update, context)
    elif context.user_data.get("ai_mode"):
        await update.message.chat.send_action("typing")
        try:
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(
                None, lambda: call_claude(AI_SYSTEM, text)
            )
            await update.message.reply_text(
                f"🤖 {answer}\n\n_Задайте ещё вопрос или выберите раздел ниже:_",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"AI error: {e}")
            await update.message.reply_text(
                "Извините, не могу ответить прямо сейчас. Попробуйте позже.",
                reply_markup=main_menu_keyboard()
            )
        context.user_data["ai_mode"] = False
    else:
        await update.message.reply_text(
            "Выберите раздел в меню ниже 👇",
            reply_markup=main_menu_keyboard()
        )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    reminders = user.get("reminders", True)
    status = "включены ✅" if reminders else "выключены ❌"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔔 Выключить напоминания" if reminders else "🔔 Включить напоминания",
            callback_data="toggle_reminders"
        )],
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
    """Фоновый поток: каждый день в 09:00 шлёт напоминания через urllib."""
    import time as _time
    while True:
        now = datetime.now()
        # Следующий запуск в 09:00
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if next_run <= now:
            from datetime import timedelta
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

    app.add_handler(CallbackQueryHandler(next_quiz_callback, pattern=r"^(next_quiz|show_score)$"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(toggle_reminders|reset_progress)$"))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем напоминания в фоновом потоке (без job-queue)
    t = threading.Thread(target=_reminder_loop, args=(TELEGRAM_TOKEN,), daemon=True)
    t.start()

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
