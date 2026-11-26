# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/routes.py>
# Thanks to Eyaadh <https://github.com/eyaadh>
# Thanks to adarsh-goel
# (c) @biisal
import re
import time
import math
import asyncio
import logging
import secrets
import mimetypes
from urllib.parse import quote_plus
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from pyrogram.errors import FloodWait
from biisal.bot import multi_clients, work_loads, StreamBot
from biisal.server.exceptions import FIleNotFound, InvalidHash
from biisal import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer, yield_striped_file
from ..utils.database import Database
from ..utils.file_properties import get_hash, get_name
from ..utils.video_thumb import build_local_stream_url, extract_frame_10min
from ..utils.video_transcode import TRANSCODE_PROFILES, stream_transcoded_video
from biisal.utils.render_template import render_page
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
    return {
        "msg_id": msg_id,
        "hash": secure_hash,
        "stream_code": stream_code,
        "title": row.get("title") or file_name,
        "file_name": file_name,
        "file_size": row.get("file_size", 0),
        "mime_type": row.get("mime_type") or "video/x-matroska",
        "tmdb_id": row.get("tmdb_id"),
        "created_at": row.get("created_at"),
        "stream_url": f"{base_url}{msg_id}/{quoted_name}?hash={secure_hash}",
        "watch_url": f"{base_url}watch/{msg_id}/{quoted_name}?hash={secure_hash}",
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


@routes.get(r"/transcode/{msg_id:\d+}/{quality:\d+p?}", allow_head=True)
async def transcode_video_handler(request: web.Request):
    try:
        msg_id = int(request.match_info["msg_id"])
        raw_quality = request.match_info["quality"].lower().rstrip("p")
        target_height = int(raw_quality)
        if target_height not in TRANSCODE_PROFILES:
            return web.Response(status=400, text="Unsupported quality profile")

        secure_hash = (request.rel_url.query.get("hash") or "").strip()
        ss_raw = (request.rel_url.query.get("ss") or "0").strip()
        start_seconds = int(ss_raw) if ss_raw.isdigit() else 0

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
                    "Content-Type": "video/x-matroska",
                    "Accept-Ranges": "none",
                    "Cache-Control": "no-store",
                },
            )

        file_name = file_id.file_name or f"video_{msg_id}.mkv"
        return await stream_transcoded_video(
            request=request,
            msg_id=msg_id,
            file_name=file_name,
            secure_hash=secure_hash,
            target_height=target_height,
            start_seconds=start_seconds,
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
        rows = await _video_db.get_videos(
            query_text=query_text,
            tmdb_id=tmdb_id,
            limit=limit,
            skip=offset,
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
        base_url = _resolve_base_url(request)
        return web.json_response({"ok": True, "video": _format_video_row(row, base_url)})
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


async def _run_broadcast_job(job_id: str, text: str, user_ids: list):
    job = _broadcast_jobs.get(job_id)
    if not job:
        return
    try:
        for uid in user_ids:
            try:
                await StreamBot.send_message(chat_id=uid, text=text)
                job["sent"] += 1
            except FloodWait as fw:
                await asyncio.sleep(min(int(fw.value), 30))
                try:
                    await StreamBot.send_message(chat_id=uid, text=text)
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


@routes.post("/api/admin/broadcast")
async def admin_broadcast_handler(request: web.Request):
    if not _is_owner_request(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403)
    try:
        payload = await request.json()
        text = str(payload.get("message") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Message cannot be empty"}, status=400)

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
            asyncio.create_task(_run_broadcast_job(job_id, text, user_ids))

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


@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        return web.Response(text=await render_page(id, secure_hash), content_type='text/html')
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
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
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

    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "attachment"

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

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )
