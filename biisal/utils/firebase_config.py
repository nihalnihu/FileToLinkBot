import time
from typing import Optional
import aiohttp
from biisal.vars import Var

_last_known_url: Optional[str] = None


def _extract_string_field(fields: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        entry = fields.get(key)
        if isinstance(entry, dict):
            val = (entry.get("stringValue") or "").strip()
            if val:
                return val
    return ""


async def _fetch_doc_fields(session: aiohttp.ClientSession, doc_path: str) -> dict:
    project_id = getattr(Var, "FIREBASE_PROJECT_ID", "fire-fa173") or "fire-fa173"
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}"
        f"/databases/(default)/documents/{doc_path}"
    )
    async with session.get(url) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json()
        return data.get("fields") or {}


async def get_mrdb_download_url() -> str:
    """Always fetches the latest MRDb APK URL from Firestore settings/config (update_url)."""
    global _last_known_url

    found = ""
    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            settings_fields = await _fetch_doc_fields(session, "settings/config")
            found = _extract_string_field(
                settings_fields, ("update_url", "apkUrl", "downloadUrl", "appDownloadUrl")
            )
            if not found:
                update_fields = await _fetch_doc_fields(session, "app_config/update")
                found = _extract_string_field(
                    update_fields, ("apkUrl", "update_url", "downloadUrl", "appDownloadUrl", "url")
                )
            if not found:
                keys_fields = await _fetch_doc_fields(session, "app_config/keys")
                found = _extract_string_field(
                    keys_fields, ("apkUrl", "update_url", "appDownloadUrl", "downloadUrl")
                )
        if found:
            _last_known_url = found
            return found
    except Exception as exc:
        print(f"Firestore app download URL fetch failed: {exc}")

    if _last_known_url:
        return _last_known_url
    return (getattr(Var, "MRDB_APP_URL", "") or "").strip()
