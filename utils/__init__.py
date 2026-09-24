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
    get_user_instructions,
    update_user_instructions,
    clear_user_instructions,
    prepare_file_upload_payload,
    run_in_executor,
    validate_pb_token,
)
from utils.discord_helpers import (
    add_user_to_whitelist,
    async_add_user_to_whitelist,
    async_get_cog_whitelist,
    async_remove_user_from_whitelist,
    format_for_discord,
    get_cog_whitelist,
    is_user_authorized,
    remove_user_from_whitelist,
    render_footer,
    split_message,
)
from utils.gemini_files import (
    DEFAULT_STAGING_THRESHOLD_BYTES,
    GeminiFileCache,
    get_gemini_file_cache,
    stage_or_inline_part,
)
from utils.llm import (
    DEFAULT_GEMINI_MODEL,
    format_gemini_error,
    format_grounding_citations,
    generate_content_with_retry,
    get_gemini_client,
    get_gemini_model,
    is_tool_combination_error,
    is_transient_error,
    query_targets_bot_tools,
    user_requested_sources,
)


from utils.pdf import (
    compile_text_to_pdf,
    is_pdf,
)

__all__ = [
    "BodyDict",
    "compile_text_to_pdf",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_STAGING_THRESHOLD_BYTES",
    "GeminiFileCache",
    "MultiFileUpload",
    "add_user_to_whitelist",
    "async_add_user_to_whitelist",
    "async_get_cog_whitelist",
    "async_remove_user_from_whitelist",
    "format_for_discord",
    "format_gemini_error",
    "format_grounding_citations",
    "generate_content_with_retry",
    "get_cog_whitelist",

    "get_discord_user_id",
    "get_gemini_client",
    "get_gemini_file_cache",
    "get_gemini_model",
    "get_pb_client",
    "get_pb_url",
    "get_pb_user_discord_id",
    "get_user_instructions",
    "update_user_instructions",
    "clear_user_instructions",
    "is_pdf",
    "is_tool_combination_error",
    "is_transient_error",
    "is_user_authorized",
    "prepare_file_upload_payload",
    "query_targets_bot_tools",
    "remove_user_from_whitelist",

    "render_footer",
    "run_in_executor",
    "split_message",
    "stage_or_inline_part",
    "user_requested_sources",
    "validate_pb_token",
]
