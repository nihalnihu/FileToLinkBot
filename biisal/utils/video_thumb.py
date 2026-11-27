import asyncio
import contextlib
import logging
import re
import shutil
from typing import Dict, List, Optional
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
    clean_hash = (file_hash or "").strip()
    if clean_hash:
        return f"http://127.0.0.1:{Var.PORT}/{clean_hash}{int(msg_id)}"
    safe_name = quote_plus(str(file_name or f"video_{msg_id}.mkv"))
    return f"http://127.0.0.1:{Var.PORT}/{int(msg_id)}/{safe_name}?hash={clean_hash}"


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_HEADER_RE = re.compile(
    r"^\s*Stream\s+#0:(\d+)(?:\([^)]+\))?:\s*Video:\s*([^,\s(]+)",
    re.IGNORECASE,
)
_VIDEO_DIM_RE = re.compile(r",\s*(\d{3,4})x(\d{3,4})\b")
_AUDIO_HEADER_RE = re.compile(
    r"^\s*Stream\s+#0:(\d+)(?:\(([^)]+)\))?:\s*Audio:\s*(.+)$",
    re.IGNORECASE,
)
_ANY_STREAM_RE = re.compile(r"^\s*Stream\s+#0:\d+", re.IGNORECASE)
_META_TITLE_RE = re.compile(r"^\s*title\s*:\s*(.+?)\s*$", re.IGNORECASE)


async def probe_media_summary(stream_url: str, timeout: float = 8.0) -> Dict:
    """Probes duration, video codec, height, and audio tracks in a single ffmpeg pass."""
    ffmpeg_exe = _resolve_ffmpeg_exe()
    summary: Dict = {
        "duration": 0,
        "video_codec": "",
        "video_height": 0,
        "audio_tracks": [],
    }
    if not ffmpeg_exe or not stream_url:
        return summary

    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "info",
        "-analyzeduration",
        "700000",
        "-probesize",
        "700000",
        "-i",
        stream_url,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    tracks: List[Dict] = summary["audio_tracks"]
    current_track: Optional[Dict] = None
    in_metadata = False

    async def _read_summary() -> Dict:
        nonlocal current_track, in_metadata
        if proc.stderr is None:
            return summary
        while True:
            raw_line = await proc.stderr.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="ignore").rstrip("\r\n")
            if "At least one output file must be specified" in line:
                break

            if summary["duration"] <= 0:
                m_dur = _DURATION_RE.search(line)
                if m_dur:
                    hrs = int(m_dur.group(1))
                    mins = int(m_dur.group(2))
                    secs = float(m_dur.group(3))
                    summary["duration"] = max(
                        0, int(round(hrs * 3600 + mins * 60 + secs))
                    )

            if not summary["video_codec"]:
                m_vid = _VIDEO_HEADER_RE.match(line)
                if m_vid:
                    summary["video_codec"] = m_vid.group(2).strip().lower()
                    m_dim = _VIDEO_DIM_RE.search(line)
                    if m_dim:
                        with contextlib.suppress(ValueError):
                            summary["video_height"] = int(m_dim.group(2))
                    current_track = None
                    in_metadata = False
                    continue

            m_audio = _AUDIO_HEADER_RE.match(line)
            if m_audio:
                stream_idx = int(m_audio.group(1))
                lang = (m_audio.group(2) or "").strip()
                details = m_audio.group(3).strip()
                parts = [p.strip() for p in details.split(",") if p.strip()]
                codec = parts[0].split(" ")[0].lower() if parts else ""
                channels = ""
                for part in parts[1:]:
                    low = part.lower()
                    if any(
                        k in low for k in ("stereo", "mono", "5.1", "7.1", "channels", ".")
                    ):
                        if "hz" not in low and "kb/s" not in low:
                            channels = re.sub(
                                r"\s*\(default\)\s*", "", part, flags=re.IGNORECASE
                            ).strip()
                            break
                current_track = {
                    "index": len(tracks),
                    "stream_index": stream_idx,
                    "language": lang or "und",
                    "codec": codec,
                    "channels": channels,
                    "title": "",
                    "is_default": "(default)" in details.lower(),
                }
                tracks.append(current_track)
                in_metadata = False
                continue

            if _ANY_STREAM_RE.match(line):
                current_track = None
                in_metadata = False
                continue

            if current_track is not None:
                stripped = line.strip()
                if stripped.lower() == "metadata:":
                    in_metadata = True
                    continue
                if in_metadata:
                    m_title = _META_TITLE_RE.match(line)
                    if m_title:
                        current_track["title"] = m_title.group(1).strip()
                    elif not line.startswith("      "):
                        in_metadata = False
        return summary

    try:
        return await asyncio.wait_for(_read_summary(), timeout=timeout)
    except asyncio.TimeoutError:
        return summary
    except Exception as exc:
        logging.debug(f"Media probe failed: {exc}")
        return summary
    finally:
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=1.5)


async def probe_audio_tracks(stream_url: str, timeout: float = 20.0) -> List[Dict]:
    info = await probe_media_summary(stream_url, timeout=timeout)
    return info["audio_tracks"]


async def probe_duration_seconds(stream_url: str, timeout: float = 8.0) -> int:
    info = await probe_media_summary(stream_url, timeout=timeout)
    return int(info["duration"] or 0)


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
