"""
utils/gemini_files.py - Hybrid file staging and persistent caching for Google Gemini API.

Provides threshold-based inlining vs File API staging, preventing redundant
network uploads across multi-turn Discord threads while respecting Google's 48-hour
file retention policy with a 44-hour safety expiration margin.
"""

import asyncio
import hashlib
import io
import json
import os
import time
from typing import Any, Optional

from google.genai import types
import sentry_sdk

DEFAULT_STAGING_THRESHOLD_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_INLINE_PAYLOAD_BYTES = 20 * 1024 * 1024        # 20 MB (Gemini inline limit)
EXPIRATION_SAFETY_SECONDS = 44 * 3600              # 44 hours (Google auto-deletes after 48h)
DEFAULT_CACHE_PATH = os.path.join("data", "gemini_files_cache.json")


class GeminiFileCache:
    """
    Persistent cache mapping file/attachment identifiers to Gemini File API URIs.
    """

    def __init__(self, cache_path: str = DEFAULT_CACHE_PATH):
        self.cache_path = cache_path
        self._cache: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._cache = data
                        self.prune()
            except Exception as e:
                print(f"[GeminiFileCache] Failed to load cache from {self.cache_path}: {e}")
                self._cache = {}

    def _save(self) -> None:
        try:
            data_dir = os.path.dirname(self.cache_path)
            if data_dir:
                os.makedirs(data_dir, exist_ok=True)
            tmp_path = f"{self.cache_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
            os.replace(tmp_path, self.cache_path)
        except Exception as e:
            print(f"[GeminiFileCache] Failed to save cache to {self.cache_path}: {e}")

    def prune(self) -> None:
        """Remove entries older than EXPIRATION_SAFETY_SECONDS."""
        now = time.time()
        expired_keys = [
            k for k, v in self._cache.items()
            if now - v.get("uploaded_at", 0) >= EXPIRATION_SAFETY_SECONDS
        ]
        if expired_keys:
            for k in expired_keys:
                del self._cache[k]
            self._save()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieve a non-expired cache entry."""
        if key not in self._cache:
            return None
        entry = self._cache[key]
        now = time.time()
        if now - entry.get("uploaded_at", 0) >= EXPIRATION_SAFETY_SECONDS:
            del self._cache[key]
            self._save()
            return None
        return entry

    def set(self, key: str, data: dict[str, Any]) -> None:
        """Store or update a cache entry and persist."""
        self._cache[key] = data
        self._save()

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        if key in self._cache:
            del self._cache[key]
            self._save()

    def invalidate_by_uri(self, uri: str) -> None:
        """Remove cache entries associated with a specific file URI (e.g. on 404)."""
        keys_to_delete = [k for k, v in self._cache.items() if v.get("uri") == uri]
        if keys_to_delete:
            for k in keys_to_delete:
                del self._cache[k]
            self._save()

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._save()


# Shared singleton cache instance
_global_file_cache: Optional[GeminiFileCache] = None


def get_gemini_file_cache() -> GeminiFileCache:
    """Return the global GeminiFileCache singleton."""
    global _global_file_cache
    if _global_file_cache is None:
        _global_file_cache = GeminiFileCache()
    return _global_file_cache


async def stage_or_inline_part(
    client: Any,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    attachment_id: Optional[str | int] = None,
    force_stage: bool = False,
    threshold_bytes: int = DEFAULT_STAGING_THRESHOLD_BYTES,
    cache: Optional[GeminiFileCache] = None,
) -> types.Part:
    """
    Decide whether to inline file bytes or stage to the Gemini File API.

    - If client is unavailable: returns inline types.Part.from_bytes.
    - If not force_stage and file_bytes <= threshold_bytes: returns inline types.Part.from_bytes.
    - Otherwise: checks cache or uploads to client.aio.files.upload and returns types.Part.from_uri.
    - If staging fails and file_bytes <= MAX_INLINE_PAYLOAD_BYTES (20MB): gracefully falls back to inline.

    Args:
        client: genai.Client instance or None.
        filename: Original file name.
        file_bytes: Raw file content in bytes.
        mime_type: MIME type of the file.
        attachment_id: Optional unique identifier (e.g. Discord attachment ID).
        force_stage: If True, forces staging to File API regardless of file size.
        threshold_bytes: Size threshold in bytes above which files are staged (default: 2 MB).
        cache: Optional GeminiFileCache instance (defaults to global singleton).

    Returns:
        types.Part: Configured Part (either from_bytes or from_uri).
    """
    # Fallback to inline if no client configured
    if not client or not hasattr(client, "aio") or not hasattr(client.aio, "files"):
        return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    should_stage = force_stage or (len(file_bytes) > threshold_bytes)

    if not should_stage:
        return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    # Determine unique cache key
    if attachment_id is not None:
        cache_key = f"att_{attachment_id}"
    else:
        sha = hashlib.sha256(file_bytes).hexdigest()
        cache_key = f"sha_{sha}"

    active_cache = cache if cache is not None else get_gemini_file_cache()

    # Check cache for existing valid URI
    cached_entry = active_cache.get(cache_key)
    if cached_entry and cached_entry.get("uri"):
        return types.Part.from_uri(
            file_uri=cached_entry["uri"],
            mime_type=mime_type,
        )

    # Stage file via Gemini File API
    try:
        upload_config = types.UploadFileConfig(
            mime_type=mime_type,
            display_name=filename,
        )
        file_io = io.BytesIO(file_bytes)
        file_io.seek(0)

        uploaded = await client.aio.files.upload(
            file=file_io,
            config=upload_config,
        )

        # Poll if processing (e.g. audio/video files)
        poll_count = 0
        state = getattr(uploaded, "state", None)
        while state == types.FileState.PROCESSING and poll_count < 15:
            await asyncio.sleep(1)
            uploaded = await client.aio.files.get(name=uploaded.name)
            state = getattr(uploaded, "state", None)
            poll_count += 1

        if state == types.FileState.PROCESSING:
            raise TimeoutError(f"Gemini File API processing timed out for '{filename}'.")

        if state == types.FileState.FAILED:
            raise RuntimeError(f"Gemini File API processing failed for '{filename}'.")

        active_cache.set(
            cache_key,
            {
                "uri": uploaded.uri,
                "name": uploaded.name,
                "mime_type": mime_type,
                "uploaded_at": time.time(),
                "size": len(file_bytes),
                "filename": filename,
            },
        )

        return types.Part.from_uri(
            file_uri=uploaded.uri,
            mime_type=mime_type,
        )
    except Exception as e:
        print(f"[GeminiFiles] Staging file '{filename}' failed: {e}")
        sentry_sdk.capture_exception(e)

        # Fallback to inline if file fits within inline payload limits
        if len(file_bytes) <= MAX_INLINE_PAYLOAD_BYTES:
            return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        raise
