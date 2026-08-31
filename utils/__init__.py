"""
utils package for Shisho.
Exposes common utilities for database operations, Discord interaction, and Gemini LLM.
"""

from utils.db import (
    BodyDict,
    MultiFileUpload,
    get_discord_user_id,
    get_pb_client,
    get_pb_url,
    get_pb_user_discord_id,
    prepare_file_upload_payload,
    run_in_executor,
    validate_pb_token,
)
from utils.discord_helpers import (
    add_user_to_whitelist,
    async_add_user_to_whitelist,
    async_get_cog_whitelist,
    async_remove_user_from_whitelist,
    get_cog_whitelist,
    is_user_authorized,
    remove_user_from_whitelist,
    split_message,
)
from utils.llm import (
    DEFAULT_GEMINI_MODEL,
    format_gemini_error,
    generate_content_with_retry,
    get_gemini_client,
    get_gemini_model,
    is_transient_error,
)

__all__ = [
    "BodyDict",
    "DEFAULT_GEMINI_MODEL",
    "MultiFileUpload",
    "add_user_to_whitelist",
    "async_add_user_to_whitelist",
    "async_get_cog_whitelist",
    "async_remove_user_from_whitelist",
    "format_gemini_error",
    "generate_content_with_retry",
    "get_cog_whitelist",
    "get_discord_user_id",
    "get_gemini_client",
    "get_gemini_model",
    "get_pb_client",
    "get_pb_url",
    "get_pb_user_discord_id",
    "is_transient_error",
    "is_user_authorized",
    "prepare_file_upload_payload",
    "remove_user_from_whitelist",
    "run_in_executor",
    "split_message",
    "validate_pb_token",
]
