import asyncio
import contextlib
import logging
from typing import Dict, Optional

from aiohttp import web

from .video_thumb import _resolve_ffmpeg_exe, build_local_stream_url

# Limit concurrent transcodes so low-vCPU hosts stay responsive.
_TRANSCODE_SEMAPHORE = asyncio.Semaphore(2)

TRANSCODE_PROFILES: Dict[int, Dict[str, str]] = {
    720: {
        "maxrate": "1100k",
        "bufsize": "2200k",
        "crf": "25",
        "audio_bitrate": "96k",
    },
    480: {
        "maxrate": "500k",
        "bufsize": "1000k",
        "crf": "26",
        "audio_bitrate": "80k",
    },
    360: {
        "maxrate": "280k",
        "bufsize": "560k",
        "crf": "28",
        "audio_bitrate": "64k",
    },
}


async def _drain_stderr(stderr: Optional[asyncio.StreamReader]) -> None:
    if stderr is None:
        return
    try:
        while True:
            chunk = await stderr.read(4096)
            if not chunk:
                break
    except Exception:
        pass


async def stream_transcoded_video(
    request: web.Request,
    msg_id: int,
    file_name: str,
    secure_hash: str,
    target_height: int,
    start_seconds: int = 0,
) -> web.StreamResponse:
    ffmpeg_exe = _resolve_ffmpeg_exe()
    if not ffmpeg_exe:
        return web.Response(status=501, text="ffmpeg unavailable for transcoding")

    profile = TRANSCODE_PROFILES.get(target_height)
    if not profile:
        return web.Response(status=400, text="Unsupported quality profile")

    source_url = build_local_stream_url(msg_id, file_name, secure_hash)
    seek_sec = max(0, int(start_seconds))

    cmd = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "2",
    ]
    if seek_sec > 0:
        cmd.extend(["-ss", str(seek_sec)])
    cmd.extend(
        [
            "-i",
            source_url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale=-2:{target_height}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-crf",
            profile["crf"],
            "-maxrate",
            profile["maxrate"],
            "-bufsize",
            profile["bufsize"],
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            profile["audio_bitrate"],
            "-ac",
            "2",
            "-f",
            "matroska",
            "pipe:1",
        ]
    )

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "video/x-matroska",
            "Accept-Ranges": "none",
            "Cache-Control": "no-store",
            "X-Stream-Mode": f"transcoded-{target_height}p",
        },
    )

    async with _TRANSCODE_SEMAPHORE:
        proc: Optional[asyncio.subprocess.Process] = None
        stderr_task: Optional[asyncio.Task] = None
        prepared = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))
            stdout = proc.stdout
            if stdout is None:
                return web.Response(status=500, text="Missing ffmpeg output pipe")

            while True:
                chunk = await stdout.read(32768)
                if not chunk:
                    break
                if not prepared:
                    await resp.prepare(request)
                    prepared = True
                await resp.write(chunk)

            if prepared:
                await resp.write_eof()
            else:
                return web.Response(status=502, text="Transcode produced no data")
            return resp
        except (
            ConnectionResetError,
            BrokenPipeError,
            ConnectionAbortedError,
            asyncio.CancelledError,
        ):
            return resp
        except Exception as err:
            logging.warning(f"Transcode error for msg {msg_id} ({target_height}p): {err}")
            if not prepared:
                return web.Response(status=500, text=str(err))
            return resp
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with contextlib.suppress(BaseException):
                    await stderr_task
