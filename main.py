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

# 🤖 Глобальная переменная для бота (инициализируется в main)
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
    """Создаёт бота с поддержкой прокси (внутри async-контекста!)"""
    proxy = PROXY_URL
    
    if proxy:
        try:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy)
            session = AiohttpSession(connector=connector)
            logger.info(f"🔗 Бот запущен с прокси: {proxy}")
        except ImportError:
            logger.error("❌ Не установлен aiohttp-socks! Выполни: pip install aiohttp-socks")
            logger.info("🔗 Запускаю без прокси (может не работать в РФ)")
            session = AiohttpSession()
        except Exception as e:
            logger.error(f"⚠️ Ошибка подключения прокси: {e}")
            logger.info("🔗 Запускаю без прокси")
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
        "1️⃣ Напиши имя автора (ник или реальное имя)\n"
        "2️⃣ Отправь видеофайл (до 20 МБ)\n"
        "3️⃣ Добавь описание (музыка, программа, идеи)\n\n"
        "В любой момент напиши /cancel, чтобы отменить.",
        parse_mode="HTML"
    )
    await state.set_state(EditSubmission.waiting_for_author)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ Нет активного процесса отправки.")
        return
    await state.clear()
    await message.answer("❌ Отправлено: процесс отменён. Начни заново: /start")


@dp.message(EditSubmission.waiting_for_author)
async def process_author(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Пожалуйста, введи имя автора <b>текстом</b>.", parse_mode="HTML")
        return
    
    author = message.text.strip()
    if len(author) > 100:
        await message.answer("⚠️ Имя слишком длинное (макс. 100 символов). Попробуй короче.")
        return
    
    await state.update_data(author=author)
    await message.answer(
        f"✅ Автор: <b>{author}</b>\n\n"
        f"🎬 Теперь отправь <b>видеофайл</b> (не ссылку, не GIF, не фото).\n"
        f"Макс. размер: {MAX_VIDEO_SIZE_MB} МБ.",
        parse_mode="HTML"
    )
    await state.set_state(EditSubmission.waiting_for_video)


@dp.message(EditSubmission.waiting_for_video, F.video)
async def process_video(message: Message, state: FSMContext):
    video = message.video
    
    if video.file_size and video.file_size > MAX_VIDEO_BYTES:
        await message.answer(
            f"⚠️ Видео слишком большое: {video.file_size // 1024 // 1024} МБ.\n"
            f"Максимум: {MAX_VIDEO_SIZE_MB} МБ.\n"
            f"💡 Совет: сожми видео или отправь ссылку на него в описании.",
            parse_mode="HTML"
        )
        return
    
    await state.update_data(
        video_id=video.file_id,
        file_name=video.file_name or "edit.mp4",
        duration=video.duration,
        width=video.width,
        height=video.height
    )
    await message.answer(
        "🎬 Видео получено!\n\n"
        "📝 Теперь напиши <b>описание эдита</b>:\n"
        "• Какая музыка?\n• В какой программе сделан?\n• Идея или референсы?\n"
        "(можно кратко)",
        parse_mode="HTML"
    )
    await state.set_state(EditSubmission.waiting_for_description)


@dp.message(EditSubmission.waiting_for_video)
async def handle_wrong_media(message: Message):
    await message.answer(
        "⚠️ Пожалуйста, отправь именно <b>видеофайл</b>.\n"
        "• Не GIF • Не ссылку • Не фото • Не голосовое",
        parse_mode="HTML"
    )


@dp.message(EditSubmission.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Описание не может быть пустым. Напиши хотя бы пару слов.")
        return
    
    description = message.text.strip()
    if len(description) > 1000:
        await message.answer("⚠️ Описание слишком длинное (макс. 1000 символов). Сократи.")
        return
    
    data = await state.get_data()
    author = data["author"]
    video_id = data["video_id"]
    file_name = data["file_name"]
    
    caption = (
        f"🎬 <b>Новый эдит</b>\n"
        f"👤 Автор: <code>{author}</code>\n"
        f"📝 Описание: {description}\n"
        f"📎 Файл: {file_name}"
    )
    
    try:
        sent_message = await bot.send_video(
            chat_id=ADMIN_CHAT_ID,
            video=video_id,
            caption=caption,
            parse_mode="HTML",
            request_timeout=30
        )
        submission_id = sent_message.message_id
    except TelegramNetworkError as e:
        logger.error(f"🌐 Ошибка сети: {e}")
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🎬 <b>Новый эдит</b>\n👤 Автор: <code>{author}</code>\n📝 Описание: {description}\n⚠️ Видео не удалось переслать (проблема с сетью).",
            parse_mode="HTML"
        )
        submission_id = None
    except TelegramBadRequest as e:
        logger.error(f"❌ Ошибка Telegram: {e}")
        await message.answer("❌ Не удалось отправить видео. Возможно, формат не поддерживается.")
        await state.clear()
        return
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при отправке. Попробуй позже.")
        await state.clear()
        return
    
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "submission_id": submission_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "author": author,
        "description": description,
        "video_id": video_id,
        "file_name": file_name
    }
    save_to_log(log_entry)
    
    await message.answer(
        "✅ <b>Эдит успешно отправлен!</b>\n\n"
        "Спасибо за участие 🙌\n"
        "Если работа будет одобрена — ты увидишь её в канале.",
        parse_mode="HTML"
    )
    await state.clear()


@dp.callback_query(F.data.startswith("approve:") | F.data.startswith("reject:"))
async def handle_moderation(callback: CallbackQuery):
    action, submission_id = callback.data.split(":")
    await callback.answer(f"{'✅ Одобрено' if action == 'approve' else '❌ Отклонено'}", show_alert=True)


async def main():
    """Точка входа"""
    global bot
    logger.info("🚀 Бот запускается...")
    
    # ✅ Создаём бота внутри async-контекста
    bot = await create_bot()
    
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as e:
        logger.critical(f"💥 Не удалось подключиться к Telegram: {e}")
        logger.info("💡 Попробуй: 1) Проверить прокси 2) Запустить на зарубежном сервере")
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logger.info("🔌 Сессия закрыта")


if __name__ == "__main__":
    asyncio.run(main())