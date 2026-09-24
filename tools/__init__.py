"""
tools/__init__.py - AI tools package for Shisho.
"""

from tools.preferences import (
    UPDATE_USER_PREFERENCE_TOOL,
    handle_update_user_preference,
)
from tools.registry import (
    AI_CHAT_TOOLS,
    ALL_FUNCTION_DECLARATIONS,
    GENERAL_AI_CHAT_TOOLS,
    GOOGLE_SEARCH_TOOL,
    TOOL_HANDLERS,
    execute_tool,
)

__all__ = [
    "AI_CHAT_TOOLS",
    "ALL_FUNCTION_DECLARATIONS",
    "GENERAL_AI_CHAT_TOOLS",
    "GOOGLE_SEARCH_TOOL",
    "TOOL_HANDLERS",
    "UPDATE_USER_PREFERENCE_TOOL",
    "execute_tool",
    "handle_update_user_preference",
]

