"""
tools/plex.py - AI Tool definition and handler for checking Plex music playback status.
"""

from google.genai import types

from utils.plex import PlexMusicMonitor


GET_PLEX_STATUS_TOOL = types.FunctionDeclaration(
    name="get_plex_status",
    description=(
        "Checks the current Plex music playback status (Now Playing), including whether music "
        "is actively streaming, paused, or idle, along with the song title, artist, album, "
        "player device, and current playback progress."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)


def format_duration_mmss(ms: int) -> str:
    total_sec = max(0, int((ms or 0) // 1000))
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


async def handle_get_plex_status(bot, args: dict, user_id: str) -> str:
    """Handler for the get_plex_status AI tool."""
    api_cog = bot.get_cog("API") if hasattr(bot, "get_cog") else None
    monitor = getattr(api_cog, "plex_monitor", None) if api_cog else None
    owns_monitor = False

    if monitor is None:
        monitor = PlexMusicMonitor()
        owns_monitor = True

    try:
        status = await monitor.get_now_playing()
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

    if not track:
        return "Plex is configured and reachable, but nothing is currently playing and no recent track was found."

    title = track.get("title") or "Untitled Track"
    artist = track.get("artist") or "Unknown Artist"
    album = track.get("album") or ""
    year = track.get("year")
    player = track.get("player") or "Plex"
    progress_ms = int(track.get("progress_ms") or 0)
    duration_ms = int(track.get("duration_ms") or 0)
    pct = float(track.get("progress_percent") or 0.0)

    album_str = f" — Album: {album}" + (f" ({year})" if year else "") if album else ""
    prog_str = (
        f"{format_duration_mmss(progress_ms)} / {format_duration_mmss(duration_ms)} ({pct:.1f}%)"
        if duration_ms > 0
        else "Unknown duration"
    )

    if is_playing or state == "paused":
        state_label = "Playing" if state == "playing" else state.capitalize()
        return (
            f"Plex Status: {state_label} on {player}\n"
            f"Track: \"{title}\" by {artist}{album_str}\n"
            f"Progress: {prog_str}"
        )

    return (
        f"Plex Status: Idle (nothing actively streaming right now)\n"
        f"Last Played Track: \"{title}\" by {artist}{album_str}"
    )
