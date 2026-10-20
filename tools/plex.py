"""
tools/plex.py - AI Tool definitions and handlers for Plex music status, library search,
album/artist details, audio playlists, track rating, metadata modification, and community music suggestions.
"""

import os
import discord
from google.genai import types

from utils.discord_helpers import is_user_authorized
from utils.plex import PlexMusicMonitor


GET_PLEX_STATUS_TOOL = types.FunctionDeclaration(
    name="get_plex_status",
    description=(
        "Checks the current Plex music playback status (Now Playing), including whether music "
        "is actively streaming, paused, or idle, along with the song title, artist, album, "
        "audio quality (e.g. FLAC 24-bit/96kHz), player device, and current playback progress."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "include_history": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, also includes recently played tracks and top rotation albums.",
            ),
        },
    ),
)

SEARCH_PLEX_MUSIC_TOOL = types.FunctionDeclaration(
    name="search_plex_music",
    description=(
        "Searches the user's Plex music library for songs (tracks), albums, or artists matching a query. "
        "Returns rating keys, title, artist, album, duration, audio quality (e.g. FLAC 24-bit/96kHz), "
        "and play counts."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Search keyword (track title, album name, or artist name).",
            ),
            "media_type": types.Schema(
                type=types.Type.STRING,
                description="Filter results by media type: 'all', 'track', 'album', or 'artist'. Defaults to 'all'.",
                enum=["all", "track", "album", "artist"],
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of results to return (default 10, max 25).",
            ),
        },
        required=["query"],
    ),
)

GET_PLEX_ALBUM_DETAILS_TOOL = types.FunctionDeclaration(
    name="get_plex_album_details",
    description=(
        "Retrieves detailed information about an album from Plex, including its release year, "
        "studio, genre tags, overall audio quality (e.g. FLAC 24-bit), streaming links (YouTube Music / Qobuz), "
        "and the full numbered tracklist with individual track durations and play counts. "
        "Accepts either an album ratingKey or album title."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description="The Plex album ratingKey (e.g. '12345') or the album title to search for.",
            ),
        },
        required=["query_or_id"],
    ),
)

GET_PLEX_ARTIST_DETAILS_TOOL = types.FunctionDeclaration(
    name="get_plex_artist_details",
    description=(
        "Retrieves an artist profile from Plex, including biography summary, genres, countries, "
        "total library plays, owned discography (albums in Plex with release years), top tracks, "
        "and streaming links. Accepts either an artist ratingKey or artist name."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description="The Plex artist ratingKey (e.g. '12345') or the artist name to search for.",
            ),
        },
        required=["query_or_id"],
    ),
)

GET_PLEX_LIBRARY_STATS_TOOL = types.FunctionDeclaration(
    name="get_plex_library_stats",
    description=(
        "Returns summary statistics for the user's Plex music library, including total artist, album, "
        "and track counts, top most played artists, top genres, and recently added albums."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "include_recent": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to include recently added albums (defaults to true).",
            ),
        },
    ),
)

MANAGE_PLEX_PLAYLIST_TOOL = types.FunctionDeclaration(
    name="manage_plex_playlist",
    description=(
        "Manages audio playlists on Plex Media Server. Actions: "
        "'list' (view all playlists), 'view' (inspect tracks in a playlist), "
        "'create' (create a new playlist), 'add' (append tracks to a playlist), "
        "'edit' (update playlist title or summary), "
        "or 'delete' (delete a playlist). Creating, adding, editing, and deleting require bot owner authorization."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Action to perform: 'list', 'view', 'create', 'add', 'edit', or 'delete'.",
                enum=["list", "view", "create", "add", "edit", "delete"],
            ),
            "playlist_id": types.Schema(
                type=types.Type.STRING,
                description="The Plex playlist ratingKey or title (required for 'view', 'add', 'edit', 'delete').",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="Title for the new playlist (required for 'create') or updated title (for 'edit').",
            ),
            "summary": types.Schema(
                type=types.Type.STRING,
                description="Optional description/summary for the playlist (for 'edit').",
            ),
            "track_keys": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of track ratingKeys to add to the playlist (for 'create' or 'add').",
            ),
            "track_queries": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of track titles/names to search and add to the playlist (alternative to track_keys).",
            ),
        },
        required=["action"],
    ),
)

UPDATE_PLEX_METADATA_TOOL = types.FunctionDeclaration(
    name="update_plex_metadata",
    description=(
        "Modifies metadata, tags, artwork (cover/poster or background art), field locks, or triggers a metadata refresh "
        "for content on the Plex Media Server (tracks, albums, artists, playlists, movies, TV shows, seasons, or episodes). "
        "Can target an item by ratingKey, title/search query, or the currently playing item (if query_or_id is omitted). "
        "Also supports uploading an image attached to the Discord message as custom cover/poster or backdrop art. "
        "Requires bot owner or Plex admin authorization."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The Plex ratingKey (e.g. '12345' or '#12345') or title/name of the item to modify. "
                    "If omitted (or 'now_playing'), targets the currently playing track (or its album/artist when media_type is 'album'/'artist')."
                ),
            ),
            "media_type": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Media type of the target item: 'track', 'album', 'artist', 'playlist', 'movie', 'show', 'season', 'episode', or 'all'. "
                    "Defaults to 'all' (or 'track' when targeting now playing)."
                ),
                enum=["all", "track", "album", "artist", "playlist", "movie", "show", "season", "episode"],
            ),
            "artist_filter": types.Schema(
                type=types.Type.STRING,
                description="Optional artist name to disambiguate when searching for a track or album by title.",
            ),
            "album_filter": types.Schema(
                type=types.Type.STRING,
                description="Optional album title to disambiguate when searching for a track by title.",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="New title for the track, album, artist, playlist, movie, show, or episode.",
            ),
            "sort_title": types.Schema(
                type=types.Type.STRING,
                description="New sort title (titleSort) for alphabetical ordering in Plex.",
            ),
            "original_title": types.Schema(
                type=types.Type.STRING,
                description="New original title (originalTitle). For music tracks, Plex uses originalTitle as the track artist.",
            ),
            "artist": types.Schema(
                type=types.Type.STRING,
                description="New artist name (track artist for tracks, album artist for albums, or artist title).",
            ),
            "album_artist": types.Schema(
                type=types.Type.STRING,
                description="New album artist name.",
            ),
            "album": types.Schema(
                type=types.Type.STRING,
                description="New album title.",
            ),
            "summary": types.Schema(
                type=types.Type.STRING,
                description="New summary, biography, description, or review text.",
            ),
            "tagline": types.Schema(
                type=types.Type.STRING,
                description="New tagline.",
            ),
            "year": types.Schema(
                type=types.Type.INTEGER,
                description="New release year (e.g. 1984).",
            ),
            "release_date": types.Schema(
                type=types.Type.STRING,
                description="New release / originally available date in YYYY-MM-DD format.",
            ),
            "studio": types.Schema(
                type=types.Type.STRING,
                description="New studio or record label name.",
            ),
            "track_number": types.Schema(
                type=types.Type.INTEGER,
                description="New track number (or episode/season index).",
            ),
            "disc_number": types.Schema(
                type=types.Type.INTEGER,
                description="New disc number (or parent season index).",
            ),
            "content_rating": types.Schema(
                type=types.Type.STRING,
                description="New content rating (e.g. 'Explicit', 'PG-13', 'R').",
            ),
            "user_rating": types.Schema(
                type=types.Type.NUMBER,
                description="New user star rating from 0 to 5 stars (0 clears rating).",
            ),
            "genres": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of genre tags to add (or set if replace_tags is true).",
            ),
            "remove_genres": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of genre tags to remove.",
            ),
            "styles": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of style tags to add (or set if replace_tags is true).",
            ),
            "remove_styles": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of style tags to remove.",
            ),
            "moods": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of mood tags to add (or set if replace_tags is true).",
            ),
            "remove_moods": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of mood tags to remove.",
            ),
            "collections": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of collection tags to add (or set if replace_tags is true).",
            ),
            "remove_collections": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of collection tags to remove.",
            ),
            "labels": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of sharing/filter label tags to add (or set if replace_tags is true).",
            ),
            "remove_labels": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of label tags to remove.",
            ),
            "countries": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of country tags to add (or set if replace_tags is true).",
            ),
            "remove_countries": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of country tags to remove.",
            ),
            "similar_artists": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of similar artist tags to add (or set if replace_tags is true).",
            ),
            "remove_similar_artists": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of similar artist tags to remove.",
            ),
            "directors": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of director tags to add (for movies/episodes/music videos).",
            ),
            "remove_directors": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of director tags to remove.",
            ),
            "writers": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of writer tags to add.",
            ),
            "remove_writers": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of writer tags to remove.",
            ),
            "producers": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of producer tags to add.",
            ),
            "remove_producers": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="List of producer tags to remove.",
            ),
            "replace_tags": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, provided tag lists (e.g. genres) overwrite existing tags in that category instead of merging.",
            ),
            "poster_url": types.Schema(
                type=types.Type.STRING,
                description="Direct http/https image URL to set as the item's cover art / poster / avatar.",
            ),
            "background_art_url": types.Schema(
                type=types.Type.STRING,
                description="Direct http/https image URL to set as the item's background art / backdrop.",
            ),
            "use_attached_image": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, uploads an image attached to the current or replied Discord message as artwork.",
            ),
            "artwork_target": types.Schema(
                type=types.Type.STRING,
                description="When use_attached_image is true, whether to set it as 'poster' (cover/avatar, default) or 'art' (background/backdrop).",
                enum=["poster", "art"],
            ),
            "lock_fields": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to lock modified fields on Plex so future scans do not overwrite them (defaults to true).",
            ),
            "unlock_fields": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Optional list of field names to unlock (e.g. ['title', 'summary', 'genres', 'poster']).",
            ),
            "refresh_metadata": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, triggers a Plex metadata refresh on the item.",
            ),
            "match_guid": types.Schema(
                type=types.Type.STRING,
                description="Optional metadata agent GUID (e.g. 'plex://album/...', 'mbid://...') to associate/Fix Match the item with.",
            ),
            "match_name": types.Schema(
                type=types.Type.STRING,
                description="Optional title/name override when applying a metadata agent match.",
            ),
            "match_year": types.Schema(
                type=types.Type.INTEGER,
                description="Optional release year override when searching or applying a metadata agent match.",
            ),
            "match_agent": types.Schema(
                type=types.Type.STRING,
                description="Optional metadata agent identifier or alias (e.g. 'plex_music', 'musicbrainz', 'lastfm', 'plex_movie', 'tmdb', 'imdb', 'tvdb', 'plex_series').",
            ),
            "match_language": types.Schema(
                type=types.Type.STRING,
                description="Optional language code (e.g. 'en-US', 'ja-JP') when querying metadata agent matches.",
            ),
            "auto_match": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, automatically searches the Plex metadata agent and binds the item to the best match.",
            ),
            "unmatch": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, removes/strips the current metadata agent association from the item.",
            ),
        },
    ),
)

MATCH_PLEX_MEDIA_TOOL = types.FunctionDeclaration(
    name="match_plex_media",
    description=(
        "Manages Plex 'Fix Match' / Metadata Agent Association for library content (albums, artists, tracks, movies, TV shows). "
        "Actions: "
        "'search' (query metadata agents for candidate matches with confidence scores and GUIDs), "
        "'match' (apply a specific match by GUID or candidate index, or auto-match the top agent result), "
        "or 'unmatch' (remove the current agent association). "
        "Applying a match or unmatching requires bot owner or Plex admin authorization."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Action to perform: 'search' (list candidate matches), 'match' (apply a match), or 'unmatch' (remove match).",
                enum=["search", "match", "unmatch"],
            ),
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The Plex ratingKey (e.g. '12345' or '#12345') or title/name of the item in Plex. "
                    "If omitted, targets the currently playing item."
                ),
            ),
            "media_type": types.Schema(
                type=types.Type.STRING,
                description="Media type of the target item: 'album', 'artist', 'track', 'movie', 'show', 'season', 'episode', or 'all'.",
                enum=["all", "album", "artist", "track", "movie", "show", "season", "episode"],
            ),
            "artist_filter": types.Schema(
                type=types.Type.STRING,
                description="Optional artist name to disambiguate when resolving the item in Plex.",
            ),
            "album_filter": types.Schema(
                type=types.Type.STRING,
                description="Optional album title to disambiguate when resolving the item in Plex.",
            ),
            "guid": types.Schema(
                type=types.Type.STRING,
                description="Explicit metadata agent GUID (e.g. 'plex://album/5d07cd...') to bind to (for 'match').",
            ),
            "match_index": types.Schema(
                type=types.Type.INTEGER,
                description="Zero-based index of the candidate from 'search' results to apply (defaults to 0 = best match).",
            ),
            "search_title": types.Schema(
                type=types.Type.STRING,
                description="Override title (or external ID such as 'mbid://...', 'imdb://tt...', 'tmdb://...') to query the metadata agent with.",
            ),
            "search_year": types.Schema(
                type=types.Type.INTEGER,
                description="Override release year to query the metadata agent with.",
            ),
            "agent": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Metadata agent identifier or shorthand alias to use: 'plex_music' ('tv.plex.agents.music'), "
                    "'musicbrainz' ('org.musicbrainz.agents.music'), 'lastfm' ('com.plexapp.agents.lastfm'), "
                    "'plex_movie' ('tv.plex.agents.movie'), 'tmdb' ('com.plexapp.agents.themoviedb'), "
                    "'imdb' ('com.plexapp.agents.imdb'), 'plex_series' ('tv.plex.agents.series'), "
                    "'tvdb' ('com.plexapp.agents.thetvdb'), or 'personal' ('com.plexapp.agents.none')."
                ),
            ),
            "language": types.Schema(
                type=types.Type.STRING,
                description="Optional language code for the metadata agent (e.g. 'en-US', 'ja-JP').",
            ),
        },
        required=["action"],
    ),
)

RATE_PLEX_MUSIC_TOOL = types.FunctionDeclaration(
    name="rate_plex_music",
    description=(
        "Rates a song or album on Plex from 1 to 5 stars (or 0 to clear rating). "
        "If query_or_id is omitted, rates the currently playing track. Requires bot owner authorization."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "rating": types.Schema(
                type=types.Type.NUMBER,
                description="Rating from 0 to 5 stars (e.g. 5 for 5 stars, 0 to clear).",
            ),
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description="Optional track or album ratingKey or search title. If omitted, rates the currently playing track.",
            ),
        },
        required=["rating"],
    ),
)

GET_PLEX_SONIC_SIMILAR_TOOL = types.FunctionDeclaration(
    name="get_plex_sonic_similar",
    description=(
        "Uses Plex Sonic Analysis to find songs or albums in the user's library that are "
        "acoustically and sonically similar to a specified track or album (or the currently playing track). "
        "Requires Plex Sonic Analysis to be enabled on the music library (Plex Pass). "
        "Can optionally auto-create a Plex playlist from the matched sonic recommendations."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query_or_id": types.Schema(
                type=types.Type.STRING,
                description="The song title, album title, or Plex ratingKey to find acoustic matches for. If omitted, uses the currently playing track.",
            ),
            "media_type": types.Schema(
                type=types.Type.STRING,
                description="Either 'track' (default) or 'album'.",
                enum=["track", "album"],
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Number of sonically similar items to return (default 10, max 25).",
            ),
            "create_playlist": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, creates a new Plex audio playlist containing the sonically similar tracks. Requires bot owner authorization.",
            ),
            "playlist_title": types.Schema(
                type=types.Type.STRING,
                description="Custom title for the playlist if create_playlist is true (e.g. 'Sonic Mix: Midnight City').",
            ),
        },
    ),
)

GET_MUSIC_SUGGESTIONS_TOOL = types.FunctionDeclaration(
    name="get_music_suggestions",
    description=(
        "Retrieves community song and album recommendations from the "
        "shisho_music_recommendations database collection."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)

ADD_MUSIC_SUGGESTION_TOOL = types.FunctionDeclaration(
    name="add_music_suggestion",
    description=(
        "Adds a song or album recommendation to the shisho_music_recommendations "
        "collection in PocketBase. If a link (url) is provided, the song/album title "
        "and artist are optional and will be automatically extracted."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="The song or album title (optional if url is provided).",
            ),
            "artist": types.Schema(
                type=types.Type.STRING,
                description="The artist name (optional if url is provided).",
            ),
            "kind": types.Schema(
                type=types.Type.STRING,
                description="Either 'song' or 'album'. Defaults to 'song'.",
                enum=["song", "album"],
            ),
            "url": types.Schema(
                type=types.Type.STRING,
                description="Link to the track/album (Spotify, Apple Music, YouTube, Bandcamp, Qobuz, etc.).",
            ),
            "message": types.Schema(
                type=types.Type.STRING,
                description="Optional personal note or reason for recommending.",
            ),
            "submitter": types.Schema(
                type=types.Type.STRING,
                description="Name of the person recommending the music.",
            ),
        },
    ),
)

DELETE_MUSIC_SUGGESTION_TOOL = types.FunctionDeclaration(
    name="delete_music_suggestion",
    description=(
        "Deletes a music recommendation from shisho_music_recommendations by its ID, "
        "song/album title, or artist."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The ID, title, or artist of the music suggestion to delete.",
            ),
        },
        required=["query"],
    ),
)


def format_duration_mmss(ms: int) -> str:
    total_sec = max(0, int((ms or 0) // 1000))
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _get_monitor(bot) -> tuple[PlexMusicMonitor, bool]:
    api_cog = bot.get_cog("API") if hasattr(bot, "get_cog") else None
    monitor = getattr(api_cog, "plex_monitor", None) if api_cog else None
    if monitor is not None:
        return monitor, False
    return PlexMusicMonitor(), True


async def _is_authorized_plex_admin(bot, user_id: str) -> bool:
    """Checks if the user is authorized to perform mutating Plex actions."""
    if not user_id:
        return False
    owner_id_env = os.getenv("OWNER_ID", "").strip()
    if owner_id_env and str(user_id).strip() == owner_id_env:
        return True
    try:
        uid_int = int(user_id)
        bot_owner = getattr(bot, "owner_id", None)
        if bot_owner is not None and uid_int == bot_owner:
            return True
        if getattr(bot, "owner_ids", None) and uid_int in bot.owner_ids:
            return True
        if hasattr(bot, "is_owner"):
            obj = discord.Object(id=uid_int)
            if await bot.is_owner(obj):
                return True

        from utils.discord_helpers import get_cog_whitelist
        if uid_int in get_cog_whitelist("PLEX"):
            return True
    except Exception:
        pass
    return False


async def handle_get_plex_status(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_status AI tool."""
    monitor, owns_monitor = _get_monitor(bot)
    include_history = bool((args or {}).get("include_history", False))

    try:
        status = await monitor.get_now_playing()
        crate = await monitor.get_crate_data() if include_history and status.get("configured") else None
    finally:
        if owns_monitor:
            await monitor.close()

    if not status.get("configured"):
        return (
            "Plex monitoring is not configured yet (PLEX_URL and/or PLEX_TOKEN "
            "are missing from the environment)."
        )

    state = status.get("state", "stopped")
    is_playing = bool(status.get("is_playing"))
    track = status.get("track")

    lines: list[str] = []
    if not track:
        lines.append("Plex is configured and reachable, but nothing is currently playing and no recent track was found.")
    else:
        title = track.get("title") or "Untitled Track"
        artist = track.get("artist") or "Unknown Artist"
        album = track.get("album") or ""
        year = track.get("year")
        player = track.get("player") or "Plex"
        audio_quality = track.get("audio_quality") or ""
        progress_ms = int(track.get("progress_ms") or 0)
        duration_ms = int(track.get("duration_ms") or 0)
        pct = float(track.get("progress_percent") or 0.0)

        album_str = f" — Album: {album}" + (f" ({year})" if year else "") if album else ""
        quality_str = f" [{audio_quality}]" if audio_quality else ""
        prog_str = (
            f"{format_duration_mmss(progress_ms)} / {format_duration_mmss(duration_ms)} ({pct:.1f}%)"
            if duration_ms > 0
            else "Unknown duration"
        )

        if is_playing or state == "paused":
            state_label = "Playing" if state == "playing" else state.capitalize()
            lines.append(f"Plex Status: {state_label} on {player}{quality_str}")
            lines.append(f"Track: \"{title}\" by {artist}{album_str}")
            lines.append(f"Progress: {prog_str}")
        else:
            lines.append("Plex Status: Idle (nothing actively streaming right now)")
            lines.append(f"Last Played Track: \"{title}\" by {artist}{album_str}{quality_str}")

    if crate:
        top_albums = crate.get("top_albums") or []
        recent_tracks = crate.get("recent_tracks") or []
        if top_albums:
            top_summary = ", ".join(
                f"\"{a.get('title')}\" by {a.get('artist')} ({a.get('view_count', 0)} plays)"
                for a in top_albums[:5]
            )
            lines.append(f"Top Rotation: {top_summary}")
        if recent_tracks:
            recent_summary = ", ".join(
                f"\"{t.get('title')}\" by {t.get('artist')}"
                for t in recent_tracks[:5]
            )
            lines.append(f"Recently Played: {recent_summary}")

    return "\n".join(lines)


async def handle_search_plex_music(bot, args: dict, user_id: str) -> str:
    """Handler for the search_plex_music AI tool."""
    raw = args or {}
    query = str(raw.get("query", "")).strip()
    media_type = str(raw.get("media_type", "all") or "all").strip().lower()
    try:
        limit = int(raw.get("limit") or 10)
    except (ValueError, TypeError):
        limit = 10

    if not query:
        return "Error: 'query' parameter is required for searching Plex music."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        results = await monitor.search_library(query=query, media_type=media_type, limit=limit)
    finally:
        if owns_monitor:
            await monitor.close()

    if not results:
        return f"No results found in Plex music library for '{query}'."

    lines = [f"Found {len(results)} Plex music result(s) for '{query}':"]
    for item in results:
        m_type = item.get("media_type", "item")
        k = item.get("rating_key") or "N/A"
        if m_type == "track":
            title = item.get("title") or "Untitled Track"
            artist = item.get("artist") or "Unknown Artist"
            album = item.get("album") or ""
            dur = format_duration_mmss(item.get("duration_ms") or 0)
            quality = f" [{item['audio_quality']}]" if item.get("audio_quality") else ""
            plays = item.get("view_count", 0)
            alb_str = f" — *{album}*" if album else ""
            lines.append(f"- [Track #{k}] **{title}** by {artist}{alb_str} ({dur}){quality} • {plays} plays")
        elif m_type == "album":
            title = item.get("title") or "Untitled Album"
            artist = item.get("artist") or "Unknown Artist"
            year = f" ({item['year']})" if item.get("year") else ""
            plays = item.get("view_count", 0)
            lines.append(f"- [Album #{k}] **{title}** by {artist}{year} • {plays} plays")
        elif m_type == "artist":
            title = item.get("title") or "Unknown Artist"
            genres = f" ({', '.join(item['genres'][:3])})" if item.get("genres") else ""
            lines.append(f"- [Artist #{k}] **{title}**{genres}")
    return "\n".join(lines)


async def handle_get_plex_album_details(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_album_details AI tool."""
    raw = args or {}
    query_or_id = str(raw.get("query_or_id", "")).strip()
    if not query_or_id:
        return "Error: 'query_or_id' parameter is required."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        album = await monitor.get_album_details(query_or_id)
    finally:
        if owns_monitor:
            await monitor.close()

    if not album:
        return f"Album not found in Plex for '{query_or_id}'."

    title = album.get("title") or "Untitled Album"
    artist = album.get("artist") or "Unknown Artist"
    year = f" ({album['year']})" if album.get("year") else ""
    quality = f" [{album['audio_quality']}]" if album.get("audio_quality") else ""
    plays = album.get("view_count", 0)
    total_dur = format_duration_mmss(album.get("total_duration_ms") or 0)
    tracks = album.get("tracks") or []

    lines = [
        f"**{title}** by {artist}{year}{quality}",
        f"Key: #{album.get('rating_key')} • Total Tracks: {len(tracks)} ({total_dur}) • Plays: {plays}",
    ]
    if album.get("studio"):
        lines.append(f"Studio: {album['studio']}")
    if album.get("genres"):
        lines.append(f"Genres: {', '.join(album['genres'])}")
    if album.get("summary"):
        lines.append(f"Summary: {album['summary'][:300]}...")

    external = []
    if album.get("youtube_music_url"):
        external.append(f"YouTube Music: {album['youtube_music_url']}")
    if album.get("qobuz_url"):
        external.append(f"Qobuz: {album['qobuz_url']}")
    if external:
        lines.append(f"Streaming Links: {' | '.join(external)}")

    lines.append("\nTracklist:")
    for t in tracks:
        dur = format_duration_mmss(t.get("duration_ms") or 0)
        t_quality = f" [{t['audio_quality']}]" if t.get("audio_quality") else ""
        t_plays = f" • {t['view_count']} plays" if t.get("view_count") else ""
        lines.append(f"{t.get('index', 0)}. \"{t.get('title')}\" ({dur}){t_quality}{t_plays} [#{t.get('rating_key')}]")

    return "\n".join(lines)


async def handle_get_plex_artist_details(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_artist_details AI tool."""
    raw = args or {}
    query_or_id = str(raw.get("query_or_id", "")).strip()
    if not query_or_id:
        return "Error: 'query_or_id' parameter is required."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        artist = await monitor.get_artist_details(query_or_id)
    finally:
        if owns_monitor:
            await monitor.close()

    if not artist:
        return f"Artist not found in Plex for '{query_or_id}'."

    name = artist.get("name") or "Unknown Artist"
    lines = [f"**{name}** (Plex Key: #{artist.get('rating_key')})"]
    if artist.get("genres"):
        lines.append(f"Genres: {', '.join(artist['genres'])}")
    if artist.get("countries"):
        lines.append(f"Origin: {', '.join(artist['countries'])}")

    total_albums = artist.get("total_albums", 0)
    total_tracks = artist.get("total_tracks", 0)
    total_plays = artist.get("total_plays", 0)
    lines.append(f"Library Stats: {total_albums} albums, {total_tracks} tracks, {total_plays} total plays")

    if artist.get("summary"):
        lines.append(f"Bio: {artist['summary'][:400]}...")

    external = []
    if artist.get("youtube_music_url"):
        external.append(f"YouTube Music: {artist['youtube_music_url']}")
    if artist.get("qobuz_url"):
        external.append(f"Qobuz: {artist['qobuz_url']}")
    if external:
        lines.append(f"Artist Links: {' | '.join(external)}")

    top_tracks = artist.get("top_tracks") or []
    if top_tracks:
        lines.append("\nTop Tracks in Library:")
        for t in top_tracks[:5]:
            dur = format_duration_mmss(t.get("duration_ms") or 0)
            lines.append(f"- \"{t.get('title')}\" from *{t.get('album')}* ({dur}) • {t.get('view_count', 0)} plays [#{t.get('rating_key')}]")

    albums = artist.get("albums") or []
    if albums:
        lines.append("\nAlbums in Library:")
        for a in albums:
            year = f" ({a['year']})" if a.get("year") else ""
            lines.append(f"- *{a.get('title')}*{year} • {a.get('track_count', 0)} tracks, {a.get('view_count', 0)} plays [#{a.get('rating_key')}]")

    return "\n".join(lines)


async def handle_get_plex_library_stats(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_library_stats AI tool."""
    raw = args or {}
    include_recent = raw.get("include_recent", True)

    monitor, owns_monitor = _get_monitor(bot)
    try:
        stats = await monitor.get_library_stats()
    finally:
        if owns_monitor:
            await monitor.close()

    if not stats.get("configured"):
        return "Plex monitoring is not configured."

    lib_name = stats.get("library_name")
    header = f"**Plex Music Library Overview ({lib_name})**" if lib_name else "**Plex Music Library Overview**"
    lines = [
        header,
        f"- Total Artists: {stats.get('total_artists', 0)}",
        f"- Total Albums: {stats.get('total_albums', 0)}",
        f"- Total Tracks: {stats.get('total_tracks', 0)}",
    ]

    top_artists = stats.get("top_artists") or []
    if top_artists:
        top_str = ", ".join(f"{a['name']} ({a['view_count']} plays)" for a in top_artists[:5])
        lines.append(f"- Top Played Artists: {top_str}")

    top_genres = stats.get("top_genres") or []
    if top_genres:
        lines.append(f"- Top Genres: {', '.join(top_genres[:6])}")

    if include_recent:
        recent = stats.get("recently_added_albums") or []
        if recent:
            lines.append("\nRecently Added Albums:")
            for a in recent[:5]:
                year = f" ({a['year']})" if a.get("year") else ""
                lines.append(f"- *{a.get('title')}* by {a.get('artist')}{year} [#{a.get('rating_key')}]")

    return "\n".join(lines)


async def handle_manage_plex_playlist(bot, args: dict, user_id: str) -> str:
    """Handler for the manage_plex_playlist AI tool."""
    raw = args or {}
    action = str(raw.get("action", "list") or "list").strip().lower()
    playlist_id = PlexMusicMonitor._normalize_key(raw.get("playlist_id", ""))
    title = str(raw.get("title", "")).strip()
    summary = raw.get("summary")
    track_keys = raw.get("track_keys") or []
    track_queries = raw.get("track_queries") or []

    # Mutating actions check
    if action in ("create", "add", "edit", "update", "delete"):
        if not await _is_authorized_plex_admin(bot, user_id):
            return "Permission Denied: Only the bot owner or authorized users can create, modify, or delete Plex playlists."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        async def _resolve_all_track_keys() -> list[str]:
            keys: list[str] = []
            seen: set[str] = set()
            for k in track_keys:
                nk = PlexMusicMonitor._normalize_key(k)
                if nk.isdigit() and nk not in seen:
                    seen.add(nk)
                    keys.append(nk)
            if track_queries:
                for q in track_queries:
                    clean_q = PlexMusicMonitor._normalize_key(q)
                    if not clean_q:
                        continue
                    if clean_q.isdigit():
                        if clean_q not in seen:
                            seen.add(clean_q)
                            keys.append(clean_q)
                    else:
                        sr = await monitor.search_library(clean_q, media_type="track", limit=1)
                        if sr and sr[0].get("rating_key"):
                            rk = str(sr[0]["rating_key"])
                            if rk not in seen:
                                seen.add(rk)
                                keys.append(rk)
            return keys

        if action == "list":
            playlists = await monitor.get_playlists()
            if not playlists:
                return "No audio playlists found on Plex."
            lines = [f"Found {len(playlists)} Plex audio playlist(s):"]
            for p in playlists:
                dur = format_duration_mmss(p.get("duration_ms") or 0)
                smart = " [Smart]" if p.get("smart") else ""
                lines.append(f"- [#{p.get('rating_key')}] **{p.get('title')}** ({p.get('track_count', 0)} tracks, {dur}){smart}")
            return "\n".join(lines)

        elif action == "view":
            if not playlist_id:
                return "Error: 'playlist_id' (ratingKey or title) is required to view a playlist."
            pl = await monitor.get_playlist_details(playlist_id)
            if not pl:
                return f"Playlist '{playlist_id}' not found on Plex."
            total_dur = format_duration_mmss(pl.get("duration_ms") or 0)
            lines = [
                f"**{pl.get('title')}** (Key: #{pl.get('rating_key')})",
                f"Total Tracks: {pl.get('track_count', 0)} • Duration: {total_dur}",
            ]
            if pl.get("summary"):
                lines.append(f"Description: {pl['summary']}")
            tracks = pl.get("tracks") or []
            if tracks:
                lines.append("\nTracks:")
                for t in tracks:
                    dur = format_duration_mmss(t.get("duration_ms") or 0)
                    lines.append(f"{t.get('index', 0)}. \"{t.get('title')}\" by {t.get('artist')} ({dur}) [#{t.get('rating_key')}]")
            return "\n".join(lines)

        elif action == "create":
            if not title:
                return "Error: 'title' is required to create a new playlist."

            resolved_keys = await _resolve_all_track_keys()
            created = await monitor.create_playlist(title=title, track_keys=resolved_keys)
            if not created:
                return f"Failed to create Plex playlist '{title}'."
            k = created.get("rating_key") or "N/A"
            cnt = created.get("track_count", len(resolved_keys))
            return f"Successfully created Plex audio playlist **{title}** [#{k}] with {cnt} track(s)."

        elif action == "add":
            if not playlist_id:
                return "Error: 'playlist_id' is required to add tracks."
            resolved_keys = await _resolve_all_track_keys()

            if not resolved_keys:
                return "Error: Please provide at least one valid track_keys (ratingKey) or track_queries."

            # Resolve playlist rating key if name was passed
            target_pl_key = playlist_id
            if not target_pl_key.isdigit():
                pl_info = await monitor.get_playlist_details(target_pl_key)
                if pl_info:
                    target_pl_key = pl_info["rating_key"]
                else:
                    return f"Playlist '{playlist_id}' not found."

            ok = await monitor.add_to_playlist(target_pl_key, resolved_keys)
            if not ok:
                return f"Failed to add tracks to playlist [#{target_pl_key}]."
            return f"Successfully added {len(resolved_keys)} track(s) to playlist [#{target_pl_key}]."

        elif action in ("edit", "update"):
            if not playlist_id:
                return "Error: 'playlist_id' is required to edit a playlist."
            if not title and summary is None:
                return "Error: Provide 'title' and/or 'summary' to update the playlist."
            res = await monitor.update_metadata(
                rating_key_or_query=playlist_id,
                media_type="playlist",
                title=title if title else None,
                summary=str(summary).strip() if summary is not None else None,
            )
            if not res.get("success"):
                return res.get("error") or f"Failed to update playlist '{playlist_id}'."
            return (
                f"Successfully updated playlist **{res.get('title')}** "
                f"[#{res.get('rating_key')}] (updated: {', '.join(res.get('updated_fields', []))})."
            )

        elif action == "delete":
            if not playlist_id:
                return "Error: 'playlist_id' is required to delete a playlist."
            target_pl_key = playlist_id
            if not target_pl_key.isdigit():
                pl_info = await monitor.get_playlist_details(target_pl_key)
                if pl_info:
                    target_pl_key = pl_info["rating_key"]
                else:
                    return f"Playlist '{playlist_id}' not found."

            ok = await monitor.delete_playlist(target_pl_key)
            if not ok:
                return f"Failed to delete playlist [#{target_pl_key}]."
            return f"Successfully deleted playlist [#{target_pl_key}]."

        return f"Unknown playlist action '{action}'. Valid actions: 'list', 'view', 'create', 'add', 'edit', 'delete'."

    finally:
        if owns_monitor:
            await monitor.close()


def _extract_image_from_context(context: dict | None) -> tuple[str, bytes, str] | None:
    """
    Extract the first image attachment (filename, bytes, content_type) from current or reply
    Discord message attachments in the tool execution context.
    """
    if not context or not isinstance(context, dict):
        return None
    raw_atts = context.get("current_attachments") or context.get("reply_attachments")
    if raw_atts is None:
        raw_atts = context.get("attachments", [])
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    for att in raw_atts:
        if isinstance(att, dict) and "bytes" in att:
            fname = str(att.get("filename") or "cover.jpg")
            ct = str(att.get("content_type") or "").lower()
            if ct.startswith("image/") or fname.lower().endswith(image_exts):
                if not ct.startswith("image/"):
                    ct = "image/png" if fname.lower().endswith(".png") else (
                        "image/webp" if fname.lower().endswith(".webp") else "image/jpeg"
                    )
                return fname, att["bytes"], ct
        elif isinstance(att, (tuple, list)) and len(att) >= 2:
            fname = str(att[0] or "cover.jpg")
            data_bytes = att[1]
            if fname.lower().endswith(image_exts) and isinstance(data_bytes, (bytes, bytearray)):
                ct = "image/png" if fname.lower().endswith(".png") else (
                    "image/webp" if fname.lower().endswith(".webp") else "image/jpeg"
                )
                return fname, bytes(data_bytes), ct
    return None


async def handle_update_plex_metadata(
    bot, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for the update_plex_metadata AI tool."""
    if not await _is_authorized_plex_admin(bot, user_id):
        return "Permission Denied: Only the bot owner or authorized users can modify metadata on the Plex Media Server."

    raw = args or {}
    query_or_id = str(raw.get("query_or_id") or "").strip()
    media_type = str(raw.get("media_type") or "all").strip().lower()
    artist_filter = str(raw.get("artist_filter") or "").strip()
    album_filter = str(raw.get("album_filter") or "").strip()

    def _opt_str(key: str) -> str | None:
        if key not in raw or raw[key] is None:
            return None
        return str(raw[key]).strip()

    def _opt_int(key: str) -> tuple[int | None, str | None]:
        if key not in raw or raw[key] is None or raw[key] == "":
            return None, None
        try:
            return int(raw[key]), None
        except (ValueError, TypeError):
            return None, f"Error: '{key}' must be an integer."

    def _opt_float(key: str) -> tuple[float | None, str | None]:
        if key not in raw or raw[key] is None or raw[key] == "":
            return None, None
        try:
            return float(raw[key]), None
        except (ValueError, TypeError):
            return None, f"Error: '{key}' must be a number."

    def _opt_str_list(key: str) -> list[str] | None:
        if key not in raw or raw[key] is None:
            return None
        val = raw[key]
        if isinstance(val, str):
            return [s.strip() for s in val.split(",") if s.strip()]
        if isinstance(val, list):
            return [str(s).strip() for s in val if str(s).strip()]
        return None

    year, err = _opt_int("year")
    if err:
        return err
    track_number, err = _opt_int("track_number")
    if err:
        return err
    disc_number, err = _opt_int("disc_number")
    if err:
        return err
    user_rating, err = _opt_float("user_rating")
    if err:
        return err
    match_year, err = _opt_int("match_year")
    if err:
        return err

    poster_url = _opt_str("poster_url") or _opt_str("cover_url")
    background_art_url = _opt_str("background_art_url") or _opt_str("art_url")
    use_attached_image = bool(raw.get("use_attached_image", False))
    artwork_target = str(raw.get("artwork_target") or "poster").strip().lower()

    poster_bytes: bytes | None = None
    poster_ct = "image/jpeg"
    art_bytes: bytes | None = None
    art_ct = "image/jpeg"

    attached_img = _extract_image_from_context(context)
    has_other_edits = any(
        raw.get(k) is not None
        for k in (
            "title", "sort_title", "original_title", "artist", "album_artist", "album",
            "summary", "tagline", "year", "release_date", "originally_available_at",
            "studio", "track_number", "disc_number", "content_rating", "user_rating",
            "genres", "remove_genres", "styles", "remove_styles", "moods", "remove_moods",
            "collections", "remove_collections", "labels", "remove_labels",
            "countries", "remove_countries", "similar_artists", "remove_similar_artists",
            "directors", "remove_directors", "writers", "remove_writers",
            "producers", "remove_producers", "poster_url", "cover_url",
            "background_art_url", "art_url", "unlock_fields",
            "match_guid", "match_agent",
        )
    ) or bool(raw.get("refresh_metadata")) or bool(raw.get("auto_match")) or bool(raw.get("unmatch"))

    if use_attached_image or (attached_img is not None and not has_other_edits):
        if not attached_img:
            return "Error: 'use_attached_image' was set, but no image attachment was found in the message or reply."
        _, img_data, img_ct = attached_img
        if artwork_target in ("art", "backdrop", "background"):
            art_bytes = img_data
            art_ct = img_ct
        else:
            poster_bytes = img_data
            poster_ct = img_ct

    lock_fields = True if raw.get("lock_fields") is None else bool(raw.get("lock_fields"))
    replace_tags = bool(raw.get("replace_tags", False))
    refresh_metadata = bool(raw.get("refresh_metadata", False))
    auto_match = bool(raw.get("auto_match", False))
    unmatch = bool(raw.get("unmatch", False))

    monitor, owns_monitor = _get_monitor(bot)
    try:
        res = await monitor.update_metadata(
            rating_key_or_query=query_or_id,
            media_type=media_type,
            artist_filter=artist_filter,
            album_filter=album_filter,
            title=_opt_str("title"),
            sort_title=_opt_str("sort_title"),
            original_title=_opt_str("original_title"),
            artist=_opt_str("artist"),
            album_artist=_opt_str("album_artist"),
            album=_opt_str("album"),
            summary=_opt_str("summary"),
            tagline=_opt_str("tagline"),
            year=year,
            originally_available_at=_opt_str("release_date") or _opt_str("originally_available_at"),
            studio=_opt_str("studio"),
            track_number=track_number,
            disc_number=disc_number,
            content_rating=_opt_str("content_rating"),
            user_rating=user_rating,
            genres=_opt_str_list("genres"),
            remove_genres=_opt_str_list("remove_genres"),
            styles=_opt_str_list("styles"),
            remove_styles=_opt_str_list("remove_styles"),
            moods=_opt_str_list("moods"),
            remove_moods=_opt_str_list("remove_moods"),
            collections=_opt_str_list("collections"),
            remove_collections=_opt_str_list("remove_collections"),
            labels=_opt_str_list("labels"),
            remove_labels=_opt_str_list("remove_labels"),
            countries=_opt_str_list("countries"),
            remove_countries=_opt_str_list("remove_countries"),
            similar_artists=_opt_str_list("similar_artists"),
            remove_similar_artists=_opt_str_list("remove_similar_artists"),
            directors=_opt_str_list("directors"),
            remove_directors=_opt_str_list("remove_directors"),
            writers=_opt_str_list("writers"),
            remove_writers=_opt_str_list("remove_writers"),
            producers=_opt_str_list("producers"),
            remove_producers=_opt_str_list("remove_producers"),
            replace_tags=replace_tags,
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            poster_content_type=poster_ct,
            background_art_url=background_art_url,
            background_art_bytes=art_bytes,
            background_art_content_type=art_ct,
            lock_fields=lock_fields,
            unlock_fields=_opt_str_list("unlock_fields"),
            refresh_metadata=refresh_metadata,
            match_guid=_opt_str("match_guid"),
            match_name=_opt_str("match_name"),
            match_year=match_year,
            match_agent=_opt_str("match_agent"),
            match_language=_opt_str("match_language"),
            auto_match=auto_match,
            unmatch=unmatch,
        )
    finally:
        if owns_monitor:
            await monitor.close()

    if not res.get("success"):
        return f"Error: {res.get('error') or 'Failed to update Plex metadata.'}"

    m_type = str(res.get("media_type") or "item").capitalize()
    rk = res.get("rating_key") or "N/A"
    item_title = res.get("title") or f"#{rk}"
    prev_title = res.get("previous_title") or ""
    updated = res.get("updated_fields") or []

    title_display = f"**{item_title}**"
    if prev_title and prev_title != item_title:
        title_display = f"**{item_title}** (was *{prev_title}*)"

    match_suffix = ""
    if res.get("matched_guid"):
        match_suffix = f" [Matched GUID: `{res['matched_guid']}`]"

    return (
        f"Successfully updated Plex {m_type} [#{rk}] {title_display} "
        f"(modified: {', '.join(updated)}).{match_suffix}"
    )


async def handle_match_plex_media(bot, args: dict, user_id: str) -> str:
    """Handler for the match_plex_media (Fix Match / Agent Association) AI tool."""
    raw = args or {}
    action = str(raw.get("action") or "search").strip().lower()
    query_or_id = str(raw.get("query_or_id") or "").strip()
    media_type = str(raw.get("media_type") or "all").strip().lower()
    artist_filter = str(raw.get("artist_filter") or "").strip()
    album_filter = str(raw.get("album_filter") or "").strip()
    guid = str(raw.get("guid") or "").strip() or None
    search_title = str(raw.get("search_title") or raw.get("title") or "").strip() or None
    agent = str(raw.get("agent") or "").strip() or None
    language = str(raw.get("language") or "").strip() or None

    search_year: int | None = None
    raw_year = raw.get("search_year") if raw.get("search_year") is not None else raw.get("year")
    if raw_year is not None and raw_year != "":
        try:
            search_year = int(raw_year)
        except (ValueError, TypeError):
            return "Error: 'search_year' must be an integer."

    match_index = 0
    if raw.get("match_index") is not None and raw.get("match_index") != "":
        try:
            match_index = int(raw["match_index"])
        except (ValueError, TypeError):
            return "Error: 'match_index' must be an integer."

    if action in ("match", "apply", "auto", "unmatch"):
        if not await _is_authorized_plex_admin(bot, user_id):
            return "Permission Denied: Only the bot owner or authorized users can fix matches or unmatch content on Plex."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        if action == "search":
            res = await monitor.search_metadata_matches(
                rating_key_or_query=query_or_id,
                media_type=media_type,
                title=search_title,
                year=search_year,
                agent=agent,
                language=language,
                artist_filter=artist_filter,
                album_filter=album_filter,
            )
            if not res.get("success"):
                return f"Error: {res.get('error') or 'Failed to search Plex metadata matches.'}"

            matches = res.get("matches") or []
            m_type = str(res.get("media_type") or "item").capitalize()
            rk = res.get("rating_key") or "N/A"
            item_title = res.get("title") or f"#{rk}"
            agent_str = f" (Agent: `{res['agent']}`)" if res.get("agent") else ""

            if not matches:
                return f"No metadata agent matches found for Plex {m_type} [#{rk}] **{item_title}**{agent_str}."

            lines = [
                f"Found {len(matches)} Fix Match candidate(s) for Plex {m_type} [#{rk}] **{item_title}**{agent_str}:"
            ]
            for m in matches[:15]:
                yr = f" ({m['year']})" if m.get("year") else ""
                sc = f" • Score: {m['score']}%" if m.get("score") is not None else ""
                summ = f" — {m['summary'][:120]}" if m.get("summary") else ""
                lines.append(
                    f"{m.get('index', 0)}. **{m.get('name')}**{yr}{sc} • GUID: `{m.get('guid')}`{summ}"
                )
            return "\n".join(lines)

        elif action in ("match", "apply", "auto"):
            res = await monitor.fix_metadata_match(
                rating_key_or_query=query_or_id,
                media_type=media_type,
                guid=guid,
                name=search_title if guid else None,
                title=search_title,
                year=search_year,
                agent=agent,
                language=language,
                match_index=match_index,
                unmatch=False,
                artist_filter=artist_filter,
                album_filter=album_filter,
            )
            if not res.get("success"):
                return f"Error: {res.get('error') or 'Failed to apply Plex metadata match.'}"

            m_type = str(res.get("media_type") or "item").capitalize()
            rk = res.get("rating_key") or "N/A"
            matched_name = res.get("matched_name") or res.get("title") or f"#{rk}"
            yr_str = f" ({res['matched_year']})" if res.get("matched_year") else ""
            sc_str = f" [Score: {res['matched_score']}%]" if res.get("matched_score") is not None else ""
            return (
                f"Successfully matched Plex {m_type} [#{rk}] to **{matched_name}**{yr_str}{sc_str} "
                f"(GUID: `{res.get('matched_guid')}`)."
            )

        elif action == "unmatch":
            res = await monitor.fix_metadata_match(
                rating_key_or_query=query_or_id,
                media_type=media_type,
                unmatch=True,
                artist_filter=artist_filter,
                album_filter=album_filter,
            )
            if not res.get("success"):
                return f"Error: {res.get('error') or 'Failed to unmatch Plex item.'}"

            m_type = str(res.get("media_type") or "item").capitalize()
            rk = res.get("rating_key") or "N/A"
            item_title = res.get("title") or f"#{rk}"
            return f"Successfully unmatched Plex {m_type} [#{rk}] **{item_title}** from its metadata agent."

        return f"Unknown match action '{action}'. Valid actions: 'search', 'match', 'unmatch'."
    finally:
        if owns_monitor:
            await monitor.close()


async def handle_rate_plex_music(bot, args: dict, user_id: str) -> str:
    """Handler for the rate_plex_music AI tool."""
    if not await _is_authorized_plex_admin(bot, user_id):
        return "Permission Denied: Only the bot owner or authorized users can rate Plex media."

    raw = args or {}
    try:
        rating = float(raw.get("rating", 0.0))
    except (ValueError, TypeError):
        return "Error: 'rating' must be a numeric value (e.g. 5 for 5 stars, 0 to clear)."

    query_or_id = PlexMusicMonitor._normalize_key(raw.get("query_or_id", ""))

    monitor, owns_monitor = _get_monitor(bot)
    try:
        target_key = query_or_id
        target_name = ""

        if not target_key:
            # Rate currently playing track
            now = await monitor.get_now_playing()
            track = now.get("track")
            if not track or not track.get("rating_key"):
                return "Nothing is currently playing on Plex to rate. Please provide a track title or ratingKey."
            target_key = str(track["rating_key"])
            target_name = f"\"{track.get('title')}\" by {track.get('artist')}"

        elif not target_key.isdigit():
            # Search by title
            results = await monitor.search_library(target_key, limit=1)
            if not results or not results[0].get("rating_key"):
                return f"Could not find any music in Plex matching '{target_key}' to rate."
            target_key = str(results[0]["rating_key"])
            target_name = f"\"{results[0].get('title')}\""

        ok = await monitor.rate_item(target_key, rating)
        if not ok:
            return f"Failed to set rating for Plex item [#{target_key}]."

        display_name = f" ({target_name})" if target_name else ""
        if rating <= 0:
            return f"Cleared rating for Plex item [#{target_key}]{display_name}."
        return f"Rated Plex item [#{target_key}]{display_name} with {rating:g} star(s)."
    finally:
        if owns_monitor:
            await monitor.close()


async def handle_get_plex_sonic_similar(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_sonic_similar AI tool."""
    raw = args or {}
    query_or_id = PlexMusicMonitor._normalize_key(raw.get("query_or_id", ""))
    media_type = str(raw.get("media_type", "track") or "track").strip().lower()
    try:
        limit = int(raw.get("limit") or 10)
    except (ValueError, TypeError):
        limit = 10
    create_pl = bool(raw.get("create_playlist", False))
    pl_title = str(raw.get("playlist_title", "")).strip()

    monitor, owns_monitor = _get_monitor(bot)
    try:
        seed_label = ""
        seed_key = query_or_id

        if not seed_key:
            now = await monitor.get_now_playing()
            track = now.get("track")
            if not track or not track.get("rating_key"):
                return (
                    "Nothing is currently streaming on Plex. Please specify a track title, "
                    "album, or ratingKey to find sonically similar music."
                )
            seed_key = str(track["rating_key"])
            seed_label = f"\"{track.get('title')}\" by {track.get('artist')}"
        else:
            seed_label = f"'{query_or_id}'"

        similar = await monitor.get_sonically_similar(
            rating_key_or_name=seed_key,
            media_type=media_type,
            limit=limit,
        )

        if not similar:
            return (
                f"No sonically similar items found for {seed_label}. "
                "Note: Sonic Analysis must be enabled and completed on your Plex Media Server (Plex Pass)."
            )

        type_label = "album(s)" if media_type == "album" else "track(s)"
        lines = [f"Found {len(similar)} sonically similar {type_label} for {seed_label}:"]

        for item in similar:
            dist_str = f" [Distance: {item['distance']:.4f}]" if item.get("distance") is not None else ""
            k = item.get("rating_key") or "N/A"
            if media_type == "album":
                title = item.get("title") or "Untitled Album"
                artist = item.get("artist") or "Unknown Artist"
                year = f" ({item['year']})" if item.get("year") else ""
                lines.append(f"- [Album #{k}] **{title}** by {artist}{year}{dist_str}")
            else:
                title = item.get("title") or "Untitled Track"
                artist = item.get("artist") or "Unknown Artist"
                dur = format_duration_mmss(item.get("duration_ms") or 0)
                quality = f" [{item['audio_quality']}]" if item.get("audio_quality") else ""
                lines.append(f"- [Track #{k}] **{title}** by {artist} ({dur}){quality}{dist_str}")

        if create_pl:
            if not await _is_authorized_plex_admin(bot, user_id):
                lines.append("\nNote: Playlist creation skipped (requires bot owner authorization).")
            else:
                track_keys = [PlexMusicMonitor._normalize_key(t.get("rating_key", "")) for t in similar if PlexMusicMonitor._normalize_key(t.get("rating_key", "")).isdigit()]
                if track_keys:
                    final_title = pl_title or f"Sonic Mix: {query_or_id or 'Now Playing'}"
                    created = await monitor.create_playlist(title=final_title, track_keys=track_keys)
                    if created:
                        lines.append(f"\nCreated Plex playlist **{final_title}** [#{created.get('rating_key')}] with {len(track_keys)} track(s)!")
                    else:
                        lines.append(f"\nFailed to create playlist '{final_title}' on Plex.")

        return "\n".join(lines)
    finally:
        if owns_monitor:
            await monitor.close()


async def handle_get_music_suggestions(bot, args: dict, user_id: str) -> str:
    """Handler for the get_music_suggestions AI tool."""
    monitor, owns_monitor = _get_monitor(bot)
    try:
        items = await monitor.get_music_suggestions(limit=25)
    finally:
        if owns_monitor:
            await monitor.close()

    if not items:
        return "No community music suggestions have been submitted yet."

    lines = [f"Found {len(items)} music suggestion(s):"]
    for s in items:
        kind = (s.get("kind") or "song").capitalize()
        title = s.get("title") or "Untitled"
        artist = s.get("artist") or "Unknown Artist"
        submitter = s.get("submitter") or "Anonymous"
        msg = f" — \"{s['message']}\"" if s.get("message") else ""
        links = []
        if s.get("url"):
            links.append(f"Original: {s['url']}")
        if s.get("youtube_url"):
            links.append(f"YouTube: {s['youtube_url']}")
        if s.get("qobuz_url"):
            links.append(f"Qobuz: {s['qobuz_url']}")
        links_str = f" ({' | '.join(links)})" if links else ""
        lines.append(f"- [{s.get('id')}] ({kind}) **{title}** by {artist} (from {submitter}){msg}{links_str}")
    return "\n".join(lines)


async def handle_add_music_suggestion(bot, args: dict, user_id: str) -> str:
    """Handler for the add_music_suggestion AI tool."""
    raw = args or {}
    title = str(raw.get("title", "")).strip()
    artist = str(raw.get("artist", "")).strip()
    kind = str(raw.get("kind", "song") or "song").strip().lower()
    if kind not in ("song", "album"):
        kind = "song"
    url = str(raw.get("url", "")).strip()

    if not url and (not title or not artist):
        return "Error: Please provide either a link (url), or both 'title' and 'artist'."
    message = str(raw.get("message", "") or raw.get("reason", "")).strip()
    submitter = str(raw.get("submitter", "")).strip()

    if not submitter and user_id and hasattr(bot, "get_user"):
        try:
            u = bot.get_user(int(user_id))
            if u:
                submitter = getattr(u, "display_name", "") or getattr(u, "name", "")
        except Exception:
            pass

    monitor, owns_monitor = _get_monitor(bot)
    try:
        entry = await monitor.add_music_suggestion(
            title=title,
            artist=artist,
            kind=kind,
            url=url,
            reason=message,
            submitter=submitter or "Discord User",
            suggested_from="Discord AI",
            is_public=True,
        )
        library_check = await monitor.check_in_library(
            title=entry.get("title", title),
            artist=entry.get("artist", artist),
            kind=entry.get("kind", kind),
        )
    finally:
        if owns_monitor:
            await monitor.close()

    extra_links = []
    if entry.get("youtube_url"):
        extra_links.append(f"YouTube: {entry['youtube_url']}")
    if entry.get("qobuz_url"):
        extra_links.append(f"Qobuz: {entry['qobuz_url']}")
    extra_str = f" [{' | '.join(extra_links)}]" if extra_links else ""

    library_str = ""
    if library_check.get("in_library"):
        matched = library_check.get("matched_item") or {}
        k = matched.get("rating_key") or ""
        library_str = f" (Note: Already owned in your Plex library! #{k})" if k else " (Note: Already owned in your Plex library!)"

    return (
        f"Added {entry.get('kind', kind)} recommendation: "
        f"\"{entry.get('title')}\" by {entry.get('artist')} (ID: {entry.get('id')}).{extra_str}{library_str}"
    )


async def handle_delete_music_suggestion(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_music_suggestion AI tool."""
    query = str((args or {}).get("query", "")).strip()
    if not query:
        return "Error: 'query' parameter is required."

    monitor, owns_monitor = _get_monitor(bot)
    try:
        removed = await monitor.delete_music_suggestion(query)
    finally:
        if owns_monitor:
            await monitor.close()

    if not removed:
        return f"No music suggestion found matching '{query}'."
    return f"Deleted music suggestion: \"{removed.get('title')}\" by {removed.get('artist')} (ID: {removed.get('id')})."
