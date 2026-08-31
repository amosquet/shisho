"""
tools/printing.py - AI Tool definitions and handlers for Physical Printing.
"""

from google.genai import types
from utils.db import run_in_executor


PRINT_DOCUMENT_TOOL = types.FunctionDeclaration(
    name="print_document",
    description="Sends a document, note, text summary, or reading list to the physical printer via PocketBase Realtime queue or email.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "content": types.Schema(
                type=types.Type.STRING,
                description="Text content, summary, or note text to print",
            ),
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Optional filename for the print job (e.g. 'reading_list.txt', 'notes.txt')",
            ),
            "note_id": types.Schema(
                type=types.Type.STRING,
                description="Optional saved note ID or title to print",
            ),
        },
    ),
)


async def handle_print_document(bot, args: dict, user_id: str) -> str:
    """Handler for the print_document AI tool."""
    print_cog = bot.get_cog("Print")
    if not print_cog:
        return "Error: Print cog is unavailable."
    content = str(args.get("content", "")).strip()
    filename = str(args.get("filename", "")).strip() or "document.txt"
    note_id = str(args.get("note_id", "")).strip()

    file_bytes = b""
    if note_id:
        notes_cog = bot.get_cog("Notes")
        if notes_cog:
            notes = await notes_cog.get_notes(user_id, query=note_id)
            if notes and isinstance(notes, list) and len(notes) > 0:
                n = notes[0]
                title = n.get("title") or "Note"
                body = n.get("text", "")
                content = f"{title}\n{'=' * len(title)}\n\n{body}\n"
                clean_title = "".join(
                    c for c in title if c.isalnum() or c in (" ", "_", "-")
                ).strip()
                filename = f"{clean_title or 'Note'}.txt"

    if not file_bytes and content:
        file_bytes = content.encode("utf-8")

    if not file_bytes:
        return "Error: No printable content or note found to print."

    try:
        success, res_id = await run_in_executor(
            print_cog._add_to_pocketbase,
            file_bytes,
            filename,
            str(user_id),
        )
        if success:
            return f"Successfully added '{filename}' to the PocketBase Realtime print queue."
    except Exception as e:
        # If PocketBase is unreachable or errors, fallback to email directly
        try:
            await run_in_executor(
                print_cog.send_print_email,
                filename,
                file_bytes,
                f"AI Print Fallback: {filename}",
            )
            return f"PocketBase print queue was unavailable ({e}), but '{filename}' was dispatched to the printer via email fallback."
        except Exception as mail_err:
            return f"Failed to queue print job via PocketBase ({e}) and email fallback failed ({mail_err})."

    return "Error: Failed to process print job."
