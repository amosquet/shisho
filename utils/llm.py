"""
utils/llm.py - Centralized Google Gemini LLM utilities and error handlers for Shisho.
Standardizes Gemini client initialization, model configuration, transient error detection,
retry logic with exponential backoff, and Sentry error reporting.
"""

import asyncio
import json
import os
import re
import sys
from typing import Any, Optional


from google import genai
from google.genai import errors, types
import sentry_sdk

# Shared Model Constant
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
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
    "flash": "gemini-3.7-flash",
    "pro": "gemini-2.5-pro",
    "flash-lite": "gemini-2.5-flash-lite",
    "flash lite": "gemini-2.5-flash-lite",
    "lite": "gemini-2.5-flash-lite",
    "3.7-flash": "gemini-3.7-flash",
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
    Capture exception to Sentry, log to stderr, and return a user-friendly error message.

    Args:
        error: The caught exception.
        include_details: Whether to include verbatim error details for slash commands.

    Returns:
        User-facing error message string.
    """
    print(f"[Gemini API Error] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
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


def is_tool_combination_error(error: Exception) -> bool:
    """
    Check if an exception indicates that the model or API does not support
    combining built-in tools (like google_search) with custom function declarations.
    """
    msg = str(error).lower()
    return (
        "tool" in msg
        and (
            "combination" in msg
            or "cannot be used with function_declarations" in msg
            or "cannot be used with" in msg
            or "unsupported" in msg
            or "not supported" in msg
            or "google_search" in msg
            or "multiple tool" in msg
            or "configuration" in msg
            or "invalid argument" in msg
            or "invalid_argument" in msg
        )
    ) or (
        isinstance(error, errors.APIError)
        and getattr(error, "code", None) == 400
        and ("tool" in msg or "invalid_argument" in msg or "invalid argument" in msg)
    )



def query_targets_bot_tools(text: str) -> bool:
    """
    Determine if a prompt explicitly targets Shisho's bot tools (reading list,
    reminders, notes, vault, printing, channels, anki).
    """
    if not text or not isinstance(text, str):
        return False
    t = text.lower()
    tool_keywords = [
        "reading list",
        "read book",
        "add book",
        "books list",
        "recommendation",
        "reminder",
        "remind me",
        "alarm",
        "schedule",
        "note",
        "notes",
        "vault",
        "obsidian",
        "print",
        "printer",
        "anki",
        "flashcard",
        "channel",
        "discord channel",
        "ai model",
        "switch model",
        "change model",
    ]
    return any(k in t for k in tool_keywords)



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


SOURCE_REQUEST_PATTERN = re.compile(
    r"\b(sources?|citations?|references?)\b|"
    r"\b(cite\s+(your|the)?\s*sources?|cite\s+(this|it))\b|"
    r"\b(include|with|show|provide|give|list|send)\s+(?:(?:me|us|the)\s+)?(links?|sources?|citations?|references?)\b|"
    r"\bwhere\s+did\s+you\s+(get|find)\s+this\b",
    re.IGNORECASE,
)



def user_requested_sources(text: str) -> bool:
    """
    Check whether a user prompt or text explicitly requests sources, citations, references, or links.
    """
    if not text or not isinstance(text, str):
        return False
    return bool(SOURCE_REQUEST_PATTERN.search(text))


def format_grounding_citations(
    text: str, grounding_metadata: Any, include_sources: bool = False
) -> str:
    """
    Process Google Search grounding metadata on a Gemini response.

    If include_sources is False, returns text as-is without appending sources or citations.
    If include_sources is True, inserts inline citations and appends a Sources list with
    <URL> embed suppression for Discord.
    """
    if not text or not grounding_metadata or not include_sources:
        return text

    chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
    supports = getattr(grounding_metadata, "grounding_supports", None) or []

    if not chunks:
        return text

    valid_sources = []
    for i, chunk in enumerate(chunks):
        web = getattr(chunk, "web", None)
        if web and getattr(web, "uri", None):
            title = (
                getattr(web, "title", None)
                or getattr(web, "domain", None)
                or f"Source {i+1}"
            )
            valid_sources.append(
                {"index": i + 1, "title": title.strip(), "uri": web.uri.strip()}
            )

    if not valid_sources:
        return text

    # Insert inline citations if supports are provided
    sorted_supports = sorted(
        [
            s
            for s in supports
            if getattr(s, "segment", None)
            and getattr(s.segment, "end_index", None) is not None
        ],
        key=lambda s: s.segment.end_index,
        reverse=True,
    )

    cited_indices = set()
    for support in sorted_supports:
        end_index = support.segment.end_index
        indices = getattr(support, "grounding_chunk_indices", None) or []
        if not indices or end_index is None or end_index > len(text) or end_index < 0:
            continue

        citation_links = []
        for idx in indices:
            if 0 <= idx < len(chunks):
                web = getattr(chunks[idx], "web", None)
                if web and getattr(web, "uri", None):
                    src_num = idx + 1
                    cited_indices.add(src_num)
                    citation_links.append(f"[[{src_num}]](<{web.uri}>)")

        if citation_links:
            cite_str = " " + " ".join(citation_links)
            text = text[:end_index] + cite_str + text[end_index:]

    # Append Sources block at the end
    if cited_indices:
        sources_to_show = [s for s in valid_sources if s["index"] in cited_indices]
    else:
        sources_to_show = valid_sources[:5]

    if sources_to_show:
        source_lines = [
            f"{s['index']}. [{s['title']}](<{s['uri']}>)"
            for s in sources_to_show[:8]
        ]
        text = text.rstrip() + "\n\n**Sources:**\n" + "\n".join(source_lines)

    return text

