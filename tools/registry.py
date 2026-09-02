"""
tools/registry.py - Centralized AI Tool Registry and Dispatcher for Shisho.
"""

from typing import Callable, Coroutine, Any
from google.genai import types
import sentry_sdk

from tools.reading_list import (
    ADD_BOOK_TOOL,
    GET_READING_LIST_TOOL,
    UPDATE_BOOK_TOOL,
    DELETE_BOOK_TOOL,
    handle_add_book,
    handle_get_reading_list,
    handle_update_book,
    handle_delete_book,
)
from tools.recommendations import (
    GET_RECOMMENDATIONS_TOOL,
    ADD_RECOMMENDATION_TOOL,
    DELETE_RECOMMENDATION_TOOL,
    handle_get_recommendations,
    handle_add_recommendation,
    handle_delete_recommendation,
)
from tools.reminders import (
    SET_REMINDER_TOOL,
    LIST_REMINDERS_TOOL,
    UPDATE_REMINDER_TOOL,
    DELETE_REMINDER_TOOL,
    handle_set_reminder,
    handle_list_reminders,
    handle_update_reminder,
    handle_delete_reminder,
)
from tools.notes import (
    ADD_NOTE_TOOL,
    GET_NOTES_TOOL,
    DELETE_NOTE_TOOL,
    ARCHIVE_NOTE_TOOL,
    UNARCHIVE_NOTE_TOOL,
    DELETE_ARCHIVED_NOTES_TOOL,
    UPDATE_NOTE_TOOL,
    handle_add_note,
    handle_get_notes,
    handle_delete_note,
    handle_archive_note,
    handle_unarchive_note,
    handle_delete_archived_notes,
    handle_update_note,
)
from tools.printing import (
    PRINT_DOCUMENT_TOOL,
    LIST_PRINT_JOBS_TOOL,
    CANCEL_PRINT_JOB_TOOL,
    handle_print_document,
    handle_list_print_jobs,
    handle_cancel_print_job,
)
from tools.models import (
    SET_AI_MODEL_TOOL,
    GET_AI_MODEL_TOOL,
    handle_set_ai_model,
    handle_get_ai_model,
)

# Unified List of all Function Declarations for Gemini
ALL_FUNCTION_DECLARATIONS: list[types.FunctionDeclaration] = [
    ADD_BOOK_TOOL,
    GET_READING_LIST_TOOL,
    UPDATE_BOOK_TOOL,
    DELETE_BOOK_TOOL,
    SET_REMINDER_TOOL,
    LIST_REMINDERS_TOOL,
    UPDATE_REMINDER_TOOL,
    DELETE_REMINDER_TOOL,
    ADD_NOTE_TOOL,
    GET_NOTES_TOOL,
    DELETE_NOTE_TOOL,
    DELETE_ARCHIVED_NOTES_TOOL,
    ARCHIVE_NOTE_TOOL,
    UNARCHIVE_NOTE_TOOL,
    UPDATE_NOTE_TOOL,
    GET_RECOMMENDATIONS_TOOL,
    ADD_RECOMMENDATION_TOOL,
    DELETE_RECOMMENDATION_TOOL,
    PRINT_DOCUMENT_TOOL,
    LIST_PRINT_JOBS_TOOL,
    CANCEL_PRINT_JOB_TOOL,
    SET_AI_MODEL_TOOL,
    GET_AI_MODEL_TOOL,
]

AI_CHAT_TOOLS: list[types.Tool] = [
    types.Tool(function_declarations=ALL_FUNCTION_DECLARATIONS)
]

# Dispatch Table mapping tool names to async execution handlers
TOOL_HANDLERS: dict[str, Callable[[Any, dict, str], Coroutine[Any, Any, str]]] = {
    "add_book": handle_add_book,
    "get_reading_list": handle_get_reading_list,
    "update_book": handle_update_book,
    "delete_book": handle_delete_book,
    "get_recommendations": handle_get_recommendations,
    "add_recommendation": handle_add_recommendation,
    "delete_recommendation": handle_delete_recommendation,
    "set_reminder": handle_set_reminder,
    "list_reminders": handle_list_reminders,
    "update_reminder": handle_update_reminder,
    "delete_reminder": handle_delete_reminder,
    "add_note": handle_add_note,
    "get_notes": handle_get_notes,
    "delete_note": handle_delete_note,
    "delete_archived_notes": handle_delete_archived_notes,
    "archive_note": handle_archive_note,
    "unarchive_note": handle_unarchive_note,
    "update_note": handle_update_note,
    "print_document": handle_print_document,
    "list_print_jobs": handle_list_print_jobs,
    "cancel_print_job": handle_cancel_print_job,
    "set_ai_model": handle_set_ai_model,
    "get_ai_model": handle_get_ai_model,
}


import inspect


async def execute_tool(
    bot: Any, name: str, args: dict, user_id: str, context: dict | None = None
) -> str:
    """
    Executes a tool call requested by Gemini.

    Args:
        bot: The discord.ext.commands.Bot instance.
        name: The name of the tool function to call.
        args: Dictionary of keyword arguments provided by the model.
        user_id: The Discord user ID of the caller as a string.
        context: Optional dictionary containing execution context (e.g. message attachments).

    Returns:
        String result message to feed back to the model.
    """
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f"Error: Unknown tool '{name}'."

    try:
        sig = inspect.signature(handler)
        if "context" in sig.parameters:
            return await handler(bot, args, user_id, context=context)
        return await handler(bot, args, user_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"An error occurred while executing {name}: {str(e)}"
