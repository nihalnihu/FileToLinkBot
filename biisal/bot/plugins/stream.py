#(c) Adarsh-Goel
#(c) @biisal
import os
import re
import datetime
import asyncio
from asyncio import TimeoutError
from typing import Optional, Tuple
from biisal.bot import StreamBot
from biisal.utils.database import Database
from biisal.utils.human_readable import humanbytes
from biisal.vars import Var
from urllib.parse import quote_plus
from pyrogram import filters, Client
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
#from utils_bot import get_shortlink

from biisal.utils.file_properties import get_name, get_hash, get_media_file_size, get_media_from_message
from biisal.utils.firebase_config import get_mrdb_download_url
from biisal.utils.video_thumb import build_local_stream_url, extract_frame_10min
from biisal.utils.ai_cleaner import auto_clean_filename_with_ai
db = Database(Var.DATABASE_URL, Var.name)


async def _auto_clean_video_metadata(
    msg_id: int,
    file_name: str,
    file_hash: str,
    caption: Optional[str] = None,
):
    if os.environ.get("AUTO_AI_RENAME", "true").strip().lower() in ("0", "false", "no"):
        return
    try:
        updates = await auto_clean_filename_with_ai(
            file_name,
            msg_id=msg_id,
            file_hash=file_hash,
            caption=caption,
        )
        if updates:
            await db.update_video_metadata(str(msg_id), updates)
    except Exception as exc:
        print(f"Background AI metadata clean failed for {msg_id}: {exc}")


async def _capture_and_store_thumbnail(msg_id: int, file_name: str, file_hash: str):
    try:
        local_url = build_local_stream_url(msg_id, file_name, file_hash)
        jpeg_bytes = await extract_frame_10min(local_url)
        if jpeg_bytes:
            await db.save_thumbnail(msg_id, jpeg_bytes)
    except Exception as exc:
        print(f"Background thumbnail capture failed for {msg_id}: {exc}")


async def _background_index_and_clean(
    log_msg: Message,
    user_id: int,
    first_name: str,
    file_name: str,
    file_hash: str,
    file_size: int,
    stream_code: str,
    stream_link: str,
    is_owner: bool,
    is_video: bool,
    mime_type: str,
    caption: Optional[str],
):
    try:
        await log_msg.reply_text(
            text=(
                f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** [{first_name}](tg://user?id={user_id})\n"
                f"**Uꜱᴇʀ ɪᴅ :** `{user_id}`\n"
                f"**Code :** `{stream_code}`\n"
                f"**Stream ʟɪɴᴋ :** {stream_link}"
            ),
            disable_web_page_preview=True,
            quote=True,
        )
    except Exception as log_err:
        print(f"Failed to post BIN_CHANNEL info for {log_msg.id}: {log_err}")

    if not is_video:
        return

    clean_caption = (caption or "").strip()
    title, tmdb_id = _parse_video_caption(clean_caption, file_name)
    try:
        await db.add_video(
            {
                "msg_id": int(log_msg.id),
                "hash": file_hash,
                "stream_code": stream_code,
                "file_name": file_name,
                "file_size": int(file_size or 0),
                "mime_type": mime_type,
                "title": title,
                "caption": clean_caption,
                "tmdb_id": tmdb_id,
                "uploaded_by_owner": is_owner,
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            }
        )
        await _auto_clean_video_metadata(
            int(log_msg.id), file_name, file_hash, caption=clean_caption
        )
        await _capture_and_store_thumbnail(int(log_msg.id), file_name, file_hash)
    except Exception as db_err:
        print(f"Failed to index video for app stream: {db_err}")

_VIDEO_EXTENSIONS = (".mkv", ".mp4", ".webm", ".mov", ".avi", ".m4v", ".ts")


def _is_video_media(m: Message) -> Tuple[bool, str]:
    media = get_media_from_message(m)
    file_name = (getattr(media, "file_name", None) or "").strip()
    mime_type = (getattr(media, "mime_type", None) or "").lower().strip()
    if m.video is not None or mime_type.startswith("video/"):
        if not mime_type and file_name.lower().endswith(".mkv"):
            mime_type = "video/x-matroska"
        return True, mime_type or "video/mp4"
    if file_name.lower().endswith(_VIDEO_EXTENSIONS):
        inferred = "video/x-matroska" if file_name.lower().endswith(".mkv") else "video/mp4"
        return True, inferred
    return False, mime_type


def _parse_video_caption(caption: Optional[str], file_name: str):
    raw = (caption or "").strip()
    tmdb_id = None
    m_tmdb = re.search(
        r"(?:#?tmdb(?:_?id)?[:=\s]+|themoviedb\.org/movie/)(\d+)",
        raw,
        flags=re.IGNORECASE,
    )
    if m_tmdb:
        try:
            tmdb_id = int(m_tmdb.group(1))
        except ValueError:
            tmdb_id = None

    # Always use the actual file name (never Telegram caption text) as default title.
    clean_title = (file_name or "").strip() or "Untitled Video"
    return clean_title, tmdb_id


MY_PASS = os.environ.get("MY_PASS", None)
pass_dict = {}
pass_db = Database(Var.DATABASE_URL, "ag_passwords")

msg_text = """<b>Your link is ready! 🎬

File Name: <i>{}</i>
File Size: {}

🔑 MRDb Stream Code: <code>{}</code>
<i>(Tap the code to copy)</i>

📱 Watch easily in the MRDb App:
Open <b>MRDb</b> → <b>About</b> → <b>Stream</b>, paste this code, and tap <b>Play</b> for smooth HD streaming!

📥 Direct Download: {}
▶️ Watch Online: {}</b>"""



@StreamBot.on_message((filters.private) & (filters.document | filters.video | filters.audio | filters.photo) , group=4)
async def private_receive_handler(c: Client, m: Message):
    full_name = " ".join(p for p in [m.from_user.first_name, m.from_user.last_name] if p).strip()
    uname = m.from_user.username or ""
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id, name=full_name, username=uname)
        await c.send_message(
            Var.BIN_CHANNEL,
            f"New User Joined! : \n\n Name : [{m.from_user.first_name}](tg://user?id={m.from_user.id}) Started Your Bot!!"
        )
    else:
        await db.update_user_profile(m.from_user.id, name=full_name, username=uname)
    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await c.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await c.send_message(
                    chat_id=m.chat.id,
                    text="You are banned!\n\n  **𝙲𝚘𝚗𝚍𝚊𝚌𝚝 𝙰𝚍𝚖𝚒𝚗 [Support](https://t.me/nihh_all) 𝙷𝙴 Wɪʟʟ Hᴇʟᴘ Yᴏᴜ**",
                    
                    disable_web_page_preview=True
                )
                return 
        except UserNotParticipant:
            await c.send_photo(
                chat_id=m.chat.id,
                photo="https://telegra.ph/file/5eb253f28ed7ed68cb4e6.png",
                caption=""""<b>Hᴇʏ ᴛʜᴇʀᴇ!\n\nPʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴜsᴇ ᴍᴇ ! 😊\n\nDᴜᴇ ᴛᴏ sᴇʀᴠᴇʀ ᴏᴠᴇʀʟᴏᴀᴅ, ᴏɴʟʏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ sᴜʙsᴄʀɪʙᴇʀs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ !</b>""",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("Jᴏɪɴ ɴᴏᴡ 🚩", url=f"https://t.me/{Var.UPDATES_CHANNEL}")
                        ]
                    ]
                ),
                
            )
            return
        except Exception as e:
            await m.reply_text(e)
            await c.send_message(
                chat_id=m.chat.id,
                text="**Sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ Wʀᴏɴɢ. Cᴏɴᴛᴀᴄᴛ** [Support](https://t.me/nihh_all)",
                
                disable_web_page_preview=True)
            return
    ban_chk = await db.is_banned(int(m.from_user.id))
    if ban_chk == True:
        return await m.reply(Var.BAN_ALERT)
    try:
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
        file_name = get_name(log_msg) or f"video_{log_msg.id}.mkv"
        file_hash = get_hash(log_msg)
        file_size = get_media_file_size(m)
        stream_code = f"MRDB-{log_msg.id}-{file_hash.upper()}"
        stream_link = f"{Var.URL}watch/{file_hash}{log_msg.id}"
        online_link = f"{Var.URL}{file_hash}{log_msg.id}"

        is_owner = bool(m.from_user and (m.from_user.id in Var.OWNER_ID))
        is_video, mime_type = _is_video_media(m)

        app_download_url = await get_mrdb_download_url()
        reply_buttons = [
            [
                InlineKeyboardButton("🎭 ᴡᴀᴛᴄʜ", url=stream_link),
                InlineKeyboardButton('📥 ᴅᴏᴡɴʟᴏᴀᴅ', url=online_link),
            ],
        ]
        if app_download_url:
            reply_buttons.append(
                [InlineKeyboardButton("📱 Download MRDb App", url=app_download_url)]
            )
        reply_buttons.append(
            [InlineKeyboardButton('☕ 𝖻𝗎𝗒 𝗆𝖾 𝖺 𝖼𝗈𝖿𝖿𝖾𝖾 ☕', url="https://www.buymeacoffee.com/nihaal")]
        )

        # Send the download/stream reply immediately, then run DB indexing,
        # audio metadata probing, TMDB matching, and AI renaming in background.
        await m.reply_text(
            text=msg_text.format(file_name, humanbytes(file_size), stream_code, online_link, stream_link),
            quote=True,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(reply_buttons)
        )
        asyncio.create_task(
            _background_index_and_clean(
                log_msg=log_msg,
                user_id=int(m.from_user.id),
                first_name=m.from_user.first_name or "User",
                file_name=file_name,
                file_hash=file_hash,
                file_size=int(file_size or 0),
                stream_code=stream_code,
                stream_link=stream_link,
                is_owner=is_owner,
                is_video=is_video,
                mime_type=mime_type,
                caption=m.caption,
            )
        )
    except FloodWait as e:
        print(f"Sleeping for {str(e.x)}s")
        await asyncio.sleep(e.x)
        await c.send_message(chat_id=Var.BIN_CHANNEL, text=f"Gᴏᴛ FʟᴏᴏᴅWᴀɪᴛ ᴏғ {str(e.x)}s from [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n\n**𝚄𝚜𝚎𝚛 𝙸𝙳 :** `{str(m.from_user.id)}`", disable_web_page_preview=True)
    except Exception as e:
        print(f"Error handling private upload: {e}")
        try:
            await m.reply_text(
                f"<b>Upload failed:</b> <code>{e}</code>\n\n"
                "Make sure this bot is added as Admin in BIN_CHANNEL.",
                quote=True,
            )
        except Exception:
            pass

@StreamBot.on_message(filters.channel & ~filters.group & (filters.document | filters.video | filters.photo)  & ~filters.forwarded, group=-1)
async def channel_receive_handler(bot, broadcast):
    if int(broadcast.chat.id) in Var.BAN_CHNL:
        print("chat trying to get straming link is found in BAN_CHNL,so im not going to give stram link")
        return
    ban_chk = await db.is_banned(int(broadcast.chat.id))
    if (int(broadcast.chat.id) in Var.BANNED_CHANNELS) or (ban_chk == True):
        await bot.leave_chat(broadcast.chat.id)
        return
    try:
        log_msg = await broadcast.forward(chat_id=Var.BIN_CHANNEL)
        file_hash = get_hash(log_msg)
        stream_link = f"{Var.URL}watch/{file_hash}{log_msg.id}"
        online_link = f"{Var.URL}{file_hash}{log_msg.id}"
        await log_msg.reply_text(
            text=f"**Channel Name:** `{broadcast.chat.title}`\n**CHANNEL ID:** `{broadcast.chat.id}`\n**Rᴇǫᴜᴇsᴛ ᴜʀʟ:** {stream_link}",
            quote=True
        )
        await bot.edit_message_reply_markup(
            chat_id=broadcast.chat.id,
            message_id=broadcast.id,
            reply_markup=InlineKeyboardMarkup( [[
                
                InlineKeyboardButton("🎭 ᴡᴀᴛᴄʜ", url=stream_link),
                InlineKeyboardButton('📥 ᴅᴏᴡɴʟᴏᴀᴅ', url=online_link)
            ],[
                InlineKeyboardButton('☕ 𝖻𝗎𝗒 𝗆𝖾 𝖺 𝖼𝗈𝖿𝖿𝖾𝖾 ☕', url="https://www.buymeacoffee.com/nihaal")
       
            ]]
            )
         )
    except FloodWait as w:
        print(f"Sleeping for {str(w.x)}s")
        await asyncio.sleep(w.x)
        await bot.send_message(chat_id=Var.BIN_CHANNEL,
                            text=f"GOT FLOODWAIT OF {str(w.x)}s FROM {broadcast.chat.title}\n\n**CHANNEL ID:** `{str(broadcast.chat.id)}`",
                            disable_web_page_preview=True)
    except Exception as e:
        await bot.send_message(chat_id=Var.BIN_CHANNEL, text=f"**#ERROR_TRACKEBACK:** `{e}`", disable_web_page_preview=True)
        print(f"Cᴀɴ'ᴛ Eᴅɪᴛ Bʀᴏᴀᴅᴄᴀsᴛ Mᴇssᴀɢᴇ!\nEʀʀᴏʀ:  **Give me edit permission in updates and bin Channel!{e}**")

