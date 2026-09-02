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
    description="Lists the user's reminders from PocketBase. Can filter by status ('active', 'sent', or 'all').",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="Status filter: 'active' (default for upcoming reminders), 'sent' (for completed/past reminders), or 'all'",
            ),
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

UPDATE_REMINDER_TOOL = types.FunctionDeclaration(
    name="update_reminder",
    description="Updates or reschedules an existing reminder for the user by reminder ID, index, or text keyword.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The reminder text keyword, index number, or ID to update",
            ),
            "when": types.Schema(
                type=types.Type.STRING,
                description="New reminder time (e.g. 'tomorrow at 3pm', 'in 2 hours')",
            ),
            "text": types.Schema(
                type=types.Type.STRING,
                description="New reminder message/text",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Optional timezone (e.g. 'US/Eastern', 'US/Pacific', 'Asia/Tokyo')",
            ),
            "is_sent": types.Schema(
                type=types.Type.BOOLEAN,
                description="Optional flag to mark reminder as sent or active",
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
    when = str(args.get("when") or args.get("time") or args.get("remind_at") or "").strip()
    text = str(args.get("text") or args.get("reminder_text") or args.get("message") or "").strip()
    tz = str(args.get("timezone") or args.get("tz") or "").strip()
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
    raw_status = args.get("status") or args.get("filter")
    status = str(raw_status).strip().lower() if raw_status is not None else "active"
    limit = args.get("limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    elif not isinstance(limit, int):
        limit = None
    res = await reminders_cog.get_reminders_text(user_id, for_discord=False, status=status, limit=limit)
    return res or "No reminders found."


async def handle_delete_reminder(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_reminder AI tool."""
    reminders_cog = bot.get_cog("Reminders")
    if not reminders_cog:
        return "Error: Reminders cog is unavailable."
    query = str(args.get("query") or args.get("reminder") or args.get("id") or "").strip()
    if not query:
        return "Error: Reminder text, index, ID, or 'all' is required."
    res = await reminders_cog.delete_reminder(user_id, query)
    return res


async def handle_update_reminder(bot, args: dict, user_id: str) -> str:
    """Handler for the update_reminder AI tool."""
    reminders_cog = bot.get_cog("Reminders")
    if not reminders_cog:
        return "Error: Reminders cog is unavailable."
    query = str(args.get("query") or args.get("reminder") or args.get("id") or "").strip()
    if not query:
        return "Error: Reminder text, index, or ID is required."
    when = args.get("when") or args.get("time") or args.get("remind_at")
    when_str = str(when).strip() if when is not None else None
    text = args.get("text") or args.get("reminder_text") or args.get("message")
    text_str = str(text).strip() if text is not None else None
    tz = args.get("timezone") or args.get("tz")
    tz_str = str(tz).strip() if tz is not None else None
    raw_is_sent = args.get("is_sent")
    if isinstance(raw_is_sent, str):
        is_sent = raw_is_sent.strip().lower() in ("true", "1", "yes")
    elif raw_is_sent is not None:
        is_sent = bool(raw_is_sent)
    else:
        is_sent = None

    return await reminders_cog.update_reminder(
        user_id,
        reminder_id_or_query=query,
        when=when_str,
        text=text_str,
        user_tz=tz_str,
        is_sent=is_sent,
    )

