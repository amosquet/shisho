import asyncio
from collections import OrderedDict
import hashlib
import json
import os
import re
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

from utils.db import (
    get_pb_client,
    get_pb_url,
    prepare_file_upload_payload,
    run_in_executor,
)

MUSIC_RECOMMENDATIONS_COLLECTION = "shisho_music_recommendations"


class PlexMusicMonitor:
    """Monitors a Plex Media Server for active music sessions, history, top rotation, and cover art."""

    def __init__(
        self,
        plex_url: str | None = None,
        plex_token: str | None = None,
        plex_user: str | None = None,
        cache_ttl: float | None = None,
        state_file: str | None = None,
        suggestions_file: str | None = None,
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
        default_suggestions_file = os.path.join(base_dir, "data", "music_suggestions.json")
        self.state_file = state_file or default_state_file
        self.suggestions_file = suggestions_file or default_suggestions_file

        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._crate_lock = asyncio.Lock()
        self._suggestions_lock = asyncio.Lock()

        # Cached session snapshot
        self._cached_payload: dict[str, Any] | None = None
        self._cached_at: float = 0.0
        self._current_thumb_path: str = ""

        # Cached crate data (recent tracks + top rotation)
        self._cached_crate: dict[str, Any] | None = None
        self._cached_crate_at: float = 0.0
        self.crate_cache_ttl: float = 60.0

        # Allowlist mapping version_key -> thumb_path (bounded LRU)
        self._known_thumbs: OrderedDict[str, str] = OrderedDict()
        # Cover image bytes LRU cache: version_key -> (thumb_path, bytes, content_type)
        self._cover_cache_map: OrderedDict[str, tuple[str, bytes, str]] = OrderedDict()
        # Keep legacy single tuple attribute for backwards compatibility
        self._cover_cache: tuple[str, bytes, str] | None = None

        # Last played track persisted across restarts
        self._last_played: dict[str, Any] | None = None
        self._last_persisted_key: str = ""
        self._history_checked: bool = False
        self._load_last_played()

    @property
    def is_configured(self) -> bool:
        return bool(self.plex_url and self.plex_token)

    def _register_thumb(self, version_key: str, thumb_path: str) -> None:
        if not version_key or not thumb_path:
            return
        self._known_thumbs[version_key] = thumb_path
        self._known_thumbs.move_to_end(version_key)
        while len(self._known_thumbs) > 250:
            self._known_thumbs.popitem(last=False)

    def _load_last_played(self) -> None:
        try:
            if self.state_file and os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("title"):
                    self._last_played = data
                    self._last_persisted_key = str(data.get("rating_key", ""))
                    thumb = str(data.get("thumb_path") or "")
                    ver = str(data.get("cover_version") or "")
                    if thumb:
                        self._current_thumb_path = thumb
                        if ver:
                            self._register_thumb(ver, thumb)
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
        if p.startswith("//") or ".." in p:
            return ""
        return p

    @staticmethod
    def _cover_version_key(rating_key: str, thumb_path: str) -> str:
        raw = f"{rating_key}:{thumb_path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _format_audio_quality(
        codec: str,
        bit_depth: int | None,
        sampling_rate: int | None,
        bitrate_kbps: int | None,
    ) -> str:
        clean_codec = (codec or "").strip().upper()
        if clean_codec == "DCA":
            clean_codec = "DTS"
        elif clean_codec == "MPEG4":
            clean_codec = "AAC"

        rate_str = ""
        if sampling_rate and sampling_rate > 0:
            khz = sampling_rate / 1000.0
            rate_str = f"{khz:g}kHz"

        lossless_codecs = {"FLAC", "ALAC", "WAV", "AIFF", "PCM", "DSD"}
        details: list[str] = []
        if bit_depth and bit_depth > 0 and rate_str:
            details.append(f"{bit_depth}-bit/{rate_str}")
        elif bit_depth and bit_depth > 0:
            details.append(f"{bit_depth}-bit")
        elif rate_str and clean_codec in lossless_codecs:
            details.append(rate_str)
        elif bitrate_kbps and bitrate_kbps > 0:
            details.append(f"{bitrate_kbps} kbps")
        elif rate_str:
            details.append(rate_str)

        if clean_codec and details:
            return f"{clean_codec} • {' '.join(details)}"
        if clean_codec:
            return clean_codec
        return " ".join(details)

    def _extract_audio_stream_info_json(self, raw: dict[str, Any]) -> dict[str, Any]:
        codec = ""
        bit_depth: int | None = None
        sampling_rate: int | None = None
        bitrate_kbps: int | None = None

        media_list = raw.get("Media")
        if isinstance(media_list, dict):
            media_list = [media_list]
        if isinstance(media_list, list) and media_list:
            m0 = media_list[0] if isinstance(media_list[0], dict) else {}
            codec = str(m0.get("audioCodec") or "").strip()
            raw_br = m0.get("bitrate")
            if raw_br is not None:
                try:
                    bitrate_kbps = int(raw_br)
                except (TypeError, ValueError):
                    pass

            parts = m0.get("Part")
            if isinstance(parts, dict):
                parts = [parts]
            if isinstance(parts, list) and parts:
                p0 = parts[0] if isinstance(parts[0], dict) else {}
                streams = p0.get("Stream")
                if isinstance(streams, dict):
                    streams = [streams]
                if isinstance(streams, list):
                    for st in streams:
                        if not isinstance(st, dict):
                            continue
                        st_type = str(st.get("streamType", ""))
                        if st_type in ("2", "") or st.get("audioChannelLayout") or st.get("samplingRate"):
                            if not codec:
                                codec = str(st.get("codec") or "").strip()
                            if st.get("bitDepth") is not None:
                                try:
                                    bit_depth = int(st["bitDepth"])
                                except (TypeError, ValueError):
                                    pass
                            if st.get("samplingRate") is not None:
                                try:
                                    sampling_rate = int(st["samplingRate"])
                                except (TypeError, ValueError):
                                    pass
                            if bitrate_kbps is None and st.get("bitrate") is not None:
                                try:
                                    bitrate_kbps = int(st["bitrate"])
                                except (TypeError, ValueError):
                                    pass
                            break

        quality_label = self._format_audio_quality(codec, bit_depth, sampling_rate, bitrate_kbps)
        return {
            "audio_codec": codec.upper() if codec else None,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "bitrate_kbps": bitrate_kbps,
            "audio_quality": quality_label or None,
        }

    def _extract_audio_stream_info_xml(self, elem: ET.Element) -> dict[str, Any]:
        codec = ""
        bit_depth: int | None = None
        sampling_rate: int | None = None
        bitrate_kbps: int | None = None

        media_elem = elem.find("Media")
        if media_elem is not None:
            codec = (media_elem.attrib.get("audioCodec") or "").strip()
            raw_br = media_elem.attrib.get("bitrate")
            if raw_br:
                try:
                    bitrate_kbps = int(raw_br)
                except (TypeError, ValueError):
                    pass
            for st in media_elem.findall(".//Stream"):
                if st.attrib.get("streamType") in ("2", None) or st.attrib.get("samplingRate"):
                    if not codec:
                        codec = (st.attrib.get("codec") or "").strip()
                    if st.attrib.get("bitDepth"):
                        try:
                            bit_depth = int(st.attrib["bitDepth"])
                        except (TypeError, ValueError):
                            pass
                    if st.attrib.get("samplingRate"):
                        try:
                            sampling_rate = int(st.attrib["samplingRate"])
                        except (TypeError, ValueError):
                            pass
                    break

        quality_label = self._format_audio_quality(codec, bit_depth, sampling_rate, bitrate_kbps)
        return {
            "audio_codec": codec.upper() if codec else None,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "bitrate_kbps": bitrate_kbps,
            "audio_quality": quality_label or None,
        }

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

            audio_info = self._extract_audio_stream_info_json(item)
            track = self._extract_track_dict(item, state=state, player_name=player_name)
            track.update(audio_info)
            tracks.append(track)
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

            audio_info = self._extract_audio_stream_info_xml(elem)
            track = self._extract_track_dict(elem.attrib, state=state, player_name=player_name)
            track.update(audio_info)
            tracks.append(track)
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
        parent_rating_key = str(raw.get("parentRatingKey") or "")
        thumb_path = self._sanitize_thumb_path(str(thumb_raw))
        version_key = self._cover_version_key(rating_key, thumb_path) if thumb_path else ""
        if version_key and thumb_path:
            self._register_thumb(version_key, thumb_path)

        view_count = 0
        if raw.get("viewCount") is not None:
            try:
                view_count = int(raw["viewCount"])
            except (TypeError, ValueError):
                view_count = 0

        last_viewed_at: int | None = None
        if raw.get("lastViewedAt") is not None:
            try:
                last_viewed_at = int(raw["lastViewedAt"])
            except (TypeError, ValueError):
                last_viewed_at = None

        return {
            "rating_key": rating_key,
            "parent_rating_key": parent_rating_key,
            "title": title,
            "artist": artist,
            "album_artist": album_artist or artist,
            "album": album,
            "year": year,
            "duration_ms": duration_ms,
            "progress_ms": progress_ms,
            "progress_percent": progress_percent,
            "view_count": view_count,
            "last_played_at": last_viewed_at,
            "state": state,
            "player": player_name,
            "thumb_path": thumb_path,
            "cover_version": version_key,
            "updated_at": time.time(),
        }

    def _extract_album_dict(self, raw: dict[str, Any]) -> dict[str, Any]:
        rating_key = str(raw.get("ratingKey") or raw.get("key") or "")
        title = str(raw.get("title") or "Untitled Album").strip()
        artist = str(raw.get("parentTitle") or raw.get("originalTitle") or "Unknown Artist").strip()

        raw_year = raw.get("year") or raw.get("parentYear")
        year: int | None = None
        if raw_year is not None:
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                year = None

        view_count = 0
        if raw.get("viewCount") is not None:
            try:
                view_count = int(raw["viewCount"])
            except (TypeError, ValueError):
                view_count = 0

        last_viewed_at: int | None = None
        if raw.get("lastViewedAt") is not None:
            try:
                last_viewed_at = int(raw["lastViewedAt"])
            except (TypeError, ValueError):
                last_viewed_at = None

        thumb_raw = raw.get("thumb") or raw.get("parentThumb") or ""
        thumb_path = self._sanitize_thumb_path(str(thumb_raw))
        version_key = self._cover_version_key(rating_key, thumb_path) if thumb_path else ""
        if version_key and thumb_path:
            self._register_thumb(version_key, thumb_path)

        return {
            "rating_key": rating_key,
            "title": title,
            "artist": artist,
            "year": year,
            "view_count": view_count,
            "last_played_at": last_viewed_at,
            "thumb_path": thumb_path,
            "cover_version": version_key,
        }

    async def _fetch_plex_items(self, query_path: str) -> list[dict[str, Any]]:
        if not self.is_configured:
            return []
        session = await self._get_http_session()
        url = f"{self.plex_url}{query_path}"
        try:
            async with session.get(url, headers=self._plex_headers()) as resp:
                if resp.status != 200:
                    return []
                content_type = resp.headers.get("Content-Type", "").lower()
                if "json" in content_type:
                    data = await resp.json()
                    container = data.get("MediaContainer") or {}
                    metadata = container.get("Metadata") or []
                    if isinstance(metadata, dict):
                        return [metadata]
                    if isinstance(metadata, list):
                        return [m for m in metadata if isinstance(m, dict)]
                else:
                    text = await resp.text()
                    try:
                        data = json.loads(text)
                        container = data.get("MediaContainer") or {}
                        metadata = container.get("Metadata") or []
                        if isinstance(metadata, list):
                            return [m for m in metadata if isinstance(m, dict)]
                    except Exception:
                        root = ET.fromstring(text)
                        return [dict(elem.attrib) for elem in root]
        except Exception:
            return []
        return []

    async def _fetch_recent_track_from_plex(self) -> dict[str, Any] | None:
        """Fallback to fetch the most recently played track from Plex if no session is active."""
        items = await self._fetch_plex_items(
            "/library/all?type=10&sort=lastViewedAt:desc&lastViewedAt>>=0"
            "&X-Plex-Container-Start=0&X-Plex-Container-Size=1"
        )
        if items:
            track = self._extract_track_dict(items[0], state="stopped")
            track.update(self._extract_audio_stream_info_json(items[0]))
            return track
        return None

    def _format_public_track(self, raw_track: dict[str, Any], base_url: str = "", elapsed_sec: float = 0.0, state: str = "stopped") -> dict[str, Any]:
        track = dict(raw_track)
        thumb_path = track.pop("thumb_path", "")
        cover_version = track.pop("cover_version", "")
        if cover_version and thumb_path:
            self._register_thumb(cover_version, thumb_path)

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

        return track

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

        track = self._format_public_track(
            raw_track,
            base_url=base_url,
            elapsed_sec=elapsed_sec,
            state=state,
        )

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

    async def get_crate_data(self, base_url: str = "") -> dict[str, Any]:
        """Fetch Recently Played tracks and Top Rotation albums/tracks from Plex."""
        if not self.is_configured:
            return {
                "configured": False,
                "recent_tracks": [],
                "top_albums": [],
            }

        now = time.time()
        if self._cached_crate is None or (now - self._cached_crate_at) >= self.crate_cache_ttl:
            async with self._crate_lock:
                now = time.time()
                if self._cached_crate is None or (now - self._cached_crate_at) >= self.crate_cache_ttl:
                    recent_raw, top_albums_raw = await asyncio.gather(
                        self._fetch_plex_items(
                            "/library/all?type=10&sort=lastViewedAt:desc&lastViewedAt>>=0"
                            "&X-Plex-Container-Start=0&X-Plex-Container-Size=12"
                        ),
                        self._fetch_plex_items(
                            "/library/all?type=9&sort=viewCount:desc,lastViewedAt:desc&viewCount>>=0"
                            "&X-Plex-Container-Start=0&X-Plex-Container-Size=8"
                        ),
                    )

                    recent_tracks: list[dict[str, Any]] = []
                    for item in recent_raw:
                        t = self._extract_track_dict(item, state="stopped")
                        t.update(self._extract_audio_stream_info_json(item))
                        recent_tracks.append(t)

                    top_albums: list[dict[str, Any]] = []
                    for item in top_albums_raw:
                        top_albums.append(self._extract_album_dict(item))

                    # Fallback: if Plex has no album-level viewCounts, derive top rotation from top tracks
                    if not top_albums:
                        top_tracks_raw = await self._fetch_plex_items(
                            "/library/all?type=10&sort=viewCount:desc,lastViewedAt:desc&viewCount>>=0"
                            "&X-Plex-Container-Start=0&X-Plex-Container-Size=24"
                        )
                        seen_albums: set[str] = set()
                        for item in top_tracks_raw:
                            t = self._extract_track_dict(item, state="stopped")
                            album_key = f"{t.get('album') or t.get('title')}:{t.get('album_artist')}"
                            if album_key in seen_albums:
                                continue
                            seen_albums.add(album_key)
                            top_albums.append({
                                "rating_key": t.get("rating_key", ""),
                                "title": t.get("album") or t.get("title", "Untitled"),
                                "artist": t.get("album_artist") or t.get("artist", "Unknown Artist"),
                                "year": t.get("year"),
                                "view_count": t.get("view_count", 0),
                                "last_played_at": t.get("last_played_at"),
                                "thumb_path": t.get("thumb_path", ""),
                                "cover_version": t.get("cover_version", ""),
                            })
                            if len(top_albums) >= 8:
                                break

                    self._cached_crate = {
                        "recent_tracks": recent_tracks,
                        "top_albums": top_albums,
                    }
                    self._cached_crate_at = time.time()

        prefix = base_url.rstrip("/") if base_url else ""
        formatted_recent = [
            self._format_public_track(t, base_url=base_url, state="stopped")
            for t in (self._cached_crate or {}).get("recent_tracks", [])
        ]

        formatted_top = []
        for alb in (self._cached_crate or {}).get("top_albums", []):
            a_copy = dict(alb)
            thumb_path = a_copy.pop("thumb_path", "")
            cover_version = a_copy.pop("cover_version", "")
            if cover_version and thumb_path:
                self._register_thumb(cover_version, thumb_path)
                a_copy["cover_url"] = f"{prefix}/api/plex/cover?v={cover_version}"
            else:
                a_copy["cover_url"] = None
            formatted_top.append(a_copy)

        return {
            "configured": True,
            "recent_tracks": formatted_recent,
            "top_albums": formatted_top,
        }

    async def get_album_details(self, rating_key: str, base_url: str = "") -> dict[str, Any] | None:
        """Fetch album metadata and its tracklist from Plex."""
        clean_key = str(rating_key or "").strip()
        if not clean_key or not clean_key.isdigit() or not self.is_configured:
            return None

        album_items, track_items = await asyncio.gather(
            self._fetch_plex_items(f"/library/metadata/{clean_key}"),
            self._fetch_plex_items(f"/library/metadata/{clean_key}/children"),
        )

        if not album_items and not track_items:
            return None

        album_raw = album_items[0] if album_items else {}
        title = album_raw.get("title") or "Untitled Album"
        artist = (
            album_raw.get("parentTitle")
            or album_raw.get("grandparentTitle")
            or album_raw.get("originalTitle")
            or "Unknown Artist"
        )
        year = album_raw.get("year")
        summary = album_raw.get("summary") or ""
        view_count = int(album_raw.get("viewCount") or 0)
        last_played_at = album_raw.get("lastViewedAt")
        studio = album_raw.get("studio") or ""

        genres: list[str] = []
        raw_genres = album_raw.get("Genre") or []
        if isinstance(raw_genres, list):
            for g in raw_genres:
                if isinstance(g, dict) and g.get("tag"):
                    genres.append(str(g["tag"]))
                elif isinstance(g, str):
                    genres.append(g)

        thumb_path = self._sanitize_thumb_path(str(album_raw.get("thumb") or ""))
        cover_version = self._cover_version_key(clean_key, thumb_path) if thumb_path else ""
        if cover_version and thumb_path:
            self._register_thumb(cover_version, thumb_path)

        prefix = base_url.rstrip("/") if base_url else ""
        cover_url = f"{prefix}/api/plex/cover?v={cover_version}" if cover_version else None

        tracks: list[dict[str, Any]] = []
        total_duration_ms = 0
        formats_set: set[str] = set()

        for idx, t_raw in enumerate(track_items, start=1):
            t_num = int(t_raw.get("index") or t_raw.get("trackNumber") or idx)
            t_title = t_raw.get("title") or f"Track {t_num}"
            t_artist = (
                t_raw.get("originalTitle")
                or t_raw.get("grandparentTitle")
                or t_raw.get("parentTitle")
                or artist
            )
            dur = int(t_raw.get("duration") or 0)
            total_duration_ms += dur
            t_plays = int(t_raw.get("viewCount") or 0)

            audio_info = self._extract_audio_stream_info_json(t_raw)
            if audio_info.get("audio_quality"):
                formats_set.add(audio_info["audio_quality"])

            tracks.append({
                "rating_key": str(t_raw.get("ratingKey") or ""),
                "index": t_num,
                "title": t_title,
                "artist": t_artist,
                "duration_ms": dur,
                "view_count": t_plays,
                "audio_codec": audio_info.get("audio_codec"),
                "audio_quality": audio_info.get("audio_quality"),
            })

        tracks.sort(key=lambda t: t.get("index", 0))

        # Best overall audio format label
        best_quality = ""
        if formats_set:
            sorted_formats = sorted(
                list(formats_set),
                key=lambda f: (1 if "FLAC" in f else 0, 1 if "24-bit" in f else 0),
                reverse=True,
            )
            best_quality = sorted_formats[0]

        # Resolve YouTube Music and Qobuz links for the album
        youtube_music_url, qobuz_url = await asyncio.gather(
            self._lookup_youtube_music_url(title, artist, kind="album"),
            self._lookup_qobuz_url(title, artist, kind="album"),
        )

        return {
            "rating_key": clean_key,
            "title": title,
            "artist": artist,
            "year": year,
            "summary": summary,
            "genres": genres,
            "studio": studio,
            "view_count": view_count,
            "last_played_at": last_played_at,
            "cover_url": cover_url,
            "track_count": len(tracks),
            "total_duration_ms": total_duration_ms,
            "audio_quality": best_quality,
            "youtube_url": youtube_music_url,
            "youtube_music_url": youtube_music_url,
            "qobuz_url": qobuz_url,
            "tracks": tracks,
        }

    async def get_cover_image(self, version_key: str = "") -> tuple[bytes, str] | None:
        """Fetch and cache the cover art bytes for a known version_key or the current track."""
        if not self.is_configured:
            return None

        target_thumb = ""
        clean_key = (version_key or "").strip()

        if clean_key and clean_key in self._cover_cache_map:
            cached_thumb, cached_bytes, cached_ct = self._cover_cache_map[clean_key]
            self._cover_cache_map.move_to_end(clean_key)
            return cached_bytes, cached_ct

        if clean_key and clean_key in self._known_thumbs:
            target_thumb = self._known_thumbs[clean_key]
        else:
            if not self._current_thumb_path:
                await self.get_now_playing()
            target_thumb = self._current_thumb_path

        thumb_path = self._sanitize_thumb_path(target_thumb)
        if not thumb_path:
            return None

        if not clean_key and self._cover_cache and self._cover_cache[0] == thumb_path:
            return self._cover_cache[1], self._cover_cache[2]

        for cached_k, (c_thumb, c_bytes, c_ct) in self._cover_cache_map.items():
            if c_thumb == thumb_path:
                self._cover_cache_map.move_to_end(cached_k)
                return c_bytes, c_ct

        session = await self._get_http_session()
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
                            cache_k = clean_key or self._cover_version_key("", thumb_path)
                            self._cover_cache = (thumb_path, body, content_type)
                            self._cover_cache_map[cache_k] = (thumb_path, body, content_type)
                            self._cover_cache_map.move_to_end(cache_k)
                            while len(self._cover_cache_map) > 60:
                                self._cover_cache_map.popitem(last=False)
                            return body, content_type
            except Exception:
                continue

        return None

    # -------------------------------------------------------------------------
    # Community Music Suggestions (PocketBase `shisho_music_recommendations` + local fallback)
    # -------------------------------------------------------------------------
    def _read_suggestions_sync(self) -> list[dict[str, Any]]:
        try:
            if self.suggestions_file and os.path.exists(self.suggestions_file):
                with open(self.suggestions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def _write_suggestions_sync(self, items: list[dict[str, Any]]) -> None:
        if not self.suggestions_file:
            return
        os.makedirs(os.path.dirname(self.suggestions_file), exist_ok=True)
        with open(self.suggestions_file, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    async def _lookup_itunes_cover(self, title: str, artist: str, kind: str = "song") -> str:
        """Fetch public album art URL from iTunes Search API for community music recommendations."""
        query = f"{title} {artist}".strip()
        if not query:
            return ""
        entity = "album" if kind == "album" else "song"
        encoded = urllib.parse.quote(query)
        url = f"https://itunes.apple.com/search?term={encoded}&media=music&entity={entity}&limit=1"
        session = await self._get_http_session()
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    data = json.loads(text)
                    results = data.get("results") or []
                    if results and isinstance(results[0], dict):
                        art = str(results[0].get("artworkUrl100") or "")
                        if art:
                            return art.replace("100x100bb", "300x300bb")
        except Exception:
            pass
        return ""

    async def _lookup_youtube_url(
        self, title: str, artist: str, kind: str = "song", original_url: str = ""
    ) -> str:
        """Resolve a direct YouTube video link (or search fallback) for a recommended song/album."""
        lower_orig = (original_url or "").lower()
        if "youtube.com/" in lower_orig or "youtu.be/" in lower_orig:
            return original_url

        query = f"{title} {artist}".strip()
        if not query:
            return ""
        if kind == "album" and "album" not in query.lower():
            search_term = f"{query} full album"
        else:
            search_term = query

        encoded = urllib.parse.quote(search_term)
        search_url = f"https://www.youtube.com/results?search_query={encoded}"
        session = await self._get_http_session()
        try:
            async with session.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ShishoBot/1.0)"},
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                    if match:
                        return f"https://www.youtube.com/watch?v={match.group(1)}"
        except Exception:
            pass
        return search_url

    async def _lookup_youtube_music_url(
        self, title: str, artist: str, kind: str = "album", original_url: str = ""
    ) -> str:
        """Resolve a direct YouTube Music album/playlist link (or search fallback)."""
        lower_orig = (original_url or "").lower()
        if "music.youtube.com/" in lower_orig:
            return original_url
        if ("youtube.com/playlist?list=" in lower_orig or "youtu.be/" in lower_orig) and "list=" in lower_orig:
            match = re.search(r"list=([a-zA-Z0-9_-]+)", original_url)
            if match:
                return f"https://music.youtube.com/playlist?list={match.group(1)}"

        query = f"{title} {artist}".strip()
        if not query:
            return ""

        encoded_music_query = urllib.parse.quote(query)
        music_search_fallback = f"https://music.youtube.com/search?q={encoded_music_query}"

        # Search for official album / playlist on YouTube to find a playlistId
        search_term = f"{query} album"
        encoded = urllib.parse.quote(search_term)
        # sp=EgIQAw%3D%3D filters YouTube search results specifically to Playlists / Albums
        search_url = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAw%3D%3D"
        session = await self._get_http_session()
        try:
            async with session.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ShishoBot/1.0)"},
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    match = re.search(r'"playlistId":"(OLAK5uy_[a-zA-Z0-9_-]+)"', html)
                    if not match:
                        match = re.search(r'"playlistId":"([a-zA-Z0-9_-]{18,})"', html)
                    if match:
                        return f"https://music.youtube.com/playlist?list={match.group(1)}"
        except Exception:
            pass
        return music_search_fallback

    async def _lookup_qobuz_url(
        self, title: str, artist: str, kind: str = "song", original_url: str = ""
    ) -> str:
        """Resolve a direct Qobuz store album/release link (or search fallback) for a recommendation."""
        lower_orig = (original_url or "").lower()
        if "qobuz.com/" in lower_orig:
            return original_url

        query = f"{title} {artist}".strip()
        if not query:
            return ""
        encoded = urllib.parse.quote(query)
        search_url = f"https://www.qobuz.com/us-en/search?q={encoded}"
        session = await self._get_http_session()
        try:
            async with session.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ShishoBot/1.0)"},
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    match = re.search(r'href="(/us-en/album/[^"#?]+)"', html)
                    if match:
                        return f"https://www.qobuz.com{match.group(1)}"
        except Exception:
            pass
        return search_url

    async def _download_cover_image(self, image_url: str) -> tuple[str, bytes] | None:
        """Download cover image bytes from a remote URL for PocketBase file upload."""
        if not image_url:
            return None
        session = await self._get_http_session()
        try:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    if body and len(body) <= 5 * 1024 * 1024:
                        ct = (resp.headers.get("Content-Type") or "").lower()
                        ext = "png" if "png" in ct else ("webp" if "webp" in ct else "jpg")
                        return (f"cover.{ext}", body)
        except Exception:
            pass
        return None

    @staticmethod
    def _record_field(record: Any, key: str, default: Any = "") -> Any:
        val = getattr(record, key, None)
        if val is None and hasattr(record, "get"):
            try:
                val = record.get(key)
            except Exception:
                val = None
        return default if val is None else val

    @classmethod
    def _record_to_suggestion_dict(cls, record: Any, pb_url: str = "") -> dict[str, Any]:
        rec_id = str(cls._record_field(record, "id", "") or "")
        coll_id = str(
            cls._record_field(record, "collection_id", "")
            or cls._record_field(record, "collectionId", "")
            or MUSIC_RECOMMENDATIONS_COLLECTION
        )
        raw_cover = cls._record_field(record, "cover", "")
        if isinstance(raw_cover, list):
            cover_name = str(raw_cover[0]) if raw_cover else ""
        else:
            cover_name = str(raw_cover or "")

        cover_url = str(cls._record_field(record, "cover_url", "") or "")
        if cover_name and pb_url and rec_id:
            cover_url = f"{pb_url.rstrip('/')}/api/files/{coll_id}/{rec_id}/{cover_name}"

        title = str(cls._record_field(record, "title", "") or "")
        artist = str(cls._record_field(record, "artist", "") or "")
        kind = str(cls._record_field(record, "kind", "song") or "song").lower()
        if kind not in ("song", "album"):
            kind = "song"
        url = str(cls._record_field(record, "url", "") or "")
        youtube_url = str(cls._record_field(record, "youtube_url", "") or "")
        qobuz_url = str(cls._record_field(record, "qobuz_url", "") or "")
        message = str(cls._record_field(record, "message", "") or "")
        submitter = str(cls._record_field(record, "submitter", "") or "Anonymous")
        suggested_from = str(cls._record_field(record, "suggested_from", "Web Form") or "Web Form")
        created = str(cls._record_field(record, "created", "") or "")
        updated = str(cls._record_field(record, "updated", "") or "")
        raw_public = cls._record_field(record, "is_public", True)
        is_public = bool(raw_public) if raw_public is not None else True

        return {
            "id": rec_id,
            "title": title,
            "artist": artist,
            "kind": kind,
            "url": url,
            "youtube_url": youtube_url,
            "qobuz_url": qobuz_url,
            "message": message,
            "submitter": submitter,
            "suggested_from": suggested_from,
            "created": created,
            "updated": updated,
            "date_suggested": created[:10] if created else "",
            "cover_url": cover_url,
            "is_public": is_public,
        }

    async def _resolve_metadata_from_url(self, url: str) -> tuple[str, str, str, str]:
        """
        Extract (title, artist, kind, thumbnail_url) from a streaming music URL
        using oEmbed and OpenGraph metadata.
        """
        title = ""
        artist = ""
        kind = "song"
        thumb = ""
        lower = (url or "").lower()
        if not lower:
            return title, artist, kind, thumb

        session = await self._get_http_session()

        # 1. YouTube / YouTube Music oEmbed
        if "youtube.com/" in lower or "youtu.be/" in lower:
            try:
                oe_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
                async with session.get(oe_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_title = str(data.get("title") or "").strip()
                        raw_author = re.sub(
                            r"\s*-\s*Topic$",
                            "",
                            str(data.get("author_name") or "").strip(),
                            flags=re.I,
                        ).strip()
                        thumb = str(data.get("thumbnail_url") or "")
                        if " - " in raw_title:
                            parts = raw_title.split(" - ", 1)
                            artist = parts[0].strip()
                            title = parts[1].strip()
                        else:
                            title = raw_title
                            artist = raw_author
                        if "album" in raw_title.lower() or "full album" in raw_title.lower():
                            kind = "album"
                        return title, artist, kind, thumb
            except Exception:
                pass

        # 2. Spotify oEmbed & OpenGraph
        if "open.spotify.com/" in lower:
            if "/album/" in lower:
                kind = "album"
            try:
                oe_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
                async with session.get(oe_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        title = str(data.get("title") or "").strip()
                        thumb = str(data.get("thumbnail_url") or "")
            except Exception:
                pass

        # 3. Generic OpenGraph Scraping
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; ShishoBot/1.0; +https://shisho.amcloud.dev)"}
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    html = await resp.text()

                    og_title_m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                    if not og_title_m:
                        og_title_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
                    og_title = og_title_m.group(1).strip() if og_title_m else ""

                    og_desc_m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                    if not og_desc_m:
                        og_desc_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', html, re.I)
                    og_desc = og_desc_m.group(1).strip() if og_desc_m else ""

                    og_img_m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                    if not og_img_m:
                        og_img_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
                    if og_img_m and not thumb:
                        thumb = og_img_m.group(1).strip()

                    og_type_m = re.search(r'<meta[^>]+property=["\']og:type["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
                    if og_type_m and "album" in og_type_m.group(1).lower():
                        kind = "album"

                    if "spotify.com" in lower and og_desc:
                        parts = [p.strip() for p in og_desc.split("·")]
                        if parts and not artist:
                            artist = parts[0]
                        if not title and og_title:
                            title = og_title
                    elif "music.apple.com" in lower and og_title:
                        clean = re.sub(r"\s+on Apple\s+Music.*$", "", og_title, flags=re.I).strip()
                        if " by " in clean:
                            t_part, a_part = clean.split(" by ", 1)
                            if not title:
                                title = t_part.strip()
                            if not artist:
                                artist = a_part.strip()
                    elif "qobuz.com" in lower and og_title:
                        clean = re.sub(r"\s*-\s*Qobuz.*$", "", og_title, flags=re.I).strip()
                        if ", " in clean:
                            t_part, a_part = clean.split(", ", 1)
                            if not title:
                                title = t_part.strip()
                            if not artist:
                                artist = a_part.strip()
                    elif "bandcamp.com" in lower and og_title:
                        if ", by " in og_title:
                            t_part, a_part = og_title.split(", by ", 1)
                            if not title:
                                title = t_part.strip()
                            if not artist:
                                artist = a_part.strip()

                    if not title and og_title:
                        if " - " in og_title:
                            parts = og_title.split(" - ", 1)
                            artist = artist or parts[0].strip()
                            title = parts[1].strip()
                        elif " by " in og_title:
                            parts = og_title.split(" by ", 1)
                            title = parts[0].strip()
                            artist = artist or parts[1].strip()
                        else:
                            title = og_title

                    if not artist and og_desc and "by " in og_desc:
                        m_by = re.search(r"by\s+([^,.\n]+)", og_desc, re.I)
                        if m_by:
                            artist = m_by.group(1).strip()
        except Exception:
            pass

        return title, artist, kind, thumb

    @staticmethod
    def validate_music_suggestion_payload(data: dict[str, Any]) -> dict[str, str]:
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object.")

        def clean_str(val: Any, max_len: int, allow_newlines: bool = False) -> str:
            s = str(val or "")
            if allow_newlines:
                s = "".join(ch for ch in s if ch in "\r\n\t" or not (0 <= ord(ch) < 32 or ord(ch) == 127))
            else:
                s = "".join(ch for ch in s if not (0 <= ord(ch) < 32 or ord(ch) == 127))
            return s.strip()[:max_len].strip()

        title = clean_str(data.get("title", ""), max_len=200)
        artist = clean_str(data.get("artist", ""), max_len=150)
        kind = clean_str(data.get("kind", "song"), max_len=20).lower()
        if kind not in ("song", "album"):
            kind = "song"
        url = clean_str(data.get("url", ""), max_len=400)
        reason = clean_str(data.get("reason", ""), max_len=1000, allow_newlines=True)
        submitter = clean_str(data.get("submitter", ""), max_len=100) or "Anonymous"

        if url:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("Please provide a valid http:// or https:// link.")

        if not url and (not title or not artist):
            raise ValueError("Please provide either a music link, or both a Song/Album Title and an Artist.")

        return {
            "title": title,
            "artist": artist,
            "kind": kind,
            "url": url,
            "reason": reason,
            "submitter": submitter,
        }

    async def add_music_suggestion(
        self,
        title: str = "",
        artist: str = "",
        kind: str = "song",
        url: str = "",
        reason: str = "",
        submitter: str = "Anonymous",
        suggested_from: str = "Web Form",
        is_public: bool = True,
    ) -> dict[str, Any]:
        clean_kind = kind if kind in ("song", "album") else "song"
        resolved_thumb = ""

        # Auto-extract title and artist from URL if either was omitted
        if url and (not title or not artist):
            res_t, res_a, res_k, res_thumb = await self._resolve_metadata_from_url(url)
            if not title and res_t:
                title = res_t
            if not artist and res_a:
                artist = res_a
            if clean_kind == "song" and res_k == "album":
                clean_kind = "album"
            if res_thumb:
                resolved_thumb = res_thumb

        # Fallback defaults if URL scraping couldn't resolve title/artist
        if not title:
            parsed = urllib.parse.urlparse(url) if url else None
            domain = parsed.netloc.replace("www.", "") if parsed and parsed.netloc else "Music Link"
            title = f"Shared on {domain}" if url else "Untitled Track"
        if not artist:
            artist = "Unknown Artist"

        cover_url, youtube_url, qobuz_url = await asyncio.gather(
            self._lookup_itunes_cover(title, artist, kind=clean_kind),
            self._lookup_youtube_url(title, artist, kind=clean_kind, original_url=url),
            self._lookup_qobuz_url(title, artist, kind=clean_kind, original_url=url),
        )
        if not cover_url and resolved_thumb:
            cover_url = resolved_thumb

        cover_file = await self._download_cover_image(cover_url) if cover_url else None

        def _add_to_pb() -> dict[str, Any]:
            pb = get_pb_client()
            pb_url = get_pb_url()

            pb_entry: dict[str, Any] = {
                "title": title,
                "artist": artist,
                "kind": clean_kind,
                "url": url,
                "youtube_url": youtube_url,
                "qobuz_url": qobuz_url,
                "message": reason,
                "submitter": submitter or "Anonymous",
                "suggested_from": suggested_from,
                "cover_url": cover_url,
                "is_public": bool(is_public),
            }

            files = {"cover": cover_file} if cover_file else None
            if files:
                try:
                    final_payload = prepare_file_upload_payload(pb_entry, files)
                    rec = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).create(final_payload)
                except Exception:
                    rec = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).create(pb_entry)
            else:
                rec = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).create(pb_entry)

            formatted = self._record_to_suggestion_dict(rec, pb_url=pb_url)
            if not formatted.get("cover_url") and cover_url:
                formatted["cover_url"] = cover_url
            if not formatted.get("youtube_url") and youtube_url:
                formatted["youtube_url"] = youtube_url
            if not formatted.get("qobuz_url") and qobuz_url:
                formatted["qobuz_url"] = qobuz_url
            return formatted

        try:
            return await run_in_executor(_add_to_pb)
        except Exception:
            # Fallback to local JSON file if PocketBase is not configured or unreachable
            now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            entry = {
                "id": uuid.uuid4().hex[:10],
                "title": title,
                "artist": artist,
                "kind": clean_kind,
                "url": url,
                "youtube_url": youtube_url,
                "qobuz_url": qobuz_url,
                "message": reason,
                "submitter": submitter or "Anonymous",
                "suggested_from": suggested_from,
                "created": now_iso,
                "updated": now_iso,
                "date_suggested": now_iso[:10],
                "cover_url": cover_url,
                "is_public": bool(is_public),
            }
            async with self._suggestions_lock:
                items = self._read_suggestions_sync()
                items.insert(0, entry)
                self._write_suggestions_sync(items[:200])
            return entry

    async def get_music_suggestions(self, limit: int = 50) -> list[dict[str, Any]]:
        def _fetch_from_pb() -> list[dict[str, Any]]:
            pb = get_pb_client()
            pb_url = get_pb_url()

            records = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).get_list(
                1,
                limit,
                query_params={
                    "sort": "-created",
                    "filter": "is_public = true",
                },
            )

            items = getattr(records, "items", None) or []
            return [self._record_to_suggestion_dict(r, pb_url=pb_url) for r in items]

        try:
            return await run_in_executor(_fetch_from_pb)
        except Exception:
            async with self._suggestions_lock:
                items = self._read_suggestions_sync()
            public_items = [i for i in items if i.get("is_public", True)]
            return public_items[:limit]

    async def delete_music_suggestion(self, query: str) -> dict[str, Any] | None:
        clean_target = (query or "").strip()
        if not clean_target:
            return None

        def _delete_from_pb() -> dict[str, Any] | None:
            pb = get_pb_client()
            pb_url = get_pb_url()

            # 1. Try exact record ID
            try:
                rec = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).get_one(clean_target)
                if rec:
                    dto = self._record_to_suggestion_dict(rec, pb_url=pb_url)
                    pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).delete(rec.id)
                    return dto
            except Exception:
                pass

            # 2. Search by title or artist
            safe_q = clean_target.replace("\\", "\\\\").replace("'", "\\'")
            try:
                records = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).get_full_list(
                    query_params={"filter": f"title ~ '{safe_q}' || artist ~ '{safe_q}'"}
                )
            except Exception:
                records = pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).get_full_list()

            matched_list = [
                r for r in (records or [])
                if (
                    clean_target.lower() in str(self._record_field(r, "title", "")).lower()
                    or clean_target.lower() in str(self._record_field(r, "artist", "")).lower()
                    or clean_target.lower() == str(self._record_field(r, "id", "")).lower()
                )
            ]
            if not matched_list:
                return None

            matched = matched_list[0]
            for r in matched_list:
                if str(self._record_field(r, "title", "")).lower() == clean_target.lower():
                    matched = r
                    break

            dto = self._record_to_suggestion_dict(matched, pb_url=pb_url)
            pb.collection(MUSIC_RECOMMENDATIONS_COLLECTION).delete(matched.id)
            return dto

        try:
            return await run_in_executor(_delete_from_pb)
        except Exception:
            q = clean_target.lower()
            async with self._suggestions_lock:
                items = self._read_suggestions_sync()
                removed = None
                remaining = []
                for item in items:
                    if removed is None and (
                        str(item.get("id", "")).lower() == q
                        or str(item.get("title", "")).lower() == q
                        or q in str(item.get("title", "")).lower()
                    ):
                        removed = item
                    else:
                        remaining.append(item)
                if removed is not None:
                    self._write_suggestions_sync(remaining)
                return removed

