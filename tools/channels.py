"""
tools/channels.py - AI Tool definitions and handlers for Discord channel messaging.
"""

import re
from typing import Any
import discord
from google.genai import types
import sentry_sdk

from utils.discord_helpers import format_for_discord, split_message, is_user_authorized


SEND_CHANNEL_MESSAGE_TOOL = types.FunctionDeclaration(
    name="send_channel_message",
    description="Sends or posts a message directly to a specified Discord text channel or thread. Use this when the user asks you to send a message, post an update, announce something, or introduce yourself in another channel (e.g. '#checkpoints', '🏁checkpoints', 'general', channel mention <#id>, or channel ID).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "channel": types.Schema(
                type=types.Type.STRING,
                description="The target Discord channel name (e.g. 'checkpoints', '🏁checkpoints', '#checkpoints', 'general'), channel mention (e.g. '<#123456789>'), or numeric channel ID.",
            ),
            "message": types.Schema(
                type=types.Type.STRING,
                description="The text content or markdown formatted message to send to the channel.",
            ),
        },
        required=["channel", "message"],
    ),
)

LIST_CHANNELS_TOOL = types.FunctionDeclaration(
    name="list_channels",
    description="Lists the available text channels in the current Discord server.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)


def _normalize_name(name: str) -> str:
    """Normalize channel name by removing symbols, emojis, dashes, spaces, and converting to lowercase."""
    return re.sub(r"[^a-zA-Z0-9]", "", name).lower()


def _resolve_target_channel(bot: Any, channel_query: str, guild: discord.Guild | None) -> discord.abc.GuildChannel | discord.Thread | None:
    """
    Find a Discord channel or thread matching channel_query.
    Supports:
    1. Channel mentions: <#123456789>
    2. Raw channel IDs: 123456789
    3. Exact channel name: 'checkpoints' or '#checkpoints'
    4. Normalized / emoji-stripped name: 'checkpoints' matching '🏁checkpoints'
    5. Substring match
    """
    if not channel_query:
        return None

    clean_query = channel_query.strip()

    # 1. Mention check: <#123456789>
    mention_match = re.match(r"<#(\d+)>", clean_query)
    if mention_match:
        ch_id = int(mention_match.group(1))
        ch = bot.get_channel(ch_id)
        if ch:
            return ch

    # 2. Raw numeric ID
    if clean_query.isdigit():
        ch_id = int(clean_query)
        ch = bot.get_channel(ch_id)
        if ch:
            return ch

    # 3. Name resolution within guild(s)
    candidate_guilds = [guild] if guild else list(getattr(bot, "guilds", []))

    # Strip leading '#'
    stripped_name = clean_query.lstrip("#").strip()
    norm_query = _normalize_name(stripped_name)

    for g in candidate_guilds:
        if not g:
            continue

        channels = list(getattr(g, "text_channels", [])) + list(getattr(g, "threads", [])) + list(getattr(g, "voice_channels", []))

        # Pass 1: Exact name match (case-insensitive)
        for ch in channels:
            if ch.name.lower() == stripped_name.lower():
                return ch

        # Pass 2: Normalized name match (removes emojis/symbols)
        if norm_query:
            for ch in channels:
                if _normalize_name(ch.name) == norm_query:
                    return ch

        # Pass 3: Substring / contains match
        if norm_query and len(norm_query) >= 3:
            for ch in channels:
                ch_norm = _normalize_name(ch.name)
                if norm_query in ch_norm or ch_norm in norm_query:
                    return ch

    return None


async def handle_send_channel_message(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for sending messages to a target Discord channel."""
    channel_query = str(args.get("channel", "")).strip()
    message_content = str(args.get("message", "")).strip()

    if not channel_query:
        return "Error: 'channel' argument is required."
    if not message_content:
        return "Error: 'message' argument is required."

    # Determine active guild from context
    guild = None
    if context:
        msg = context.get("message")
        if msg and hasattr(msg, "guild") and msg.guild:
            guild = msg.guild
        interaction = context.get("interaction")
        if not guild and interaction and hasattr(interaction, "guild") and interaction.guild:
            guild = interaction.guild
        if not guild:
            guild = context.get("guild")

    target_channel = _resolve_target_channel(bot, channel_query, guild)

    if not target_channel:
        available_str = ""
        if guild and hasattr(guild, "text_channels"):
            text_names = [f"#{c.name}" for c in guild.text_channels[:15]]
            available_str = f" Available channels: {', '.join(text_names)}."
        return f"Error: Could not find channel '{channel_query}'.{available_str}"

    # Permission check for the bot in target channel
    guild_obj = getattr(target_channel, "guild", guild)
    if guild_obj and hasattr(target_channel, "permissions_for") and hasattr(guild_obj, "me") and guild_obj.me:
        perms = target_channel.permissions_for(guild_obj.me)
        if hasattr(perms, "send_messages") and not perms.send_messages:
            return f"Permission Error: I do not have permission to send messages in #{target_channel.name} (<#{target_channel.id}>)."

    try:
        formatted_text = format_for_discord(message_content)
        chunks = split_message(formatted_text)
        if not chunks:
            chunks = [formatted_text]

        for chunk in chunks:
            await target_channel.send(chunk)

        return f"Successfully sent message to #{target_channel.name} (<#{target_channel.id}>)."
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Failed to send message to #{target_channel.name}: {e}"


async def handle_list_channels(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for listing available text channels."""
    guild = None
    if context:
        msg = context.get("message")
        if msg and hasattr(msg, "guild") and msg.guild:
            guild = msg.guild
        interaction = context.get("interaction")
        if not guild and interaction and hasattr(interaction, "guild") and interaction.guild:
            guild = interaction.guild
        if not guild:
            guild = context.get("guild")

    if not guild and getattr(bot, "guilds", None):
        guild = bot.guilds[0]

    if not guild or not hasattr(guild, "text_channels"):
        return "No server channels available."

    channels_info = []
    for ch in guild.text_channels:
        channels_info.append(f"- #{ch.name} (ID: {ch.id})")

    return "Available channels:\n" + "\n".join(channels_info)
