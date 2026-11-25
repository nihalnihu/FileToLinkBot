# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/routes.py>
# Thanks to Eyaadh <https://github.com/eyaadh>
# Thanks to adarsh-goel
# (c) @biisal
import re
import time
import math
import logging
import secrets
import mimetypes
from urllib.parse import quote_plus
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from biisal.bot import multi_clients, work_loads, StreamBot
from biisal.server.exceptions import FIleNotFound, InvalidHash
from biisal import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer
from ..utils.database import Database
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
    }


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
    except (AttributeError, BadStatusLine, ConnectionResetError):
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
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

class_cache = {}

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)
    
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]
    
    if Var.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logging.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logging.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
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
