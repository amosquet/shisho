"""
tools/exporting.py - AI Tool definition and handler for exporting documents, notes, dynamic text, and attachments to PDF.
"""

import io
import os
from typing import Any
import discord
from google.genai import types
from PIL import Image
import sentry_sdk

from utils.pdf import compile_text_to_pdf, is_pdf


EXPORT_PDF_TOOL = types.FunctionDeclaration(
    name="export_pdf",
    description="Generates a styled PDF from any reasonable source (plaintext, markdown, chat summary, saved PocketBase note, Obsidian vault note, or file attachment) and sends it directly back to the user in chat as an attachment.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "content": types.Schema(
                type=types.Type.STRING,
                description="Text or Markdown content, chat summary, or explanation to compile into PDF.",
            ),
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Optional filename for the exported PDF (e.g. 'lecture_notes.pdf', 'summary.pdf').",
            ),
            "note_id": types.Schema(
                type=types.Type.STRING,
                description="Optional saved PocketBase note ID or note title to export as PDF.",
            ),
            "vault_path": types.Schema(
                type=types.Type.STRING,
                description="Optional relative path or note title in the Obsidian vault to export as PDF (e.g. 'Biology/Lecture 4.md').",
            ),
            "paper_size": types.Schema(
                type=types.Type.STRING,
                description="Optional paper size: 'letter' (default) or 'legal'.",
                enum=["letter", "legal"],
            ),
        },
    ),
)


async def handle_export_pdf(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for the export_pdf AI tool."""
    from tools.printing import _resolve_printable_content

    # Force target filename to end in .pdf
    filename = str(args.get("filename") or "").strip()
    if filename and not filename.lower().endswith(".pdf"):
        args["filename"] = os.path.splitext(filename)[0] + ".pdf"
    elif not filename:
        args["filename"] = "document.pdf"

    file_bytes, final_filename, error_msg = await _resolve_printable_content(
        bot, args, user_id, context
    )
    if error_msg:
        return error_msg

    if not final_filename.lower().endswith(".pdf"):
        final_filename = os.path.splitext(final_filename)[0] + ".pdf"

    paper_size = "legal" if str(args.get("paper_size") or "").lower() == "legal" else "letter"

    # Convert non-PDF content to PDF if needed
    if not is_pdf(file_bytes):
        # 1. Try treating as an image first
        image_converted = False
        try:
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            pdf_buf = io.BytesIO()
            img.save(pdf_buf, format="PDF")
            file_bytes = pdf_buf.getvalue()
            image_converted = True
        except Exception:
            image_converted = False

        # 2. If not an image, compile as text/markdown
        if not image_converted:
            try:
                text_content = file_bytes.decode("utf-8")
                doc_title = os.path.splitext(final_filename)[0].replace("_", " ").title()
                file_bytes = compile_text_to_pdf(
                    text_content, title=doc_title, paper_size=paper_size
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                return f"Error compiling text to PDF: {e}"

    if context is not None and isinstance(context.get("out_files"), list):
        context["out_files"].append(
            {
                "filename": final_filename,
                "bytes": file_bytes,
            }
        )
        return f"Successfully generated '{final_filename}' and attached it to the chat message."
    else:
        return f"Successfully generated '{final_filename}', but output context was unavailable to attach the file."
