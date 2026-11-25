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
db = Database(Var.DATABASE_URL, Var.name)

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
        raw = (raw[: m_tmdb.start()] + raw[m_tmdb.end() :]).strip()

    clean_title = re.sub(r"\s+", " ", raw).strip(" -|:\n\t")
    if not clean_title:
        base_name = os.path.splitext(file_name or "Untitled Video")[0]
        clean_title = re.sub(r"[._]+", " ", base_name).strip() or "Untitled Video"
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
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await c.send_message(
            Var.BIN_CHANNEL,
            f"New User Joined! : \n\n Name : [{m.from_user.first_name}](tg://user?id={m.from_user.id}) Started Your Bot!!"
        )
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
        stream_link = f"{Var.URL}watch/{str(log_msg.id)}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL}{str(log_msg.id)}/{quote_plus(file_name)}?hash={file_hash}"

        is_owner = bool(m.from_user and (m.from_user.id in Var.OWNER_ID))
        is_video, mime_type = _is_video_media(m)
        if is_video:
            title, tmdb_id = _parse_video_caption(m.caption, file_name)
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
                        "tmdb_id": tmdb_id,
                        "uploaded_by_owner": is_owner,
                        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                    }
                )
            except Exception as db_err:
                print(f"Failed to index video for app stream: {db_err}")

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

        await log_msg.reply_text(text=f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n**Uꜱᴇʀ ɪᴅ :** `{m.from_user.id}`\n**Code :** `{stream_code}`\n**Stream ʟɪɴᴋ :** {stream_link}", disable_web_page_preview=True,  quote=True)
        await m.reply_text(
            text=msg_text.format(file_name, humanbytes(file_size), stream_code, online_link, stream_link),
            quote=True,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(reply_buttons)
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
        stream_link = f"{Var.URL}watch/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
        online_link = f"{Var.URL}{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
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

