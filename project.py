import asyncio
import logging
import os
import re
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from yt_dlp import YoutubeDL

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Конфигурация бота
BOT_TOKEN = "7144123711:AAGoBlsXLVVHAGn2dNGO7Xs4jriYZZ97xjM"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (ограничение Telegram для ботов)

# Инициализация бота с правильными параметрами
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# Класс для хранения состояния загрузки
class DownloadStatus:
    def __init__(self):
        self.progress = 0
        self.message: Optional[Message] = None
        self.last_update = 0
        self.cancelled = False


# Функция для создания прогресс бара
def progress_bar(percentage: float, length: int = 10) -> str:
    filled = "▓" * int(percentage / 100 * length)
    empty = "░" * (length - len(filled))
    return f"{filled}{empty} {percentage:.1f}%"


# Функции для проверки ссылок
def is_youtube_url(url: str) -> bool:
    youtube_domains = [
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "m.youtube.com",
    ]
    return any(domain in url for domain in youtube_domains)

def is_vk_url(url: str) -> bool:
    vk_domains = [
        "vk.com",
        "m.vk.com",
        "vkontakte.ru",
        "m.vkontakte.ru",
    ]
    return any(domain in url for domain in vk_domains)

def is_tiktok_url(url: str) -> bool:
    tiktok_domains = [
        "tiktok.com",
        "www.tiktok.com",
        "vm.tiktok.com",
        "m.tiktok.com",
    ]
    return any(domain in url for domain in tiktok_domains)


# Функция для получения информации о медиа
async def get_media_info(url: str, service: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    
    if service == "tiktok":
        ydl_opts["extractor_args"] = {"tiktok": {"watermark": False}}
    
    with YoutubeDL(ydl_opts) as ydl:
        info = await asyncio.to_thread(ydl.extract_info, url, download=False)
        return info


# Функция для скачивания медиа
async def download_media(
    url: str, 
    chat_id: int, 
    message_id: int, 
    status: DownloadStatus,
    media_type: str = "audio",
    service: str = "youtube"
) -> Optional[str]:
    def progress_hook(d):
        nonlocal status
        if status.cancelled:
            raise Exception("Download cancelled by user")
        if d["status"] == "downloading":
            progress = d.get("_percent_str", "0%").replace("%", "")
            try:
                progress_float = float(progress)
                if (
                    progress_float - status.last_update > 5
                    or progress_float == 100
                ):
                    status.progress = progress_float
                    status.last_update = progress_float
                    asyncio.create_task(
                        update_progress_message(
                            chat_id, message_id, status.progress, media_type
                        )
                    )
            except ValueError:
                pass

    ydl_opts = {
        "progress_hooks": [progress_hook],
        "outtmpl": f"downloads/%(id)s.%(ext)s",
        "quiet": True,
    }

    if media_type == "audio":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        })
    else:
        if service == "tiktok":
            ydl_opts.update({
                "format": "download_addr[height<=720]/download_addr[height<=480]/download_addr",
                "extractor_args": {"tiktok": {"watermark": False}}
            })
        else:
            ydl_opts.update({
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"
            })

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            
            if media_type == "audio":
                media_file = ydl.prepare_filename(info)
                media_file = os.path.splitext(media_file)[0] + ".mp3"
            else:
                media_file = ydl.prepare_filename(info)
                
            return media_file
    except Exception as e:
        logger.error(f"Error downloading {media_type}: {e}")
        return None


# Обновление сообщения с прогрессом
async def update_progress_message(
    chat_id: int, message_id: int, progress: float, media_type: str
):
    try:
        media_text = "audio" if media_type == "audio" else "video"
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"<b>⏳ Downloading {media_text}...</b>\n\n{progress_bar(progress)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚫 Cancel", callback_data="cancel_download"
                        )
                    ]
                ]
            ),
        )
    except Exception as e:
        logger.warning(f"Couldn't update progress message: {e}")


# Обработка команды /start
@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = """
<b>🎵 Media Downloader Bot</b>

Send me a link from:
- YouTube
- TikTok
- VK

I can download:
- Audio (MP3)
- Video (without watermark for TikTok)

<b>📌 Features:</b>
- Fast conversion
- High quality audio/video
- Progress tracking
- File size optimization

<i>Just paste any supported URL...</i>
"""
    await message.answer(welcome_text)


# Обработка YouTube ссылок
@dp.message(F.text & (F.text.contains("youtube") | F.text.contains("youtu.be")))
async def handle_youtube_link(message: Message):
    url = message.text.strip()
    
    if not is_youtube_url(url):
        await message.reply("⚠️ Please send a valid YouTube URL.")
        return

    try:
        info = await get_media_info(url, "youtube")
        if not info:
            await message.reply("❌ Could not get video information.")
            return

        title = info.get("title", "Unknown title")
        duration = info.get("duration", 0)
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="🎵 Download MP3", callback_data=f"youtube_audio:{url}"
            ),
            InlineKeyboardButton(
                text="🎬 Download Video", callback_data=f"youtube_video:{url}"
            ),
            width=2
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Cancel", callback_data="cancel"
            )
        )
        
        sent_msg = await message.reply(
            f"<b>🎬 YouTube Video Found:</b>\n\n"
            f"<b>📌 Title:</b> {title}\n"
            f"<b>⏱ Duration:</b> {duration // 60}:{duration % 60:02d}\n\n"
            f"<i>Choose download format:</i>",
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error processing YouTube link: {e}")
        await message.reply("❌ An error occurred while processing the video.")


# Обработка VK ссылок
@dp.message(F.text & (F.text.contains("vk.com") | F.text.contains("vkontakte.ru")))
async def handle_vk_link(message: Message):
    url = message.text.strip()
    
    if not is_vk_url(url):
        await message.reply("⚠️ Please send a valid VK URL.")
        return

    try:
        info = await get_media_info(url, "vk")
        if not info:
            await message.reply("❌ Could not get video information.")
            return

        title = info.get("title", "VK Video")
        duration = info.get("duration", 0)
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="🎵 Download MP3", callback_data=f"vk_audio:{url}"
            ),
            InlineKeyboardButton(
                text="🎬 Download Video", callback_data=f"vk_video:{url}"
            ),
            width=2
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Cancel", callback_data="cancel"
            )
        )
        
        sent_msg = await message.reply(
            f"<b>🎬 VK Video Found:</b>\n\n"
            f"<b>📌 Title:</b> {title}\n"
            f"<b>⏱ Duration:</b> {duration // 60}:{duration % 60:02d}\n\n"
            f"<i>Choose download format:</i>",
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error processing VK link: {e}")
        await message.reply("❌ An error occurred while processing the video.")


# Обработка TikTok ссылок
@dp.message(F.text & (F.text.contains("tiktok.com") | F.text.contains("vm.tiktok.com")))
async def handle_tiktok_link(message: Message):
    url = message.text.strip()
    
    if not is_tiktok_url(url):
        await message.reply("⚠️ Please send a valid TikTok URL.")
        return

    try:
        info = await get_media_info(url, "tiktok")
        if not info:
            await message.reply("❌ Could not get video information.")
            return

        title = info.get("title", "TikTok Video")
        duration = info.get("duration", 0)
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="🎵 Download MP3", callback_data=f"tiktok_audio:{url}"
            ),
            InlineKeyboardButton(
                text="🎬 Download Video (no watermark)", 
                callback_data=f"tiktok_video:{url}"
            ),
            width=2
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Cancel", callback_data="cancel"
            )
        )
        
        sent_msg = await message.reply(
            f"<b>🎬 TikTok Video Found:</b>\n\n"
            f"<b>📌 Title:</b> {title}\n"
            f"<b>⏱ Duration:</b> {duration // 60}:{duration % 60:02d}\n\n"
            f"<i>Choose download format:</i>",
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error processing TikTok link: {e}")
        await message.reply("❌ An error occurred while processing the video.")


# Обработка нажатия на инлайн кнопки
@dp.callback_query(F.data.startswith("youtube_") | F.data.startswith("vk_") | F.data.startswith("tiktok_"))
async def process_download(callback: CallbackQuery):
    data_parts = callback.data.split(":", 1)
    action = data_parts[0]
    url = data_parts[1] if len(data_parts) > 1 else ""
    
    service, media_type = action.split("_", 1)
    status = DownloadStatus()
    
    try:
        status.message = callback.message
        media_text = "audio" if media_type == "audio" else "video"
        
        await callback.message.edit_text(
            f"<b>⏳ Downloading {media_text} from {service.capitalize()}...</b>\n\n{progress_bar(0)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🚫 Cancel", callback_data="cancel_download"
                        )
                    ]
                ]
            ),
        )
        
        media_file = await download_media(
            url, 
            callback.message.chat.id, 
            callback.message.message_id, 
            status,
            media_type,
            service
        )
        
        if not media_file:
            await callback.message.edit_text(f"❌ Failed to download {media_text}.")
            return
            
        file_size = os.path.getsize(media_file)
        if file_size > MAX_FILE_SIZE:
            await callback.message.edit_text(
                f"⚠️ The {media_text} file is too large to send via Telegram "
                f"(max {MAX_FILE_SIZE / 1024 / 1024:.1f}MB)."
            )
            os.remove(media_file)
            return
            
        with open(media_file, "rb") as media:
            await callback.message.edit_text(f"<b>📤 Uploading {media_text}...</b>")
            
            if media_type == "audio":
                await bot.send_audio(
                    chat_id=callback.message.chat.id,
                    audio=types.BufferedInputFile(
                        media.read(), filename=os.path.basename(media_file)
                    ),
                    reply_to_message_id=callback.message.reply_to_message.message_id,
                )
            else:
                await bot.send_video(
                    chat_id=callback.message.chat.id,
                    video=types.BufferedInputFile(
                        media.read(), filename=os.path.basename(media_file)
                    ),
                    reply_to_message_id=callback.message.reply_to_message.message_id,
                )
                
        os.remove(media_file)
        await callback.message.edit_text(f"✅ {media_text.capitalize()} sent successfully!")
        
    except Exception as e:
        logger.error(f"Error in download process: {e}")
        if status.message:
            await status.message.edit_text("❌ An error occurred during processing.")


# Обработка отмены загрузки
@dp.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery):
    await callback.answer("Download cancelled", show_alert=True)
    await callback.message.edit_text("🚫 Download cancelled by user.")


# Обработка обычной отмены
@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery):
    await callback.message.delete()


# Запуск бота
async def main():
    os.makedirs("downloads", exist_ok=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())