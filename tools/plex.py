"""
tools/plex.py - AI Tool definitions and handlers for Plex music status, crate history, and music suggestions.
"""

from google.genai import types

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
    finally:
        if owns_monitor:
            await monitor.close()

    extra_links = []
    if entry.get("youtube_url"):
        extra_links.append(f"YouTube: {entry['youtube_url']}")
    if entry.get("qobuz_url"):
        extra_links.append(f"Qobuz: {entry['qobuz_url']}")
    extra_str = f" [{' | '.join(extra_links)}]" if extra_links else ""

    return (
        f"Added {entry.get('kind', kind)} recommendation: "
        f"\"{entry.get('title')}\" by {entry.get('artist')} (ID: {entry.get('id')}).{extra_str}"
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

