"""
utils/llm.py - Centralized Google Gemini LLM utilities and error handlers for Shisho.
Standardizes Gemini client initialization, model configuration, transient error detection,
retry logic with exponential backoff, and Sentry error reporting.
"""

import asyncio
import os
from typing import Any, Optional

from google import genai
from google.genai import errors, types
import sentry_sdk

# Shared Model Constant
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Standard User-Facing Message Constants
MSG_NO_API_KEY = (
    "Gemini API key is not configured. Please set GEMINI_API_KEY in the environment."
)
MSG_NO_API_KEY_EPHEMERAL = "Gemini API key is not configured."
MSG_HIGH_DEMAND = (
    "Gemini is currently experiencing high demand. Please try again later."
)
MSG_API_ERROR = "An error occurred while communicating with the API."
MSG_UNEXPECTED_ERROR = "An unexpected error occurred."
MSG_EMPTY_RESPONSE = "Received empty response from Gemini."

IGNORED_BOT_ERROR_PREFIXES = (
    "Gemini API key is not configured",
    "Gemini is currently experiencing high demand",
    "An error occurred while communicating with the API",
    "An unexpected error occurred",
    "API Error:",
)


def get_gemini_model(model: Optional[str] = None) -> str:
    """
    Return the configured Gemini model name or default.
    """
    if model:
        return model
    return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def get_gemini_client(api_key: Optional[str] = None) -> Optional[genai.Client]:
    """
    Initialize and return a Google Gemini client.

    Args:
        api_key: Optional API key override. If not provided, reads GEMINI_API_KEY from environment.

    Returns:
        genai.Client or None if no API key is set.
    """
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key)


def is_transient_error(error: Exception) -> bool:
    """
    Check if an exception represents a transient/high-demand or rate-limited error.
    """
    error_msg = str(error).lower()
    return (
        "high demand" in error_msg
        or "503" in error_msg
        or "resource_exhausted" in error_msg
        or "resourceexhausted" in error_msg
        or "unavailable" in error_msg
        or "rate limit" in error_msg
        or "quota" in error_msg
        or getattr(error, "code", None) in (429, 503)
        or getattr(error, "status_code", None) in (429, 503)
    )


def format_gemini_error(error: Exception, include_details: bool = False) -> str:
    """
    Capture exception to Sentry and return a user-friendly error message.

    Args:
        error: The caught exception.
        include_details: Whether to include verbatim error details for slash commands.

    Returns:
        User-facing error message string.
    """
    try:
        sentry_sdk.capture_exception(error)
    except Exception:
        pass

    if isinstance(error, errors.APIError):
        if is_transient_error(error):
            return MSG_HIGH_DEMAND
        return f"API Error: {str(error)}" if include_details else MSG_API_ERROR

    if is_transient_error(error):
        return MSG_HIGH_DEMAND

    return (
        f"An unexpected error occurred: {str(error)}"
        if include_details
        else MSG_UNEXPECTED_ERROR
    )


async def generate_content_with_retry(
    client: genai.Client,
    model: str,
    contents: Any,
    config: Optional[types.GenerateContentConfig] = None,
    max_retries: int = 2,
    backoff_factor: float = 1.0,
    **kwargs: Any,
) -> Any:
    """
    Execute client.aio.models.generate_content with transient error retry logic.

    Args:
        client: genai.Client instance.
        model: Model identifier string.
        contents: Contents payload.
        config: Optional GenerateContentConfig.
        max_retries: Maximum number of retries on transient errors.
        backoff_factor: Backoff base multiplier in seconds.
        **kwargs: Additional parameters passed to generate_content.

    Returns:
        GenerateContentResponse from Gemini SDK.
    """
    backoff = kwargs.pop("backoff_seconds", backoff_factor)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await client.aio.models.generate_content(
                model=model, contents=contents, config=config, **kwargs
            )
        except Exception as e:
            last_error = e
            if is_transient_error(e) and attempt < max_retries:
                await asyncio.sleep(backoff * (2**attempt))
                continue
            raise

    if last_error:
        raise last_error
