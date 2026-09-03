"""
tools/obsidian.py - AI Tool definitions and handlers for Obsidian / Markdown Vault operations.
All tools are strictly gated to the bot owner.
"""

import json
import os
import discord
from google.genai import types

from utils.db import run_in_executor
from utils.discord_helpers import is_user_authorized
from utils import obsidian as vault_utils


async def _is_bot_owner(bot, user_id: str) -> bool:
    """Checks if the user ID belongs to the bot owner."""
    if not user_id:
        return False

    owner_id_env = os.getenv("OWNER_ID")
    if owner_id_env and str(user_id).strip() == owner_id_env.strip():
        return True

    try:
        uid_int = int(user_id)
        if hasattr(bot, "is_owner"):
            obj = discord.Object(id=uid_int)
            if await bot.is_owner(obj):
                return True
        if getattr(bot, "owner_id", None) == uid_int:
            return True
        if getattr(bot, "owner_ids", None) and uid_int in bot.owner_ids:
            return True
    except Exception:
        pass

    return False


async def _is_vault_authorized(bot, user_id: str) -> bool:
    """Checks if the user ID belongs to the bot owner or is whitelisted for vault access."""
    if not user_id:
        return False

    if await _is_bot_owner(bot, user_id):
        return True

    try:
        uid_int = int(user_id)
        if is_user_authorized(uid_int, "VAULT") or is_user_authorized(uid_int, "OBSIDIAN"):
            return True
    except Exception:
        pass

    return False


OWNER_DENIED_MSG = (
    "Permission Denied: Only the bot owner or authorized whitelisted users are permitted to access or modify the Obsidian vault."
)


# =========================================================================
# Tool Function Declarations
# =========================================================================

VAULT_LIST_FILES_TOOL = types.FunctionDeclaration(
    name="vault_list_files",
    description="Lists files, notes, and subdirectories in the local Obsidian vault folder. Useful for exploring vault structure or discovering existing notes.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "directory": types.Schema(
                type=types.Type.STRING,
                description="Optional relative directory path to list (e.g. 'Projects', 'Journal', 'Reading List'). Defaults to the vault root.",
            ),
            "recursive": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to list files recursively through subdirectories (defaults to false).",
            ),
        },
    ),
)

VAULT_READ_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_read_note",
    description="Reads the complete content and parsed YAML frontmatter of a markdown note from the Obsidian vault.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path or note name within the vault (e.g. 'Projects/Shisho.md', 'Journal/2026-09-02', 'Reading List/Dune.md').",
            ),
        },
        required=["path"],
    ),
)

VAULT_WRITE_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_write_note",
    description="Creates a new markdown note or completely overwrites an existing note in the Obsidian vault with full formatting, YAML frontmatter, and wikilinks.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path or note name to write (e.g. 'Projects/NewFeature.md', 'Index/MOC-AI.md'). Subdirectories will be created automatically.",
            ),
            "content": types.Schema(
                type=types.Type.STRING,
                description="The complete markdown content to write into the note.",
            ),
            "overwrite": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to overwrite if the note already exists (defaults to true). Set to false to prevent accidental overwrite.",
            ),
        },
        required=["path", "content"],
    ),
)

VAULT_PATCH_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_patch_note",
    description="Performs a surgical replacement of a specific text snippet or block within an existing Obsidian note. Ideal for updating formatting, fixing typos, or inserting links without rewriting the entire file.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path to the note to patch (e.g. 'Projects/Shisho.md').",
            ),
            "target_snippet": types.Schema(
                type=types.Type.STRING,
                description="The exact text snippet currently in the note that should be replaced.",
            ),
            "replacement_snippet": types.Schema(
                type=types.Type.STRING,
                description="The new replacement text to substitute in place of target_snippet.",
            ),
            "replace_all": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to replace all occurrences of target_snippet if multiple matches are found (defaults to false).",
            ),
        },
        required=["path", "target_snippet", "replacement_snippet"],
    ),
)

VAULT_APPEND_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_append_note",
    description="Appends text, logs, quotes, or notes to an existing Obsidian note, either at the very end of the file or under a specific Markdown heading (e.g. '## Notes' or '## Log').",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path to the note (e.g. 'Journal/2026-09-02.md', 'Books/Dune.md').",
            ),
            "content": types.Schema(
                type=types.Type.STRING,
                description="The text content or bullet points to append.",
            ),
            "heading": types.Schema(
                type=types.Type.STRING,
                description="Optional markdown heading under which to append content (e.g. '## Notes', '## Quotes', '## Summary'). If the heading does not exist, it will be created at the end.",
            ),
        },
        required=["path", "content"],
    ),
)

VAULT_SEARCH_TOOL = types.FunctionDeclaration(
    name="vault_search",
    description="Searches across the Obsidian vault for notes matching a query. Supports searching by note text/body, filenames, frontmatter properties, or tags (#tag).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The search term, keyword, or tag (e.g. 'architecture', '#ai', 'author: Weir').",
            ),
            "search_in": types.Schema(
                type=types.Type.STRING,
                description="Where to search: 'all' (default), 'content' (note body), 'filename' (note titles/paths), 'tag' (tags only), or 'frontmatter' (YAML metadata only).",
            ),
            "max_results": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of matching notes to return (default is 15).",
            ),
        },
        required=["query"],
    ),
)

VAULT_DELETE_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_delete_note",
    description="Deletes a note or directory from the Obsidian vault. By default, safely moves it into the vault's '.trash/' folder.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path of the note or folder to delete (e.g. 'Scratch/temp.md').",
            ),
            "permanent": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, permanently unlinks the file instead of moving it to .trash/ (default is false).",
            ),
        },
        required=["path"],
    ),
)

VAULT_MOVE_NOTE_TOOL = types.FunctionDeclaration(
    name="vault_move_note",
    description="Renames or moves a note or folder within the Obsidian vault.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "source_path": types.Schema(
                type=types.Type.STRING,
                description="Current relative path of the note or folder (e.g. 'Inbox/idea.md').",
            ),
            "target_path": types.Schema(
                type=types.Type.STRING,
                description="New relative destination path (e.g. 'Projects/idea.md').",
            ),
        },
        required=["source_path", "target_path"],
    ),
)

VAULT_GET_BACKLINKS_TOOL = types.FunctionDeclaration(
    name="vault_get_backlinks",
    description="Finds all notes in the Obsidian vault that reference/link to a specific note via [[wikilinks]] or markdown links.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "target": types.Schema(
                type=types.Type.STRING,
                description="The note name, title, or relative path to find backlinks for (e.g. 'Shisho', 'Projects/Shisho').",
            ),
        },
        required=["target"],
    ),
)


# =========================================================================
# Handlers
# =========================================================================

async def handle_vault_list_files(bot, args: dict, user_id: str) -> str:
    """Handler for vault_list_files."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    directory = str(args.get("directory", "")).strip()
    recursive = bool(args.get("recursive", False))

    try:
        files = await run_in_executor(
            vault_utils.list_vault_files,
            dir_path=directory,
            recursive=recursive,
        )
        if not files:
            return f"No files found in vault directory '{directory or '/'}'."

        lines = [f"Files in vault `{directory or '/'}` ({len(files)} items):"]
        for item in files:
            prefix = "📁" if item["is_dir"] else "📄"
            lines.append(f"- {prefix} `{item['path']}`" + (f" ({item['size']} bytes)" if not item["is_dir"] else ""))
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing vault files: {str(e)}"


async def handle_vault_read_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_read_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    path = str(args.get("path", "")).strip()
    if not path:
        return "Error: 'path' parameter is required."

    try:
        note_data = await run_in_executor(vault_utils.read_note, rel_path=path)
        output = [
            f"### Note: `{note_data['path']}` ({note_data['size']} bytes)",
        ]
        if note_data.get("frontmatter"):
            output.append(f"**Frontmatter:**\n```json\n{json.dumps(note_data['frontmatter'], indent=2)}\n```")
        output.append(f"**Content:**\n```markdown\n{note_data['content']}\n```")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error reading note '{path}': {str(e)}"


async def handle_vault_write_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_write_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    overwrite = bool(args.get("overwrite", True))

    if not path:
        return "Error: 'path' parameter is required."

    try:
        res = await run_in_executor(
            vault_utils.write_note,
            rel_path=path,
            content=content,
            overwrite=overwrite,
        )
        return f"Successfully {res['status']} note `{res['path']}` ({res['size']} bytes) in Obsidian vault."
    except Exception as e:
        return f"Error writing note '{path}': {str(e)}"


async def handle_vault_patch_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_patch_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    path = str(args.get("path", "")).strip()
    target_snippet = str(args.get("target_snippet", ""))
    replacement_snippet = str(args.get("replacement_snippet", ""))
    replace_all = bool(args.get("replace_all", False))

    if not path or not target_snippet:
        return "Error: 'path' and 'target_snippet' parameters are required."

    try:
        res = await run_in_executor(
            vault_utils.patch_note,
            rel_path=path,
            target_snippet=target_snippet,
            replacement_snippet=replacement_snippet,
            replace_all=replace_all,
        )
        return f"Successfully patched `{res['path']}` (replaced {res['replaced_occurrences']} occurrence(s))."
    except Exception as e:
        return f"Error patching note '{path}': {str(e)}"


async def handle_vault_append_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_append_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    heading = args.get("heading")
    if heading:
        heading = str(heading).strip()

    if not path or not content:
        return "Error: 'path' and 'content' parameters are required."

    try:
        res = await run_in_executor(
            vault_utils.append_note,
            rel_path=path,
            content=content,
            heading=heading,
        )
        target_desc = f"under heading '{heading}'" if heading else "at the end of file"
        return f"Successfully appended content to `{res['path']}` ({target_desc})."
    except Exception as e:
        return f"Error appending to note '{path}': {str(e)}"


async def handle_vault_search(bot, args: dict, user_id: str) -> str:
    """Handler for vault_search."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    query = str(args.get("query", "")).strip()
    search_in = str(args.get("search_in", "all")).strip().lower()
    max_results = int(args.get("max_results", 15))

    if not query:
        return "Error: 'query' parameter is required."

    try:
        results = await run_in_executor(
            vault_utils.search_vault,
            query=query,
            search_in=search_in,
            max_results=max_results,
        )
        if not results:
            return f"No matches found for '{query}' in Obsidian vault (search_in='{search_in}')."

        lines = [f"Found {len(results)} matching note(s) for '{query}':"]
        for r in results:
            lines.append(f"- 📄 **`{r['path']}`**")
            for m in r["matches"][:3]:
                lines.append(f"  └ {m}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching vault: {str(e)}"


async def handle_vault_delete_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_delete_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    path = str(args.get("path", "")).strip()
    permanent = bool(args.get("permanent", False))

    if not path:
        return "Error: 'path' parameter is required."

    try:
        res = await run_in_executor(
            vault_utils.delete_note,
            rel_path=path,
            permanent=permanent,
        )
        if res.get("status") == "moved_to_trash":
            return f"Safely moved `{res['path']}` to vault trash (`{res['trash_path']}`)."
        return f"Permanently deleted `{res['path']}` from vault."
    except Exception as e:
        return f"Error deleting note '{path}': {str(e)}"


async def handle_vault_move_note(bot, args: dict, user_id: str) -> str:
    """Handler for vault_move_note."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    source_path = str(args.get("source_path", "")).strip()
    target_path = str(args.get("target_path", "")).strip()

    if not source_path or not target_path:
        return "Error: 'source_path' and 'target_path' are required."

    try:
        res = await run_in_executor(
            vault_utils.move_note,
            source_rel=source_path,
            target_rel=target_path,
        )
        return f"Successfully moved `{res['from']}` to `{res['to']}`."
    except Exception as e:
        return f"Error moving note: {str(e)}"


async def handle_vault_get_backlinks(bot, args: dict, user_id: str) -> str:
    """Handler for vault_get_backlinks."""
    if not await _is_vault_authorized(bot, user_id):
        return OWNER_DENIED_MSG

    target = str(args.get("target", "")).strip()
    if not target:
        return "Error: 'target' parameter is required."

    try:
        backlinks = await run_in_executor(
            vault_utils.get_backlinks,
            target_name_or_path=target,
        )
        if not backlinks:
            return f"No backlinks found in vault pointing to '[[{target}]]'."

        lines = [f"Notes linking to `[[{target}]]` ({len(backlinks)} reference(s)):"]
        for b in backlinks:
            links_str = ", ".join(f"`[[{link}]]`" for link in b["matched_links"])
            lines.append(f"- 📄 `{b['source_path']}`: {links_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error finding backlinks for '{target}': {str(e)}"
