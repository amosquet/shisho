"""
tools/models.py - AI Tool definitions and handlers for AI Model configuration and management.
"""

import os
import discord
from google.genai import types

from utils.llm import get_gemini_model, set_gemini_model, validate_gemini_model


SET_AI_MODEL_TOOL = types.FunctionDeclaration(
    name="set_ai_model",
    description="Changes the active Gemini AI model used by Shisho globally and persists the configuration. Only the bot owner is permitted to perform this action.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "model": types.Schema(
                type=types.Type.STRING,
                description="The name or alias of the Gemini model to switch to (e.g. 'gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'pro', 'flash', 'flash-lite')",
            ),
        },
        required=["model"],
    ),
)

GET_AI_MODEL_TOOL = types.FunctionDeclaration(
    name="get_ai_model",
    description="Retrieves the currently active Gemini AI model name used by Shisho.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)


async def _is_bot_owner(bot, user_id: str) -> bool:
    """Checks if the user ID belongs to the bot owner."""
    if not user_id:
        return False

    owner_id_env = os.getenv("OWNER_ID")
    if owner_id_env and str(user_id).strip() == owner_id_env.strip():
        return True

    try:
        uid_int = int(user_id)
        if hasattr(bot, "is_owner"):
            obj = discord.Object(id=uid_int)
            if await bot.is_owner(obj):
                return True
        if getattr(bot, "owner_id", None) == uid_int:
            return True
        if getattr(bot, "owner_ids", None) and uid_int in bot.owner_ids:
            return True
    except Exception:
        pass

    return False


async def handle_set_ai_model(bot, args: dict, user_id: str) -> str:
    """Handler for the set_ai_model AI tool."""
    if not await _is_bot_owner(bot, user_id):
        return "Permission denied: Only the bot owner is permitted to change the active AI model."

    model_arg = str(args.get("model", args.get("model_name", ""))).strip()
    if not model_arg:
        return "Error: 'model' parameter is required."

    ai_cog = bot.get_cog("AIChat") if hasattr(bot, "get_cog") else None
    client = getattr(ai_cog, "client", None)

    is_valid, canonical, msg = await validate_gemini_model(model_arg, client=client)
    if not is_valid:
        return f"Cannot switch model: {msg}"

    set_gemini_model(canonical)
    return f"Successfully changed the active AI model to '{canonical}'. This setting is now globally active and persisted across restarts."


async def handle_get_ai_model(bot, args: dict, user_id: str) -> str:
    """Handler for the get_ai_model AI tool."""
    current_model = get_gemini_model()
    return f"The currently active AI model is '{current_model}'."
