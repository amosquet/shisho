import os
import sentry_sdk
from google.genai import types
from utils.db import run_in_executor


PRINT_DOCUMENT_TOOL = types.FunctionDeclaration(
    name="print_document",
    description="Sends a document, note, text summary, or attached file to the physical printer via PocketBase Realtime queue or email fallback.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "content": types.Schema(
                type=types.Type.STRING,
                description="Text content, summary, or note text to print",
            ),
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Optional filename for the print job (e.g. 'hw1_260825.pdf', 'reading_list.txt', 'notes.txt')",
            ),
            "note_id": types.Schema(
                type=types.Type.STRING,
                description="Optional saved note ID or title to print",
            ),
        },
    ),
)


async def handle_print_document(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the print_document AI tool."""
    print_cog = bot.get_cog("Print")
    if not print_cog:
        return "Error: Print cog is unavailable."
    content = str(args.get("content", "")).strip()
    filename = str(args.get("filename", "")).strip()
    note_id = str(args.get("note_id", "")).strip()

    file_bytes = b""
    attachments = (context or {}).get("attachments", []) if isinstance(context, dict) else []

    # 1. Check if an attached file matches the requested filename or if user wants to print the attachment
    if attachments:
        matched_att = None
        if filename:
            for att in attachments:
                att_name = att.get("filename", "")
                if att_name.lower() == filename.lower():
                    matched_att = att
                    break
            if not matched_att:
                # Check base filename without extension or case-insensitive partial match
                for att in attachments:
                    att_name = att.get("filename", "")
                    if os.path.splitext(att_name.lower())[0] == os.path.splitext(filename.lower())[0]:
                        matched_att = att
                        break
        # If no explicit match or generic filename, pick the attachment
        if not matched_att and (not note_id and not content or filename in ("document.pdf", "document.txt", "file.pdf", "print.pdf", "attachment")):
            matched_att = attachments[0]
        elif not matched_att and len(attachments) == 1 and not note_id and not content:
            matched_att = attachments[0]

        if matched_att:
            file_bytes = matched_att.get("bytes", b"")
            filename = matched_att.get("filename", filename or "attachment.pdf")

    # 2. Check note_id if no attachment matched
    if not file_bytes and note_id:
        notes_cog = bot.get_cog("Notes")
        if notes_cog:
            notes = await notes_cog.get_notes(user_id, query=note_id)
            if notes and isinstance(notes, list) and len(notes) > 0:
                n = notes[0]
                if n.get("attachment_urls") and len(n["attachment_urls"]) > 0:
                    att_url = n["attachment_urls"][0]
                    att_name = n["attachment_filenames"][0] if n.get("attachment_filenames") else "note_attachment.pdf"
                    headers = {}
                    if n.get("file_token"):
                        headers["Authorization"] = n["file_token"]
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            async with session.get(att_url, headers=headers) as resp:
                                if resp.status == 200:
                                    file_bytes = await resp.read()
                                    filename = att_name
                    except Exception as e:
                        sentry_sdk.capture_exception(e)

                if not file_bytes:
                    title = n.get("title") or "Note"
                    body = n.get("text", "")
                    content = f"{title}\n{'=' * len(title)}\n\n{body}\n"
                    clean_title = "".join(
                        c for c in title if c.isalnum() or c in (" ", "_", "-")
                    ).strip()
                    filename = f"{clean_title or 'Note'}.txt"

    # 3. Check content if no attachment or note attachment was found
    if not file_bytes and content:
        if not filename:
            filename = "document.txt"
        file_bytes = content.encode("utf-8")

    if not file_bytes:
        return "Error: No printable content, note, or attachment found to print."

    if not filename:
        filename = "document.txt"

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
