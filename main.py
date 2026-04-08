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
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession

from dotenv import load_dotenv
from aiohttp import web

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

# 📦 Машина состояний
class EditSubmission(StatesGroup):
    waiting_for_author = State()
    waiting_for_video = State()
    waiting_for_description = State()

bot = None
dp = Dispatcher()


def save_to_log(entry: dict) -> None:
    logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except json.JSONDecodeError:
            logger.warning("Файл логов повреждён")
    logs.append(entry)
    tmp_file = LOG_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)
    tmp_file.replace(LOG_FILE)


async def create_bot():
    proxy = PROXY_URL
    if proxy:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy)
            session = AiohttpSession(connector=connector)
            logger.info(f"🔗 Бот с прокси: {proxy}")
        except Exception as e:
            logger.error(f"⚠️ Ошибка прокси: {e}")
            session = AiohttpSession()
    else:
        session = AiohttpSession()
        logger.info("🔗 Бот без прокси")
    
    return Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )


# 🔘 Кнопка отмены
def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎬 Отправь свой эдит.\n\n"
        "Сначала напиши имя автора:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(EditSubmission.waiting_for_author)


@dp.message(Command("cancel"))
@dp.callback_query(F.data == "cancel")
async def cmd_cancel(event: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text("❌ Отменено. Напиши /start чтобы начать заново.")
    else:
        await event.answer("❌ Отменено. Напиши /start чтобы начать заново.")


@dp.message(EditSubmission.waiting_for_author)
async def process_author(message: Message, state: FSMContext):
    if not message.text: return
    await state.update_data(author=message.text.strip())
    await message.answer(
        f"👤 {message.text}\n\nТеперь отправь видео:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(EditSubmission.waiting_for_video)


@dp.message(EditSubmission.waiting_for_video, F.video)
async def process_video(message: Message, state: FSMContext):
    video = message.video
    if video.file_size and video.file_size > MAX_VIDEO_BYTES:
        await message.answer(f"⚠️ Видео > {MAX_VIDEO_SIZE_MB} МБ. Сожми или скинь ссылку.")
        return
    
    await state.update_data(
        video_id=video.file_id,
        file_name=video.file_name or "edit.mp4"
    )
    await message.answer(
        "🎬 Видео принято.\n\nНапиши описание (музыка, программа, идея):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(EditSubmission.waiting_for_description)


@dp.message(EditSubmission.waiting_for_video)
async def handle_wrong_media(message: Message):
    await message.answer("⚠️ Нужно именно видеофайл.", reply_markup=get_cancel_keyboard())


@dp.message(EditSubmission.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    if not message.text: return
    
    data = await state.get_data()
    author = data["author"]
    video_id = data["video_id"]
    description = message.text.strip()
    
    caption = f"🎬 Новый эдит\n👤 Автор: {author}\n📝 {description}"
    
    try:
        await bot.send_video(
            chat_id=ADMIN_CHAT_ID,
            video=video_id,
            caption=caption
        )
        save_to_log({
            "timestamp": datetime.now().isoformat(),
            "author": author,
            "description": description,
            "video_id": video_id
        })
        await message.answer("✅ Готово! Спасибо.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer("❌ Ошибка отправки.")
    
    await state.clear()


async def main():
    global bot
    logger.info("🚀 Запуск...")
    bot = await create_bot()
    
    # 🌐 Web-server для Render (чтобы не спал)
    async def handle_ping(request):
        return web.Response(text="Bot is alive")
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Web server on port {port}")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())