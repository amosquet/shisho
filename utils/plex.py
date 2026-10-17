import asyncio
import hashlib
import json
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp


class PlexMusicMonitor:
    """Monitors a Plex Media Server for active music sessions and caches cover art."""

    def __init__(
        self,
        plex_url: str | None = None,
        plex_token: str | None = None,
        plex_user: str | None = None,
        cache_ttl: float | None = None,
        state_file: str | None = None,
    ):
        self.plex_url = (plex_url if plex_url is not None else os.getenv("PLEX_URL", "")).strip().rstrip("/")
        self.plex_token = (plex_token if plex_token is not None else os.getenv("PLEX_TOKEN", "")).strip()
        self.plex_user = (plex_user if plex_user is not None else os.getenv("PLEX_USER", "")).strip()

        raw_ttl = cache_ttl if cache_ttl is not None else os.getenv("PLEX_CACHE_TTL", "5")
        try:
            self.cache_ttl = max(1.0, float(raw_ttl))
        except (TypeError, ValueError):
            self.cache_ttl = 5.0

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_state_file = os.path.join(base_dir, "data", "plex_last_played.json")
        self.state_file = state_file or default_state_file

        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

        # Cached session snapshot
        self._cached_payload: dict[str, Any] | None = None
        self._cached_at: float = 0.0
        self._current_thumb_path: str = ""

        # Cover image cache: (thumb_path, bytes, content_type)
        self._cover_cache: tuple[str, bytes, str] | None = None

        # Last played track persisted across restarts
        self._last_played: dict[str, Any] | None = None
        self._last_persisted_key: str = ""
        self._history_checked: bool = False
        self._load_last_played()

    @property
    def is_configured(self) -> bool:
        return bool(self.plex_url and self.plex_token)

    def _load_last_played(self) -> None:
        try:
            if self.state_file and os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("title"):
                    self._last_played = data
                    self._last_persisted_key = str(data.get("rating_key", ""))
                    if data.get("thumb_path"):
                        self._current_thumb_path = str(data["thumb_path"])
        except Exception:
            pass

    def _save_last_played(self, track_data: dict[str, Any]) -> None:
        try:
            if not self.state_file:
                return
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(track_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=6.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _plex_headers(self) -> dict[str, str]:
        return {
            "X-Plex-Token": self.plex_token,
            "Accept": "application/json",
        }

    def _matches_user(self, user_title: str, user_id: str) -> bool:
        if not self.plex_user:
            return True
        target = self.plex_user.lower()
        return user_title.lower() == target or user_id.lower() == target

    @staticmethod
    def _state_priority(state: str) -> int:
        s = (state or "").lower()
        if s == "playing":
            return 3
        if s == "buffering":
            return 2
        if s == "paused":
            return 1
        return 0

    @staticmethod
    def _sanitize_thumb_path(path: str) -> str:
        p = (path or "").strip()
        if not p or not p.startswith("/"):
            return ""
        # Prevent protocol-relative or traversal tricks
        if p.startswith("//") or ".." in p:
            return ""
        return p

    @staticmethod
    def _cover_version_key(rating_key: str, thumb_path: str) -> str:
        raw = f"{rating_key}:{thumb_path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def _parse_json_sessions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        container = payload.get("MediaContainer") or {}
        metadata = container.get("Metadata") or []
        if isinstance(metadata, dict):
            metadata = [metadata]
        tracks: list[dict[str, Any]] = []
        for item in metadata:
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type", "")).lower()
            if media_type != "track":
                continue

            user_obj = item.get("User") or {}
            user_title = str(user_obj.get("title", "")) if isinstance(user_obj, dict) else ""
            user_id = str(user_obj.get("id", "")) if isinstance(user_obj, dict) else ""
            if not self._matches_user(user_title, user_id):
                continue

            player_obj = item.get("Player") or {}
            state = (
                str(player_obj.get("state", "playing")).lower()
                if isinstance(player_obj, dict)
                else "playing"
            )
            player_name = (
                str(player_obj.get("product") or player_obj.get("title") or "")
                if isinstance(player_obj, dict)
                else ""
            )

            tracks.append(self._extract_track_dict(item, state=state, player_name=player_name))
        return tracks

    def _parse_xml_sessions(self, xml_text: str) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return tracks

        for elem in root:
            tag_lower = elem.tag.lower()
            item_type = (elem.attrib.get("type") or "").lower()
            if tag_lower != "track" and item_type != "track":
                continue

            user_elem = elem.find("User")
            user_title = user_elem.attrib.get("title", "") if user_elem is not None else ""
            user_id = user_elem.attrib.get("id", "") if user_elem is not None else ""
            if not self._matches_user(user_title, user_id):
                continue

            player_elem = elem.find("Player")
            state = (
                player_elem.attrib.get("state", "playing").lower()
                if player_elem is not None
                else "playing"
            )
            player_name = (
                (player_elem.attrib.get("product") or player_elem.attrib.get("title") or "")
                if player_elem is not None
                else ""
            )

            tracks.append(self._extract_track_dict(elem.attrib, state=state, player_name=player_name))
        return tracks

    def _extract_track_dict(
        self,
        raw: dict[str, Any],
        state: str = "playing",
        player_name: str = "",
    ) -> dict[str, Any]:
        rating_key = str(raw.get("ratingKey") or raw.get("key") or "")
        title = str(raw.get("title") or "Untitled Track").strip()
        album_artist = str(raw.get("grandparentTitle") or "").strip()
        track_artist = str(raw.get("originalTitle") or "").strip()
        artist = track_artist or album_artist or "Unknown Artist"
        album = str(raw.get("parentTitle") or "").strip()

        raw_year = raw.get("parentYear") or raw.get("year")
        year: int | None = None
        if raw_year is not None:
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                year = None

        try:
            duration_ms = max(0, int(raw.get("duration") or 0))
        except (TypeError, ValueError):
            duration_ms = 0

        try:
            progress_ms = max(0, int(raw.get("viewOffset") or 0))
        except (TypeError, ValueError):
            progress_ms = 0

        if duration_ms > 0 and progress_ms > duration_ms:
            progress_ms = duration_ms

        progress_percent = (
            round((progress_ms / duration_ms) * 100, 2) if duration_ms > 0 else 0.0
        )

        thumb_raw = (
            raw.get("thumb")
            or raw.get("parentThumb")
            or raw.get("grandparentThumb")
            or ""
        )
        thumb_path = self._sanitize_thumb_path(str(thumb_raw))
        version_key = self._cover_version_key(rating_key, thumb_path) if thumb_path else ""

        return {
            "rating_key": rating_key,
            "title": title,
            "artist": artist,
            "album_artist": album_artist or artist,
            "album": album,
            "year": year,
            "duration_ms": duration_ms,
            "progress_ms": progress_ms,
            "progress_percent": progress_percent,
            "state": state,
            "player": player_name,
            "thumb_path": thumb_path,
            "cover_version": version_key,
            "updated_at": time.time(),
        }

    async def _fetch_recent_track_from_plex(self) -> dict[str, Any] | None:
        """Fallback to fetch the most recently played track from Plex if no session is active."""
        if not self.is_configured:
            return None
        session = await self._get_http_session()
        url = (
            f"{self.plex_url}/library/all"
            "?type=10&sort=lastViewedAt:desc&lastViewedAt>>=0"
            "&X-Plex-Container-Start=0&X-Plex-Container-Size=1"
        )
        try:
            async with session.get(url, headers=self._plex_headers()) as resp:
                if resp.status != 200:
                    return None
                content_type = resp.headers.get("Content-Type", "").lower()
                if "json" in content_type:
                    data = await resp.json()
                    container = data.get("MediaContainer") or {}
                    metadata = container.get("Metadata") or []
                    if isinstance(metadata, list) and metadata:
                        track = self._extract_track_dict(metadata[0], state="stopped")
                        last_viewed = metadata[0].get("lastViewedAt")
                        if last_viewed:
                            try:
                                track["last_played_at"] = int(last_viewed)
                            except (TypeError, ValueError):
                                pass
                        return track
                else:
                    text = await resp.text()
                    tracks = self._parse_xml_sessions(text)
                    if tracks:
                        tracks[0]["state"] = "stopped"
                        return tracks[0]
        except Exception:
            return None
        return None

    def _format_public_response(
        self,
        internal_state: dict[str, Any],
        base_url: str = "",
        elapsed_sec: float = 0.0,
    ) -> dict[str, Any]:
        is_playing = bool(internal_state.get("is_playing", False))
        state = str(internal_state.get("state", "stopped"))
        raw_track = internal_state.get("track")

        if not raw_track:
            return {
                "configured": self.is_configured,
                "is_playing": False,
                "state": "stopped",
                "track": None,
            }

        track = dict(raw_track)
        thumb_path = track.pop("thumb_path", "")
        cover_version = track.pop("cover_version", "")

        # Interpolate progress if actively playing and served from cache
        duration_ms = int(track.get("duration_ms", 0) or 0)
        progress_ms = int(track.get("progress_ms", 0) or 0)
        if state == "playing" and elapsed_sec > 0:
            progress_ms = progress_ms + int(elapsed_sec * 1000)
            if duration_ms > 0:
                progress_ms = min(duration_ms, progress_ms)
            track["progress_ms"] = progress_ms
            track["progress_percent"] = (
                round((progress_ms / duration_ms) * 100, 2) if duration_ms > 0 else 0.0
            )

        if thumb_path and cover_version:
            prefix = base_url.rstrip("/") if base_url else ""
            track["cover_url"] = f"{prefix}/api/plex/cover?v={cover_version}"
        else:
            track["cover_url"] = None

        return {
            "configured": self.is_configured,
            "is_playing": is_playing,
            "state": state,
            "track": track,
        }

    async def get_now_playing(self, base_url: str = "") -> dict[str, Any]:
        if not self.is_configured:
            return {
                "configured": False,
                "is_playing": False,
                "state": "stopped",
                "track": None,
            }

        now = time.time()
        if self._cached_payload is not None and (now - self._cached_at) < self.cache_ttl:
            return self._format_public_response(
                self._cached_payload,
                base_url=base_url,
                elapsed_sec=max(0.0, now - self._cached_at),
            )

        async with self._lock:
            now = time.time()
            if self._cached_payload is not None and (now - self._cached_at) < self.cache_ttl:
                return self._format_public_response(
                    self._cached_payload,
                    base_url=base_url,
                    elapsed_sec=max(0.0, now - self._cached_at),
                )

            active_tracks: list[dict[str, Any]] = []
            session = await self._get_http_session()
            sessions_url = f"{self.plex_url}/status/sessions"

            try:
                async with session.get(sessions_url, headers=self._plex_headers()) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("Content-Type", "").lower()
                        if "json" in content_type:
                            data = await resp.json()
                            active_tracks = self._parse_json_sessions(data)
                        else:
                            text = await resp.text()
                            # Try JSON first in case Content-Type header is generic
                            try:
                                data = json.loads(text)
                                active_tracks = self._parse_json_sessions(data)
                            except Exception:
                                active_tracks = self._parse_xml_sessions(text)
            except Exception:
                active_tracks = []

            if active_tracks:
                active_tracks.sort(
                    key=lambda t: self._state_priority(t.get("state", "")),
                    reverse=True,
                )
                chosen = active_tracks[0]
                state = chosen.get("state", "playing")
                is_playing = state in ("playing", "buffering")

                if chosen.get("thumb_path"):
                    self._current_thumb_path = chosen["thumb_path"]

                # Snapshot as last_played
                last_snapshot = dict(chosen)
                last_snapshot["last_played_at"] = int(time.time())
                self._last_played = last_snapshot
                if chosen.get("rating_key") and chosen["rating_key"] != self._last_persisted_key:
                    self._last_persisted_key = chosen["rating_key"]
                    self._save_last_played(last_snapshot)

                self._cached_payload = {
                    "is_playing": is_playing,
                    "state": state,
                    "track": chosen,
                }
            else:
                # No active session; check fallback history once if we have no last_played
                if self._last_played is None and not self._history_checked:
                    self._history_checked = True
                    recent = await self._fetch_recent_track_from_plex()
                    if recent:
                        self._last_played = recent
                        if recent.get("thumb_path"):
                            self._current_thumb_path = recent["thumb_path"]
                        self._save_last_played(recent)

                idle_track = None
                if self._last_played:
                    idle_track = dict(self._last_played)
                    idle_track["state"] = "stopped"
                    idle_track["progress_ms"] = 0
                    idle_track["progress_percent"] = 0.0
                    if idle_track.get("thumb_path"):
                        self._current_thumb_path = idle_track["thumb_path"]

                self._cached_payload = {
                    "is_playing": False,
                    "state": "stopped",
                    "track": idle_track,
                }

            self._cached_at = time.time()
            return self._format_public_response(self._cached_payload, base_url=base_url)

    async def get_cover_image(self) -> tuple[bytes, str] | None:
        """Fetch and cache the cover art bytes for the current or last played track."""
        if not self.is_configured:
            return None

        if not self._current_thumb_path:
            await self.get_now_playing()

        thumb_path = self._sanitize_thumb_path(self._current_thumb_path)
        if not thumb_path:
            return None

        if self._cover_cache and self._cover_cache[0] == thumb_path:
            return self._cover_cache[1], self._cover_cache[2]

        session = await self._get_http_session()
        # Use Plex photo transcoder for optimal sizing (300x300), falling back to direct thumb URL
        encoded_thumb = urllib.parse.quote(thumb_path, safe="/")
        transcode_url = (
            f"{self.plex_url}/photo/:/transcode"
            f"?width=300&height=300&minSize=1&upscale=1&url={encoded_thumb}"
        )
        direct_url = f"{self.plex_url}{thumb_path}"

        for target_url in (transcode_url, direct_url):
            try:
                async with session.get(
                    target_url,
                    headers={"X-Plex-Token": self.plex_token, "Accept": "image/*"},
                ) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("Content-Type", "image/jpeg")
                        if not content_type.startswith("image/"):
                            content_type = "image/jpeg"
                        body = await resp.read()
                        if body:
                            self._cover_cache = (thumb_path, body, content_type)
                            return body, content_type
            except Exception:
                continue

        return None
