"""
tests/test_plex.py - Unit tests for PlexMusicMonitor and Shisho Plex API endpoints.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.api import API
from utils.plex import PlexMusicMonitor


class FakeResponse:
    def __init__(self, status=200, json_data=None, text_data="", body=b"", headers=None):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestPlexMusicMonitor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmp_dir.name, "plex_last_played.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    async def test_unconfigured_returns_idle(self):
        monitor = PlexMusicMonitor(
            plex_url="",
            plex_token="",
            state_file=self.state_file,
        )
        self.assertFalse(monitor.is_configured)
        res = await monitor.get_now_playing()
        self.assertEqual(
            res,
            {
                "configured": False,
                "is_playing": False,
                "state": "stopped",
                "track": None,
            },
        )
        await monitor.close()

    async def test_active_json_session_and_cover_proxy(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            plex_user="sadako",
            cache_ttl=10.0,
            state_file=self.state_file,
        )

        plex_sessions_payload = {
            "MediaContainer": {
                "size": 2,
                "Metadata": [
                    {
                        "type": "movie",
                        "title": "Some Movie",
                        "User": {"title": "sadako"},
                        "Player": {"state": "playing"},
                    },
                    {
                        "type": "track",
                        "ratingKey": "98765",
                        "title": " Plastic Love ",
                        "grandparentTitle": "Mariya Takeuchi",
                        "parentTitle": "VARIETY",
                        "parentYear": 1984,
                        "duration": 296000,
                        "viewOffset": 148000,
                        "thumb": "/library/metadata/98765/thumb/1700000000",
                        "User": {"title": "sadako", "id": "1"},
                        "Player": {"state": "playing", "product": "Plexamp"},
                    },
                ],
            }
        }

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                json_data=plex_sessions_payload,
                headers={"Content-Type": "application/json"},
            )
        )
        monitor._session = fake_session

        res = await monitor.get_now_playing(base_url="https://shisho.amcloud.dev")
        self.assertTrue(res["configured"])
        self.assertTrue(res["is_playing"])
        self.assertEqual(res["state"], "playing")
        track = res["track"]
        self.assertIsNotNone(track)
        self.assertEqual(track["title"], "Plastic Love")
        self.assertEqual(track["artist"], "Mariya Takeuchi")
        self.assertEqual(track["album"], "VARIETY")
        self.assertEqual(track["year"], 1984)
        self.assertEqual(track["duration_ms"], 296000)
        self.assertEqual(track["progress_ms"], 148000)
        self.assertEqual(track["progress_percent"], 50.0)
        self.assertEqual(track["player"], "Plexamp")
        self.assertTrue(
            track["cover_url"].startswith("https://shisho.amcloud.dev/api/plex/cover?v=")
        )
        # Ensure internal thumb_path and token are not leaked in public response
        self.assertNotIn("thumb_path", track)
        self.assertNotIn("secret_token", json.dumps(res))

        # Verify persistence to state_file
        self.assertTrue(os.path.exists(self.state_file))

        # Test cover proxy fetch and caching
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                body=b"\xff\xd8\xff\xe0fakejpeg",
                headers={"Content-Type": "image/jpeg"},
            )
        )
        cover = await monitor.get_cover_image()
        self.assertIsNotNone(cover)
        self.assertEqual(cover[0], b"\xff\xd8\xff\xe0fakejpeg")
        self.assertEqual(cover[1], "image/jpeg")

        # Second call should hit memory cache without calling fake_session.get again
        fake_session.get.reset_mock()
        cover_cached = await monitor.get_cover_image()
        self.assertEqual(cover_cached, cover)
        fake_session.get.assert_not_called()

        await monitor.close()

    async def test_xml_fallback_and_idle_last_played(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            cache_ttl=1.0,
            state_file=self.state_file,
        )

        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <MediaContainer size="1">
            <Track ratingKey="555" type="track" title=" Giorno's Theme " grandparentTitle="Yugo Kanno" parentTitle="Golden Wind OST" duration="225000" viewOffset="45000" thumb="/library/metadata/555/thumb/1">
                <User id="1" title="sadako" />
                <Player state="paused" product="Plex Web" />
            </Track>
        </MediaContainer>
        """
        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                text_data=xml_response,
                headers={"Content-Type": "text/xml"},
            )
        )
        monitor._session = fake_session

        res = await monitor.get_now_playing()
        self.assertFalse(res["is_playing"])
        self.assertEqual(res["state"], "paused")
        self.assertEqual(res["track"]["title"], "Giorno's Theme")
        self.assertEqual(res["track"]["progress_percent"], 20.0)

        # Now expire cache and return empty sessions -> should return last played track in stopped state
        monitor._cached_at = 0
        empty_xml = '<MediaContainer size="0"></MediaContainer>'
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                text_data=empty_xml,
                headers={"Content-Type": "text/xml"},
            )
        )
        idle_res = await monitor.get_now_playing()
        self.assertFalse(idle_res["is_playing"])
        self.assertEqual(idle_res["state"], "stopped")
        self.assertIsNotNone(idle_res["track"])
        self.assertEqual(idle_res["track"]["title"], "Giorno's Theme")
        self.assertEqual(idle_res["track"]["progress_ms"], 0)

        await monitor.close()

    async def test_api_cog_handlers(self):
        bot = MagicMock()
        api_cog = API(bot)
        api_cog.plex_monitor = MagicMock()
        api_cog.plex_monitor.get_now_playing = AsyncMock(
            return_value={
                "configured": True,
                "is_playing": True,
                "state": "playing",
                "track": {"title": "Test Song"},
            }
        )
        api_cog.plex_monitor.get_cover_image = AsyncMock(
            return_value=(b"imgbytes", "image/png")
        )

        req = MagicMock()
        req.headers = {"Host": "shisho.amcloud.dev", "X-Forwarded-Proto": "https"}
        req.scheme = "https"

        resp = await api_cog.handle_plex_now_playing(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["is_playing"])
        self.assertEqual(body["track"]["title"], "Test Song")
        api_cog.plex_monitor.get_now_playing.assert_awaited_once_with(
            base_url="https://shisho.amcloud.dev"
        )

        cover_resp = await api_cog.handle_plex_cover(req)
        self.assertEqual(cover_resp.status, 200)
        self.assertEqual(cover_resp.body, b"imgbytes")
        self.assertEqual(cover_resp.content_type, "image/png")

    async def test_ai_tool_and_slash_command(self):
        from tools.registry import execute_tool

        bot = MagicMock()
        api_cog = API(bot)
        api_cog.plex_monitor = MagicMock()
        api_cog.plex_monitor.get_now_playing = AsyncMock(
            return_value={
                "configured": True,
                "is_playing": True,
                "state": "playing",
                "track": {
                    "title": "Roundabout",
                    "artist": "Yes",
                    "album": "Fragile",
                    "year": 1971,
                    "progress_ms": 60000,
                    "duration_ms": 510000,
                    "progress_percent": 11.8,
                    "player": "Plexamp",
                },
            }
        )
        api_cog.plex_monitor.get_cover_image = AsyncMock(
            return_value=(b"\x89PNG\r\n\x1a\n", "image/png")
        )
        bot.get_cog.return_value = api_cog

        # 1. Test AI Chat tool dispatch
        tool_output = await execute_tool(bot, "get_plex_status", {}, "12345")
        self.assertIn("Playing on Plexamp", tool_output)
        self.assertIn('"Roundabout" by Yes', tool_output)
        self.assertIn("1:00 / 8:30 (11.8%)", tool_output)

        # 2. Test /nowplaying Discord slash command
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        await api_cog.nowplaying.callback(api_cog, interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        _, kwargs = interaction.followup.send.call_args
        embed = kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertEqual(embed.title, "Roundabout")
        self.assertIn("Yes", embed.description)
        self.assertIsNotNone(kwargs.get("file"))

    async def test_audio_quality_and_music_suggestions(self):
        suggestions_file = os.path.join(self.tmp_dir.name, "music_suggestions.json")
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
            suggestions_file=suggestions_file,
        )

        # Verify audio quality formatting
        self.assertEqual(
            monitor._format_audio_quality("flac", 24, 96000, 3000),
            "FLAC • 24-bit/96kHz",
        )
        self.assertEqual(
            monitor._format_audio_quality("mp3", None, 44100, 320),
            "MP3 • 320 kbps",
        )

        # Mock iTunes, YouTube, and Qobuz lookups
        monitor._lookup_itunes_cover = AsyncMock(return_value="https://example.com/art.jpg")
        monitor._lookup_youtube_url = AsyncMock(return_value="https://www.youtube.com/watch?v=abc123xyz00")
        monitor._lookup_qobuz_url = AsyncMock(return_value="https://www.qobuz.com/us-en/album/long-season/123")
        monitor._download_cover_image = AsyncMock(return_value=None)

        entry = await monitor.add_music_suggestion(
            title="Long Season",
            artist="Fishmans",
            kind="album",
            url="https://open.spotify.com/album/123",
            reason="Masterpiece live/studio album",
            submitter="Alice",
        )
        self.assertEqual(entry["title"], "Long Season")
        self.assertEqual(entry["artist"], "Fishmans")
        self.assertEqual(entry["kind"], "album")
        self.assertEqual(entry["url"], "https://open.spotify.com/album/123")
        self.assertEqual(entry["youtube_url"], "https://www.youtube.com/watch?v=abc123xyz00")
        self.assertEqual(entry["qobuz_url"], "https://www.qobuz.com/us-en/album/long-season/123")
        self.assertEqual(entry["cover_url"], "https://example.com/art.jpg")

        items = await monitor.get_music_suggestions()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["submitter"], "Alice")

        removed = await monitor.delete_music_suggestion("Long Season")
        self.assertIsNotNone(removed)
        self.assertEqual(len(await monitor.get_music_suggestions()), 0)

        # Test URL-only suggestion where title and artist are automatically resolved
        monitor._resolve_metadata_from_url = AsyncMock(
            return_value=("Around the World", "Daft Punk", "song", "https://example.com/around.jpg")
        )
        url_entry = await monitor.add_music_suggestion(
            url="https://open.spotify.com/track/xyz789",
            submitter="Charlie",
        )
        self.assertEqual(url_entry["title"], "Around the World")
        self.assertEqual(url_entry["artist"], "Daft Punk")
        self.assertEqual(url_entry["url"], "https://open.spotify.com/track/xyz789")

        await monitor.close()

    async def test_pocketbase_music_recommendations(self):
        from unittest.mock import patch

        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )
        monitor._lookup_itunes_cover = AsyncMock(return_value="https://example.com/art.jpg")
        monitor._lookup_youtube_url = AsyncMock(return_value="https://www.youtube.com/watch?v=tNdN4efddQc")
        monitor._lookup_qobuz_url = AsyncMock(return_value="https://www.qobuz.com/us-en/album/discovery-daft-punk/0724384960650")
        monitor._download_cover_image = AsyncMock(return_value=("cover.jpg", b"fakejpeg"))

        fake_rec = MagicMock()
        fake_rec.id = "rec_pb_123"
        fake_rec.collection_id = "shisho_music_recommendations"
        fake_rec.title = "Discovery"
        fake_rec.artist = "Daft Punk"
        fake_rec.kind = "album"
        fake_rec.url = "https://open.spotify.com/album/xyz"
        fake_rec.youtube_url = "https://www.youtube.com/watch?v=tNdN4efddQc"
        fake_rec.qobuz_url = "https://www.qobuz.com/us-en/album/discovery-daft-punk/0724384960650"
        fake_rec.message = "Classic French house"
        fake_rec.submitter = "Bob"
        fake_rec.suggested_from = "Web Form"
        fake_rec.created = "2026-10-17 06:00:00.000Z"
        fake_rec.updated = "2026-10-17 06:00:00.000Z"
        fake_rec.cover = "cover_abc.jpg"
        fake_rec.cover_url = ""
        fake_rec.is_public = True

        mock_coll = MagicMock()
        mock_coll.create.return_value = fake_rec
        mock_list_res = MagicMock()
        mock_list_res.items = [fake_rec]
        mock_coll.get_list.return_value = mock_list_res
        mock_coll.get_one.return_value = fake_rec

        mock_pb = MagicMock()
        mock_pb.collection.return_value = mock_coll

        with patch("utils.plex.get_pb_client", return_value=mock_pb), patch(
            "utils.plex.get_pb_url", return_value="https://pb.example.com"
        ):
            created = await monitor.add_music_suggestion(
                title="Discovery",
                artist="Daft Punk",
                kind="album",
                url="https://open.spotify.com/album/xyz",
                reason="Classic French house",
                submitter="Bob",
            )
            mock_pb.collection.assert_called_with("shisho_music_recommendations")
            self.assertEqual(created["id"], "rec_pb_123")
            self.assertEqual(created["url"], "https://open.spotify.com/album/xyz")
            self.assertEqual(created["youtube_url"], "https://www.youtube.com/watch?v=tNdN4efddQc")
            self.assertEqual(
                created["qobuz_url"],
                "https://www.qobuz.com/us-en/album/discovery-daft-punk/0724384960650",
            )
            self.assertEqual(
                created["cover_url"],
                "https://pb.example.com/api/files/shisho_music_recommendations/rec_pb_123/cover_abc.jpg",
            )

            listed = await monitor.get_music_suggestions()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["title"], "Discovery")
            self.assertEqual(listed[0]["artist"], "Daft Punk")
            self.assertEqual(listed[0]["youtube_url"], "https://www.youtube.com/watch?v=tNdN4efddQc")
            self.assertEqual(
                listed[0]["qobuz_url"],
                "https://www.qobuz.com/us-en/album/discovery-daft-punk/0724384960650",
            )

            deleted = await monitor.delete_music_suggestion("rec_pb_123")
            self.assertIsNotNone(deleted)
            mock_coll.delete.assert_called_once_with("rec_pb_123")

        await monitor.close()

    async def test_get_album_details(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )

        album_payload = [{
            "ratingKey": "12345",
            "title": "Random Access Memories",
            "parentTitle": "Daft Punk",
            "year": 2013,
            "summary": "Fourth studio album by Daft Punk.",
            "Genre": [{"tag": "Electronic"}, {"tag": "Disco"}],
            "studio": "Columbia Records",
            "viewCount": 50,
            "thumb": "/library/metadata/12345/thumb/123456",
        }]

        tracks_payload = [
            {
                "ratingKey": "5002",
                "index": 2,
                "title": "Giorgio by Moroder",
                "duration": 544000,
                "viewCount": 20,
                "Media": [{
                    "audioCodec": "flac",
                    "bitrate": 2800,
                    "Part": [{
                        "Stream": [{
                            "streamType": 2,
                            "codec": "flac",
                            "bitDepth": 24,
                            "samplingRate": 88200,
                        }]
                    }]
                }],
            },
            {
                "ratingKey": "5001",
                "index": 1,
                "title": "Give Life Back to Music",
                "duration": 275000,
                "viewCount": 30,
                "Media": [{
                    "audioCodec": "flac",
                    "bitrate": 2800,
                    "Part": [{
                        "Stream": [{
                            "streamType": 2,
                            "codec": "flac",
                            "bitDepth": 24,
                            "samplingRate": 88200,
                        }]
                    }]
                }],
            },
        ]

        async def fake_fetch(endpoint):
            if endpoint == "/library/metadata/12345":
                return album_payload
            if endpoint == "/library/metadata/12345/children":
                return tracks_payload
            return []

        monitor._fetch_plex_items = fake_fetch
        monitor._lookup_youtube_music_url = AsyncMock(return_value="https://music.youtube.com/playlist?list=album123")
        monitor._lookup_qobuz_url = AsyncMock(return_value="https://www.qobuz.com/us-en/album/ram/123")

        data = await monitor.get_album_details("12345", base_url="https://shisho.amcloud.dev")
        self.assertIsNotNone(data)
        self.assertEqual(data["title"], "Random Access Memories")
        self.assertEqual(data["artist"], "Daft Punk")
        self.assertEqual(data["year"], 2013)
        self.assertEqual(data["track_count"], 2)
        self.assertEqual(data["total_duration_ms"], 819000)
        self.assertEqual(data["genres"], ["Electronic", "Disco"])
        self.assertEqual(data["youtube_url"], "https://music.youtube.com/playlist?list=album123")
        self.assertEqual(data["youtube_music_url"], "https://music.youtube.com/playlist?list=album123")
        self.assertEqual(data["qobuz_url"], "https://www.qobuz.com/us-en/album/ram/123")
        self.assertIn("cover_url", data)

        # Check track ordering
        self.assertEqual(data["tracks"][0]["title"], "Give Life Back to Music")
        self.assertEqual(data["tracks"][0]["index"], 1)
        self.assertEqual(data["tracks"][1]["title"], "Giorgio by Moroder")
        self.assertEqual(data["tracks"][1]["index"], 2)
        self.assertIn("FLAC", data["audio_quality"])

        # Invalid/non-digit rating key returns None
        self.assertIsNone(await monitor.get_album_details("invalid_id"))

        await monitor.close()

    async def test_handle_plex_album_endpoint(self):
        bot = MagicMock()
        api_cog = API(bot)
        api_cog.plex_monitor = MagicMock()

        # 1. Missing ID parameter
        req_missing = MagicMock()
        req_missing.query = {}
        resp_missing = await api_cog.handle_plex_album(req_missing)
        self.assertEqual(resp_missing.status, 400)

        # 2. Album not found
        api_cog.plex_monitor.get_album_details = AsyncMock(return_value=None)
        req_not_found = MagicMock()
        req_not_found.query = {"id": "99999"}
        req_not_found.headers = {"Host": "shisho.amcloud.dev", "X-Forwarded-Proto": "https"}
        req_not_found.scheme = "https"
        resp_not_found = await api_cog.handle_plex_album(req_not_found)
        self.assertEqual(resp_not_found.status, 404)

        # 3. Successful retrieval
        fake_album = {
            "rating_key": "12345",
            "title": "Random Access Memories",
            "artist": "Daft Punk",
            "tracks": [],
        }
        api_cog.plex_monitor.get_album_details = AsyncMock(return_value=fake_album)
        req_ok = MagicMock()
        req_ok.query = {"id": "12345"}
        req_ok.headers = {"Host": "shisho.amcloud.dev", "X-Forwarded-Proto": "https"}
        req_ok.scheme = "https"
        resp_ok = await api_cog.handle_plex_album(req_ok)
        self.assertEqual(resp_ok.status, 200)
        self.assertEqual(resp_ok.headers.get("Cache-Control"), "public, max-age=300, stale-while-revalidate=1800")
        body = json.loads(resp_ok.text)
        self.assertEqual(body["title"], "Random Access Memories")

    async def test_get_artist_details(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )

        artist_payload = [{
            "ratingKey": "801",
            "title": "Daft Punk",
            "summary": "French electronic music duo formed in 1993.",
            "Genre": [{"tag": "Electronic"}, {"tag": "French House"}],
            "Country": [{"tag": "France"}],
            "thumb": "/library/metadata/801/thumb/123",
            "art": "/library/metadata/801/art/456",
        }]

        albums_payload = [{
            "ratingKey": "12345",
            "title": "Random Access Memories",
            "year": 2013,
            "viewCount": 50,
            "leafCount": 13,
            "thumb": "/library/metadata/12345/thumb/789",
        }]

        tracks_payload = [
            {
                "ratingKey": "5001",
                "title": "Get Lucky",
                "parentTitle": "Random Access Memories",
                "parentRatingKey": "12345",
                "duration": 248000,
                "viewCount": 40,
                "Media": [{"audioCodec": "flac"}],
            },
            {
                "ratingKey": "5002",
                "title": "Giorgio by Moroder",
                "parentTitle": "Random Access Memories",
                "parentRatingKey": "12345",
                "duration": 544000,
                "viewCount": 20,
                "Media": [{"audioCodec": "flac"}],
            },
        ]

        async def fake_fetch(endpoint):
            if endpoint == "/library/metadata/801":
                return artist_payload
            if endpoint == "/library/metadata/801/children":
                return albums_payload
            if endpoint == "/library/metadata/801/allLeaves":
                return tracks_payload
            return []

        monitor._fetch_plex_items = fake_fetch
        monitor._lookup_youtube_music_artist_url = AsyncMock(
            return_value="https://music.youtube.com/channel/UCRr1xG_2WIDs18a6cIiCxeA"
        )
        monitor._lookup_qobuz_url = AsyncMock(
            return_value="https://www.qobuz.com/us-en/interpreter/daft-punk/123"
        )

        data = await monitor.get_artist_details("801", base_url="https://shisho.amcloud.dev")
        self.assertIsNotNone(data)
        self.assertEqual(data["name"], "Daft Punk")
        self.assertEqual(data["genres"], ["Electronic", "French House"])
        self.assertEqual(data["countries"], ["France"])
        self.assertEqual(data["total_albums"], 1)
        self.assertEqual(data["total_tracks"], 2)
        self.assertEqual(data["total_plays"], 60)
        self.assertEqual(data["youtube_music_url"], "https://music.youtube.com/channel/UCRr1xG_2WIDs18a6cIiCxeA")
        self.assertEqual(data["qobuz_url"], "https://www.qobuz.com/us-en/interpreter/daft-punk/123")
        self.assertEqual(len(data["top_tracks"]), 2)
        self.assertEqual(data["top_tracks"][0]["title"], "Get Lucky")
        self.assertEqual(data["albums"][0]["title"], "Random Access Memories")
        self.assertEqual(data["total_singles"], 0)
        self.assertEqual(data["singles"], [])
        self.assertIn("avatar_url", data)
        self.assertIn("backdrop_url", data)

        await monitor.close()

    async def test_get_artist_details_with_singles_and_eps(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )

        artist_payload = [{
            "ratingKey": "2313",
            "title": "YOASOBI",
            "summary": "Japanese music duo.",
            "Genre": [{"tag": "J-Pop"}],
            "Country": [{"tag": "Japan"}],
        }]

        # /children returns NO studio albums for YOASOBI
        albums_payload = []

        # /allLeaves returns tracks from EPs and singles
        tracks_payload = [
            {
                "ratingKey": "3955",
                "title": "勇者",
                "parentTitle": "THE BOOK 3",
                "parentRatingKey": "3954",
                "parentYear": 2023,
                "parentThumb": "/thumb/3954",
                "duration": 194000,
                "viewCount": 50,
                "Media": [{"audioCodec": "flac"}],
            },
            {
                "ratingKey": "2368",
                "title": "ハルカ",
                "parentTitle": "ハルカ - Single",
                "parentRatingKey": "2367",
                "parentYear": 2020,
                "parentThumb": "/thumb/2367",
                "duration": 243000,
                "viewCount": 88,
                "Media": [{"audioCodec": "flac"}],
            },
        ]

        hubs_payload = {
            "Hub": [
                {
                    "hubIdentifier": "artist.singles",
                    "title": "Singles and EPs",
                    "Metadata": [
                        {
                            "ratingKey": "3954",
                            "title": "THE BOOK 3",
                            "year": 2023,
                            "viewCount": 200,
                            "leafCount": 10,
                            "thumb": "/thumb/3954",
                        },
                        {
                            "ratingKey": "2367",
                            "title": "ハルカ - Single",
                            "year": 2020,
                            "viewCount": 88,
                            "leafCount": 1,
                            "thumb": "/thumb/2367",
                        },
                    ],
                }
            ]
        }

        async def fake_fetch_items(endpoint):
            if endpoint == "/library/metadata/2313":
                return artist_payload
            if endpoint == "/library/metadata/2313/children":
                return albums_payload
            if endpoint == "/library/metadata/2313/allLeaves":
                return tracks_payload
            return []

        async def fake_fetch_container(endpoint):
            if endpoint == "/library/metadata/2313/hubs":
                return hubs_payload
            if endpoint == "/library/sections":
                return {"Directory": [{"key": "1", "type": "artist", "title": "Music"}]}
            return {}

        monitor._fetch_plex_items = fake_fetch_items
        monitor._fetch_plex_container = fake_fetch_container
        monitor._lookup_youtube_music_artist_url = AsyncMock(return_value="")
        monitor._lookup_qobuz_url = AsyncMock(return_value="")

        data = await monitor.get_artist_details("2313", base_url="https://shisho.amcloud.dev")
        self.assertIsNotNone(data)
        self.assertEqual(data["name"], "YOASOBI")
        self.assertEqual(data["total_albums"], 0)
        self.assertEqual(data["albums"], [])
        self.assertEqual(data["total_singles"], 2)
        self.assertEqual(len(data["singles"]), 2)
        single_titles = [s["title"] for s in data["singles"]]
        self.assertIn("THE BOOK 3", single_titles)
        self.assertIn("ハルカ - Single", single_titles)
        self.assertTrue(all(s["cover_url"].startswith("https://shisho.amcloud.dev/api/plex/cover") for s in data["singles"]))

        # Test cache hit
        cached = await monitor.get_artist_details("2313", base_url="https://shisho.amcloud.dev")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["total_singles"], 2)
        self.assertEqual(len(cached["singles"]), 2)

        await monitor.close()

    async def test_handle_plex_artist_endpoint(self):
        bot = MagicMock()
        api_cog = API(bot)
        api_cog.plex_monitor = MagicMock()

        # 1. Missing ID parameter
        req_missing = MagicMock()
        req_missing.query = {}
        resp_missing = await api_cog.handle_plex_artist(req_missing)
        self.assertEqual(resp_missing.status, 400)

        # 2. Artist not found
        api_cog.plex_monitor.get_artist_details = AsyncMock(return_value=None)
        req_not_found = MagicMock()
        req_not_found.query = {"id": "99999"}
        req_not_found.headers = {"Host": "shisho.amcloud.dev", "X-Forwarded-Proto": "https"}
        req_not_found.scheme = "https"
        resp_not_found = await api_cog.handle_plex_artist(req_not_found)
        self.assertEqual(resp_not_found.status, 404)

        # 3. Successful retrieval
        fake_artist = {
            "rating_key": "801",
            "name": "Daft Punk",
            "albums": [],
            "top_tracks": [],
        }
        api_cog.plex_monitor.get_artist_details = AsyncMock(return_value=fake_artist)
        req_ok = MagicMock()
        req_ok.query = {"id": "801"}
        req_ok.headers = {"Host": "shisho.amcloud.dev", "X-Forwarded-Proto": "https"}
        req_ok.scheme = "https"
        resp_ok = await api_cog.handle_plex_artist(req_ok)
        self.assertEqual(resp_ok.status, 200)
        self.assertEqual(resp_ok.headers.get("Cache-Control"), "public, max-age=300, stale-while-revalidate=1800")
        body = json.loads(resp_ok.text)
        self.assertEqual(body["name"], "Daft Punk")

    async def test_get_artist_details_resolves_album_key(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        album_payload = [{
            "ratingKey": "500",
            "type": "album",
            "parentRatingKey": "400",
            "title": "Random Access Memories",
        }]
        artist_payload = [{
            "ratingKey": "400",
            "type": "artist",
            "title": "Daft Punk",
        }]

        async def fake_fetch(endpoint):
            if endpoint == "/library/metadata/500":
                return album_payload
            if endpoint == "/library/metadata/400":
                return artist_payload
            return []

        monitor._fetch_plex_items = fake_fetch
        monitor._lookup_youtube_music_artist_url = AsyncMock(return_value="")
        monitor._lookup_qobuz_url = AsyncMock(return_value="")

        data = await monitor.get_artist_details("500")
        self.assertIsNotNone(data)
        self.assertEqual(data["rating_key"], "400")
        self.assertEqual(data["name"], "Daft Punk")

        await monitor.close()

    async def test_get_album_and_artist_cache_hits(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        album_payload = [{
            "ratingKey": "100",
            "type": "album",
            "title": "Discovery",
        }]
        track_payload = [{
            "ratingKey": "101",
            "title": "One More Time",
            "duration": 320000,
        }]

        fetch_count = 0

        async def fake_fetch(endpoint):
            nonlocal fetch_count
            fetch_count += 1
            if endpoint == "/library/metadata/100":
                return album_payload
            if endpoint == "/library/metadata/100/children":
                return track_payload
            return []

        monitor._fetch_plex_items = fake_fetch
        monitor._lookup_youtube_music_url = AsyncMock(return_value="https://music.youtube.com/test")
        monitor._lookup_qobuz_url = AsyncMock(return_value="https://qobuz.com/test")

        # First call: populates cache
        res1 = await monitor.get_album_details("100")
        self.assertIsNotNone(res1)
        self.assertEqual(fetch_count, 2)
        self.assertEqual(monitor._lookup_youtube_music_url.call_count, 1)

        # Second call: must hit cache without fetching from Plex or lookup
        res2 = await monitor.get_album_details("100")
        self.assertIsNotNone(res2)
        self.assertEqual(fetch_count, 2)
        self.assertEqual(monitor._lookup_youtube_music_url.call_count, 1)
        self.assertEqual(res2["title"], "Discovery")

        await monitor.close()

    async def test_search_library_hubs_and_direct(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        hub_data = {
            "Hub": [
                {
                    "type": "track",
                    "Metadata": [
                        {
                            "type": "track",
                            "ratingKey": "101",
                            "title": "Midnight City",
                            "grandparentTitle": "M83",
                            "parentTitle": "Hurry Up, We're Dreaming",
                            "duration": 243000,
                        }
                    ],
                },
                {
                    "type": "album",
                    "Metadata": [
                        {
                            "type": "album",
                            "ratingKey": "201",
                            "title": "Hurry Up, We're Dreaming",
                            "parentTitle": "M83",
                            "year": 2011,
                        }
                    ],
                },
                {
                    "type": "artist",
                    "Metadata": [
                        {
                            "type": "artist",
                            "ratingKey": "301",
                            "title": "M83",
                            "summary": "French electronic music project.",
                            "Genre": [{"tag": "Synthpop"}, {"tag": "Shoegaze"}],
                        }
                    ],
                },
            ]
        }

        monitor._fetch_plex_container = AsyncMock(return_value=hub_data)

        # Search all
        all_res = await monitor.search_library("M83", media_type="all")
        self.assertEqual(len(all_res), 3)
        self.assertEqual(all_res[0]["media_type"], "track")
        self.assertEqual(all_res[0]["title"], "Midnight City")
        self.assertEqual(all_res[1]["media_type"], "album")
        self.assertEqual(all_res[2]["media_type"], "artist")

        # Search tracks only
        track_res = await monitor.search_library("M83", media_type="track")
        self.assertEqual(len(track_res), 1)
        self.assertEqual(track_res[0]["media_type"], "track")

        # Search albums only
        album_res = await monitor.search_library("M83", media_type="album")
        self.assertEqual(len(album_res), 1)
        self.assertEqual(album_res[0]["media_type"], "album")

        # Search artists only
        artist_res = await monitor.search_library("M83", media_type="artist")
        self.assertEqual(len(artist_res), 1)
        self.assertEqual(artist_res[0]["media_type"], "artist")

        await monitor.close()

    async def test_get_album_and_artist_name_resolution(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        monitor.search_library = AsyncMock()
        monitor.search_library.side_effect = [
            [{"rating_key": "999", "title": "Random Access Memories", "media_type": "album"}],
            [{"rating_key": "888", "title": "Daft Punk", "media_type": "artist"}],
        ]

        monitor._fetch_plex_items = AsyncMock()
        monitor._fetch_plex_items.side_effect = [
            [{"ratingKey": "999", "title": "Random Access Memories", "type": "album"}],
            [{"ratingKey": "1001", "title": "Get Lucky", "duration": 360000}],
            [{"ratingKey": "888", "title": "Daft Punk", "type": "artist"}],
            [{"ratingKey": "999", "title": "Random Access Memories"}],
            [{"ratingKey": "1001", "title": "Get Lucky", "duration": 360000}],
        ]
        monitor._lookup_youtube_music_url = AsyncMock(return_value="")
        monitor._lookup_qobuz_url = AsyncMock(return_value="")
        monitor._lookup_youtube_music_artist_url = AsyncMock(return_value="")

        # Calling with title should resolve via search_library
        album = await monitor.get_album_details("Random Access Memories")
        self.assertIsNotNone(album)
        self.assertEqual(album["rating_key"], "999")
        self.assertEqual(album["title"], "Random Access Memories")

        artist = await monitor.get_artist_details("Daft Punk")
        self.assertIsNotNone(artist)
        self.assertEqual(artist["rating_key"], "888")
        self.assertEqual(artist["name"], "Daft Punk")

        await monitor.close()

    async def test_get_library_stats(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        async def fake_container(path):
            if "type=8" in path:
                return {"totalSize": "25"}
            if "type=9" in path:
                return {"totalSize": "100"}
            if "type=10" in path:
                return {"totalSize": "1250"}
            return {}

        async def fake_items(path):
            if "type=9&sort=addedAt:desc" in path:
                return [{"ratingKey": "1", "title": "New Album", "parentTitle": "Artist A", "year": 2026}]
            if "type=8&sort=viewCount:desc" in path:
                return [{"ratingKey": "10", "title": "Artist A", "viewCount": 500, "Genre": [{"tag": "Electronic"}]}]
            return []

        monitor._fetch_plex_container = fake_container
        monitor._fetch_plex_items = fake_items

        stats = await monitor.get_library_stats()
        self.assertTrue(stats["configured"])
        self.assertEqual(stats["total_artists"], 25)
        self.assertEqual(stats["total_albums"], 100)
        self.assertEqual(stats["total_tracks"], 1250)
        self.assertEqual(len(stats["recently_added_albums"]), 1)
        self.assertEqual(stats["top_genres"], ["Electronic"])

        await monitor.close()

    async def test_playlists_crud(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        monitor.get_server_machine_identifier = AsyncMock(return_value="server_12345")

        # 1. get_playlists
        monitor._fetch_plex_items = AsyncMock(return_value=[
            {
                "ratingKey": "50",
                "title": "Synthwave Favorites",
                "summary": "Retro vibes",
                "leafCount": 12,
                "duration": 2500000,
            }
        ])
        playlists = await monitor.get_playlists()
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]["rating_key"], "50")
        self.assertEqual(playlists[0]["title"], "Synthwave Favorites")

        # 2. get_playlist_details
        monitor._fetch_plex_items = AsyncMock()
        monitor._fetch_plex_items.side_effect = [
            [{"ratingKey": "50", "title": "Synthwave Favorites", "summary": "Retro vibes"}],
            [{"ratingKey": "101", "title": "Track 1", "grandparentTitle": "Artist 1", "duration": 180000}],
        ]
        pl = await monitor.get_playlist_details("50")
        self.assertIsNotNone(pl)
        self.assertEqual(pl["rating_key"], "50")
        self.assertEqual(len(pl["tracks"]), 1)

        # 3. create_playlist
        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.post = MagicMock(return_value=FakeResponse(
            status=200,
            json_data={"MediaContainer": {"Metadata": [{"ratingKey": "51", "title": "Chillout", "leafCount": 2}]}},
            headers={"Content-Type": "application/json"},
        ))
        fake_session.put = MagicMock(return_value=FakeResponse(status=200))
        fake_session.delete = MagicMock(return_value=FakeResponse(status=200))
        monitor._session = fake_session

        created = await monitor.create_playlist("Chillout", ["101", "102"])
        self.assertIsNotNone(created)
        self.assertEqual(created["rating_key"], "51")

        # 4. add_to_playlist
        added = await monitor.add_to_playlist("51", ["103"])
        self.assertTrue(added)

        # 5. delete_playlist
        deleted = await monitor.delete_playlist("51")
        self.assertTrue(deleted)

        await monitor.close()

    async def test_rate_item_and_check_in_library(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.put = MagicMock(return_value=FakeResponse(status=200))
        monitor._session = fake_session

        ok = await monitor.rate_item("101", 5.0)
        self.assertTrue(ok)
        put_call = fake_session.put.call_args[0][0]
        self.assertIn("rating=10", put_call)

        clear_ok = await monitor.rate_item("101", 0)
        self.assertTrue(clear_ok)
        put_clear = fake_session.put.call_args[0][0]
        self.assertIn("rating=-1", put_clear)

        # Test check_in_library
        monitor.search_library = AsyncMock(return_value=[
            {"rating_key": "101", "title": "Plastic Love", "artist": "Mariya Takeuchi", "media_type": "track"}
        ])
        check1 = await monitor.check_in_library("Plastic Love", "Mariya Takeuchi")
        self.assertTrue(check1["in_library"])
        self.assertEqual(check1["matched_item"]["rating_key"], "101")

        check2 = await monitor.check_in_library("Nonexistent Song", "Unknown")
        self.assertFalse(check2["in_library"])

        await monitor.close()

    async def test_ai_tool_handlers(self):
        from tools.plex import (
            handle_search_plex_music,
            handle_get_plex_album_details,
            handle_get_plex_artist_details,
            handle_get_plex_library_stats,
            handle_manage_plex_playlist,
            handle_rate_plex_music,
            handle_add_music_suggestion,
        )

        mock_bot = MagicMock()
        mock_bot.owner_id = 123456789

        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        # Mock API cog with monitor
        api_cog = MagicMock()
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        # 1. handle_search_plex_music
        monitor.search_library = AsyncMock(return_value=[
            {
                "media_type": "track",
                "rating_key": "10",
                "title": "Song A",
                "artist": "Artist X",
                "album": "Album Y",
                "duration_ms": 180000,
                "audio_quality": "FLAC • 24-bit/96kHz",
                "view_count": 42,
            }
        ])
        out_search = await handle_search_plex_music(mock_bot, {"query": "Song A"}, "123456789")
        self.assertIn("Song A", out_search)
        self.assertIn("Artist X", out_search)
        self.assertIn("FLAC", out_search)

        # 2. handle_get_plex_album_details
        monitor.get_album_details = AsyncMock(return_value={
            "rating_key": "20",
            "title": "Album Y",
            "artist": "Artist X",
            "year": 2024,
            "audio_quality": "FLAC 24-bit",
            "view_count": 50,
            "total_duration_ms": 360000,
            "tracks": [{"index": 1, "title": "Song A", "duration_ms": 180000, "audio_quality": "FLAC", "view_count": 42, "rating_key": "10"}],
        })
        out_album = await handle_get_plex_album_details(mock_bot, {"query_or_id": "20"}, "123456789")
        self.assertIn("Album Y", out_album)
        self.assertIn("Tracklist:", out_album)

        # 3. handle_get_plex_artist_details
        monitor.get_artist_details = AsyncMock(return_value={
            "rating_key": "30",
            "name": "Artist X",
            "genres": ["Indie"],
            "total_albums": 2,
            "total_tracks": 15,
            "total_plays": 200,
            "top_tracks": [{"title": "Song A", "album": "Album Y", "duration_ms": 180000, "view_count": 42, "rating_key": "10"}],
            "albums": [{"title": "Album Y", "year": 2024, "track_count": 10, "view_count": 50, "rating_key": "20"}],
        })
        out_artist = await handle_get_plex_artist_details(mock_bot, {"query_or_id": "Artist X"}, "123456789")
        self.assertIn("Artist X", out_artist)
        self.assertIn("Top Tracks in Library:", out_artist)

        # 4. handle_get_plex_library_stats
        monitor.get_library_stats = AsyncMock(return_value={
            "configured": True,
            "total_artists": 10,
            "total_albums": 30,
            "total_tracks": 300,
            "top_artists": [{"name": "Artist X", "view_count": 200}],
            "top_genres": ["Indie", "Rock"],
            "recently_added_albums": [{"title": "Album Y", "artist": "Artist X", "year": 2024, "rating_key": "20"}],
        })
        out_stats = await handle_get_plex_library_stats(mock_bot, {}, "123456789")
        self.assertIn("Plex Music Library Overview", out_stats)
        self.assertIn("Total Artists: 10", out_stats)

        # 5. handle_manage_plex_playlist permissions: unauthorized user
        out_denied = await handle_manage_plex_playlist(mock_bot, {"action": "create", "title": "My Mix"}, "999999999")
        self.assertIn("Permission Denied", out_denied)

        # Authorized user
        monitor.create_playlist = AsyncMock(return_value={"rating_key": "60", "title": "My Mix", "track_count": 2})
        out_created = await handle_manage_plex_playlist(mock_bot, {"action": "create", "title": "My Mix", "track_keys": ["10", "11"]}, "123456789")
        self.assertIn("Successfully created", out_created)
        self.assertIn("My Mix", out_created)

        # 6. handle_rate_plex_music permissions and execution
        out_rate_denied = await handle_rate_plex_music(mock_bot, {"rating": 5}, "999999999")
        self.assertIn("Permission Denied", out_rate_denied)

        monitor.rate_item = AsyncMock(return_value=True)
        monitor.get_now_playing = AsyncMock(return_value={"track": {"rating_key": "10", "title": "Song A", "artist": "Artist X"}})
        out_rate_now = await handle_rate_plex_music(mock_bot, {"rating": 5}, "123456789")
        self.assertIn("Rated Plex item", out_rate_now)
        self.assertIn("5 star(s)", out_rate_now)

        # 7. handle_add_music_suggestion with kind parameter (regression test for kind bug)
        monitor.add_music_suggestion = AsyncMock(return_value={
            "id": "rec123",
            "title": "Discovery",
            "artist": "Daft Punk",
            "kind": "album",
            "youtube_url": "https://youtube.com/test",
        })
        monitor.check_in_library = AsyncMock(return_value={"in_library": True, "matched_item": {"rating_key": "20"}})
        out_suggest = await handle_add_music_suggestion(mock_bot, {"title": "Discovery", "artist": "Daft Punk", "kind": "album"}, "123456789")
        self.assertIn("Added album recommendation", out_suggest)
        self.assertIn("Discovery", out_suggest)
        self.assertIn("Already owned in your Plex library!", out_suggest)

        # 8. handle_get_plex_sonic_similar
        from tools.plex import handle_get_plex_sonic_similar
        monitor.get_sonically_similar = AsyncMock(return_value=[
            {
                "rating_key": "1002",
                "title": "Fly-day Chinatown",
                "artist": "Yasuha",
                "duration_ms": 207000,
                "distance": 0.1425,
                "audio_quality": "FLAC",
            }
        ])
        out_sonic = await handle_get_plex_sonic_similar(mock_bot, {"query_or_id": "Plastic Love"}, "123456789")
        self.assertIn("Fly-day Chinatown", out_sonic)
        self.assertIn("Distance: 0.1425", out_sonic)

        # Sonic fallback when not available
        monitor.get_sonically_similar = AsyncMock(return_value=[])
        out_sonic_empty = await handle_get_plex_sonic_similar(mock_bot, {"query_or_id": "Obscure Track"}, "123456789")
        self.assertIn("No sonically similar items found", out_sonic_empty)
        self.assertIn("Plex Pass", out_sonic_empty)

        await monitor.close()

    async def test_get_sonically_similar_and_sonic_playlist(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        nearest_items = [
            {
                "ratingKey": "501",
                "type": "track",
                "title": "Stay With Me",
                "grandparentTitle": "Miki Matsubara",
                "duration": 312000,
                "distance": "0.1582",
            },
            {
                "ratingKey": "502",
                "type": "track",
                "title": "4:00 AM",
                "grandparentTitle": "Taeko Onuki",
                "duration": 285000,
                "distance": "0.2104",
            },
        ]

        monitor._fetch_plex_items = AsyncMock(return_value=nearest_items)

        # 1. Query by numeric key
        similar = await monitor.get_sonically_similar("101", media_type="track", limit=5)
        self.assertEqual(len(similar), 2)
        self.assertEqual(similar[0]["title"], "Stay With Me")
        self.assertEqual(similar[0]["artist"], "Miki Matsubara")
        self.assertEqual(similar[0]["distance"], 0.1582)

        # 2. Query by name (resolves via search_library)
        monitor.search_library = AsyncMock(return_value=[{"rating_key": "101", "title": "Plastic Love"}])
        similar_by_name = await monitor.get_sonically_similar("Plastic Love", media_type="track")
        self.assertEqual(len(similar_by_name), 2)
        monitor.search_library.assert_called_once_with("Plastic Love", media_type="track", limit=1)

        # 3. create_sonic_playlist
        monitor.create_playlist = AsyncMock(return_value={"rating_key": "99", "title": "City Pop Sonic Mix", "track_count": 2})
        pl = await monitor.create_sonic_playlist("City Pop Sonic Mix", "101")
        self.assertIsNotNone(pl)
        self.assertEqual(pl["rating_key"], "99")
        monitor.create_playlist.assert_called_once_with(title="City Pop Sonic Mix", track_keys=["501", "502"])

        await monitor.close()

    async def test_edge_cases_key_normalization_and_combined_playlist_tracks(self):
        from tools.plex import (
            handle_manage_plex_playlist,
            handle_rate_plex_music,
            handle_get_plex_sonic_similar,
        )

        mock_bot = MagicMock()
        mock_bot.owner_id = 123456789

        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )
        api_cog = MagicMock()
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        # 1. Verify _normalize_key strips '#' prefix on numeric keys
        self.assertEqual(monitor._normalize_key("#101"), "101")
        self.assertEqual(monitor._normalize_key("  #999 "), "999")
        self.assertEqual(monitor._normalize_key("#1 Song"), "#1 Song")

        # 2. Verify manage_plex_playlist combines both track_keys (with '#') and track_queries
        monitor.search_library = AsyncMock(return_value=[{"rating_key": "202", "title": "Track Two"}])
        monitor.create_playlist = AsyncMock(return_value={"rating_key": "77", "title": "Combined Mix", "track_count": 2})

        res_create = await handle_manage_plex_playlist(
            mock_bot,
            {
                "action": "create",
                "title": "Combined Mix",
                "track_keys": ["#101"],
                "track_queries": ["Track Two"],
            },
            "123456789",
        )
        self.assertIn("Successfully created", res_create)
        monitor.create_playlist.assert_awaited_once_with(title="Combined Mix", track_keys=["101", "202"])

        # 3. Verify manage_plex_playlist list, view, add, delete with '#77' key
        monitor.get_playlists = AsyncMock(return_value=[
            {"rating_key": "77", "title": "Combined Mix", "track_count": 2, "duration_ms": 360000, "smart": False}
        ])
        res_list = await handle_manage_plex_playlist(mock_bot, {"action": "list"}, "999999")
        self.assertIn("Combined Mix", res_list)

        monitor.get_playlist_details = AsyncMock(return_value={
            "rating_key": "77",
            "title": "Combined Mix",
            "summary": "Test summary",
            "track_count": 1,
            "duration_ms": 180000,
            "tracks": [{"index": 1, "rating_key": "101", "title": "Track One", "artist": "Artist A", "duration_ms": 180000}],
        })
        res_view = await handle_manage_plex_playlist(mock_bot, {"action": "view", "playlist_id": "#77"}, "999999")
        self.assertIn("Combined Mix", res_view)
        self.assertIn("Track One", res_view)

        monitor.add_to_playlist = AsyncMock(return_value=True)
        res_add = await handle_manage_plex_playlist(
            mock_bot,
            {"action": "add", "playlist_id": "#77", "track_keys": ["#303"]},
            "123456789",
        )
        self.assertIn("Successfully added 1 track(s)", res_add)
        monitor.add_to_playlist.assert_awaited_once_with("77", ["303"])

        monitor.delete_playlist = AsyncMock(return_value=True)
        res_del = await handle_manage_plex_playlist(
            mock_bot,
            {"action": "delete", "playlist_id": "#77"},
            "123456789",
        )
        self.assertIn("Successfully deleted playlist [#77]", res_del)
        monitor.delete_playlist.assert_awaited_once_with("77")

        # 4. Verify rate_plex_music with '#101' does not trigger search_library
        monitor.search_library.reset_mock()
        monitor.rate_item = AsyncMock(return_value=True)
        res_rate = await handle_rate_plex_music(
            mock_bot,
            {"query_or_id": "#101", "rating": 4.5},
            "123456789",
        )
        self.assertIn("Rated Plex item [#101]", res_rate)
        monitor.search_library.assert_not_called()
        monitor.rate_item.assert_awaited_once_with("101", 4.5)

        # 5. Verify get_plex_sonic_similar with create_playlist=True for authorized vs unauthorized
        monitor.get_sonically_similar = AsyncMock(return_value=[
            {"rating_key": "501", "title": "Similar 1", "artist": "Artist 1", "duration_ms": 200000, "distance": 0.12},
        ])
        monitor.create_playlist = AsyncMock(return_value={"rating_key": "88", "title": "Sonic Mix", "track_count": 1})

        res_sonic_unauth = await handle_get_plex_sonic_similar(
            mock_bot,
            {"query_or_id": "#101", "create_playlist": True},
            "999999999",
        )
        self.assertIn("Playlist creation skipped", res_sonic_unauth)
        monitor.create_playlist.assert_not_called()

        res_sonic_auth = await handle_get_plex_sonic_similar(
            mock_bot,
            {"query_or_id": "#101", "create_playlist": True, "playlist_title": "Custom Sonic Mix"},
            "123456789",
        )
        self.assertIn("Created Plex playlist **Custom Sonic Mix** [#88]", res_sonic_auth)
        monitor.create_playlist.assert_awaited_once_with(title="Custom Sonic Mix", track_keys=["501"])

        await monitor.close()

    async def test_get_music_section_id_resolution(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        # 1. Normal resolution matching "Music"
        monitor._fetch_plex_container = AsyncMock(return_value={
            "Directory": [
                {"key": "1", "type": "movie", "title": "Movies"},
                {"key": "2", "type": "show", "title": "TV Shows"},
                {"key": "3", "type": "artist", "title": "Music"},
                {"key": "4", "type": "artist", "title": "Audiobooks"},
            ]
        })
        sec_id = await monitor.get_music_section_id()
        self.assertEqual(sec_id, "3")

        # 2. Case-insensitivity ("music")
        monitor._music_section_id = None
        monitor._fetch_plex_container = AsyncMock(return_value={
            "Directory": [
                {"key": "5", "type": "artist", "title": "music"},
            ]
        })
        sec_id = await monitor.get_music_section_id(force_refresh=True)
        self.assertEqual(sec_id, "5")

        # 3. Fallback to first artist section if "Music" title is missing
        monitor._music_section_id = None
        monitor._fetch_plex_container = AsyncMock(return_value={
            "Directory": [
                {"key": "10", "type": "movie", "title": "Movies"},
                {"key": "7", "type": "artist", "title": "FLAC Discography"},
            ]
        })
        sec_id = await monitor.get_music_section_id(force_refresh=True)
        self.assertEqual(sec_id, "7")

        # 4. Custom library name
        monitor_custom = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            plex_music_library="Hi-Fi",
            state_file=self.state_file,
        )
        monitor_custom._fetch_plex_container = AsyncMock(return_value={
            "Directory": [
                {"key": "3", "type": "artist", "title": "Music"},
                {"key": "9", "type": "artist", "title": "Hi-Fi"},
            ]
        })
        custom_sec_id = await monitor_custom.get_music_section_id()
        self.assertEqual(custom_sec_id, "9")

        await monitor.close()
        await monitor_custom.close()

    async def test_session_filtering_by_library_section(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            plex_user="sadako",
            state_file=self.state_file,
        )
        monitor._music_section_id = "3"

        # JSON Sessions: one track from "Music", one track from "Audiobooks"
        json_payload = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "track",
                        "title": "Audiobook Chapter 1",
                        "librarySectionTitle": "Audiobooks",
                        "librarySectionID": "4",
                        "User": {"title": "sadako"},
                        "Player": {"state": "playing"},
                    },
                    {
                        "type": "track",
                        "title": "Music Song 1",
                        "librarySectionTitle": "Music",
                        "librarySectionID": "3",
                        "User": {"title": "sadako"},
                        "Player": {"state": "playing"},
                    },
                ]
            }
        }
        tracks = monitor._parse_json_sessions(json_payload)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["title"], "Music Song 1")

        # XML Sessions: one from "Soundtracks", one from "Music"
        xml_text = """
        <MediaContainer>
            <Track type="track" title="Film Score 1" librarySectionTitle="Soundtracks" librarySectionID="8">
                <User title="sadako" />
                <Player state="playing" />
            </Track>
            <Track type="track" title="Music Song 2" librarySectionTitle="Music" librarySectionID="3">
                <User title="sadako" />
                <Player state="playing" />
            </Track>
        </MediaContainer>
        """
        xml_tracks = monitor._parse_xml_sessions(xml_text)
        self.assertEqual(len(xml_tracks), 1)
        self.assertEqual(xml_tracks[0]["title"], "Music Song 2")

        await monitor.close()

    async def test_recent_and_crate_scoped_to_music_section(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )
        monitor.get_music_section_id = AsyncMock(return_value="3")

        queried_paths = []
        async def fake_fetch(path):
            queried_paths.append(path)
            if "type=10" in path:
                return [
                    {"ratingKey": "101", "title": "Track 1", "librarySectionTitle": "Music", "librarySectionID": "3"},
                    {"ratingKey": "999", "title": "Other Track", "librarySectionTitle": "Podcasts", "librarySectionID": "9"},
                ]
            if "type=9" in path:
                return [
                    {"ratingKey": "201", "title": "Album 1", "librarySectionTitle": "Music", "librarySectionID": "3"},
                    {"ratingKey": "888", "title": "Other Album", "librarySectionTitle": "Podcasts", "librarySectionID": "9"},
                ]
            return []

        monitor._fetch_plex_items = fake_fetch

        # 1. Recent track
        recent = await monitor._fetch_recent_track_from_plex()
        self.assertIsNotNone(recent)
        self.assertEqual(recent["title"], "Track 1")
        self.assertTrue(any(p.startswith("/library/sections/3/all?type=10") for p in queried_paths))

        # 2. Crate data
        crate = await monitor.get_crate_data()
        self.assertEqual(len(crate["recent_tracks"]), 1)
        self.assertEqual(crate["recent_tracks"][0]["title"], "Track 1")
        self.assertEqual(len(crate["top_albums"]), 1)
        self.assertEqual(crate["top_albums"][0]["title"], "Album 1")
        self.assertTrue(any(p.startswith("/library/sections/3/all?type=9") for p in queried_paths))

        await monitor.close()

    async def test_search_and_stats_scoped_to_music_section(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )
        monitor.get_music_section_id = AsyncMock(return_value="3")

        hub_queried = []
        async def fake_container(path):
            hub_queried.append(path)
            if "hubs/search" in path:
                return {
                    "Hub": [
                        {
                            "type": "track",
                            "Metadata": [
                                {"type": "track", "ratingKey": "101", "title": "Music Track", "librarySectionTitle": "Music", "librarySectionID": "3"},
                                {"type": "track", "ratingKey": "999", "title": "Podcast Episode", "librarySectionTitle": "Podcasts", "librarySectionID": "9"},
                            ],
                        },
                        {
                            "type": "movie",
                            "Metadata": [
                                {"type": "movie", "ratingKey": "555", "title": "Some Movie", "librarySectionTitle": "Movies", "librarySectionID": "1"},
                            ],
                        },
                    ]
                }
            if "type=8" in path:
                return {"totalSize": "10"}
            if "type=9" in path:
                return {"totalSize": "50"}
            if "type=10" in path:
                return {"totalSize": "500"}
            return {}

        monitor._fetch_plex_container = fake_container
        monitor._fetch_plex_items = AsyncMock(return_value=[])

        # Test search scoping
        results = await monitor.search_library("test", media_type="all")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Music Track")
        self.assertIn("sectionId=3", hub_queried[0])

        # Test stats scoping
        stats = await monitor.get_library_stats()
        self.assertEqual(stats["library_name"], "Music")
        self.assertEqual(stats["section_id"], "3")
        self.assertEqual(stats["total_artists"], 10)
        self.assertEqual(stats["total_albums"], 50)
        self.assertEqual(stats["total_tracks"], 500)
        self.assertTrue(any(p.startswith("/library/sections/3/all?type=8") for p in hub_queried))
        self.assertTrue(any(p.startswith("/library/sections/3/all?type=9") for p in hub_queried))
        self.assertTrue(any(p.startswith("/library/sections/3/all?type=10") for p in hub_queried))

        # Test album & artist rejecting non-music section items
        monitor._fetch_plex_items = AsyncMock(return_value=[
            {"ratingKey": "900", "title": "Audiobook Title", "librarySectionTitle": "Audiobooks", "librarySectionID": "4"}
        ])
        self.assertIsNone(await monitor.get_album_details("900"))
        self.assertIsNone(await monitor.get_artist_details("900"))

        await monitor.close()

    async def test_update_metadata_fields_tags_artwork_and_refresh(self):
        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.put = MagicMock(return_value=FakeResponse(status=200))
        fake_session.post = MagicMock(return_value=FakeResponse(status=200))
        monitor._session = fake_session

        # Seed caches to verify invalidation
        monitor._album_cache["200"] = (9999999999.0, {"title": "Old Album"})
        monitor._artist_cache["300"] = (9999999999.0, {"name": "Old Artist"})

        async def fake_fetch_items(path):
            if path == "/library/metadata/101":
                return [{
                    "ratingKey": "101",
                    "type": "track",
                    "title": "Plastic Love",
                    "parentRatingKey": "200",
                    "grandparentRatingKey": "300",
                    "librarySectionID": "3",
                    "Genre": [{"tag": "Pop"}, {"tag": "Disco"}],
                }]
            if path == "/library/metadata/200":
                return [{
                    "ratingKey": "200",
                    "type": "album",
                    "title": "VARIETY",
                    "parentRatingKey": "300",
                    "librarySectionID": "3",
                    "Genre": [{"tag": "Pop"}, {"tag": "City Pop"}],
                    "Style": [{"tag": "80s"}],
                }]
            if path == "/library/metadata/300":
                return [{
                    "ratingKey": "300",
                    "type": "artist",
                    "title": "Mariya Takeuchi",
                    "librarySectionID": "3",
                }]
            return []

        monitor._fetch_plex_items = fake_fetch_items

        # 1. Update scalar fields + add/remove tags on a track
        res_track = await monitor.update_metadata(
            "#101",
            media_type="track",
            title="Plastic Love (12\" Extended Club Mix)",
            artist="Mariya Takeuchi",
            track_number=7,
            disc_number=1,
            genres=["City Pop", "Funk"],
            remove_genres=["Pop"],
            moods=["Upbeat"],
            unlock_fields=["summary"],
        )
        self.assertTrue(res_track["success"])
        self.assertEqual(res_track["rating_key"], "101")
        self.assertEqual(res_track["media_type"], "track")
        self.assertEqual(res_track["title"], "Plastic Love (12\" Extended Club Mix)")
        self.assertEqual(res_track["previous_title"], "Plastic Love")
        self.assertIn("title", res_track["updated_fields"])
        self.assertIn("genres", res_track["updated_fields"])
        self.assertIn("moods", res_track["updated_fields"])
        self.assertIn("unlocked_fields", res_track["updated_fields"])

        put_url = fake_session.put.call_args[0][0]
        self.assertIn("/library/sections/3/all?", put_url)
        self.assertIn("type=10", put_url)
        self.assertIn("id=101", put_url)
        self.assertIn("title.value=Plastic+Love+%2812%22+Extended+Club+Mix%29", put_url)
        self.assertIn("originalTitle.value=Mariya+Takeuchi", put_url)
        self.assertIn("index.value=7", put_url)
        self.assertIn("parentIndex.value=1", put_url)
        self.assertIn("genre%5B0%5D.tag.tag=City+Pop", put_url)
        self.assertIn("genre%5B1%5D.tag.tag=Funk", put_url)
        self.assertIn("genre%5B%5D.tag.tag-=Pop", put_url)
        self.assertIn("summary.locked=0", put_url)

        # Verify caches were invalidated
        self.assertEqual(len(monitor._album_cache), 0)
        self.assertEqual(len(monitor._artist_cache), 0)

        # 2. Replace tags on an album (resolving parent album from track key #101 when media_type='album')
        fake_session.put.reset_mock()
        res_album = await monitor.update_metadata(
            "#101",
            media_type="album",
            studio="Moon Records",
            originally_available_at="1984-04-25",
            genres=["City Pop", "Boogie"],
            replace_tags=True,
            poster_url="https://example.com/variety.jpg",
            refresh_metadata=True,
        )
        self.assertTrue(res_album["success"])
        self.assertEqual(res_album["rating_key"], "200")
        self.assertEqual(res_album["media_type"], "album")
        self.assertIn("studio", res_album["updated_fields"])
        self.assertIn("release_date", res_album["updated_fields"])
        self.assertIn("year", res_album["updated_fields"])
        self.assertIn("genres", res_album["updated_fields"])
        self.assertIn("poster", res_album["updated_fields"])
        self.assertIn("refresh_metadata", res_album["updated_fields"])

        # First PUT is the metadata edit, second PUT is the refresh
        self.assertEqual(fake_session.put.call_count, 2)
        album_put_url = fake_session.put.call_args_list[0][0][0]
        self.assertIn("type=9", album_put_url)
        self.assertIn("id=200", album_put_url)
        self.assertIn("studio.value=Moon+Records", album_put_url)
        self.assertIn("originallyAvailableAt.value=1984-04-25", album_put_url)
        self.assertIn("year.value=1984", album_put_url)
        # 'Pop' was in existing Genre list on #200 but not in ['City Pop', 'Boogie'], so it must be removed
        self.assertIn("genre%5B%5D.tag.tag-=Pop", album_put_url)

        refresh_put_url = fake_session.put.call_args_list[1][0][0]
        self.assertIn("/library/metadata/200/refresh", refresh_put_url)

        poster_post_url = fake_session.post.call_args[0][0]
        self.assertIn("/library/metadata/200/posters?url=https%3A%2F%2Fexample.com%2Fvariety.jpg", poster_post_url)

        # 3. Resolve artist from currently playing track when query_or_id is omitted and media_type='artist'
        monitor.get_now_playing = AsyncMock(return_value={
            "configured": True,
            "is_playing": True,
            "state": "playing",
            "track": {
                "rating_key": "101",
                "parent_rating_key": "200",
                "grandparent_rating_key": "300",
                "title": "Plastic Love",
                "artist": "Mariya Takeuchi",
                "album": "VARIETY",
            },
        })
        fake_session.put.reset_mock()
        res_artist = await monitor.update_metadata(
            "",
            media_type="artist",
            summary="Japanese singer-songwriter and pioneer of City Pop.",
            countries=["Japan"],
        )
        self.assertTrue(res_artist["success"])
        self.assertEqual(res_artist["rating_key"], "300")
        self.assertEqual(res_artist["media_type"], "artist")
        artist_put_url = fake_session.put.call_args[0][0]
        self.assertIn("type=8", artist_put_url)
        self.assertIn("id=300", artist_put_url)
        self.assertIn("country%5B0%5D.tag.tag=Japan", artist_put_url)

        # 4. Update non-music item (e.g. movie) resolved via global hub search
        monitor.search_library = AsyncMock(return_value=[])
        monitor._fetch_plex_container = AsyncMock(return_value={
            "Hub": [{
                "type": "movie",
                "Metadata": [{
                    "ratingKey": "777",
                    "type": "movie",
                    "title": "Inception",
                    "librarySectionID": "1",
                }],
            }]
        })
        fake_session.put.reset_mock()
        res_movie = await monitor.update_metadata(
            "Inception",
            media_type="movie",
            tagline="Your mind is the scene of the crime.",
            collections=["Sci-Fi", "Christopher Nolan"],
            directors=["Christopher Nolan"],
        )
        self.assertTrue(res_movie["success"])
        self.assertEqual(res_movie["rating_key"], "777")
        self.assertEqual(res_movie["media_type"], "movie")
        movie_put_url = fake_session.put.call_args[0][0]
        self.assertIn("/library/sections/1/all?", movie_put_url)
        self.assertIn("type=1", movie_put_url)
        self.assertIn("id=777", movie_put_url)
        self.assertIn("collection%5B0%5D.tag.tag=Sci-Fi", movie_put_url)
        self.assertIn("director%5B0%5D.tag.tag=Christopher+Nolan", movie_put_url)

        await monitor.close()

    async def test_handle_update_plex_metadata_tool_and_api_endpoint(self):
        from tools.plex import handle_update_plex_metadata, handle_manage_plex_playlist
        from tools.registry import execute_tool

        mock_bot = MagicMock()
        mock_bot.owner_id = 123456789

        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )
        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.put = MagicMock(return_value=FakeResponse(status=200))
        fake_session.post = MagicMock(return_value=FakeResponse(status=200))
        monitor._session = fake_session
        monitor._fetch_plex_items = AsyncMock(return_value=[{
            "ratingKey": "500",
            "type": "album",
            "title": "Discovery",
            "librarySectionID": "3",
        }])

        api_cog = API(mock_bot)
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        # 1. Permission Denied for unauthorized user
        denied = await handle_update_plex_metadata(
            mock_bot,
            {"query_or_id": "500", "title": "Discovery (Remastered)"},
            "999999999",
        )
        self.assertIn("Permission Denied", denied)

        # 2. Authorized metadata update via execute_tool with attached image in context
        context = {
            "current_attachments": [
                {
                    "filename": "custom_cover.png",
                    "bytes": b"\x89PNG\r\n\x1a\ncoverbytes",
                    "content_type": "image/png",
                }
            ]
        }
        out_ok = await execute_tool(
            mock_bot,
            "update_plex_metadata",
            {
                "query_or_id": "#500",
                "media_type": "album",
                "title": "Discovery (Japan Edition)",
                "genres": ["French House", "Electronic"],
                "use_attached_image": True,
            },
            "123456789",
            context=context,
        )
        self.assertIn("Successfully updated Plex Album [#500]", out_ok)
        self.assertIn("Discovery (Japan Edition)", out_ok)
        self.assertIn("poster", out_ok)
        fake_session.post.assert_called_once()
        post_args, post_kwargs = fake_session.post.call_args
        self.assertIn("/library/metadata/500/posters", post_args[0])
        self.assertEqual(post_kwargs.get("data"), b"\x89PNG\r\n\x1a\ncoverbytes")
        self.assertEqual(post_kwargs["headers"]["Content-Type"], "image/png")

        # 3. Playlist edit action via handle_manage_plex_playlist
        monitor._fetch_plex_items = AsyncMock(return_value=[{
            "ratingKey": "77",
            "type": "playlist",
            "title": "Old Mix",
        }])
        fake_session.put.reset_mock()
        pl_edit_out = await handle_manage_plex_playlist(
            mock_bot,
            {
                "action": "edit",
                "playlist_id": "#77",
                "title": "Late Night Synth",
                "summary": "Curated synthwave tracks",
            },
            "123456789",
        )
        self.assertIn("Successfully updated playlist **Late Night Synth** [#77]", pl_edit_out)
        pl_put_url = fake_session.put.call_args[0][0]
        self.assertIn("/playlists/77?", pl_put_url)
        self.assertIn("title=Late+Night+Synth", pl_put_url)

        await monitor.close()

    async def test_fix_match_and_agent_association(self):
        from tools.plex import handle_match_plex_media
        from tools.registry import execute_tool

        mock_bot = MagicMock()
        mock_bot.owner_id = 123456789

        monitor = PlexMusicMonitor(
            plex_url="http://mock-plex:32400",
            plex_token="valid_token",
            state_file=self.state_file,
        )
        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.put = MagicMock(return_value=FakeResponse(status=200))
        monitor._session = fake_session

        monitor._fetch_plex_items = AsyncMock(return_value=[{
            "ratingKey": "200",
            "type": "album",
            "title": "VARIETY",
            "librarySectionID": "3",
        }])

        queried_containers = []
        async def fake_container(path):
            queried_containers.append(path)
            if "/library/metadata/200/matches?" in path:
                return {
                    "SearchResult": [
                        {
                            "guid": "plex://album/5d07cd8e403c640290f12345",
                            "name": "VARIETY (30th Anniversary Edition)",
                            "year": 2014,
                            "score": 98,
                            "summary": "Mariya Takeuchi • Warner Music Japan",
                        },
                        {
                            "guid": "plex://album/5d07cd8e403c640290f67890",
                            "name": "VARIETY",
                            "year": 1984,
                            "score": 100,
                            "summary": "Mariya Takeuchi • Moon Records",
                        },
                    ]
                }
            return {}

        monitor._fetch_plex_container = fake_container

        api_cog = API(mock_bot)
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        # 1. Search Fix Match candidates via AI tool with shorthand agent alias ('musicbrainz')
        search_out = await execute_tool(
            mock_bot,
            "match_plex_media",
            {
                "action": "search",
                "query_or_id": "#200",
                "media_type": "album",
                "search_title": "VARIETY",
                "search_year": 1984,
                "agent": "musicbrainz",
                "language": "ja-JP",
            },
            "999999999",  # search is read-only
        )
        self.assertIn("Found 2 Fix Match candidate(s)", search_out)
        self.assertIn("org.musicbrainz.agents.music", search_out)
        self.assertIn("plex://album/5d07cd8e403c640290f67890", search_out)
        self.assertTrue(any("agent=org.musicbrainz.agents.music" in p and "manual=1" in p for p in queried_containers))

        # 2. Mutating 'match' action denied for unauthorized user
        denied_match = await handle_match_plex_media(
            mock_bot,
            {"action": "match", "query_or_id": "#200", "match_index": 1},
            "999999999",
        )
        self.assertIn("Permission Denied", denied_match)

        # 3. Apply candidate by match_index=1 for authorized owner
        fake_session.put.reset_mock()
        match_out = await execute_tool(
            mock_bot,
            "match_plex_media",
            {
                "action": "match",
                "query_or_id": "#200",
                "media_type": "album",
                "match_index": 1,
                "agent": "plex_music",
            },
            "123456789",
        )
        self.assertIn("Successfully matched Plex Album [#200] to **VARIETY** (1984) [Score: 100%]", match_out)
        self.assertIn("plex://album/5d07cd8e403c640290f67890", match_out)
        match_put_url = fake_session.put.call_args[0][0]
        self.assertIn("/library/metadata/200/match?", match_put_url)
        self.assertIn("guid=plex%3A%2F%2Falbum%2F5d07cd8e403c640290f67890", match_put_url)
        self.assertIn("name=VARIETY", match_put_url)
        self.assertIn("year=1984", match_put_url)

        # 4. Unmatch item via AI tool
        fake_session.put.reset_mock()
        unmatch_out = await execute_tool(
            mock_bot,
            "match_plex_media",
            {
                "action": "unmatch",
                "query_or_id": "#200",
                "media_type": "album",
            },
            "123456789",
        )
        self.assertIn("Successfully unmatched Plex Album [#200]", unmatch_out)
        unmatch_put_url = fake_session.put.call_args[0][0]
        self.assertTrue(unmatch_put_url.endswith("/library/metadata/200/unmatch"))

        # 5. Direct match_guid inside update_plex_metadata
        fake_session.put.reset_mock()
        upd_match_out = await execute_tool(
            mock_bot,
            "update_plex_metadata",
            {
                "query_or_id": "#200",
                "media_type": "album",
                "match_guid": "plex://album/5d07cd8e403c640290f67890",
                "match_name": "VARIETY",
                "match_year": 1984,
            },
            "123456789",
        )
        self.assertIn("agent_match", upd_match_out)
        self.assertIn("plex://album/5d07cd8e403c640290f67890", upd_match_out)

        await monitor.close()


class TestPlexDirectoryAndFiles(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmp_dir.name, "plex_last_played.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    async def test_track_file_path_extraction_json(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            plex_user="sadako",
            state_file=self.state_file,
        )

        session_payload = {
            "MediaContainer": {
                "size": 1,
                "Metadata": [
                    {
                        "type": "track",
                        "ratingKey": "101",
                        "title": "Airbag",
                        "grandparentTitle": "Radiohead",
                        "parentTitle": "OK Computer",
                        "duration": 284000,
                        "viewOffset": 50000,
                        "User": {"title": "sadako", "id": "1"},
                        "Player": {"state": "playing", "product": "Plexamp"},
                        "Media": [
                            {
                                "audioCodec": "flac",
                                "bitrate": 950,
                                "Part": [
                                    {
                                        "file": "/music/Radiohead/OK Computer/01 Airbag.flac",
                                        "size": 32000000,
                                        "Stream": [
                                            {
                                                "streamType": "2",
                                                "codec": "flac",
                                                "bitDepth": 24,
                                                "samplingRate": 96000,
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                json_data=session_payload,
                headers={"Content-Type": "application/json"},
            )
        )
        monitor._session = fake_session

        res = await monitor.get_now_playing()
        track = res["track"]
        self.assertIsNotNone(track)
        self.assertEqual(track["file_path"], "/music/Radiohead/OK Computer/01 Airbag.flac")
        self.assertEqual(track["file_name"], "01 Airbag.flac")
        self.assertEqual(track["folder_path"], "/music/Radiohead/OK Computer")
        self.assertEqual(track["file_size"], 32000000)

        # Test tool handler formatting
        from tools.plex import handle_get_plex_status
        mock_bot = MagicMock()
        api_cog = MagicMock()
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        status_text = await handle_get_plex_status(mock_bot, {}, "123456789")
        self.assertIn("Airbag", status_text)
        self.assertIn("File: `/music/Radiohead/OK Computer/01 Airbag.flac`", status_text)

        await monitor.close()

    async def test_track_file_path_extraction_xml(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )

        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <MediaContainer size="1">
            <Track ratingKey="202" type="track" title="Paranoid Android" grandparentTitle="Radiohead" parentTitle="OK Computer" duration="383000">
                <User id="1" title="sadako" />
                <Player state="paused" product="Plex Web" />
                <Media audioCodec="flac">
                    <Part file="/storage/music/Radiohead/OK Computer/02 Paranoid Android.flac" size="45000000">
                        <Stream streamType="2" codec="flac" bitDepth="16" samplingRate="44100" />
                    </Part>
                </Media>
            </Track>
        </MediaContainer>
        """
        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(
            return_value=FakeResponse(
                status=200,
                text_data=xml_response,
                headers={"Content-Type": "text/xml"},
            )
        )
        monitor._session = fake_session

        res = await monitor.get_now_playing()
        track = res["track"]
        self.assertIsNotNone(track)
        self.assertEqual(track["file_path"], "/storage/music/Radiohead/OK Computer/02 Paranoid Android.flac")
        self.assertEqual(track["file_name"], "02 Paranoid Android.flac")
        self.assertEqual(track["folder_path"], "/storage/music/Radiohead/OK Computer")

        await monitor.close()

    async def test_album_details_folder_and_track_files(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            state_file=self.state_file,
        )

        album_payload = {
            "MediaContainer": {
                "size": 1,
                "Metadata": [
                    {
                        "type": "album",
                        "ratingKey": "300",
                        "title": "Kid A",
                        "parentTitle": "Radiohead",
                        "year": 2000,
                    }
                ]
            }
        }
        tracks_payload = {
            "MediaContainer": {
                "size": 2,
                "Metadata": [
                    {
                        "type": "track",
                        "ratingKey": "301",
                        "index": 1,
                        "title": "Everything in Its Right Place",
                        "duration": 251000,
                        "Media": [
                            {
                                "audioCodec": "flac",
                                "Part": [
                                    {
                                        "file": "/music/Radiohead/Kid A/01 Everything in Its Right Place.flac",
                                        "size": 28000000,
                                        "Stream": [{"codec": "flac", "bitDepth": 24, "samplingRate": 48000}]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "type": "track",
                        "ratingKey": "302",
                        "index": 2,
                        "title": "Kid A",
                        "duration": 284000,
                        "Media": [
                            {
                                "audioCodec": "flac",
                                "Part": [
                                    {
                                        "file": "/music/Radiohead/Kid A/02 Kid A.flac",
                                        "size": 31000000,
                                        "Stream": [{"codec": "flac", "bitDepth": 24, "samplingRate": 48000}]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        def fake_get(url, headers=None):
            if "/children" in url:
                return FakeResponse(status=200, json_data=tracks_payload, headers={"Content-Type": "application/json"})
            return FakeResponse(status=200, json_data=album_payload, headers={"Content-Type": "application/json"})

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(side_effect=fake_get)
        monitor._session = fake_session
        monitor._lookup_youtube_music_url = AsyncMock(return_value="https://music.youtube.com/search?q=Kid+A")
        monitor._lookup_qobuz_url = AsyncMock(return_value="https://open.qobuz.com/album/123")

        album = await monitor.get_album_details("300")
        self.assertIsNotNone(album)
        self.assertEqual(album["folder_path"], "/music/Radiohead/Kid A")
        self.assertEqual(len(album["tracks"]), 2)
        self.assertEqual(album["tracks"][0]["file_name"], "01 Everything in Its Right Place.flac")
        self.assertEqual(album["tracks"][0]["file_path"], "/music/Radiohead/Kid A/01 Everything in Its Right Place.flac")
        self.assertEqual(album["tracks"][1]["file_name"], "02 Kid A.flac")

        # Test handle_get_plex_album_details
        from tools.plex import handle_get_plex_album_details
        mock_bot = MagicMock()
        api_cog = MagicMock()
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        album_text = await handle_get_plex_album_details(mock_bot, {"query_or_id": "300"}, "123456789")
        self.assertIn("Folder: `/music/Radiohead/Kid A`", album_text)
        self.assertIn("`01 Everything in Its Right Place.flac`", album_text)

        await monitor.close()

    async def test_get_music_section_locations_and_browse_folders(self):
        monitor = PlexMusicMonitor(
            plex_url="http://plex.local:32400",
            plex_token="secret_token",
            plex_music_library="Music",
            state_file=self.state_file,
        )

        sections_payload = {
            "MediaContainer": {
                "Directory": [
                    {
                        "key": "1",
                        "title": "Music",
                        "type": "artist",
                        "Location": [
                            {"id": 1, "path": "/volume1/Music"},
                            {"id": 2, "path": "/volume2/LosslessAudio"},
                        ]
                    }
                ]
            }
        }

        root_folder_payload = {
            "MediaContainer": {
                "size": 2,
                "title1": "Music",
                "Directory": [
                    {"key": "10", "title": "Electronic"},
                    {"key": "20", "title": "Rock"},
                ],
                "Metadata": []
            }
        }

        subfolder_payload = {
            "MediaContainer": {
                "size": 2,
                "title1": "Music",
                "title2": "Rock",
                "Directory": [
                    {"key": "21", "title": "Radiohead"},
                ],
                "Metadata": [
                    {
                        "type": "track",
                        "ratingKey": "999",
                        "title": "Bonus Song",
                        "duration": 180000,
                        "Media": [
                            {
                                "audioCodec": "flac",
                                "Part": [
                                    {"file": "/volume1/Music/Rock/Bonus Song.flac", "size": 15000000}
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        def fake_get(url, headers=None):
            if "/library/sections" in url and "/folder" not in url:
                return FakeResponse(status=200, json_data=sections_payload, headers={"Content-Type": "application/json"})
            if "/folder?parent=20" in url:
                return FakeResponse(status=200, json_data=subfolder_payload, headers={"Content-Type": "application/json"})
            if "/folder" in url:
                return FakeResponse(status=200, json_data=root_folder_payload, headers={"Content-Type": "application/json"})
            return FakeResponse(status=404)

        fake_session = MagicMock()
        fake_session.closed = False
        fake_session.close = AsyncMock()
        fake_session.get = MagicMock(side_effect=fake_get)
        monitor._session = fake_session

        locations = await monitor.get_music_section_locations()
        self.assertEqual(locations, ["/volume1/Music", "/volume2/LosslessAudio"])

        # Test root browsing
        root_res = await monitor.browse_folders()
        self.assertTrue(root_res["configured"])
        self.assertEqual(root_res["locations"], ["/volume1/Music", "/volume2/LosslessAudio"])
        self.assertEqual(len(root_res["directories"]), 2)
        self.assertEqual(root_res["directories"][0]["title"], "Electronic")
        self.assertEqual(root_res["directories"][1]["title"], "Rock")

        # Test subfolder browsing
        sub_res = await monitor.browse_folders(folder_key="20")
        self.assertEqual(sub_res["title2"], "Rock")
        self.assertEqual(len(sub_res["directories"]), 1)
        self.assertEqual(sub_res["directories"][0]["title"], "Radiohead")
        self.assertEqual(len(sub_res["files"]), 1)
        self.assertEqual(sub_res["files"][0]["file_name"], "Bonus Song.flac")
        self.assertEqual(sub_res["files"][0]["file_path"], "/volume1/Music/Rock/Bonus Song.flac")

        # Test browse_plex_directory tool handler
        from tools.plex import handle_browse_plex_directory
        from tools.registry import execute_tool

        mock_bot = MagicMock()
        api_cog = MagicMock()
        api_cog.plex_monitor = monitor
        mock_bot.get_cog = MagicMock(return_value=api_cog)

        out_root = await handle_browse_plex_directory(mock_bot, {}, "123456789")
        self.assertIn("Root Storage Locations: `/volume1/Music`, `/volume2/LosslessAudio`", out_root)
        self.assertIn("📁 **Electronic**", out_root)
        self.assertIn("📁 **Rock**", out_root)

        out_sub = await handle_browse_plex_directory(mock_bot, {"folder_key": "20"}, "123456789")
        self.assertIn("Rock", out_sub)
        self.assertIn("📁 **Radiohead**", out_sub)
        self.assertIn("Bonus Song.flac", out_sub)

        # Test registry dispatch
        reg_out = await execute_tool(mock_bot, "browse_plex_directory", {"folder_key": "20"}, "123456789")
        self.assertIn("Radiohead", reg_out)

        await monitor.close()

    def test_listening_query_tool_selection(self):
        from tools.registry import select_tools_for_query
        from utils.llm import query_targets_bot_tools

        queries = [
            "What am I currently listening to",
            "what am i listening to",
            "what is playing right now?",
            "what song is this",
            "check my music playback",
            "what tune is playing",
            "who is playing",
        ]
        for q in queries:
            self.assertTrue(query_targets_bot_tools(q), f"Failed query_targets_bot_tools for: {q}")
            tools = select_tools_for_query(q)
            names = [
                fn.name
                for t in tools
                if hasattr(t, "function_declarations") and t.function_declarations
                for fn in t.function_declarations
            ]
            self.assertIn("get_plex_status", names, f"get_plex_status missing for query: {q}")


if __name__ == "__main__":
    unittest.main()
