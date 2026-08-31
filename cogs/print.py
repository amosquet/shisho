import io
import mimetypes
import os
import smtplib
from email.message import EmailMessage

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk

from utils.db import (
    get_pb_client,
    prepare_file_upload_payload,
    run_in_executor,
)
from utils.discord_helpers import is_user_authorized


class RetryPrintWithEmailView(discord.ui.View):
    """Interactive button view allowing users to retry sending the print job via Email fallback."""

    def __init__(
        self,
        filename: str,
        file_bytes: bytes,
        user_id: int,
        print_cog: "PrintCog",
    ):
        super().__init__(timeout=300)
        self.filename = filename
        self.file_bytes = file_bytes
        self.user_id = user_id
        self.print_cog = print_cog

    @discord.ui.button(
        label="Retry via Email",
        style=discord.ButtonStyle.primary,
        emoji="✉️",
    )
    async def retry_email_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the user who requested this print job can retry it.",
                ephemeral=True,
            )
            return

        button.disabled = True
        button.label = "Sending via Email..."
        await interaction.response.edit_message(view=self)

        try:
            await run_in_executor(
                self.print_cog.send_print_email,
                self.filename,
                self.file_bytes,
                f"Discord Print Fallback: {self.filename}",
            )
            await interaction.edit_original_response(
                content=f"✅ Dispatched **`{self.filename}`** to the printer via email fallback!",
                view=None,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.edit_original_response(
                content=f"❌ Failed to dispatch via email: `{e}`",
                view=None,
            )


class PrintCog(commands.Cog, name="Print"):
    """Cog for dispatching print jobs to the local printer via PocketBase Realtime with Email fallback."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.smtp_host = os.getenv("EMAIL_SMTP_HOST")
        self.smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 465))
        self.smtp_user = os.getenv("EMAIL_USER")
        self.smtp_pass = os.getenv("EMAIL_PASS")
        self.printer_email = os.getenv("PRINTER_EMAIL")

        self.ctx_menu = app_commands.ContextMenu(
            name="Print Attachment",
            callback=self.print_attachment_ctx,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    def send_print_email(
        self, filename: str, file_bytes: bytes, subject: str = "Discord Print Job"
    ):
        """Synchronously send an email with the print attachment via SMTP."""
        if not all([self.smtp_host, self.smtp_user, self.smtp_pass, self.printer_email]):
            raise ValueError(
                "Printer email (PRINTER_EMAIL) or SMTP settings are not fully configured."
            )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.smtp_user
        msg["To"] = self.printer_email
        msg.set_content(
            f"Print job sent from Shisho Discord Bot.\nFilename: {filename}"
        )

        ctype, encoding = mimetypes.guess_type(filename)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        msg.add_attachment(
            file_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

        with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
            server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)

    def _add_to_pocketbase(
        self, file_bytes: bytes, filename: str, requester_discord_id: str
    ) -> tuple[bool, str]:
        """Create a print_jobs record in PocketBase."""
        pb = get_pb_client()
        entry = {
            "filename": filename,
            "status": "queued",
            "requester_discord_id": str(requester_discord_id),
        }
        files = {"file": (filename, file_bytes)}
        payload = prepare_file_upload_payload(entry, files)
        rec = pb.collection("print_jobs").create(payload)
        return True, getattr(rec, "id", "queued")

    async def queue_or_fallback(
        self,
        interaction: discord.Interaction,
        filename: str,
        file_bytes: bytes,
    ):
        """Attempt to queue print job in PocketBase, offering email retry on failure."""
        pb_success = False
        error_reason = ""

        try:
            pb_success, result_id = await run_in_executor(
                self._add_to_pocketbase,
                file_bytes,
                filename,
                str(interaction.user.id),
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            pb_success = False
            error_reason = str(e)

        if pb_success:
            await interaction.followup.send(
                f"🖨️ Queued **`{filename}`** in PocketBase Realtime print queue!",
                ephemeral=True,
            )
        else:
            reason_msg = f" ({error_reason})" if error_reason else ""
            view = RetryPrintWithEmailView(
                filename=filename,
                file_bytes=file_bytes,
                user_id=interaction.user.id,
                print_cog=self,
            )
            await interaction.followup.send(
                f"⚠️ Shisho was unable to add **`{filename}`** to the print queue{reason_msg}.\n"
                f"Would you like to retry by sending it directly via email?",
                view=view,
                ephemeral=True,
            )

    @app_commands.command(
        name="print",
        description="Send a document, saved note, or text to your physical printer.",
    )
    @app_commands.describe(
        file="File attachment to print (PDF, TXT, PNG, JPG)",
        note_id="Optional: Print an existing saved note by ID or keyword",
        text="Optional: Direct text snippet to print",
    )
    async def print_command(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment | None = None,
        note_id: str | None = None,
        text: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not file and not note_id and not text:
            await interaction.followup.send(
                "Please provide a file attachment, note ID, or text to print.",
                ephemeral=True,
            )
            return

        filename = ""
        file_bytes = b""

        if file:
            allowed_extensions = (".pdf", ".txt", ".png", ".jpg", ".jpeg")
            if not file.filename.lower().endswith(allowed_extensions):
                await interaction.followup.send(
                    "Only PDF, TXT, and image files (PNG/JPG) are supported for printing.",
                    ephemeral=True,
                )
                return

            try:
                file_bytes = await file.read()
                filename = file.filename
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await interaction.followup.send(
                    f"Failed to read Discord attachment: `{e}`", ephemeral=True
                )
                return

        elif note_id:
            notes_cog = self.bot.get_cog("Notes")
            if not notes_cog:
                await interaction.followup.send(
                    "Notes service is currently unavailable.", ephemeral=True
                )
                return

            notes = await notes_cog.get_notes(str(interaction.user.id), query=note_id)
            if not notes or not isinstance(notes, list) or len(notes) == 0:
                await interaction.followup.send(
                    f"Note matching `{note_id}` was not found.", ephemeral=True
                )
                return

            note = notes[0]
            # Check if note has an attachment to print
            if note.get("attachment_urls") and len(note["attachment_urls"]) > 0:
                att_url = note["attachment_urls"][0]
                att_name = note["attachment_filenames"][0] if note.get("attachment_filenames") else "note_attachment.pdf"
                headers = {}
                if note.get("file_token"):
                    headers["Authorization"] = note["file_token"]

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(att_url, headers=headers) as resp:
                            if resp.status == 200:
                                file_bytes = await resp.read()
                                filename = att_name
                except Exception as e:
                    sentry_sdk.capture_exception(e)

            if not file_bytes:
                title = note.get("title") or "Note"
                body = note.get("text", "")
                content = f"{title}\n{'=' * len(title)}\n\n{body}\n"
                clean_title = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).strip()
                filename = f"{clean_title or 'Note'}.txt"
                file_bytes = content.encode("utf-8")

        elif text:
            filename = "print_text.txt"
            file_bytes = text.encode("utf-8")

        await self.queue_or_fallback(interaction, filename, file_bytes)

    async def print_attachment_ctx(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """Context menu to print attachments or text directly from a message."""
        await interaction.response.defer(ephemeral=True)

        if not message.attachments and not message.content:
            await interaction.followup.send(
                "This message has no attachments or text to print.", ephemeral=True
            )
            return

        filename = ""
        file_bytes = b""

        if message.attachments:
            att = message.attachments[0]
            allowed_extensions = (".pdf", ".txt", ".png", ".jpg", ".jpeg")
            if not att.filename.lower().endswith(allowed_extensions):
                await interaction.followup.send(
                    f"Attachment `{att.filename}` is not a supported print format (PDF, TXT, PNG, JPG).",
                    ephemeral=True,
                )
                return

            try:
                file_bytes = await att.read()
                filename = att.filename
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await interaction.followup.send(
                    f"Failed to read attachment: `{e}`", ephemeral=True
                )
                return
        else:
            filename = f"message_{message.id}.txt"
            file_bytes = f"Message from {message.author}:\n\n{message.content}".encode(
                "utf-8"
            )

        await self.queue_or_fallback(interaction, filename, file_bytes)


async def setup(bot: commands.Bot):
    await bot.add_cog(PrintCog(bot))
