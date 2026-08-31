"""
tools/reminders.py - AI Tool definitions and handlers for the Reminders domain.
"""

from google.genai import types


SET_REMINDER_TOOL = types.FunctionDeclaration(
    name="set_reminder",
    description="Sets a reminder for the user. Supports natural language times like 'in 5 minutes', 'tomorrow at 3pm', 'next Friday at 10am'.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "when": types.Schema(
                type=types.Type.STRING,
                description="When to remind the user (e.g. 'in 2 hours', 'tomorrow at 10am', 'Friday at 3pm')",
            ),
            "text": types.Schema(
                type=types.Type.STRING,
                description="The content / message of what to remind the user about",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Optional timezone or abbreviation (e.g. 'US/Eastern', 'jp', 'fr', 'ca', 'US/Central', 'US/Pacific')",
            ),
        },
        required=["when", "text"],
    ),
)

LIST_REMINDERS_TOOL = types.FunctionDeclaration(
    name="list_reminders",
    description="Lists the user's active, upcoming reminders. Use this whenever the user asks what reminders they have scheduled.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of reminders to return (optional)",
            ),
        },
    ),
)

DELETE_REMINDER_TOOL = types.FunctionDeclaration(
    name="delete_reminder",
    description="Deletes or cancels an active reminder for the user by reminder text keyword, index number, ID, or 'all' to delete all active reminders.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The reminder text keyword, index number, ID, or 'all'",
            ),
        },
        required=["query"],
    ),
)


async def handle_set_reminder(bot, args: dict, user_id: str) -> str:
    """Handler for the set_reminder AI tool."""
    reminders_cog = bot.get_cog("Reminders")
    if not reminders_cog:
        return "Error: Reminders cog is unavailable."
    when = str(args.get("when", "")).strip()
    text = str(args.get("text", "")).strip()
    tz = str(args.get("timezone", "")).strip()
    if not when or not text:
        return "Error: 'when' and 'text' are required for a reminder."
    res = await reminders_cog.add_reminder(
        user_id, when, text, for_discord=True, user_tz=tz or None
    )
    return res


async def handle_list_reminders(bot, args: dict, user_id: str) -> str:
    """Handler for the list_reminders AI tool."""
    reminders_cog = bot.get_cog("Reminders")
    if not reminders_cog:
        return "Error: Reminders cog is unavailable."
    res = await reminders_cog.get_reminders_text(user_id, for_discord=False)
    return res or "No active reminders."


async def handle_delete_reminder(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_reminder AI tool."""
    reminders_cog = bot.get_cog("Reminders")
    if not reminders_cog:
        return "Error: Reminders cog is unavailable."
    query = str(args.get("query", args.get("reminder", ""))).strip()
    if not query:
        return "Error: Reminder text, index, ID, or 'all' is required."
    res = await reminders_cog.delete_reminder(user_id, query)
    return res
