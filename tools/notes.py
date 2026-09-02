"""
tools/notes.py - AI Tool definitions and handlers for the Notes domain.
"""

from google.genai import types


ADD_NOTE_TOOL = types.FunctionDeclaration(
    name="add_note",
    description="Saves a personal note to the user's notes in PocketBase. If image, audio, or document attachments are provided with the message, they are automatically uploaded and stored with the note.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "text": types.Schema(
                type=types.Type.STRING,
                description="The body / content of the note",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="Optional title or subject for the note",
            ),
            "archived": types.Schema(
                type=types.Type.BOOLEAN,
                description="Optional flag whether to create this note as archived (defaults to false)",
            ),
        },
        required=["text"],
    ),
)

GET_NOTES_TOOL = types.FunctionDeclaration(
    name="get_notes",
    description="Retrieves personal notes from the user's PocketBase notes collection. By default, only active (unarchived) notes are returned unless 'archived' is explicitly set to true.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Optional search keywords to filter or search notes by title, text, or editor content (e.g. 'purdue hackers finance'). Pass search keywords only without conversational filler like 'my' or 'note'.",
            ),
            "archived": types.Schema(
                type=types.Type.BOOLEAN,
                description="Optional filter: set to true for archived notes only, false for active/unarchived notes only (default is false).",
            ),
        },
    ),
)

ARCHIVE_NOTE_TOOL = types.FunctionDeclaration(
    name="archive_note",
    description="Archives a personal note in the user's PocketBase notes collection by title, keyword, or note ID.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, keyword, or ID of the note to archive",
            ),
        },
        required=["query"],
    ),
)

UNARCHIVE_NOTE_TOOL = types.FunctionDeclaration(
    name="unarchive_note",
    description="Unarchives / restores an archived personal note in the user's PocketBase notes collection by title, keyword, or note ID.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, keyword, or ID of the note to unarchive",
            ),
        },
        required=["query"],
    ),
)

DELETE_NOTE_TOOL = types.FunctionDeclaration(
    name="delete_note",
    description="Deletes a personal note from the user's PocketBase notes by note title, text keyword, or note ID. If query is 'all archived' or 'archived', deletes all archived notes.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, keyword, or ID of the note to delete, or 'all archived' to delete all archived notes",
            ),
        },
        required=["query"],
    ),
)

DELETE_ARCHIVED_NOTES_TOOL = types.FunctionDeclaration(
    name="delete_archived_notes",
    description="Permanently deletes all archived personal notes for the user from PocketBase.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
    ),
)

UPDATE_NOTE_TOOL = types.FunctionDeclaration(
    name="update_note",
    description="Updates an existing personal note in PocketBase (title, content, or archived status).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, keyword, or ID of the note to update",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="New title for the note (optional)",
            ),
            "text": types.Schema(
                type=types.Type.STRING,
                description="New text/content for the note (optional)",
            ),
            "archived": types.Schema(
                type=types.Type.BOOLEAN,
                description="Set archived status to true or false (optional)",
            ),
        },
        required=["query"],
    ),
)


def extract_attachments_from_context(context: dict | None) -> list[tuple[str, bytes]]:
    """Extracts a list of (filename, bytes) tuples from an execution context."""
    if not context or not isinstance(context, dict):
        return []
    raw_atts = context.get("attachments", [])
    result: list[tuple[str, bytes]] = []
    for att in raw_atts:
        if isinstance(att, dict) and "filename" in att and "bytes" in att:
            result.append((att["filename"], att["bytes"]))
        elif isinstance(att, (tuple, list)) and len(att) == 2:
            result.append((att[0], att[1]))
    return result


async def handle_add_note(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the add_note AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    text = str(args.get("text", "")).strip()
    title = str(args.get("title", "")).strip()
    archived = bool(args.get("archived", False))
    if not text and not title:
        return "Error: Note text or title is required."
    attachments = extract_attachments_from_context(context)
    res = await notes_cog.add_note(
        user_id,
        text=text,
        title=title,
        attachments=attachments or None,
        archived=archived,
    )
    return res


async def handle_get_notes(bot, args: dict, user_id: str) -> str:
    """Handler for the get_notes AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    query = str(args.get("query", "")).strip()
    archived_arg = args.get("archived")
    if archived_arg is None:
        archived = False
    elif isinstance(archived_arg, str) and archived_arg.lower() in ("all", "both", "any"):
        archived = None
    elif isinstance(archived_arg, str):
        archived = archived_arg.lower() in ("true", "1", "yes", "archived")
    else:
        archived = bool(archived_arg)

    notes = await notes_cog.get_notes(user_id, limit=10, query=query or None, archived=archived)
    if isinstance(notes, str):
        return notes
    if not notes:
        filter_str = "archived " if archived is True else ("active " if archived is False else "")
        return f"No {filter_str}notes found{' matching ' + query if query else ''}."
    formatted = []
    for n in notes:
        t = n.get("title") or "Untitled Note"
        txt = n.get("text") or n.get("editor") or ""
        created = n.get("created") or ""
        status = " [Archived]" if n.get("archived") else ""
        att_info = ""
        if n.get("attachment_filenames"):
            att_info = f"\nAttachments: {', '.join(n['attachment_filenames'])}"
        formatted.append(f"Title: {t}{status}\nContent: {txt}{att_info}\nDate: {created}")
    return "\n---\n".join(formatted)


async def handle_archive_note(bot, args: dict, user_id: str) -> str:
    """Handler for the archive_note AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip()
    if not query:
        return "Error: Note title, keyword, or ID is required."
    return await notes_cog.archive_note(user_id, query)


async def handle_unarchive_note(bot, args: dict, user_id: str) -> str:
    """Handler for the unarchive_note AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip()
    if not query:
        return "Error: Note title, keyword, or ID is required."
    return await notes_cog.unarchive_note(user_id, query)


async def handle_delete_note(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_note AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip()
    if not query:
        return "Error: Note title, keyword, or ID is required."
    res = await notes_cog.delete_note(user_id, query)
    return res


async def handle_delete_archived_notes(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_archived_notes AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    return await notes_cog.delete_archived_notes(user_id)


async def handle_update_note(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the update_note AI tool."""
    notes_cog = bot.get_cog("Notes")
    if not notes_cog:
        return "Error: Notes cog is unavailable."
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: Note title, keyword, or ID is required."
    title = args.get("title")
    text = args.get("text")
    editor = args.get("editor")
    archived = args.get("archived")
    if isinstance(archived, str):
        archived = archived.lower() in ("true", "1", "yes")
    attachments = extract_attachments_from_context(context)
    return await notes_cog.update_note(
        user_id,
        query,
        title=title if title is not None else None,
        text=text if text is not None else None,
        editor=editor if editor is not None else None,
        archived=archived if archived is not None else None,
        attachments=attachments or None,
    )

