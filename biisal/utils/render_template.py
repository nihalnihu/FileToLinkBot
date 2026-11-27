from biisal.vars import Var
from biisal.bot import StreamBot
from biisal.utils.database import Database
from biisal.utils.human_readable import humanbytes
from biisal.utils.file_properties import get_file_ids
from biisal.utils.video_thumb import build_local_stream_url, probe_media_summary
from biisal.server.exceptions import InvalidHash
import urllib.parse
import logging
import aiohttp
import jinja2

_duration_cache = {}
_media_codec_cache = {}
_render_db = Database(Var.DATABASE_URL, Var.name)


def get_cached_duration(msg_id: int) -> int:
    return _duration_cache.get(int(msg_id), 0)


def set_cached_duration(msg_id: int, duration_seconds: int) -> None:
    dur = max(0, int(duration_seconds or 0))
    if dur <= 0:
        return
    if len(_duration_cache) > 500:
        _duration_cache.clear()
    _duration_cache[int(msg_id)] = dur


def _infer_video_codec(file_name: str, probed_codec: str = "") -> str:
    clean = (probed_codec or "").strip().lower()
    if clean:
        if clean in ("h265", "h.265", "hev1", "hvc1"):
            return "hevc"
        if clean in ("avc", "avc1"):
            return "h264"
        return clean
    lower = (file_name or "").lower()
    if any(tok in lower for tok in ("hevc", "h265", "h.265", "x265")):
        return "hevc"
    if any(tok in lower for tok in ("h264", "h.264", "x264", "avc")):
        return "h264"
    return ""


def _needs_browser_transcode(
    file_name: str,
    mime_type: str,
    *,
    audio_tracks=None,
) -> bool:
    lower_name = (file_name or "").lower()
    lower_mime = (mime_type or "").lower()
    if lower_name.endswith((".mkv", ".avi", ".ts", ".flv", ".wmv")):
        return True
    if "matroska" in lower_mime or "x-msvideo" in lower_mime:
        return True
    if audio_tracks:
        codecs = {
            str(t.get("codec") or "").strip().lower()
            for t in audio_tracks
            if isinstance(t, dict)
        }
        if codecs and not (codecs & {"aac", "mp3", "opus", "vorbis", "flac"}):
            return True
    # Common browser-incompatible codecs often packaged even inside .mp4/.mkv
    for token in ("hevc", "h265", "h.265", "x265", "ddp", "dd5.1", "eac3", "ac3", "dts"):
        if token in lower_name:
            return True
    return False


async def render_page(id, secure_hash, src=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(id))
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message with - ID {id}")
        raise InvalidHash

    rel_stream = f"/{secure_hash}{id}"
    abs_src = urllib.parse.urljoin(Var.URL, f"{secure_hash}{id}")

    tag = file_data.mime_type.split("/")[0].strip()
    file_size = humanbytes(file_data.file_size)
    raw_file_name = file_data.file_name or f"video_{id}.mkv"
    display_title = raw_file_name.replace("_", " ")
    db_row = None
    try:
        db_row = await _render_db.videos.find_one(
            {"msg_id": int(id)},
            {
                "_id": 0,
                "title": 1,
                "metadata_edited": 1,
                "duration": 1,
                "video_codec": 1,
                "audio_tracks": 1,
            },
        )
        if db_row and db_row.get("metadata_edited") and (db_row.get("title") or "").strip():
            display_title = str(db_row["title"]).strip()
    except Exception:
        pass

    msg_key = int(id)
    duration_seconds = int(getattr(file_data, "duration", 0) or 0)
    video_codec = _infer_video_codec(
        raw_file_name,
        (db_row or {}).get("video_codec") or _media_codec_cache.get(msg_key, ""),
    )
    audio_tracks = (db_row or {}).get("audio_tracks") or []

    if tag in ["video", "audio"]:
        template_file = "biisal/template/req.html"
        if duration_seconds <= 0:
            duration_seconds = get_cached_duration(msg_key)
        if duration_seconds <= 0 and db_row and db_row.get("duration"):
            try:
                duration_seconds = max(0, int(db_row["duration"]))
            except (TypeError, ValueError):
                duration_seconds = 0
        if duration_seconds <= 0 or not video_codec:
            local_url = build_local_stream_url(msg_key, raw_file_name, secure_hash)
            summary = await probe_media_summary(local_url, timeout=4.0)
            probed_dur = int(summary.get("duration") or 0)
            probed_vcodec = _infer_video_codec(
                raw_file_name, str(summary.get("video_codec") or "")
            )
            probed_tracks = summary.get("audio_tracks") or []
            if duration_seconds <= 0 and probed_dur > 0:
                duration_seconds = probed_dur
            if probed_vcodec:
                video_codec = probed_vcodec
                if len(_media_codec_cache) > 500:
                    _media_codec_cache.clear()
                _media_codec_cache[msg_key] = probed_vcodec
            update_doc = {}
            if duration_seconds > 0:
                update_doc["duration"] = duration_seconds
            if probed_vcodec:
                update_doc["video_codec"] = probed_vcodec
            if probed_tracks and not audio_tracks:
                audio_tracks = probed_tracks
                update_doc["audio_tracks"] = probed_tracks
            if update_doc:
                try:
                    await _render_db.videos.update_one(
                        {"msg_id": msg_key}, {"$set": update_doc}
                    )
                except Exception:
                    pass
        if duration_seconds > 0:
            set_cached_duration(msg_key, duration_seconds)
    else:
        template_file = "biisal/template/dl.html"
        async with aiohttp.ClientSession() as s:
            async with s.get(abs_src) as u:
                content_len = u.headers.get("Content-Length")
                if content_len and content_len.isdigit():
                    file_size = humanbytes(int(content_len))

    with open(template_file, encoding="utf-8") as f:
        template = jinja2.Template(f.read())

    file_name = display_title
    stream_code = f"MRDB-{id}-{secure_hash.upper()}"
    prefer_transcode = _needs_browser_transcode(
        raw_file_name, file_data.mime_type, audio_tracks=audio_tracks
    )

    return template.render(
        msg_id=id,
        secure_hash=secure_hash,
        stream_code=stream_code,
        file_name=file_name,
        raw_file_name=raw_file_name,
        file_url=rel_stream,
        download_url=f"{rel_stream}?dl=1",
        remux_url=f"/transcode/{id}/remux?hash={secure_hash}&fmt=mp4",
        transcode_720_url=f"/transcode/{id}/720p?hash={secure_hash}&fmt=mp4",
        transcode_480_url=f"/transcode/{id}/480p?hash={secure_hash}&fmt=mp4",
        transcode_360_url=f"/transcode/{id}/360p?hash={secure_hash}&fmt=mp4",
        thumb_url=f"/api/thumb/{id}",
        prefer_transcode=prefer_transcode,
        video_codec=video_codec,
        duration_seconds=duration_seconds,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
    )