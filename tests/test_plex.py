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
        self.assertEqual(resp_ok.headers.get("Cache-Control"), "public, max-age=60")
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
        self.assertIn("avatar_url", data)
        self.assertIn("backdrop_url", data)

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
        self.assertEqual(resp_ok.headers.get("Cache-Control"), "public, max-age=60")
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




