import asyncio
import json
import os
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession

from dotenv import load_dotenv
from aiohttp import web  # <-- Добавили веб-сервер

# 🔧 Загружаем переменные из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", 20))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

LOG_FILE = Path("submissions.json")
MAX_VIDEO_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

# 🪵 Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

# 📦 Машина состояний (FSM)
class EditSubmission(StatesGroup):
    waiting_for_author = State()
    waiting_for_video = State()
    waiting_for_description = State()

# 🤖 Глобальная переменная для бота
bot = None
dp = Dispatcher()


def save_to_log(entry: dict) -> None:
    """Сохраняет заявку в JSON-файл"""
    logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except json.JSONDecodeError:
            logger.warning("Файл логов повреждён, создаём новый")
    
    logs.append(entry)
    
    tmp_file = LOG_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)
    tmp_file.replace(LOG_FILE)
    logger.info(f"✅ Заявка сохранена: {entry.get('author', 'unknown')}")


async def create_bot():
    """Создаёт бота"""
    proxy = PROXY_URL
    
    if proxy:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy)
            session = AiohttpSession(connector=connector)
            logger.info(f"🔗 Бот запущен с прокси: {proxy}")
        except Exception as e:
            logger.error(f"⚠️ Ошибка прокси: {e}. Запуск без прокси.")
            session = AiohttpSession()
    else:
        session = AiohttpSession()
        logger.info("🔗 Бот запущен без прокси")
    
    return Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 <b>Привет! Это бот для отправки эдитов.</b>\n\n"
        "Чтобы отправить работу:\n"
        "1️⃣ Напиши имя автора\n"
        "2️⃣ Отправь видеофайл (до 20 МБ)\n"
        "3️⃣ Добавь описание\n\n"
        "В любой момент напиши /cancel.",
        parse_mode="HTML"
    )
    await state.set_state(EditSubmission.waiting_for_author)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отмена. Начни заново: /start")


@dp.message(EditSubmission.waiting_for_author)
async def process_author(message: Message, state: FSMContext):
    if not message.text: return
    await state.update_data(author=message.text.strip())
    await message.answer(f"✅ Автор: {message.text}\n\nТеперь отправь видео.")
    await state.set_state(EditSubmission.waiting_for_video)


@dp.message(EditSubmission.waiting_for_video, F.video)
async def process_video(message: Message, state: FSMContext):
    video = message.video
    if video.file_size > MAX_VIDEO_BYTES:
        await message.answer(f"⚠️ Видео слишком большое (макс {MAX_VIDEO_SIZE_MB} МБ).")
        return
    
    await state.update_data(video_id=video.file_id, file_name=video.file_name or "edit.mp4")
    await message.answer("🎬 Видео получено! Напиши описание.")
    await state.set_state(EditSubmission.waiting_for_description)


@dp.message(EditSubmission.waiting_for_video)
async def handle_wrong_media(message: Message):
    await message.answer("⚠️ Отправь именно видеофайл.")


@dp.message(EditSubmission.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    if not message.text: return
    
    data = await state.get_data()
    caption = f"🎬 <b>Новый эдит</b>\n👤 Автор: <code>{data['author']}</code>\n📝 Описание: {message.text}"
    
    try:
        await bot.send_video(chat_id=ADMIN_CHAT_ID, video=data["video_id"], caption=caption, parse_mode="HTML")
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "author": data["author"],
            "description": message.text
        }
        save_to_log(log_entry)
        await message.answer("✅ Эдит отправлен!")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer("❌ Ошибка отправки.")
    
    await state.clear()


async def main():
    global bot
    logger.info("🚀 Бот запускается...")
    bot = await create_bot()
    
    # 🌐 ЧАСТЬ 1: Запускаем мини-веб-сервер для Render (чтобы он не спал)
    async def handle_ping(request):
        return web.Response(text="Bot is alive")

    app = web.Application()
    app.router.add_get('/', handle_ping) # Отвечаем на корень сайта
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render сам назначает порт, мы его берем из переменных
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Веб-сервер запущен на порту {port} (для предотвращения сна)")

    # 🤖 ЧАСТЬ 2: Запускаем бота
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())