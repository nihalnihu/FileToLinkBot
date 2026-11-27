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
    output_format: str = "matroska",
    audio_stream_index: int = 0,
    video_codec: str = "",
    audio_codec: str = "",
    audio_channels: str = "",
) -> web.StreamResponse:
    ffmpeg_exe = _resolve_ffmpeg_exe()
    if not ffmpeg_exe:
        return web.Response(status=501, text="ffmpeg unavailable for transcoding")

    is_remux = int(target_height or 0) == 0
    profile = None if is_remux else TRANSCODE_PROFILES.get(target_height)
    if not is_remux and not profile:
        return web.Response(status=400, text="Unsupported quality profile")

    source_url = build_local_stream_url(msg_id, file_name, secure_hash)
    seek_sec = max(0, int(start_seconds))
    is_mp4 = (output_format or "").strip().lower() == "mp4"

    cmd = [
        ffmpeg_exe,
        "-y",
        "-loglevel",
        "error",
        "-probesize",
        "500000",
        "-analyzeduration",
        "500000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "2",
    ]
    if seek_sec > 0:
        cmd.extend(["-ss", str(seek_sec)])
    audio_idx = max(0, int(audio_stream_index or 0))
    cmd.extend(
        [
            "-i",
            source_url,
            "-map",
            "0:v:0",
            "-map",
            f"0:a:{audio_idx}?",
        ]
    )

    if is_remux:
        cmd.extend(["-c:v", "copy"])
        if (video_codec or "").strip().lower() in ("hevc", "h265", "h.265", "x265"):
            cmd.extend(["-tag:v", "hvc1"])
        audio_bitrate = "112k"
    else:
        cmd.extend(
            [
                "-vf",
                f"scale=-2:{target_height}:flags=fast_bilinear",
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
                "-g",
                "30",
                "-keyint_min",
                "30",
                "-threads",
                "0",
            ]
        )
        audio_bitrate = profile["audio_bitrate"]

    acodec_low = (audio_codec or "").strip().lower()
    ach_low = (audio_channels or "").strip().lower()
    can_copy_aac = (
        is_remux
        and acodec_low == "aac"
        and "5.1" not in ach_low
        and "7.1" not in ach_low
    )
    if can_copy_aac:
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-ac", "2"])

    cmd.extend(["-flush_packets", "1"])
    if is_mp4:
        cmd.extend(
            [
                "-f",
                "mp4",
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-frag_duration",
                "500000",
                "-min_frag_duration",
                "200000",
                "pipe:1",
            ]
        )
    else:
        cmd.extend(["-f", "matroska", "pipe:1"])

    mode_label = "remux" if is_remux else f"transcoded-{target_height}p"
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "video/mp4" if is_mp4 else "video/x-matroska",
            "Accept-Ranges": "none",
            "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Stream-Mode": f"{mode_label}-{'mp4' if is_mp4 else 'mkv'}",
        },
    )

    async with _TRANSCODE_SEMAPHORE:
        proc: Optional[asyncio.subprocess.Process] = None
        stderr_task: Optional[asyncio.Task] = None
        prepared = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))
            stdout = proc.stdout
            if stdout is None:
                return web.Response(status=500, text="Missing ffmpeg output pipe")

            while True:
                if request.transport is None or request.transport.is_closing():
                    break
                chunk = await stdout.read(32768)
                if not chunk:
                    break
                if request.transport is None or request.transport.is_closing():
                    break
                if not prepared:
                    await resp.prepare(request)
                    prepared = True
                await resp.write(chunk)

            if prepared and request.transport is not None and not request.transport.is_closing():
                await resp.write_eof()
            elif not prepared:
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
