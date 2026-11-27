import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import aiohttp

from biisal.utils.video_thumb import build_local_stream_url, probe_media_summary
from biisal.vars import Var

DEFAULT_PROMPT_PRESET = (
    'Correct the movie or series file name into a standard format: '
    'MovieName (Year) Language Quality, like "OM (2026) Tamil" or "OM (2026) Tamil 1080p". '
    'For series, include Season and Episode like "Breaking Bad S02E05 (2009) English 1080p".'
)

_ANALYZE_SYSTEM_PROMPT = (
    "You analyze messy Telegram movie or TV series release filenames and verify the real movie/series title and year. "
    'If it is a TV series/episode (e.g. S01E02, Season 2 Episode 5), set media_type to "tv" '
    'and extract integer season and episode. Otherwise set media_type to "movie" and season/episode to null. '
    "Respond ONLY with a single valid JSON object containing keys: "
    "title (string: official clean movie or TV series name ONLY, correcting typos/abbreviations using your movie knowledge, WITHOUT season/episode, channel tags, URLs, codecs, file sizes, languages, or year), "
    'media_type ("movie" or "tv"), '
    "season (integer or null), episode (integer or null), "
    "year (integer release year or null), "
    'quality (string like "4K", "1080p", "720p", "480p", "HDRip" ONLY if present in filename, otherwise null), '
    'filename_languages (string like "Malayalam" or "Tamil, Hindi" ONLY for languages explicitly mentioned in the filename, or null if none appear in the filename), '
    'original_language (string: the exact primary original language of this movie or TV series, e.g. "Telugu", "Malayalam", "Tamil", "Hindi", "English", "Korean", "Japanese", or null).'
)

_LANG_CODE_MAP = {
    "mal": "Malayalam",
    "ml": "Malayalam",
    "malayalam": "Malayalam",
    "tam": "Tamil",
    "ta": "Tamil",
    "tamil": "Tamil",
    "hin": "Hindi",
    "hi": "Hindi",
    "hindi": "Hindi",
    "tel": "Telugu",
    "te": "Telugu",
    "telugu": "Telugu",
    "kan": "Kannada",
    "kn": "Kannada",
    "kannada": "Kannada",
    "eng": "English",
    "en": "English",
    "english": "English",
    "kor": "Korean",
    "ko": "Korean",
    "korean": "Korean",
    "jpn": "Japanese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "spa": "Spanish",
    "es": "Spanish",
    "spanish": "Spanish",
    "fre": "French",
    "fra": "French",
    "fr": "French",
    "french": "French",
    "ger": "German",
    "deu": "German",
    "de": "German",
    "german": "German",
    "ita": "Italian",
    "it": "Italian",
    "italian": "Italian",
    "ara": "Arabic",
    "ar": "Arabic",
    "arabic": "Arabic",
    "chi": "Chinese",
    "zho": "Chinese",
    "zh": "Chinese",
    "chinese": "Chinese",
    "rus": "Russian",
    "ru": "Russian",
    "russian": "Russian",
    "mar": "Marathi",
    "mr": "Marathi",
    "ben": "Bengali",
    "bn": "Bengali",
    "pun": "Punjabi",
    "pan": "Punjabi",
    "pa": "Punjabi",
    "guj": "Gujarati",
    "gu": "Gujarati",
}


def format_series_tag(season: Optional[int], episode: Optional[int]) -> str:
    if season is not None and episode is not None:
        return f"S{season:02d}E{episode:02d}"
    if season is not None:
        return f"S{season:02d}"
    if episode is not None:
        return f"E{episode:02d}"
    return ""


def build_standard_file_name(
    base_title: str,
    year: Optional[int] = None,
    language: Optional[str] = None,
    quality: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> str:
    parts: List[str] = [base_title.strip()]
    tag = format_series_tag(season, episode)
    if tag and tag not in base_title.upper():
        parts.append(tag)
    if year is not None:
        parts.append(f"({year})")
    clean_lang = (language or "").strip()
    if clean_lang:
        parts.append(clean_lang)
    clean_qual = (quality or "").strip()
    if clean_qual:
        parts.append(clean_qual)
    return " ".join(p for p in parts if p)


def _parse_opt_int(val: Any, min_v: int, max_v: int) -> Optional[int]:
    if isinstance(val, int) and min_v <= val <= max_v:
        return val
    if val is not None:
        try:
            parsed = int(str(val).strip())
            if min_v <= parsed <= max_v:
                return parsed
        except (TypeError, ValueError):
            pass
    return None


def _clean_str_or_list(val: Any) -> Optional[str]:
    if isinstance(val, list):
        joined = ", ".join(
            str(x).strip()
            for x in val
            if str(x).strip() and str(x).strip().lower() != "null"
        )
        return joined or None
    if val is not None:
        s = str(val).strip()
        if s and s.lower() != "null":
            return s
    return None


def parse_ai_analysis_json(raw_content: str) -> Optional[Dict[str, Any]]:
    start = raw_content.find("{")
    end = raw_content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw_content[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    title = str(data.get("title") or "").strip()
    if not title:
        return None

    year = _parse_opt_int(data.get("year"), 1900, 2100)
    season = _parse_opt_int(data.get("season"), 1, 99)
    episode = _parse_opt_int(data.get("episode"), 1, 999)
    media_type = str(data.get("media_type") or "").strip().lower()
    is_series = media_type in ("tv", "series") or season is not None or episode is not None

    raw_quality = _clean_str_or_list(data.get("quality"))
    quality = (
        ("4K" if raw_quality.lower() == "2160p" else raw_quality)
        if raw_quality
        else None
    )

    filename_langs = _clean_str_or_list(
        data.get("filename_languages") or data.get("language")
    )
    original_lang = _clean_str_or_list(data.get("original_language"))

    return {
        "title": title,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "filename_languages": filename_langs,
        "original_language": original_lang,
    }


def extract_languages_from_tracks(tracks: List[Dict[str, Any]]) -> Optional[str]:
    found: List[str] = []
    seen = set()
    for t in tracks or []:
        if not isinstance(t, dict):
            continue
        code = str(t.get("language") or "").strip().lower()
        mapped = _LANG_CODE_MAP.get(code)
        if mapped and mapped not in seen:
            seen.add(mapped)
            found.append(mapped)
            continue
        track_title = str(t.get("title") or "").strip().lower()
        for word in re.findall(r"[a-z]{2,12}", track_title):
            m2 = _LANG_CODE_MAP.get(word)
            if m2 and m2 not in seen:
                seen.add(m2)
                found.append(m2)
    return ", ".join(found) if found else None


def quality_from_video_height(height: int) -> Optional[str]:
    if height >= 2100:
        return "4K"
    if height >= 1000:
        return "1080p"
    if height >= 680:
        return "720p"
    if height >= 450:
        return "480p"
    if height >= 320:
        return "360p"
    return None


async def _call_ai_json(
    session: aiohttp.ClientSession,
    system_prompt: str,
    user_prompt: str,
    gemini_api_key_override: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    gemini_key = (
        (gemini_api_key_override or "").strip()
        or (getattr(Var, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")).strip()
    )
    if gemini_key:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-3.8-flash:generateContent?key={gemini_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        try:
            async with session.post(url, json=payload) as resp:
                if 200 <= resp.status < 300:
                    body = await resp.json()
                    return str(body["candidates"][0]["content"]["parts"][0]["text"]), "Bot Gemini"
        except Exception:
            pass

    poll_payload = {
        "model": "openai",
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        async with session.post("https://text.pollinations.ai/openai", json=poll_payload) as resp:
            if 200 <= resp.status < 300:
                body = await resp.json()
                return str(body["choices"][0]["message"]["content"]), "Bot Pollinations AI"
    except Exception:
        pass
    return None


async def _lookup_imdb_title_year(
    session: aiohttp.ClientSession,
    title: str,
    year: Optional[int],
    is_series: bool,
) -> Optional[Tuple[str, Optional[int]]]:
    slug = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip().replace(" ", "_")
    if not slug:
        return None
    url = f"https://v3.sg.media-imdb.com/suggestion/x/{quote(slug)}.json"
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None

    items = [
        d for d in (data.get("d") or [])
        if isinstance(d, dict) and str(d.get("id") or "").startswith("tt") and d.get("l")
    ]
    if not items:
        return None

    desired_qids = (
        {"tvSeries", "tvMiniSeries"} if is_series else {"movie", "tvMovie"}
    )
    filtered = [d for d in items if d.get("qid") in desired_qids] or [
        d for d in items if d.get("qid") != "musicVideo"
    ] or items

    query_tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
    best = None
    best_score = -1
    for d in filtered:
        cand_title = str(d.get("l") or "").strip()
        cand_tokens = set(re.findall(r"[a-z0-9]+", cand_title.lower()))
        overlap = len(query_tokens & cand_tokens)
        if overlap == 0:
            continue
        dy = _parse_opt_int(d.get("y"), 1900, 2100)
        year_bonus = 5 if (year is not None and dy is not None and abs(dy - year) <= 1) else 0
        exact_bonus = 3 if cand_tokens == query_tokens else 0
        score = overlap * 2 + year_bonus + exact_bonus
        if score > best_score:
            best_score = score
            best = d

    if not best:
        return None
    imdb_title = str(best.get("l") or "").strip()
    imdb_year = _parse_opt_int(best.get("y"), 1900, 2100)
    return (imdb_title, imdb_year) if imdb_title else None


async def _lookup_tmdb_info(
    session: aiohttp.ClientSession,
    title: str,
    year: Optional[int],
    is_series: bool,
    tmdb_api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    key = (
        (tmdb_api_key or "").strip()
        or (getattr(Var, "TMDB_API_KEY", "") or os.environ.get("TMDB_API_KEY", "")).strip()
    )
    if not key or not title.strip():
        return None

    async def _search(endpoint: str) -> List[Dict[str, Any]]:
        url = (
            f"https://api.themoviedb.org/3/search/{endpoint}"
            f"?api_key={quote(key)}&query={quote(title.strip())}&page=1"
        )
        if year is not None:
            param = "first_air_date_year" if endpoint == "tv" else "year"
            url += f"&{param}={year}"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return [x for x in (data.get("results") or []) if isinstance(x, dict)]
        except Exception:
            return []

    results = await _search("tv" if is_series else "movie")
    if not results and year is not None:
        # Retry without year filter in case filename year differed by +/-1 year.
        saved_year = year
        year = None
        results = await _search("tv" if is_series else "movie")
        year = saved_year
    if not results:
        results = await _search("movie" if is_series else "tv")
    if not results:
        return None

    best = results[0]
    if year is not None:
        for item in results:
            rd = str(item.get("release_date") or item.get("first_air_date") or "").strip()
            if len(rd) >= 4 and rd[:4].isdigit() and abs(int(rd[:4]) - year) <= 1:
                best = item
                break

    tmdb_title = str(best.get("title") or best.get("name") or "").strip()
    rd = str(best.get("release_date") or best.get("first_air_date") or "").strip()
    tmdb_year = _parse_opt_int(rd[:4], 1900, 2100) if len(rd) >= 4 else None
    raw_lang = str(best.get("original_language") or "").strip().lower()
    tmdb_lang = _LANG_CODE_MAP.get(raw_lang) or (raw_lang.upper() if len(raw_lang) == 2 else None)
    return {
        "tmdb_id": best.get("id"),
        "title": tmdb_title or None,
        "year": tmdb_year,
        "original_language": tmdb_lang,
    }


async def _fetch_original_language_from_ai(
    session: aiohttp.ClientSession,
    title: str,
    year: Optional[int],
    gemini_api_key_override: Optional[str] = None,
) -> Optional[str]:
    q = f"{title} ({year})" if year else title
    res = await _call_ai_json(
        session,
        'Return ONLY JSON: {"original_language": "<Primary language of this movie/series, e.g. Malayalam, Tamil, Hindi, Telugu, Kannada, English>"}',
        f"Movie/Series: {q}",
        gemini_api_key_override=gemini_api_key_override,
    )
    if not res:
        return None
    raw, _ = res
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
        return _clean_str_or_list(obj.get("original_language"))
    except Exception:
        return None


def _local_fallback_parse(
    raw_file_name: str,
    caption: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    combined_ref = f"{raw_file_name} {(caption or '').strip()}".strip()
    name = re.sub(r"\.[a-z0-9]{2,4}$", "", raw_file_name.strip(), flags=re.IGNORECASE)
    name = re.sub(r"^\[[^\]]{1,12}\]\s*", "", name)
    name = re.sub(r"@[a-z0-9_]+", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"www\.[a-z0-9\.\-]+", " ", name, flags=re.IGNORECASE)

    season = episode = None
    m_se = re.search(r"\bS(\d{1,2})\s*[-._ ]?\s*E(\d{1,3})\b", combined_ref, flags=re.IGNORECASE)
    if m_se:
        season, episode = int(m_se.group(1)), int(m_se.group(2))
        name = re.sub(r"\bS\d{1,2}\s*[-._ ]?\s*E\d{1,3}\b", " ", name, flags=re.IGNORECASE)
    is_series = season is not None or episode is not None

    year = None
    m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", name) or re.search(
        r"\b(19\d{2}|20\d{2})\b", combined_ref
    )
    if m_yr:
        year = int(m_yr.group(1))
        name = re.sub(rf"\b{year}\b", " ", name, count=1)

    quality = None
    m_q = re.search(
        r"\b(2160p|4K|1080p|720p|480p|360p|HDRip)\b",
        combined_ref,
        flags=re.IGNORECASE,
    )
    if m_q:
        rq = m_q.group(1).lower()
        quality = "4K" if rq in ("2160p", "4k") else ("HDRip" if rq == "hdrip" else rq)

    langs: List[str] = []
    seen = set()
    for tok in re.findall(r"[a-zA-Z]{3,12}", combined_ref):
        mapped = _LANG_CODE_MAP.get(tok.lower())
        if mapped and mapped not in seen:
            seen.add(mapped)
            langs.append(mapped)

    cleaned = re.sub(
        r"\b(2160p|4K|1080p|720p|480p|360p|HDRip|WEB-?DL|WEBRip|BluRay|HEVC|x265|x264|AAC|DDP\d*\.?\d*|ESub|HQ|\d+(?:\.\d+)?(?:GB|MB)|malayalam|tamil|hindi|telugu|kannada|english)\b",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[._\-\+\[\]()]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    return {
        "title": cleaned,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "filename_languages": ", ".join(langs) if langs else None,
        "original_language": None,
    }


async def auto_clean_filename_with_ai(
    file_name: str,
    msg_id: Optional[int] = None,
    file_hash: Optional[str] = None,
    existing_title: Optional[str] = None,
    gemini_api_key_override: Optional[str] = None,
    prompt_preset: Optional[str] = None,
    tmdb_api_key: Optional[str] = None,
    caption: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    raw = (file_name or "").strip()
    if not raw:
        return None

    clean_caption = (caption or "").strip()
    user_prompt = f"Raw filename: {raw}"
    if clean_caption and clean_caption != raw:
        user_prompt += f"\nTelegram caption (reference if filename is truncated): {clean_caption[:400]}"
    if existing_title and existing_title.strip() and existing_title.strip() != raw:
        user_prompt += f"\nExisting title in editor: {existing_title.strip()}"

    sys_prompt = _ANALYZE_SYSTEM_PROMPT
    if prompt_preset and prompt_preset.strip() and prompt_preset.strip() != DEFAULT_PROMPT_PRESET:
        sys_prompt += f" User formatting preference: {prompt_preset.strip()}"

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1. AI analyzes the raw filename + caption reference (falls back locally if AI quota/402 occurs)
        ai_res = await _call_ai_json(
            session,
            sys_prompt,
            user_prompt,
            gemini_api_key_override=gemini_api_key_override,
        )
        raw_ai, ai_provider = ai_res if ai_res else (None, "Bot Local")
        parsed = (parse_ai_analysis_json(raw_ai) if raw_ai else None) or _local_fallback_parse(
            raw, caption=clean_caption
        )
        if not parsed:
            return None

        base_title = parsed["title"]
        year = parsed["year"]
        quality = parsed["quality"]
        is_series = parsed["is_series"]
        season = parsed["season"]
        episode = parsed["episode"]
        language = parsed["filename_languages"]
        original_language = parsed["original_language"]

        # 2. Verify/correct movie or series name, year, and original language via TMDB / IMDb
        tmdb_info = await _lookup_tmdb_info(
            session,
            base_title,
            year,
            is_series,
            tmdb_api_key=tmdb_api_key,
        )
        if tmdb_info:
            if tmdb_info.get("title"):
                base_title = tmdb_info["title"]
            if tmdb_info.get("year") is not None:
                year = tmdb_info["year"]
            if tmdb_info.get("original_language"):
                original_language = tmdb_info["original_language"]
        else:
            imdb_match = await _lookup_imdb_title_year(
                session, base_title, year, is_series
            )
            if imdb_match:
                imdb_title, imdb_year = imdb_match
                title_changed = imdb_title.lower() != base_title.lower()
                base_title = imdb_title
                if imdb_year is not None:
                    year = imdb_year
                if title_changed or not original_language:
                    refreshed = await _fetch_original_language_from_ai(
                        session,
                        base_title,
                        year,
                        gemini_api_key_override=gemini_api_key_override,
                    )
                    if refreshed:
                        original_language = refreshed

        # 3. Check audio track metadata FIRST (fast 7s header probe in background)
        probed_tracks = None
        meta_language = None
        if msg_id is not None and file_hash:
            try:
                local_url = build_local_stream_url(int(msg_id), raw, str(file_hash))
                summary = await probe_media_summary(local_url, timeout=7.0)
                tracks = summary.get("audio_tracks") or []
                if tracks:
                    probed_tracks = tracks
                    meta_language = extract_languages_from_tracks(tracks)
                if not quality:
                    quality = quality_from_video_height(
                        int(summary.get("video_height") or 0)
                    )
            except Exception:
                pass

        # 4. Priority for language: Audio Metadata -> Filename -> TMDB/AI Original Language
        # (If a dual-audio MKV left one track untagged 'und' while filename lists more,
        #  keep the fuller filename language list.)
        filename_lang_str = language
        if meta_language:
            meta_count = len([x for x in meta_language.split(",") if x.strip()])
            file_count = len([x for x in (filename_lang_str or "").split(",") if x.strip()])
            if filename_lang_str and file_count > meta_count and probed_tracks and len(probed_tracks) > meta_count:
                language = filename_lang_str
            else:
                language = meta_language
        elif not language:
            if not original_language:
                original_language = await _fetch_original_language_from_ai(
                    session,
                    base_title,
                    year,
                    gemini_api_key_override=gemini_api_key_override,
                )
            language = original_language

        formatted_name = build_standard_file_name(
            base_title=base_title,
            year=year,
            language=language,
            quality=quality,
            season=season,
            episode=episode,
        )

        out: Dict[str, Any] = {
            "title": formatted_name,
            "clean_title": base_title,
            "is_series": is_series,
            "ai_provider": ai_provider,
        }
        if season is not None:
            out["season"] = season
        if episode is not None:
            out["episode"] = episode
        if year is not None:
            out["year"] = year
        if quality:
            out["quality"] = quality
        if language:
            out["language"] = language
        if probed_tracks:
            out["audio_tracks"] = probed_tracks
        return out
