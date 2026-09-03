"""
utils/llm.py - Centralized Google Gemini LLM utilities and error handlers for Shisho.
Standardizes Gemini client initialization, model configuration, transient error detection,
retry logic with exponential backoff, and Sentry error reporting.
"""

import asyncio
import json
import os
import re
from typing import Any, Optional

from google import genai
from google.genai import errors, types
import sentry_sdk

# Shared Model Constant
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
CONFIG_PATH = os.path.join("data", "config.json")

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

# In-memory cached model
_current_gemini_model: Optional[str] = None

# Common model aliases
MODEL_ALIASES = {
    "flash": "gemini-2.5-flash",
    "pro": "gemini-2.5-pro",
    "flash-lite": "gemini-2.5-flash-lite",
    "flash lite": "gemini-2.5-flash-lite",
    "lite": "gemini-2.5-flash-lite",
    "2.5-flash": "gemini-2.5-flash",
    "2.5-pro": "gemini-2.5-pro",
    "2.5-flash-lite": "gemini-2.5-flash-lite",
    "2.0-flash": "gemini-2.0-flash",
    "1.5-flash": "gemini-1.5-flash",
    "1.5-pro": "gemini-1.5-pro",
}


def normalize_gemini_model(model_name: str) -> str:
    """
    Normalizes user-supplied model names and aliases to canonical Gemini model identifiers.
    """
    clean = model_name.strip().lower()
    if clean.startswith("models/"):
        clean = clean[len("models/"):]
    clean = clean.replace(" ", "-")

    if clean in MODEL_ALIASES:
        return MODEL_ALIASES[clean]

    # If already starts with gemini-, return clean
    if clean.startswith("gemini-"):
        return clean

    # E.g. "2.5-flash" -> "gemini-2.5-flash"
    if re.match(r"^\d+\.\d+", clean):
        return f"gemini-{clean}"

    return clean


def get_gemini_model(model: Optional[str] = None, config_path: str = CONFIG_PATH) -> str:
    """
    Return the configured Gemini model name or default.
    Checks explicit parameter -> in-memory cache -> data/config.json -> GEMINI_MODEL env var -> DEFAULT_GEMINI_MODEL.
    """
    global _current_gemini_model
    if model:
        return normalize_gemini_model(model)

    if _current_gemini_model:
        return _current_gemini_model

    # Check persistent config file
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("gemini_model"):
                    _current_gemini_model = normalize_gemini_model(str(data["gemini_model"]))
                    return _current_gemini_model
        except Exception as e:
            print(f"Warning: Failed to read {config_path}: {e}")

    env_model = os.getenv("GEMINI_MODEL")
    if env_model:
        _current_gemini_model = normalize_gemini_model(env_model)
        return _current_gemini_model

    _current_gemini_model = DEFAULT_GEMINI_MODEL
    return _current_gemini_model


def set_gemini_model(model_name: str, config_path: str = CONFIG_PATH) -> str:
    """
    Sets the active Gemini model globally and persists it to config_path.
    Returns the canonical normalized model name.
    """
    global _current_gemini_model
    canonical = normalize_gemini_model(model_name)
    _current_gemini_model = canonical

    # Persist to config file
    try:
        data_dir = os.path.dirname(config_path)
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)

        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                if not isinstance(config_data, dict):
                    config_data = {}
            except Exception:
                config_data = {}

        config_data["gemini_model"] = canonical

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to persist model config to {config_path}: {e}")

    return canonical


async def validate_gemini_model(
    model_name: str, client: Optional[genai.Client] = None
) -> tuple[bool, str, str]:
    """
    Validates if a Gemini model is available for use via the Gemini API (AI Studio).

    Args:
        model_name: The requested model name or alias.
        client: Optional genai.Client instance. If not provided, initializes from env.

    Returns:
        tuple (is_valid: bool, canonical_name: str, message_or_error: str)
    """
    canonical = normalize_gemini_model(model_name)
    cli = client or get_gemini_client()
    if not cli:
        return False, canonical, "Gemini API client is not configured (missing GEMINI_API_KEY)."

    try:
        model_info = await cli.aio.models.get(model=canonical)
        actions = getattr(model_info, "supported_actions", None) or getattr(
            model_info, "supported_generation_methods", None
        )
        if actions and "generateContent" not in actions:
            return (
                False,
                canonical,
                f"Model '{canonical}' was found in AI Studio, but does not support content generation (supported actions: {', '.join(actions)}).",
            )

        disp = getattr(model_info, "display_name", None) or canonical
        return True, canonical, f"Model '{disp}' ({canonical}) is available and valid."
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "NOT_FOUND" in err_str or "not found" in err_str.lower():
            return (
                False,
                canonical,
                f"Model '{canonical}' was not found or is unavailable for use in AI Studio. Please check https://aistudio.google.com/docs/models for available models.",
            )
        return False, canonical, f"Failed to verify model '{canonical}': {err_str}"


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
