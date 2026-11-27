# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/routes.py>
# Thanks to Eyaadh <https://github.com/eyaadh>
# Thanks to adarsh-goel
# (c) @biisal
import re
import time
import math
import asyncio
import contextlib
import logging
import secrets
import mimetypes
from urllib.parse import quote_plus
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from pyrogram import enums
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from biisal.bot import multi_clients, work_loads, StreamBot
from biisal.server.exceptions import FIleNotFound, InvalidHash
from biisal import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer, yield_striped_file
from ..utils.database import Database
from ..utils.file_properties import get_hash, get_name
from ..utils.video_thumb import (
    build_local_stream_url,
    extract_frame_10min,
    probe_audio_tracks,
    probe_duration_seconds,
)
from ..utils.video_transcode import TRANSCODE_PROFILES, stream_transcoded_video
from biisal.utils.render_template import (
    get_cached_duration,
    render_page,
    set_cached_duration,
)
from biisal.vars import Var


routes = web.RouteTableDef()
_video_db = Database(Var.DATABASE_URL, Var.name)


def _resolve_base_url(request: web.Request) -> str:
    if Var.FQDN and Var.FQDN != "BIND_ADRESS:PORT":
        base = Var.URL
    else:
        base = f"{request.scheme}://{request.host}/"
    return base if base.endswith("/") else f"{base}/"

@routes.get("/", allow_head=True)
async def root_route_handler(_):
    return web.json_response(
        {
            "server_status": "running",
            "uptime": get_readable_time(time.time() - StartTime),
            "telegram_bot": "@" + StreamBot.username,
            "connected_bots": len(multi_clients),
            "loads": dict(
                ("bot" + str(c + 1), l)
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            ),
            "version": __version__,
        }
    )


def _format_video_row(row: dict, base_url: str) -> dict:
    msg_id = row.get("msg_id")
    secure_hash = row.get("hash") or ""
    file_name = row.get("file_name") or f"video_{msg_id}.mkv"
    quoted_name = quote_plus(str(file_name))
    stream_code = row.get("stream_code") or (
        f"MRDB-{msg_id}-{secure_hash.upper()}" if msg_id is not None and secure_hash else ""
    )
    disabled_audio = [
        int(x)
        for x in (row.get("disabled_audio_indices") or [])
        if isinstance(x, int) or str(x).isdigit()
    ]
    metadata_edited = bool(row.get("metadata_edited"))
    edited_title = (row.get("title") or "").strip()
    display_title = edited_title if (metadata_edited and edited_title) else file_name
    return {
        "msg_id": msg_id,
        "hash": secure_hash,
        "stream_code": stream_code,
        "title": display_title,
        "file_name": file_name,
        "file_size": row.get("file_size", 0),
        "mime_type": row.get("mime_type") or "video/x-matroska",
        "tmdb_id": row.get("tmdb_id"),
        "tmdb_title": row.get("tmdb_title"),
        "tmdb_poster": row.get("tmdb_poster"),
        "tmdb_year": row.get("tmdb_year"),
        "year": row.get("year"),
        "quality": row.get("quality"),
        "language": row.get("language"),
        "overview": row.get("overview"),
        "metadata_edited": metadata_edited,
        "hidden_for_users": bool(row.get("hidden_for_users")),
        "disabled_audio_indices": disabled_audio,
        "audio_tracks": row.get("audio_tracks") or [],
        "created_at": row.get("created_at"),
        "stream_url": f"{base_url}{secure_hash}{msg_id}" if secure_hash else f"{base_url}{msg_id}/{quoted_name}",
        "watch_url": f"{base_url}watch/{secure_hash}{msg_id}" if secure_hash else f"{base_url}watch/{msg_id}/{quoted_name}",
        "thumb_url": f"{base_url}api/thumb/{msg_id}" if msg_id is not None else None,
    }


_thumb_cache = {}
_thumb_inflight = {}


async def _extract_and_cache_thumb(msg_id: int):
    row = await _video_db.videos.find_one(
        {"msg_id": msg_id}, {"_id": 0, "file_name": 1, "hash": 1}
    )
    file_name = row.get("file_name") if row else None
    file_hash = row.get("hash") if row else None

    if not file_hash:
        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        message = await faster_client.get_messages(Var.BIN_CHANNEL, msg_id)
        if message and not message.empty:
            file_name = file_name or get_name(message) or f"video_{msg_id}.mkv"
            file_hash = get_hash(message)

    if not file_hash:
        return None

    local_url = build_local_stream_url(
        msg_id, file_name or f"video_{msg_id}.mkv", file_hash
    )
    bytes_data = await extract_frame_10min(local_url)
    if bytes_data:
        await _video_db.save_thumbnail(msg_id, bytes_data)
    return bytes_data


@routes.get(r"/api/thumb/{id:\d+}", allow_head=True)
@routes.get(r"/api/thumb/{id:\d+}.jpg", allow_head=True)
@routes.get(r"/{id:\d+}/thumb.jpg", allow_head=True)
async def get_thumbnail_handler(request: web.Request):
    msg_id = int(request.match_info["id"])
    if msg_id in _thumb_cache:
        return web.Response(
            body=_thumb_cache[msg_id],
            content_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )
    try:
        bytes_data = await _video_db.get_thumbnail(msg_id)
        if not bytes_data:
            task = _thumb_inflight.get(msg_id)
            if task is None:
                task = asyncio.create_task(_extract_and_cache_thumb(msg_id))
                _thumb_inflight[msg_id] = task
            try:
                bytes_data = await task
            finally:
                if _thumb_inflight.get(msg_id) is task:
                    _thumb_inflight.pop(msg_id, None)

        if not bytes_data:
            return web.Response(status=404, text="No video frame available")

        if len(_thumb_cache) > 500:
            _thumb_cache.clear()
        _thumb_cache[msg_id] = bytes_data

        return web.Response(
            body=bytes_data,
            content_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )
    except Exception as e:
        logging.error(f"Thumbnail error for {msg_id}: {e}")
        return web.Response(status=500, text=str(e))


@routes.get(r"/api/duration/{id:\d+}", allow_head=True)
async def get_duration_handler(request: web.Request):
    msg_id = int(request.match_info["id"])
    cached = get_cached_duration(msg_id)
    if cached > 0:
        return web.json_response({"ok": True, "duration": cached})
    try:
        db_dur = await _video_db.get_video_duration(msg_id)
        if db_dur > 0:
            set_cached_duration(msg_id, db_dur)
            return web.json_response({"ok": True, "duration": db_dur})

        row = await _video_db.videos.find_one(
            {"msg_id": msg_id}, {"_id": 0, "file_name": 1, "hash": 1}
        )
        file_name = row.get("file_name") if row else None
        file_hash = (row.get("hash") if row else None) or (
            request.rel_url.query.get("hash") or ""
        ).strip()
        if not file_hash:
            index = min(work_loads, key=work_loads.get)
            faster_client = multi_clients[index]
            message = await faster_client.get_messages(Var.BIN_CHANNEL, msg_id)
            if message and not message.empty:
                file_name = file_name or get_name(message) or f"video_{msg_id}.mkv"
                file_hash = get_hash(message)
        if not file_hash:
            return web.json_response({"ok": False, "duration": 0})

        local_url = build_local_stream_url(
            msg_id, file_name or f"video_{msg_id}.mkv", file_hash
        )
        dur = await probe_duration_seconds(local_url, timeout=12.0)
        if dur > 0:
            set_cached_duration(msg_id, dur)
            await _video_db.save_video_duration(msg_id, dur)
        return web.json_response({"ok": dur > 0, "duration": dur})
    except Exception as e:
        logging.debug(f"Duration API error for {msg_id}: {e}")
        return web.json_response({"ok": False, "duration": 0})


@routes.get(r"/transcode/{msg_id:\d+}/{quality:(?:remux|\d+p?)}", allow_head=True)
async def transcode_video_handler(request: web.Request):
    try:
        msg_id = int(request.match_info["msg_id"])
        raw_quality = request.match_info["quality"].lower().rstrip("p")
        if raw_quality == "remux":
            target_height = 0
        else:
            target_height = int(raw_quality)
            if target_height not in TRANSCODE_PROFILES:
                return web.Response(status=400, text="Unsupported quality profile")

        secure_hash = (request.rel_url.query.get("hash") or "").strip()
        ss_raw = (request.rel_url.query.get("ss") or "0").strip()
        start_seconds = int(ss_raw) if ss_raw.isdigit() else 0
        fmt = (request.rel_url.query.get("fmt") or "matroska").strip().lower()
        if fmt not in ("mp4", "matroska"):
            fmt = "matroska"

        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        tg_connect = class_cache.get(faster_client)
        if tg_connect is None:
            tg_connect = ByteStreamer(faster_client)
            class_cache[faster_client] = tg_connect

        file_id = await tg_connect.get_file_properties(msg_id)
        if not secure_hash or file_id.unique_id[:6] != secure_hash:
            return web.Response(status=403, text="Invalid hash")

        if request.method.upper() == "HEAD":
            return web.Response(
                status=200,
                headers={
                    "Content-Type": "video/mp4" if fmt == "mp4" else "video/x-matroska",
                    "Accept-Ranges": "none",
                    "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                },
            )

        file_name = file_id.file_name or f"video_{msg_id}.mkv"
        audio_stream_index = 0
        video_codec = ""
        audio_codec = ""
        audio_channels = ""
        try:
            row = await _video_db.videos.find_one(
                {"msg_id": msg_id},
                {
                    "_id": 0,
                    "disabled_audio_indices": 1,
                    "audio_tracks": 1,
                    "video_codec": 1,
                },
            )
            if row:
                video_codec = str(row.get("video_codec") or "").strip().lower()
            disabled = {
                int(x)
                for x in ((row or {}).get("disabled_audio_indices") or [])
                if isinstance(x, int) or str(x).isdigit()
            }
            tracks = (row or {}).get("audio_tracks") or []
            if tracks:
                default_idx = next(
                    (
                        int(t.get("index", i))
                        for i, t in enumerate(tracks)
                        if t.get("is_default")
                        and int(t.get("index", i)) not in disabled
                    ),
                    None,
                )
                if default_idx is not None:
                    audio_stream_index = default_idx
                elif disabled:
                    for candidate in range(len(tracks)):
                        if candidate not in disabled:
                            audio_stream_index = candidate
                            break
                for i, t in enumerate(tracks):
                    if int(t.get("index", i)) == audio_stream_index:
                        audio_codec = str(t.get("codec") or "").strip().lower()
                        audio_channels = str(t.get("channels") or "").strip().lower()
                        break
            elif disabled:
                for candidate in range(8):
                    if candidate not in disabled:
                        audio_stream_index = candidate
                        break
        except Exception:
            audio_stream_index = 0
        if target_height == 0 and not video_codec:
            lower_name = file_name.lower()
            if any(tok in lower_name for tok in ("hevc", "h265", "h.265", "x265")):
                video_codec = "hevc"
        return await stream_transcoded_video(
            request=request,
            msg_id=msg_id,
            file_name=file_name,
            secure_hash=secure_hash,
            target_height=target_height,
            start_seconds=start_seconds,
            output_format=fmt,
            audio_stream_index=audio_stream_index,
            video_codec=video_codec,
            audio_codec=audio_codec,
            audio_channels=audio_channels,
        )
    except FIleNotFound as e:
        return web.Response(status=404, text=e.message)
    except (
        AttributeError,
        BadStatusLine,
        ConnectionResetError,
        BrokenPipeError,
        ConnectionAbortedError,
    ):
        return web.Response(status=499)
    except Exception as e:
        logging.error(f"Transcode handler error: {e}")
        return web.Response(status=500, text=str(e))


@routes.get("/api/videos", allow_head=True)
async def list_videos_handler(request: web.Request):
    try:
        query_text = request.rel_url.query.get("q")
        tmdb_raw = request.rel_url.query.get("tmdb_id")
        tmdb_id = int(tmdb_raw) if tmdb_raw and tmdb_raw.isdigit() else None
        limit_raw = request.rel_url.query.get("limit", "20")
        limit = int(limit_raw) if limit_raw.isdigit() else 20
        offset_raw = (
            request.rel_url.query.get("offset")
            or request.rel_url.query.get("skip")
            or "0"
        )
        offset = int(offset_raw) if offset_raw.isdigit() else 0
        include_hidden = _is_owner_request(request) and (
            (request.rel_url.query.get("include_hidden") or "").strip().lower()
            in ("1", "true", "yes")
        )
        rows = await _video_db.get_videos(
            query_text=query_text,
            tmdb_id=tmdb_id,
            limit=limit,
            skip=offset,
            include_hidden=include_hidden,
        )
        base_url = _resolve_base_url(request)

        items = [
            _format_video_row(row, base_url)
            for row in rows
            if row.get("msg_id") is not None and row.get("hash")
        ]
        clamped_limit = max(1, min(limit, 100))
        return web.json_response(
            {
                "ok": True,
                "count": len(items),
                "has_more": len(rows) >= clamped_limit,
                "videos": items,
            }
        )
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.delete(r"/api/videos/{identifier:\S+}")
async def delete_video_handler(request: web.Request):
    try:
        identifier = request.match_info.get("identifier", "").strip()
        if not identifier:
            return web.json_response({"ok": False, "error": "Missing identifier"}, status=400)
        msg_id = int(identifier) if identifier.isdigit() else None
        deleted = await _video_db.delete_video(
            msg_id=msg_id,
            stream_code=None if msg_id is not None else identifier,
        )
        if not deleted and msg_id is None:
            row = await _video_db.get_video_by_code(identifier)
            if row and row.get("msg_id") is not None:
                deleted = await _video_db.delete_video(msg_id=int(row["msg_id"]))
        return web.json_response({"ok": True, "deleted": bool(deleted)})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.get(r"/api/code/{code:\S+}", allow_head=True)
async def resolve_code_handler(request: web.Request):
    try:
        code = request.match_info.get("code", "").strip()
        row = await _video_db.get_video_by_code(code)
        if not row or row.get("msg_id") is None or not row.get("hash"):
            return web.json_response(
                {"ok": False, "error": "Invalid or unknown stream code"},
                status=404,
            )
        if row.get("hidden_for_users") and not _is_owner_request(request):
            return web.json_response(
                {"ok": False, "error": "This stream is currently private"},
                status=404,
            )
        base_url = _resolve_base_url(request)
        return web.json_response({"ok": True, "video": _format_video_row(row, base_url)})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.get(r"/api/videos/{identifier:\S+}/tracks", allow_head=True)
async def get_video_tracks_handler(request: web.Request):
    try:
        identifier = request.match_info.get("identifier", "").strip()
        row = await _video_db.get_video_by_identifier(identifier)
        if not row or row.get("msg_id") is None or not row.get("hash"):
            return web.json_response({"ok": False, "error": "Video not found"}, status=404)

        msg_id = int(row["msg_id"])
        force_refresh = (request.rel_url.query.get("refresh") or "").strip() in ("1", "true")
        tracks = row.get("audio_tracks") or []
        if force_refresh or not tracks:
            local_url = build_local_stream_url(
                msg_id,
                row.get("file_name") or f"video_{msg_id}.mkv",
                row["hash"],
            )
            probed = await probe_audio_tracks(local_url, timeout=20.0)
            if probed:
                tracks = probed
                await _video_db.save_audio_tracks(msg_id, tracks)

        disabled = [
            int(x)
            for x in (row.get("disabled_audio_indices") or [])
            if isinstance(x, int) or str(x).isdigit()
        ]
        return web.json_response(
            {
                "ok": True,
                "audio_tracks": tracks,
                "disabled_audio_indices": disabled,
            }
        )
    except Exception as e:
        logging.error(f"Audio track inspect error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post(r"/api/admin/videos/{identifier:\S+}/ai-clean")
async def admin_ai_clean_video_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        identifier = request.match_info.get("identifier", "").strip()
        if not identifier:
            return web.json_response({"ok": False, "error": "Missing identifier"}, status=400)
        row = await _video_db.get_video_by_identifier(identifier)
        if not row or row.get("msg_id") is None:
            return web.json_response({"ok": False, "error": "Video not found"}, status=404)
        payload = {}
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            pass

        from biisal.utils.ai_cleaner import auto_clean_filename_with_ai

        msg_id = int(row["msg_id"])
        file_hash = str(row.get("hash") or "").strip()
        file_name = str(payload.get("file_name") or row.get("file_name") or "").strip()
        result = await auto_clean_filename_with_ai(
            file_name,
            msg_id=msg_id,
            file_hash=file_hash,
            existing_title=payload.get("existing_title"),
            gemini_api_key_override=payload.get("gemini_api_key"),
            prompt_preset=payload.get("prompt_preset"),
            tmdb_api_key=payload.get("tmdb_api_key"),
            caption=payload.get("caption") or row.get("caption"),
        )
        if not result:
            return web.json_response({"ok": False, "error": "AI clean failed"}, status=502)
        return web.json_response({"ok": True, "suggestion": result})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.patch(r"/api/admin/videos/{identifier:\S+}")
@routes.post(r"/api/admin/videos/{identifier:\S+}")
async def admin_update_video_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        identifier = request.match_info.get("identifier", "").strip()
        if not identifier:
            return web.json_response({"ok": False, "error": "Missing identifier"}, status=400)
        payload = await request.json()
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)
        updated = await _video_db.update_video_metadata(identifier, payload)
        if not updated:
            return web.json_response({"ok": False, "error": "Video not found"}, status=404)
        base_url = _resolve_base_url(request)
        return web.json_response({"ok": True, "video": _format_video_row(updated, base_url)})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def _is_owner_request(request: web.Request) -> bool:
    provided = (request.headers.get("X-Owner-Key") or "").strip()
    expected = (getattr(Var, "OWNER_ADMIN_KEY", "") or "").strip()
    return bool(expected and provided == expected)


@routes.get("/api/admin/stats", allow_head=True)
async def admin_stats_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        total_users, banned_users, total_videos = await asyncio.gather(
            _video_db.total_users_count(),
            _video_db.total_banned_count(),
            _video_db.total_videos_count(),
        )
        return web.json_response(
            {
                "ok": True,
                "total_users": total_users,
                "banned_users": banned_users,
                "total_videos": total_videos,
                "uptime": get_readable_time(time.time() - StartTime),
                "telegram_bot": "@" + StreamBot.username,
                "connected_bots": len(multi_clients),
                "loads": dict(
                    ("bot" + str(c + 1), l)
                    for c, (_, l) in enumerate(
                        sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                    )
                ),
            }
        )
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.get("/api/admin/users", allow_head=True)
async def admin_users_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        query_text = request.rel_url.query.get("q")
        limit_raw = request.rel_url.query.get("limit", "30")
        limit = int(limit_raw) if limit_raw.isdigit() else 30
        offset_raw = request.rel_url.query.get("offset", "0")
        offset = int(offset_raw) if offset_raw.isdigit() else 0

        rows, banned_ids, total_users = await asyncio.gather(
            _video_db.get_users_paginated(
                query_text=query_text,
                limit=limit,
                skip=offset,
            ),
            _video_db.get_banned_ids(),
            _video_db.total_users_count(),
        )

        items = [
            {
                "id": int(r["id"]),
                "name": r.get("name") or "",
                "username": r.get("username") or "",
                "join_date": r.get("join_date") or "",
                "is_banned": int(r["id"]) in banned_ids,
            }
            for r in rows
            if r.get("id") is not None
        ]
        clamped_limit = max(1, min(limit, 100))
        return web.json_response(
            {
                "ok": True,
                "total_users": total_users,
                "count": len(items),
                "has_more": len(rows) >= clamped_limit,
                "users": items,
            }
        )
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post(r"/api/admin/users/{user_id:\d+}/ban")
async def admin_ban_user_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        user_id = int(request.match_info["user_id"])
        banned = await _video_db.ban_user(user_id)
        return web.json_response({"ok": True, "user_id": user_id, "banned": bool(banned)})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.delete(r"/api/admin/users/{user_id:\d+}/ban")
async def admin_unban_user_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        user_id = int(request.match_info["user_id"])
        unbanned = await _video_db.is_unbanned(user_id)
        return web.json_response({"ok": True, "user_id": user_id, "unbanned": bool(unbanned)})
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


_broadcast_jobs = {}


async def _run_broadcast_job(
    job_id: str,
    text: str,
    user_ids: list,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: enums.ParseMode = enums.ParseMode.MARKDOWN,
):
    job = _broadcast_jobs.get(job_id)
    if not job:
        return
    try:
        for uid in user_ids:
            try:
                await StreamBot.send_message(
                    chat_id=uid,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                job["sent"] += 1
            except FloodWait as fw:
                await asyncio.sleep(min(int(fw.value), 30))
                try:
                    await StreamBot.send_message(
                        chat_id=uid,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                    )
                    job["sent"] += 1
                except Exception:
                    job["failed"] += 1
            except Exception:
                job["failed"] += 1
            await asyncio.sleep(0.08)
    except Exception as e:
        logging.error(f"Broadcast job {job_id} error: {e}")
    finally:
        job["done"] = True


def _parse_broadcast_markup(payload: dict):
    raw_buttons = payload.get("buttons")
    candidates = []
    if isinstance(raw_buttons, list):
        candidates.extend(raw_buttons)
    elif payload.get("button_text") and payload.get("button_url"):
        candidates.append(
            {
                "text": payload.get("button_text"),
                "url": payload.get("button_url"),
            }
        )
    grouped_rows = []
    row_index_map = {}
    for entry in candidates[:12]:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("text") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not label or not url:
            continue
        low_url = url.lower()
        if not (
            low_url.startswith("https://")
            or low_url.startswith("http://")
            or low_url.startswith("tg://")
        ):
            continue
        btn = InlineKeyboardButton(label[:64], url=url)
        raw_row = entry.get("row")
        if isinstance(raw_row, int) or (isinstance(raw_row, str) and raw_row.isdigit()):
            row_key = int(raw_row)
            if row_key not in row_index_map:
                row_index_map[row_key] = len(grouped_rows)
                grouped_rows.append([])
            target_row = grouped_rows[row_index_map[row_key]]
            if len(target_row) < 4:
                target_row.append(btn)
            else:
                grouped_rows.append([btn])
        elif entry.get("same_row") and grouped_rows and len(grouped_rows[-1]) < 4:
            grouped_rows[-1].append(btn)
        else:
            grouped_rows.append([btn])
    return InlineKeyboardMarkup(grouped_rows) if grouped_rows else None


@routes.post("/api/admin/broadcast")
async def admin_broadcast_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        payload = await request.json()
        text = str(payload.get("message") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Message cannot be empty"}, status=400)
        reply_markup = _parse_broadcast_markup(payload) if isinstance(payload, dict) else None
        raw_mode = str((payload.get("parse_mode") if isinstance(payload, dict) else "") or "markdown").strip().lower()
        parse_mode = (
            enums.ParseMode.DISABLED
            if raw_mode in ("plain", "none", "disabled")
            else enums.ParseMode.MARKDOWN
        )

        users_cursor = await _video_db.get_all_users()
        user_ids = []
        async for user in users_cursor:
            uid = user.get("id")
            if uid is not None:
                user_ids.append(int(uid))

        job_id = secrets.token_hex(6)
        _broadcast_jobs[job_id] = {
            "total": len(user_ids),
            "sent": 0,
            "failed": 0,
            "done": len(user_ids) == 0,
        }
        if user_ids:
            asyncio.create_task(
                _run_broadcast_job(
                    job_id,
                    text,
                    user_ids,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            )

        return web.json_response(
            {
                "ok": True,
                "started": True,
                "job_id": job_id,
                "total": len(user_ids),
                "sent": 0,
                "failed": 0,
                "done": len(user_ids) == 0,
            }
        )
    except Exception as e:
        logging.critical(e.with_traceback(None))
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.get(r"/api/admin/broadcast/{job_id:\S+}", allow_head=True)
async def admin_broadcast_status_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    job_id = request.match_info.get("job_id", "").strip()
    job = _broadcast_jobs.get(job_id)
    if not job:
        return web.json_response({"ok": False, "error": "Broadcast job not found"}, status=404)
    return web.json_response({"ok": True, "job_id": job_id, **job})


def _parse_stream_path_and_hash(request: web.Request, path: str):
    clean_path = (path or "").strip().lstrip("/")
    query_hash = (request.rel_url.query.get("hash") or "").strip()
    if query_hash:
        m_num = re.match(r"^(\d+)(?:/\S+)?$", clean_path) or re.search(
            r"(\d+)(?:/\S+)?", clean_path
        )
        if not m_num:
            raise AttributeError("Missing message id")
        return int(m_num.group(1)), query_hash
    match = re.match(r"^([a-zA-Z0-9_-]{6})(\d+)(?:/\S+)?$", clean_path)
    if match and not clean_path.isdigit():
        return int(match.group(2)), match.group(1)
    m_num = re.search(r"(\d+)(?:/\S+)?", clean_path)
    if not m_num:
        raise AttributeError("Missing message id")
    return int(m_num.group(1)), None


@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_stream_handler(request: web.Request):
    try:
        id, secure_hash = _parse_stream_path_and_hash(
            request, request.match_info["path"]
        )
        return web.Response(
            text=await render_page(id, secure_hash),
            content_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
                "Pragma": "no-cache",
            },
        )
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        id, secure_hash = _parse_stream_path_and_hash(
            request, request.match_info["path"]
        )
        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

class_cache = {}


def _get_streamer(client) -> ByteStreamer:
    streamer = class_cache.get(client)
    if streamer is None:
        streamer = ByteStreamer(client)
        class_cache[client] = streamer
    return streamer


async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)

    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if Var.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    tg_connect = _get_streamer(faster_client)
    logging.debug("before calling get_file_properties")
    file_id = await tg_connect.get_file_properties(id)
    logging.debug("after calling get_file_properties")

    if file_id.unique_id[:6] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash

    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    is_head = request.method.upper() == "HEAD"
    is_download = (
        (request.rel_url.query.get("dl") or request.rel_url.query.get("download") or "")
        .strip()
        .lower()
        in ("1", "true", "yes")
    )

    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "attachment" if is_download else "inline"

    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            guessed_mime, _ = mimetypes.guess_type(file_name)
            if not guessed_mime and file_name.lower().endswith(".mkv"):
                mime_type = "video/x-matroska"
            else:
                mime_type = guessed_mime or "application/octet-stream"
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    headers = {
        "Content-Type": f"{mime_type}",
        "Content-Length": str(req_length),
        "Content-Disposition": f'{disposition}; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Vary": "Range",
        "X-Accel-Buffering": "no",
    }
    if range_header:
        headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

    if is_head:
        return web.Response(
            status=206 if range_header else 200,
            headers=headers,
        )

    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    if len(multi_clients) > 1 and part_count >= 3:
        streamers_by_index = {
            idx: _get_streamer(client)
            for idx, client in multi_clients.items()
            if client is not None
        }
        body = yield_striped_file(
            msg_id=id,
            primary_streamer=tg_connect,
            primary_index=index,
            primary_file_id=file_id,
            streamers_by_index=streamers_by_index,
            offset=offset,
            first_part_cut=first_part_cut,
            last_part_cut=last_part_cut,
            part_count=part_count,
            chunk_size=chunk_size,
        )
    else:
        body = tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
        )

    resp = web.StreamResponse(
        status=206 if range_header else 200,
        headers=headers,
    )
    await resp.prepare(request)
    try:
        async for chunk in body:
            if request.transport is None or request.transport.is_closing():
                break
            if chunk:
                await resp.write(chunk)
        if request.transport is not None and not request.transport.is_closing():
            await resp.write_eof()
    except (
        asyncio.CancelledError,
        ConnectionResetError,
        BrokenPipeError,
        ConnectionAbortedError,
    ):
        pass
    finally:
        with contextlib.suppress(Exception):
            await body.aclose()
    return resp
