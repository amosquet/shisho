"""
tools/printing.py - AI Tool definitions and handlers for the Printing domain.
"""

import os
import sentry_sdk
from google.genai import types
from utils.db import run_in_executor


PRINT_DOCUMENT_TOOL = types.FunctionDeclaration(
    name="print_document",
    description="Sends a document, note, text summary, attached file, or Obsidian vault note to the physical printer via PocketBase Realtime queue or email fallback.",
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
                description="Optional saved PocketBase note ID or title to print",
            ),
            "vault_path": types.Schema(
                type=types.Type.STRING,
                description="Optional relative path or note title in the Obsidian vault to print (e.g. 'Biology/Lecture Note.md', 'biology lecture note')",
            ),
        },
    ),
)

LIST_PRINT_JOBS_TOOL = types.FunctionDeclaration(
    name="list_print_jobs",
    description="Lists and checks the status of print jobs in the PocketBase print queue (status: queued, printing, completed, or all).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="Optional filter by status: 'queued', 'printing', 'completed', or 'all' (default is 'all')",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of print jobs to return (optional, default 10)",
            ),
        },
    ),
)

CANCEL_PRINT_JOB_TOOL = types.FunctionDeclaration(
    name="cancel_print_job",
    description="Cancels a queued or printing print job in the PocketBase print queue by filename, job ID, or index.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Filename, job ID, or index of the print job to cancel",
            ),
        },
        required=["query"],
    ),
)


GENERIC_FILENAMES = {
    "",
    "document.pdf",
    "document.txt",
    "file.pdf",
    "print.pdf",
    "attachment",
    "attachment.pdf",
    "attachment.txt",
    "document",
    "file",
    "print",
    "this",
    "print_text.txt",
    "untitled",
    "untitled.pdf",
    "untitled.txt",
}

DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".json", ".py", ".html"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def score_attachment_match(att: dict, target_filename: str) -> float:
    """
    Score how well an attachment matches a target filename or description.
    Returns a score between 0.0 and 100.0+.
    """
    att_name = (att.get("filename") or "").strip()
    target = target_filename.strip()
    if not att_name or not target:
        return 0.0

    att_lower = att_name.lower()
    target_lower = target.lower()

    # 1. Exact full match
    if att_lower == target_lower:
        return 100.0

    att_stem = os.path.splitext(att_lower)[0]
    target_stem = os.path.splitext(target_lower)[0]

    # 2. Exact stem match (e.g. "invoice" vs "invoice.pdf")
    if att_stem == target_stem:
        return 95.0

    # 3. Direct stem containment (e.g. "hw1" in "hw1_260825.pdf")
    if (len(att_stem) >= 3 and att_stem in target_lower) or (len(target_stem) >= 3 and target_stem in att_lower):
        return 85.0

    # 4. Token-based overlap
    import re
    att_tokens = set(t for t in re.findall(r"[a-z0-9]+", att_lower) if len(t) >= 2)
    target_tokens = set(t for t in re.findall(r"[a-z0-9]+", target_lower) if len(t) >= 2)

    stop_words = {"the", "a", "an", "and", "or", "to", "for", "of", "in", "with", "on", "at", "by", "pdf", "txt", "doc", "file", "document", "print"}
    clean_att = att_tokens - stop_words
    clean_target = target_tokens - stop_words

    if clean_att and clean_target:
        overlap = clean_att & clean_target
        if overlap:
            overlap_ratio = len(overlap) / min(len(clean_att), len(clean_target))
            score = 50.0 + (len(overlap) * 10.0) + (overlap_ratio * 10.0)

            # Preference bonus: if target mentions pdf/document and attachment is PDF
            att_ext = os.path.splitext(att_lower)[1]
            if att_ext == ".pdf" and any(k in target_lower for k in ("pdf", "homework", "hw", "assignment", "paper", "doc")):
                score += 10.0
            return score

    return 0.0


def select_best_attachment(
    attachments: list[dict],
    filename: str,
    has_explicit_other_target: bool = False,
) -> dict | None:
    """
    Selects the most appropriate attachment from attachments:
    - If filename is generic or empty, picks the best attachment (preferring documents over images).
    - If filename is specific, scores attachments and picks the highest scoring match above threshold.
    - If filename is specific and no attachment matches, returns None to allow note/vault fallback.
    """
    if not attachments:
        return None

    clean_filename = filename.strip().lower()
    is_generic = clean_filename in GENERIC_FILENAMES

    if is_generic or not clean_filename:
        # Prioritize document attachments (.pdf, .txt, .md) over images
        for att in attachments:
            ext = os.path.splitext((att.get("filename") or "").lower())[1]
            if ext in DOCUMENT_EXTENSIONS:
                return att
        return attachments[0]

    # Specific filename provided: score each attachment
    best_att = None
    best_score = 0.0

    for att in attachments:
        score = score_attachment_match(att, filename)
        if score > best_score:
            best_score = score
            best_att = att

    if best_score >= 50.0:
        return best_att

    return None


async def handle_print_document(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the print_document AI tool."""
    print_cog = bot.get_cog("Print")
    if not print_cog:
        return "Error: Print cog is unavailable."
    content = str(args.get("content") or "").strip()
    filename = str(args.get("filename") or "").strip()
    note_id = str(args.get("note_id") or "").strip()
    vault_path = str(args.get("vault_path") or "").strip()

    file_bytes = b""
    attachments = (context or {}).get("attachments", []) if isinstance(context, dict) else []

    # 1. Check if an attached file matches the requested filename or if user wants to print the attachment
    if attachments:
        matched_att = select_best_attachment(
            attachments,
            filename,
            has_explicit_other_target=bool(note_id or vault_path or content),
        )

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

    # 3. Check vault_path if provided
    if not file_bytes and vault_path:
        from tools.obsidian import _is_vault_authorized
        from utils import obsidian as vault_utils

        if not await _is_vault_authorized(bot, user_id):
            return "Permission Denied: You are not authorized to access or print notes from the Obsidian vault."

        if not vault_utils.get_vault_path():
            return "Error: Obsidian vault path is not configured on the bot."

        note_data = None
        try:
            note_data = await run_in_executor(vault_utils.read_note, rel_path=vault_path)
        except Exception:
            # Fallback to search query across vault
            try:
                results = await run_in_executor(vault_utils.search_vault, query=vault_path, max_results=1)
                if results:
                    note_data = await run_in_executor(vault_utils.read_note, rel_path=results[0]["path"])
            except Exception as e:
                sentry_sdk.capture_exception(e)

        if note_data and note_data.get("content"):
            file_bytes = note_data["content"].encode("utf-8")
            if not filename:
                filename = note_data.get("filename") or f"{vault_path}.md"

    # 4. Check content if no attachment, note attachment, or vault note was found
    if not file_bytes and content:
        if not filename:
            filename = "document.txt"
        file_bytes = content.encode("utf-8")

    # 5. Fallback: if filename or note_id was specified but no printable content was found, check Obsidian vault
    if not file_bytes and (filename or note_id):
        from tools.obsidian import _is_vault_authorized
        from utils import obsidian as vault_utils

        search_target = note_id or filename
        if search_target and search_target.lower() not in (
            "document.pdf",
            "document.txt",
            "file.pdf",
            "print.pdf",
            "attachment",
            "document",
        ):
            try:
                if vault_utils.get_vault_path() and await _is_vault_authorized(bot, user_id):
                    note_data = None
                    try:
                        note_data = await run_in_executor(vault_utils.read_note, rel_path=search_target)
                    except Exception:
                        results = await run_in_executor(vault_utils.search_vault, query=search_target, max_results=1)
                        if not results:
                            import re
                            tokens = [w for w in re.findall(r"\w+", search_target) if w.lower() not in ("from", "today", "yesterday", "the", "my", "a", "an")]
                            if tokens:
                                results = await run_in_executor(vault_utils.search_vault, query=" ".join(tokens), max_results=1)
                        if results:
                            note_data = await run_in_executor(vault_utils.read_note, rel_path=results[0]["path"])

                    if note_data and note_data.get("content"):
                        file_bytes = note_data["content"].encode("utf-8")
                        filename = note_data.get("filename") or f"{search_target}.md"
            except Exception as e:
                sentry_sdk.capture_exception(e)

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


async def handle_list_print_jobs(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the list_print_jobs AI tool."""
    print_cog = bot.get_cog("Print")
    if not print_cog:
        return "Error: Print cog is unavailable."
    raw_status = args.get("status") or args.get("filter")
    status = str(raw_status).strip().lower() if raw_status is not None else "all"
    limit = args.get("limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    elif not isinstance(limit, int):
        limit = 10
    return await print_cog.get_print_jobs_text(user_id, status=status, limit=limit)


async def handle_cancel_print_job(bot, args: dict, user_id: str, context: dict | None = None) -> str:
    """Handler for the cancel_print_job AI tool."""
    print_cog = bot.get_cog("Print")
    if not print_cog:
        return "Error: Print cog is unavailable."
    query = str(args.get("query") or args.get("job") or args.get("filename") or args.get("id") or "").strip()
    if not query:
        return "Error: Print job filename, ID, or index is required."
    return await print_cog.cancel_print_job(user_id, query)
