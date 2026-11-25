
from biisal.bot import StreamBot
from biisal.vars import Var
import logging
logger = logging.getLogger(__name__)
from biisal.bot.plugins.stream import MY_PASS
from biisal.utils.human_readable import humanbytes
from biisal.utils.database import Database
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from biisal.utils.file_properties import get_name, get_hash, get_media_file_size
from biisal.utils.firebase_config import get_mrdb_download_url
db = Database(Var.DATABASE_URL, Var.name)
from pyrogram.types import ReplyKeyboardMarkup
from biisal.vars import bot_name , mv_rockers , movie_laab

SRT_TXT = """𝙷𝚢 {} ☻︎!,

I ᴀᴍ ᴀᴅᴠᴀɴᴄᴇ ғɪʟᴇ ᴛᴏ Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴏʀ Bᴏᴛ ᴡɪᴛʜ Cʜᴀɴɴᴇʟ sᴜᴘᴘᴏʀᴛ...😎

!ᴄʟɪᴄᴋ ʜᴇʟᴘ ʙᴜᴛᴛᴏɴ ᴛᴏ ᴋɴᴏᴡ ᴍᴏʀᴇ ⌫

"""

@StreamBot.on_message(filters.command("start") & filters.private )
async def start(b, m):
    full_name = " ".join(p for p in [m.from_user.first_name, m.from_user.last_name] if p).strip()
    uname = m.from_user.username or ""
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id, name=full_name, username=uname)
        await b.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )
    else:
        await db.update_user_profile(m.from_user.id, name=full_name, username=uname)
    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await b.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await b.send_message(
                    chat_id=m.chat.id,
                    text="__Sᴏʀʀʏ, Yᴏᴜ Aʀᴇ Bᴀɴɴᴇᴅ Fʀᴏᴍ Mᴇ☠︎︎. 𝐂𝐨𝐧𝐝𝐚𝐜𝐭 𝐓𝐡𝐞 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫__\n\n  **𝙃𝙚 𝗪𝗶𝗹𝗹 𝗛𝗲𝗹𝗽 𝗬𝗼𝘂**",
                    disable_web_page_preview=True
                )
                return
        except UserNotParticipant:
             await StreamBot.send_photo(
                chat_id=m.chat.id,
                photo="https://telegra.ph/file/d033d7ef1da414f32fb22.jpg",
                caption=""""<b>Hᴇʏ ᴛʜᴇʀᴇ!\n\nPʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴜsᴇ ᴍᴇ ! 😊\n\nDᴜᴇ ᴛᴏ sᴇʀᴠᴇʀ ᴏᴠᴇʀʟᴏᴀᴅ, ᴏɴʟʏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ sᴜʙsᴄʀɪʙᴇʀs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ! 𝚂𝚘𝚛𝚛𝚢㋛︎</b>""",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("Jᴏɪɴ ɴᴏᴡ ✔︎", url=f"https://t.me/{Var.UPDATES_CHANNEL}"),

                            InlineKeyboardButton("Cᴏɴᴅᴀᴛᴄ Mᴇ ☠︎︎", url="t.me/nihh_all")
                        ]
                    ]
                ),
                
            )
             return
        except Exception:
            await b.send_message(
                chat_id=m.chat.id,
                text="<b>sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ.ᴘʟᴇᴀsᴇ <a href='https://t.me/TG_BotCreator'>ᴄʟɪᴄᴋ ʜᴇʀᴇ ғᴏʀ sᴜᴘᴘᴏʀᴛ</a></b>",
                
                disable_web_page_preview=True)
            return
    app_download_url = await get_mrdb_download_url()
    start_buttons = [
        [
            InlineKeyboardButton("ᴀʙᴏᴜᴛ ✍︎", callback_data="about"),
            InlineKeyboardButton("ʜᴇʟᴘ ⌫", callback_data="help"),
        ],
        [
            InlineKeyboardButton("ᴅᴇᴠᴏʟᴏᴘᴇʀ ✰", callback_data="aboutDev"),
            InlineKeyboardButton("ᴄʜᴀɴɴᴇʟ ⚠︎", url=mv_rockers),
        ],
    ]
    if app_download_url:
        start_buttons.append(
            [InlineKeyboardButton("📱 Download MRDb App", url=app_download_url)]
        )
    start_buttons.append(
        [InlineKeyboardButton("☕ 𝖻𝗎𝗒 𝗆𝖾 𝖺 𝖼𝗈𝖿𝖿𝖾𝖾 ☕", url="https://www.buymeacoffee.com/nihaal")]
    )
    await StreamBot.send_photo(
        chat_id=m.chat.id,
        photo="https://telegra.ph/file/c0da896e59beb1c30f585.jpg",
        caption=SRT_TXT.format(m.from_user.mention(style="md")),
        reply_markup=InlineKeyboardMarkup(start_buttons),
    )
@StreamBot.on_message(filters.command("help") & filters.private )
async def help_cd(b, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await b.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )
    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await b.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await b.send_message(
                    chat_id=m.chat.id,
                    text="__𝘚𝘰𝘳𝘳𝘺, 𝘠𝘰𝘶 𝘈𝘳𝘦 𝘉𝘢𝘯𝘯𝘦𝘥 𝘍𝘳𝘰𝘮 𝘜𝘴𝘪𝘯𝘨 𝘔𝘦. ᴄᴏɴᴅᴀᴄᴛ ᴛʜᴇ Dᴇᴠᴇʟᴏᴘᴇʀ @nihh_all__\n\n  **𝙃𝙚 𝙬𝙞𝙡𝙡 𝙝𝙚𝙡𝙥 𝙮𝙤𝙪**",
                    disable_web_page_preview=True
                )
                return
        except UserNotParticipant:
             await StreamBot.send_photo(
                chat_id=m.chat.id,
                photo="https://telegra.ph/file/d033d7ef1da414f32fb22.jpg",
                caption=""""<b>Hᴇʏ ᴛʜᴇʀᴇ!\n\nPʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴜsᴇ ᴍᴇ ! 😊\n\nDᴜᴇ ᴛᴏ sᴇʀᴠᴇʀ ᴏᴠᴇʀʟᴏᴀᴅ, ᴏɴʟʏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ sᴜʙsᴄʀɪʙᴇʀs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ !</b>""",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("Jᴏɪɴ ɴᴏᴡ ✔︎", url=f"https://t.me/{Var.UPDATES_CHANNEL}"),
                            InlineKeyboardButton("Cᴏɴᴅᴀᴛᴄ Mᴇ ☠︎︎", url="t.me/nihh_all")
                        ]
                    ]
                ),
                
            )
             return
        except Exception:
            await b.send_message(
                chat_id=m.chat.id,
                text="<b>sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ.ᴘʟᴇᴀsᴇ <a href='https://t.me/nihh_all'>ᴄʟɪᴄᴋ ʜᴇʀᴇ ғᴏʀ sᴜᴘᴘᴏʀᴛ</a></b>",
                
                disable_web_page_preview=True)
            return
    await StreamBot.send_photo(
    chat_id=m.chat.id,
    photo="https://telegra.ph/file/d033d7ef1da414f32fb22.jpg",
    caption=f"<b>ᴡᴇ ᴅᴏɴᴛ ɴᴇᴇᴅ ᴍᴀɴʏ ᴄᴏᴍᴍᴀɴᴅs ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ 🤩.\n\nᴊᴜsᴛ sᴇɴᴅ ᴍᴇ ᴠɪᴅᴇᴏ ғɪʟᴇs ᴀɴᴅ ɪ ᴡɪʟʟ ɢɪᴠᴇ ʏᴏᴜ ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ & sᴛʀᴇᴀᴍᴀʙʟᴇ ʟɪɴᴋ.\n\nᴏʀ ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ᴍᴇ ɪɴ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ</a>..ᴊᴜsᴛ ᴀᴅᴅ ᴍᴇ ᴀɴᴅ ᴍᴀᴋᴇ ᴍᴇ ᴀᴅᴍɪɴ ᴀɴᴅ sᴇᴇ ᴍʏ ᴍᴀɢɪᴄ 😎</b>",
    reply_markup=InlineKeyboardMarkup(
        [
        
            [
                
                       
                InlineKeyboardButton("cʜᴀɴɴᴇʟ", url=mv_rockers),

                
                InlineKeyboardButton("Contact", url="t.me/nihh_all"),

            ],
            [
                InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),

            ]

        ]
    )
)
@StreamBot.on_message(filters.command('ban') & filters.user(Var.OWNER_ID))
async def do_ban(bot ,  message):
    userid = message.text.split(" ", 2)[1] if len(message.text.split(" ", 1)) > 1 else None
    reason = message.text.split(" ", 2)[2] if len(message.text.split(" ", 2)) > 2 else None
    if not userid:
        return await message.reply('<b>ᴘʟᴇᴀsᴇ ᴀᴅᴅ ᴀ ᴠᴀʟɪᴅ ᴜsᴇʀ/ᴄʜᴀɴɴᴇʟ ɪᴅ ᴡɪᴛʜ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ\n\nᴇx : /ban (user/channel_id) (banning reason[Optional]) \nʀᴇᴀʟ ᴇx : <code>/ban 1234567899</code>\nᴡɪᴛʜ ʀᴇᴀsᴏɴ ᴇx:<code>/ban 1234567899 seding adult links to bot</code>\nᴛᴏ ʙᴀɴ ᴀ ᴄʜᴀɴɴᴇʟ :\n<code>/ban CHANEL_ID</code>\nᴇx : <code>/ban -1001234567899</code></b>')
    text = await message.reply("<b>ʟᴇᴛ ᴍᴇ ᴄʜᴇᴄᴋ 👀</b>")
    banSts = await db.ban_user(userid)
    if banSts == True:
        await text.edit(
    text=f"<b><code>{userid}</code> ʜᴀs ʙᴇᴇɴ ʙᴀɴɴᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ\n\nSʜᴏᴜʟᴅ I sᴇɴᴅ ᴀɴ ᴀʟᴇʀᴛ ᴛᴏ ᴛʜᴇ ʙᴀɴɴᴇᴅ ᴜsᴇʀ?</b>",
    reply_markup=InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ʏᴇs ✅", callback_data=f"sendAlert_{userid}_{reason if reason else 'no reason provided'}"),
                InlineKeyboardButton("ɴᴏ ❌", callback_data=f"noAlert_{userid}"),
            ],
        ]
    ),
)
    else:
        await text.edit(f"<b>Cᴏɴᴛʀᴏʟʟ ʏᴏᴜʀ ᴀɴɢᴇʀ ʙʀᴏ...\n<code>{userid}</code> ɪs ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ !!</b>")
    return


@StreamBot.on_message(filters.command('unban') & filters.user(Var.OWNER_ID))
async def do_unban(bot ,  message):
    userid = message.text.split(" ", 2)[1] if len(message.text.split(" ", 1)) > 1 else None
    if not userid:
        return await message.reply('ɢɪᴠᴇ ᴍᴇ ᴀɴ ɪᴅ\nᴇx : <code>/unban 1234567899<code>')
    text = await message.reply("<b>ʟᴇᴛ ᴍᴇ ᴄʜᴇᴄᴋ 🥱</b>")
    unban_chk = await db.is_unbanned(userid)
    if  unban_chk == True:
        await text.edit(text=f'<b><code>{userid}</code> ɪs ᴜɴʙᴀɴɴᴇᴅ\nSʜᴏᴜʟᴅ I sᴇɴᴅ ᴛʜᴇ ʜᴀᴘᴘʏ ɴᴇᴡs ᴀʟᴇʀᴛ ᴛᴏ ᴛʜᴇ ᴜɴʙᴀɴɴᴇᴅ ᴜsᴇʀ?</b>',
        reply_markup=InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ʏᴇs ✅", callback_data=f"sendUnbanAlert_{userid}"),
                InlineKeyboardButton("ɴᴏ ❌", callback_data=f"NoUnbanAlert_{userid}"),
            ],
        ]
    ),
)

    elif unban_chk==False:
        await text.edit('<b>ᴜsᴇʀ ɪs ɴᴏᴛ ʙᴀɴɴᴇᴅ ʏᴇᴛ.</b>')
    else :
        await text.edit(f"<b>ғᴀɪʟᴇᴅ ᴛᴏ ᴜɴʙᴀɴ ᴜsᴇʀ/ᴄʜᴀɴɴᴇʟ.\nʀᴇᴀsᴏɴ : {unban_chk}</b>")



@StreamBot.on_callback_query()
async def cb_handler(client, query):
    data = query.data
    if data == "close_data":
        await query.message.delete()


    if data == "start":
        await query.message.edit_caption(
        caption= SRT_TXT.format(query.from_user.mention(style="md")),
        reply_markup=InlineKeyboardMarkup(
                [
         [
                 InlineKeyboardButton("ᴀʙᴏᴜᴛ ✍︎", callback_data="about"),
                 InlineKeyboardButton("ʜᴇʟᴘ ⌫", callback_data="help")
            ],
        
            [
                 
                 InlineKeyboardButton("ᴅᴇᴠᴏʟᴏᴘᴇʀ ✰", callback_data="aboutDev"),
                 InlineKeyboardButton("ᴄʜᴀɴɴᴇʟ ⚠︎", url=mv_rockers)
            ],
                    [InlineKeyboardButton("☕ 𝖻𝗎𝗒 𝗆𝖾 𝖺 𝖼𝗈𝖿𝖿𝖾𝖾 ☕", url="https://www.buymeacoffee.com/nihaal")]
       
        ]
            )
        )

    
    elif data == "about":
        await query.message.edit_caption(
            caption=f"<b>Mʏ ɴᴀᴍᴇ :<a href='https://t.me/FileToSpeedLink_Bot'>{bot_name}</a>\nOᴡɴᴇʀ : <a href='https://t.me/TG_BotCreator'>Nɪʜᴀʟ</a>\nʜᴏsᴛᴇᴅ ᴏɴ : ʜᴇʀᴏᴋᴜ\nᴅᴀᴛᴀʙᴀsᴇ : ᴍᴏɴɢᴏ ᴅʙ\nʟᴀɴɢᴜᴀɢᴇ : ᴘʏᴛʜᴏɴ 3</b>",
            reply_markup=InlineKeyboardMarkup(
                [[ 
                     InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
                     InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")
                  ]]
            )
        )
    elif data == "help":
        await query.message.edit_caption(
        caption=f"𝚃𝚘 𝚄𝚜𝚎 𝙾𝚗 𝙲𝚑𝚊𝚗𝚗𝚎𝚕 𝙾𝚛 𝚄𝚜𝚎 𝙷𝚎𝚛𝚎...🤔 ?",
            reply_markup=InlineKeyboardMarkup(
[
    [InlineKeyboardButton("ᴜsᴇ ɪɴ ᴄʜᴀɴɴᴇʟ", callback_data="channeluse"),
     InlineKeyboardButton("ᴜsᴇ ʜᴇʀᴇ", callback_data="usehere")],
   
                     [InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
                     InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")]
]))
        
    elif data == "usehere":
        await query.message.edit_caption(
            caption=f"Hʏ ᴅᴇᴀʀ!!🤗\n\nSᴇɴᴅ ᴍᴇ ᴀ ᴠɪᴅᴇᴏ ғɪʟᴇ 🎥.\nᴀɴᴅ ɪ ᴡɪʟʟ ᴄʀᴇᴀᴛᴇ ᴀɴᴅ sᴇɴᴅ ᴡᴀᴛᴄʜ/ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ 🚀 ᴏғ ᴛʜᴀᴛ ᴠɪᴅᴇᴏ ғɪʟᴇ. 🤧",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("ʙᴀᴄᴋ ⌫", callback_data="help"),
                  InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")]]))

    elif data == "channeluse":
        await query.message.edit_caption(
            caption=f"Hʏ ᴅᴇᴀʀ!!✨\n\nᴀᴅᴅ ᴍᴇ ɪɴ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ ᴀs ᴀᴅᴍɪɴ.👤\n<blockquote>ᴍᴜsᴛ ᴡᴀɴᴛᴇᴅ ᴘᴇʀᴍɪssɪᴏɴ : ᴇᴅɪᴛ ᴍᴇssᴀɢᴇs ᴏғ ᴏᴛʜᴇʀs ✅</blockquote>\n\nɴᴏᴡ ᴘᴏsᴛ ᴀ ғɪʟᴇ ᴏɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ʏᴏᴜ ᴡɪʟʟ sᴇᴇ ᴍʏ ᴍᴀɢɪᴄ.🪄",
            reply_markup=InlineKeyboardMarkup(
             [
                 [InlineKeyboardButton("➕ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ➕", url="http://t.me/FileToSpeedLink_Bot?startchannel=true")],
                    
                  [InlineKeyboardButton("ʙᴀᴄᴋ ⌫", callback_data="help"),
                  InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")]
                
            ]
            )
        
        )

        
    elif data == "aboutDev":
        # please don't steal credit
        await query.message.edit_caption(
            caption=f"<b>Hy ᴅᴇᴀʀ 🥰, ᴍʏ ɴᴀᴍᴇ ɪs <a href='https://t.me/nihh_all'>ɴɪʜᴀʟ</a>. ɪ,ᴀᴍ ᴄʀᴇᴀᴛᴇᴅ ᴛʜɪᴤ ʙᴏᴛ</b>",
            reply_markup=InlineKeyboardMarkup(
                [
                    
               [InlineKeyboardButton("ɪɴsᴛᴀɢʀᴀᴍ", url="https://www.instagram.com/nihh____al"),
                InlineKeyboardButton("ɢɪᴛʜᴜʙ", url="https://github.com/darkhacker34")],
                    
                    [InlineKeyboardButton("ᴛᴇʟᴇɢʀᴀᴍ", url="https://t.me/nihh_all"),
                     InlineKeyboardButton("Support💲", url="https://t.me/TG_BotCreator")],
                
                [InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
     
                InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")]
        ]
            )
        )
    
    elif data.startswith("sendAlert"):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        if len(str(user_id)) == 10:
            reason = str(data.split("_")[2])
            try:
                await client.send_message(user_id , f'<b>ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ʙʏ ᴀᴅᴍɪɴ.\nRᴇᴀsᴏɴ : {reason}</b>')
                await query.message.edit(f"<b>Aʟᴇʀᴛ sᴇɴᴛ ᴛᴏ <code>{user_id}</code>\nRᴇᴀsᴏɴ : {reason}</b>")
            except Exception as e:
                await query.message.edit(f"<b>sʀʏ ɪ ɢᴏᴛ ᴛʜɪs ᴇʀʀᴏʀ : {e}</b>")
        else:
            await query.message.edit(f"<b>Tʜᴇ ᴘʀᴏᴄᴇss ᴡᴀs ɴᴏᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʙᴇᴄᴀᴜsᴇ ᴛʜᴇ ᴜsᴇʀ ɪᴅ ᴡᴀs ɴᴏᴛ ᴠᴀʟɪᴅ, ᴏʀ ᴘᴇʀʜᴀᴘs ɪᴛ ᴡᴀs ᴀ ᴄʜᴀɴɴᴇʟ ɪᴅ</b>")

    elif data.startswith('noAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        await query.message.edit(f"<b>Tʜᴇ ʙᴀɴ ᴏɴ <code>{user_id}</code> ᴡᴀs ᴇxᴇᴄᴜᴛᴇᴅ sɪʟᴇɴᴛʟʏ.</b>")

    elif data.startswith('sendUnbanAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        if len(str(user_id)) == 10:
            try:
                unban_text = '<b>ʜᴜʀʀᴀʏ..ʏᴏᴜ ᴀʀᴇ ᴜɴʙᴀɴɴᴇᴅ ʙʏ ᴀᴅᴍɪɴ.</b>'
                await client.send_message(user_id , unban_text)
                await query.message.edit(f"<b>Uɴʙᴀɴɴᴇᴅ Aʟᴇʀᴛ sᴇɴᴛ ᴛᴏ <code>{user_id}</code>\nᴀʟᴇʀᴛ ᴛᴇxᴛ : {unban_text}</b>")
            except Exception as e:
                await query.message.edit(f"<b>sʀʏ ɪ ɢᴏᴛ ᴛʜɪs ᴇʀʀᴏʀ : {e}</b>")
        else:
            await query.message.edit(f"<b>Tʜᴇ ᴘʀᴏᴄᴇss ᴡᴀs ɴᴏᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʙᴇᴄᴀᴜsᴇ ᴛʜᴇ ᴜsᴇʀ ɪᴅ ᴡᴀs ɴᴏᴛ ᴠᴀʟɪᴅ, ᴏʀ ᴘᴇʀʜᴀᴘs ɪᴛ ᴡᴀs ᴀ ᴄʜᴀɴɴᴇʟ ɪᴅ</b>")   
    elif data.startswith('NoUnbanAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        await query.message.edit(f"Tʜᴇ ᴜɴʙᴀɴ ᴏɴ <code>{user_id}</code> ᴡᴀs ᴇxᴇᴄᴜᴛᴇᴅ sɪʟᴇɴᴛʟʏ.")
