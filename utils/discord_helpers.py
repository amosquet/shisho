"""
utils/discord_helpers.py - Centralized Discord utility helpers for Shisho.
Includes message splitting and dynamic persistent whitelist authorization checks.
"""

import asyncio
import json
import os
import re
from typing import Union

from utils.db import run_in_executor

PERMISSIONS_FILE = os.path.join("data", "permissions.json")
_permissions_cache: dict[str, list[int]] | None = None

UNLINKED_ACCOUNT_MESSAGE = (
    "You don't have a linked Shisho account yet. Run `/register` to create one in seconds!"
)


def format_for_discord(text: str) -> str:
    """
    Sanitize and format text for Discord, converting LaTeX math markup
    and equations into clean Discord markdown and Unicode symbols.
    Preserves code blocks and inline code untouched.
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Protect code blocks and inline code from modification
    placeholders: list[str] = []

    def save_placeholder(match: re.Match) -> str:
        idx = len(placeholders)
        placeholders.append(match.group(0))
        return f"\x00PLACEHOLDER_{idx}\x00"

    # Save multi-line code blocks
    text = re.sub(r"```[\s\S]*?```", save_placeholder, text)
    # Save inline code blocks
    text = re.sub(r"`[^`\n]+`", save_placeholder, text)

    # 2. Convert common LaTeX font and styling commands FIRST
    text = re.sub(r"\\(?:mathbf|textbf|boldsymbol)\{([^}]*)\}", lambda m: f"**{m.group(1)}**", text)
    text = re.sub(r"\\(?:mathit|textit)\{([^}]*)\}", lambda m: f"*{m.group(1)}*", text)
    text = re.sub(r"\\underline\{([^}]*)\}", lambda m: f"__{m.group(1)}__", text)
    text = re.sub(r"\\(?:text|mathrm|operatorname)\{([^}]*)\}", lambda m: m.group(1), text)

    # 3. Convert fractions and roots
    text = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", lambda m: f"({m.group(1)} / {m.group(2)})", text)
    text = re.sub(r"\\sqrt\{([^}]*)\}", lambda m: f"√({m.group(1)})", text)

    # 4. Convert LaTeX symbols and Greek letters to Unicode
    symbols = {
        r"\times": "×",
        r"\approx": "≈",
        r"\cdot": "·",
        r"\pm": "±",
        r"\mp": "∓",
        r"\leq": "≤",
        r"\le": "≤",
        r"\geq": "≥",
        r"\ge": "≥",
        r"\neq": "≠",
        r"\ne": "≠",
        r"\div": "÷",
        r"\degree": "°",
        r"^\circ": "°",
        r"\circ": "°",
        r"\infty": "∞",
        r"\rightarrow": "→",
        r"\leftarrow": "←",
        r"\Rightarrow": "⇒",
        r"\Leftarrow": "⇐",
        r"\pi": "π",
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\theta": "θ",
        r"\sigma": "σ",
        r"\mu": "μ",
    }
    for sym, repl in symbols.items():
        text = re.sub(re.escape(sym) + r"(?![a-zA-Z])", repl, text)

    # 5. Clean up display math $$ ... $$ and double $$ before numbers
    text = re.sub(r"\$\$([0-9])", r"$\1", text)
    text = re.sub(r"\$\$", "", text)

    # 6. Clean trailing equation closing $ attached to math expressions
    text = re.sub(r"(\*\*[^*\n]+?\*\*)\$(?=\s|[,\.!?\)]|$)", lambda m: m.group(1), text)
    text = re.sub(r"(\$[0-9,.]+)\$(?=\s|[,\.!?\)]|$)", lambda m: m.group(1), text)
    text = re.sub(r"(?<=[0-9a-zA-Z\)])\$(?=\s|\)|$|[,\.!?])", "", text)

    # 7. Normalize horizontal spaces while preserving newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,\.\)\!])", r"\1", text)
    text = re.sub(r"(\() ", r"\1", text)

    # 8. Restore code blocks and inline code
    for idx, block in enumerate(placeholders):
        text = text.replace(f"\x00PLACEHOLDER_{idx}\x00", block)

    return text.strip()



def normalize_cog_name(cog_name: str) -> str:
    """Normalize cog name to uppercase alphanumeric string (e.g., 'AIChat' -> 'AICHAT')."""
    return cog_name.upper().replace("_", "").replace(" ", "")


def _load_permissions() -> dict[str, list[int]]:
    """Load permissions from data/permissions.json if present, falling back to empty dict."""
    global _permissions_cache
    if _permissions_cache is not None:
        return _permissions_cache

    target_file = PERMISSIONS_FILE
    if not os.path.exists(target_file) and os.path.exists("permissions.json"):
        target_file = "permissions.json"

    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                _permissions_cache = {
                    normalize_cog_name(k): [int(uid) for uid in v if str(uid).isdigit()]
                    for k, v in raw_data.items()
                    if isinstance(v, list)
                }
                return _permissions_cache
        except Exception as e:
            print(f"Failed to load {target_file}: {e}")
            _permissions_cache = {}
            return _permissions_cache

    _permissions_cache = {}
    return _permissions_cache


def _save_permissions():
    """Save the permissions cache to data/permissions.json."""
    global _permissions_cache
    if _permissions_cache is None:
        return
    try:
        os.makedirs(os.path.dirname(PERMISSIONS_FILE) or ".", exist_ok=True)
        with open(PERMISSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_permissions_cache, f, indent=2)
    except Exception as e:
        print(f"Failed to save {PERMISSIONS_FILE}: {e}")


def get_cog_whitelist(cog_name: str) -> list[int]:
    """
    Get the list of whitelisted Discord user IDs for a given cog.
    Checks in-memory/persisted cache first; if not set, seeds from os.environ.
    """
    clean_name = normalize_cog_name(cog_name)
    exact_name = cog_name.upper()
    perms = _load_permissions()

    if clean_name in perms:
        return list(perms[clean_name])

    # Fallback to environment variables
    whitelist_env = os.getenv(f"WHITELIST_{clean_name}")
    if whitelist_env is None:
        whitelist_env = os.getenv(f"WHITELIST_{exact_name}")

    if whitelist_env is not None and whitelist_env.strip():
        ids = [
            int(uid.strip())
            for uid in whitelist_env.split(",")
            if uid.strip().isdigit()
        ]
        return ids

    return []


def add_user_to_whitelist(user_id: Union[int, str], cog_name: str) -> bool:
    """
    Add a user ID to a cog whitelist and persist the change.
    Returns True if user was added, False if already present.
    """
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return False

    clean_name = normalize_cog_name(cog_name)
    perms = _load_permissions()
    current = get_cog_whitelist(cog_name)

    if user_id_int in current:
        return False

    current.append(user_id_int)
    perms[clean_name] = current
    _save_permissions()

    # Sync os.environ for backward-compatibility
    os.environ[f"WHITELIST_{clean_name}"] = ",".join(str(uid) for uid in current)
    return True


def remove_user_from_whitelist(user_id: Union[int, str], cog_name: str) -> bool:
    """
    Remove a user ID from a cog whitelist and persist the change.
    Returns True if user was removed, False if not in whitelist.
    """
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return False

    clean_name = normalize_cog_name(cog_name)
    perms = _load_permissions()
    current = get_cog_whitelist(cog_name)

    if user_id_int not in current:
        return False

    current.remove(user_id_int)
    perms[clean_name] = current
    _save_permissions()

    # Sync os.environ for backward-compatibility
    os.environ[f"WHITELIST_{clean_name}"] = ",".join(str(uid) for uid in current)
    return True


async def async_get_cog_whitelist(cog_name: str) -> list[int]:
    """Non-blocking async wrapper to get a cog's whitelist."""
    return await run_in_executor(get_cog_whitelist, cog_name)


async def async_add_user_to_whitelist(user_id: Union[int, str], cog_name: str) -> bool:
    """Non-blocking async wrapper to add a user to a cog's whitelist."""
    return await run_in_executor(add_user_to_whitelist, user_id, cog_name)


async def async_remove_user_from_whitelist(user_id: Union[int, str], cog_name: str) -> bool:
    """Non-blocking async wrapper to remove a user from a cog's whitelist."""
    return await run_in_executor(remove_user_from_whitelist, user_id, cog_name)


def split_message(
    text: str, limit: int = 1990, max_len: int | None = None
) -> list[str]:
    """
    Split text into chunks satisfying Discord's message length limits.

    Args:
        text: The string to split.
        limit: Max character length per chunk (default 1990).
        max_len: Backward-compatible alias for limit.

    Returns:
        List of string chunks.
    """
    if max_len is not None:
        limit = max_len

    if not text or not str(text).strip():
        return []
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_idx = remaining.rfind("\n", 0, limit)
        if split_idx == -1 or split_idx < limit // 2:
            split_idx = remaining.rfind(" ", 0, limit)
        if split_idx == -1 or split_idx < limit // 2:
            split_idx = limit

        chunks.append(remaining[:split_idx].rstrip())
        remaining = remaining[split_idx:].lstrip()

    return [c for c in chunks if c]


def is_user_authorized(user_id: Union[int, str], cog_name: str) -> bool:
    """
    Check if a Discord user is authorized to use a given cog/plugin.

    Checks in order:
    1. Owner bypass (OWNER_ID in os.environ)
    2. Feature whitelist disable toggle (WHITELIST_ENABLE_<COG_NAME> == "false")
    3. Cog-specific whitelist (persisted in data/permissions.json or WHITELIST_<COG_NAME> in os.environ)
    4. Fallback: True if no OWNER_ID is configured, else False

    Args:
        user_id: Discord user ID (int or str).
        cog_name: Cog name (e.g. 'AIChat', 'ReadingList', 'NOTES', 'aichat').

    Returns:
        bool: True if authorized, False otherwise.
    """
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return False

    owner_id_str = os.getenv("OWNER_ID", "0")
    try:
        owner_id = int(owner_id_str)
    except (ValueError, TypeError):
        owner_id = 0

    if owner_id and user_id_int == owner_id:
        return True

    # Normalize cog name variants (e.g. 'AIChat' -> 'AICHAT', 'reading_list' -> 'READINGLIST')
    cog_clean = normalize_cog_name(cog_name)
    cog_exact = cog_name.upper()

    # Check if whitelist is explicitly disabled for this cog (making it public)
    enable_val = os.getenv(f"WHITELIST_ENABLE_{cog_clean}")
    if enable_val is None:
        enable_val = os.getenv(f"WHITELIST_ENABLE_{cog_exact}")
    if enable_val is not None and enable_val.strip().lower() == "false":
        return True

    # Public-by-default cogs (unless explicitly configured)
    if enable_val is None and cog_clean in ("AUTH",):
        return True

    # Check whitelist IDs from persistent storage or environment
    whitelist = get_cog_whitelist(cog_name)
    if whitelist:
        return user_id_int in whitelist

    # If WHITELIST_<COG> is explicitly set to empty string in env, enforce empty whitelist (deny non-owners)
    whitelist_env = os.getenv(f"WHITELIST_{cog_clean}")
    if whitelist_env is None:
        whitelist_env = os.getenv(f"WHITELIST_{cog_exact}")
    if whitelist_env is not None and whitelist_env == "":
        return False

    return not owner_id
