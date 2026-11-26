import asyncio
import logging
import shutil
from typing import Optional
from urllib.parse import quote_plus

from biisal.vars import Var

_EXTRACT_SEMAPHORE = asyncio.Semaphore(1)


def _resolve_ffmpeg_exe() -> Optional[str]:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def build_local_stream_url(msg_id: int, file_name: str, file_hash: str) -> str:
    safe_name = quote_plus(str(file_name or f"video_{msg_id}.mkv"))
    return f"http://127.0.0.1:{Var.PORT}/{int(msg_id)}/{safe_name}?hash={file_hash}"


async def _run_ffmpeg_frame(
    ffmpeg_exe: str,
    stream_url: str,
    seek_seconds: int,
    timeout: float,
) -> Optional[bytes]:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(max(0, int(seek_seconds))),
        "-i",
        stream_url,
        "-frames:v",
        "1",
        "-vf",
        "scale=480:-2",
        "-q:v",
        "4",
        "-f",
        "image2",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None

    if proc.returncode == 0 and stdout and len(stdout) > 200:
        return stdout
    return None


async def extract_frame_10min(
    stream_url: str,
    *,
    seek_seconds: int = 600,
    timeout: float = 30.0,
) -> Optional[bytes]:
    ffmpeg_exe = _resolve_ffmpeg_exe()
    if not ffmpeg_exe or not stream_url:
        return None

    async with _EXTRACT_SEMAPHORE:
        try:
            frame = await _run_ffmpeg_frame(
                ffmpeg_exe, stream_url, seek_seconds, timeout
            )
            if frame:
                return frame
            # Fallback for videos shorter than 10 minutes
            if seek_seconds > 5:
                return await _run_ffmpeg_frame(
                    ffmpeg_exe, stream_url, 5, timeout
                )
        except Exception as exc:
            logging.warning(f"Video frame extract failed: {exc}")
    return None
