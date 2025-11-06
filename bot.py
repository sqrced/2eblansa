import os
import logging
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import Command
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- Инициализация базы ---
async def init_db():
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """)
        for key in ["suggestions", "approved", "declined"]:
            await db.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))
        await db.commit()


# --- Работа со статистикой ---
async def increment_stat(key: str):
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("UPDATE stats SET value = value + 1 WHERE key = ?", (key,))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect("db.sqlite3") as db:
        stats = await db.execute_fetchall("SELECT key, value FROM stats")
        banned = await db.execute_fetchone("SELECT COUNT(*) FROM banned_users")
    data = {row[0]: row[1] for row in stats}
    data["banned"] = banned[0]
    return data


# --- Проверка бана ---
async def is_banned(user_id: int):
    async with aiosqlite.connect("db.sqlite3") as db:
        row = await db.execute_fetchone("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        return row is not None


# --- /start ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if await is_banned(message.from_user.id):
        return await message.answer("🚫 Вы заблокированы и не можете отправлять предложения.")
    await message.answer("👋 Отправь мне текст, фото, видео или файл — я передам его на модерацию!")


# --- Обработка предложений ---
@dp.message()
async def handle_suggestion(message: Message):
    if await is_banned(message.from_user.id):
        return await message.answer("🚫 Вы заблокированы и не можете отправлять предложения.")

    await increment_stat("suggestions")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{message.message_id}:{message.from_user.id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"decline:{message.message_id}:{message.from_user.id}")
        ]
    ])

    caption = f"📨 От @{message.from_user.username or 'Без ника'} ({message.from_user.id})"
    if message.caption:
        caption += f"\n\n{message.caption}"
    elif message.text:
        caption += f"\n\n{message.text}"

    # Отправляем контент всем админам
    for admin_id in ADMIN_IDS.split(","):
        if not admin_id.strip():
            continue

        try:
            admin_id = int(admin_id)
            if message.photo:
                await bot.send_photo(admin_id, message.photo[-1].file_id, caption=caption, reply_markup=kb)
            elif message.video:
                await bot.send_video(admin_id, message.video.file_id, caption=caption, reply_markup=kb)
            elif message.animation:
                await bot.send_animation(admin_id, message.animation.file_id, caption=caption, reply_markup=kb)
            elif message.document:
                await bot.send_document(admin_id, message.document.file_id, caption=caption, reply_markup=kb)
            elif message.audio:
                await bot.send_audio(admin_id, message.audio.file_id, caption=caption, reply_markup=kb)
            elif message.voice:
                await bot.send_voice(admin_id, message.voice.file_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb)
        except Exception as e:
            logger.warning(f"Не удалось отправить админу {admin_id}: {e}")

    await message.answer("🕙 Ваше предложение отправлено на модерацию!")


# --- Кнопки (одобрить / отклонить) ---
@dp.callback_query()
async def callbacks(callback: CallbackQuery):
    data = callback.data
    if data.startswith("approve:"):
        await increment_stat("approved")
        _, msg_id, user_id = data.split(":")
        msg = callback.message
        # Определяем тип и публикуем в канал
        if msg.photo:
            await bot.send_photo(CHANNEL_ID, msg.photo[-1].file_id, caption=msg.caption or msg.text)
        elif msg.video:
            await bot.send_video(CHANNEL_ID, msg.video.file_id, caption=msg.caption or msg.text)
        elif msg.animation:
            await bot.send_animation(CHANNEL_ID, msg.animation.file_id, caption=msg.caption or msg.text)
        elif msg.document:
            await bot.send_document(CHANNEL_ID, msg.document.file_id, caption=msg.caption or msg.text)
        elif msg.audio:
            await bot.send_audio(CHANNEL_ID, msg.audio.file_id, caption=msg.caption or msg.text)
        elif msg.voice:
            await bot.send_voice(CHANNEL_ID, msg.voice.file_id, caption=msg.caption or msg.text)
        else:
            await bot.send_message(CHANNEL_ID, msg.text)
        await callback.message.edit_text("✅ Одобрено и опубликовано!")
    elif data.startswith("decline:"):
        await increment_stat("declined")
        await callback.message.edit_text("❌ Предложение отклонено.")
    await callback.answer()


# --- /ban /unban ---
@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS.split(","):
        return await message.answer("🚫 У вас нет прав.")
    if not message.reply_to_message:
        return await message.answer("Ответьте на сообщение пользователя, чтобы забанить его.")

    user_id = message.reply_to_message.from_user.id
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
        await db.commit()

    await message.answer(f"🚫 Пользователь {user_id} забанен.")


@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS.split(","):
        return await message.answer("🚫 У вас нет прав.")
    if not message.reply_to_message:
        return await message.answer("Ответьте на сообщение пользователя, чтобы разбанить его.")

    user_id = message.reply_to_message.from_user.id
    async with aiosqlite.connect("db.sqlite3") as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()

    await message.answer(f"✅ Пользователь {user_id} разбанен.")


# --- /stats ---
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS.split(","):
        return await message.answer("🚫 У вас нет прав.")
    stats = await get_stats()
    text = (
        "📊 <b>Статистика бота:</b>\n"
        f"📝 Всего предложений: <b>{stats['suggestions']}</b>\n"
        f"✅ Одобрено: <b>{stats['approved']}</b>\n"
        f"❌ Отклонено: <b>{stats['declined']}</b>\n"
        f"🚫 Забанено: <b>{stats['banned']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


# --- Webhook ---
async def on_startup(app):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    logger.info("Webhook удалён")

async def handle(request):
    update = await request.json()
    await dp.feed_update(bot, types.Update(**update))
    return web.Response()

app = web.Application()
app.router.add_post(WEBHOOK_PATH, handle)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
