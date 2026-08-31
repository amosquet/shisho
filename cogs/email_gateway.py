import asyncio
from datetime import datetime
import email
from email.message import EmailMessage
import os
import smtplib
from typing import Callable, Coroutine

from discord.ext import commands
from imapclient import IMAPClient
import sentry_sdk


def is_isbn(s: str) -> bool:
    clean = s.replace("-", "").replace(" ", "").strip()
    return clean.isdigit() and len(clean) in (10, 13)


class EmailGateway(commands.Cog):
    """Email IMAP/SMTP Gateway enabling interaction with Shisho via Email/SMS."""

    def __init__(self, bot):
        self.bot = bot
        self.email_host = os.getenv("EMAIL_HOST")
        self.email_smtp_host = os.getenv("EMAIL_SMTP_HOST")
        self.email_port = int(os.getenv("EMAIL_PORT", 993))
        self.email_smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 465))
        self.email_user = os.getenv("EMAIL_USER")
        self.email_pass = os.getenv("EMAIL_PASS")
        allowed = os.getenv("ALLOWED_EMAILS", "")
        self.allowed_emails = [e.strip().lower() for e in allowed.split(",") if e.strip()]

        self._is_unloaded = False
        self._idle_task = None

        # Command Dispatch Table
        self.command_handlers: dict[str, Callable[..., Coroutine]] = {
            "!help": self._handle_help,
            "!ping": self._handle_ping,
            "!bookinfo": self._handle_bookinfo,
            "!suggestions": self._handle_suggestions,
            "!suggest": self._handle_suggest,
            "!deletesuggestion": self._handle_deletesuggestion,
            "!addbook": self._handle_addbook,
            "!deletebook": self._handle_deletebook,
            "!removebook": self._handle_deletebook,
            "!reminders": self._handle_reminders,
            "!listreminders": self._handle_reminders,
            "!remind": self._handle_remind,
            "!deletereminder": self._handle_deletereminder,
            "!cancelreminder": self._handle_deletereminder,
            "!notes": self._handle_notes,
            "!note": self._handle_note,
            "!deletenote": self._handle_deletenote,
            "!delnote": self._handle_deletenote,
        }

        if self.email_host and self.email_user and self.email_pass:
            self._idle_task = self.bot.loop.create_task(self.start_idle())

    def cog_unload(self):
        self._is_unloaded = True
        if self._idle_task:
            self._idle_task.cancel()

    def _get_owner_id(self) -> str:
        reminders_cog = self.bot.get_cog("Reminders")
        if reminders_cog and hasattr(reminders_cog, "owner_id"):
            return str(reminders_cog.owner_id)
        return str(os.getenv("OWNER_ID", "0"))

    async def start_idle(self):
        await self.bot.wait_until_ready()
        while not self._is_unloaded:
            try:
                await asyncio.to_thread(self._idle_loop)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in IMAP IDLE loop: {e}")
                sentry_sdk.capture_exception(e)
                await asyncio.sleep(10)

    def _idle_loop(self):
        with IMAPClient(self.email_host, port=self.email_port, ssl=True) as server:
            server.login(self.email_user, self.email_pass)
            server.select_folder("INBOX")

            # Initial check
            self._fetch_and_process_unread(server)

            while not self._is_unloaded:
                try:
                    server.idle()
                    # Block and wait for up to 5 minutes
                    responses = server.idle_check(timeout=300)
                    server.idle_done()

                    if responses:
                        self._fetch_and_process_unread(server)
                except Exception as e:
                    # Reraise to outer start_idle loop for reconnect
                    try:
                        server.idle_done()
                    except Exception:
                        pass
                    raise e

    def _fetch_and_process_unread(self, server):
        messages = server.search(["UNSEEN"])
        if not messages:
            return

        response = server.fetch(messages, ["RFC822"])
        for msgid, data in response.items():
            if b"RFC822" in data:
                msg = email.message_from_bytes(data[b"RFC822"])
                sender = email.utils.parseaddr(msg.get("From"))[1].lower()

                if sender not in self.allowed_emails:
                    print(f"Unauthorized email from {sender}")
                    continue

                subject = msg.get("Subject", "")
                body = ""
                attachments = []

                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        if content_type == "text/plain" and "attachment" not in content_disposition:
                            body = part.get_payload(decode=True).decode()
                        elif part.get_content_maintype() != "multipart" and part.get("Content-Disposition") is not None:
                            filename = part.get_filename()
                            if filename:
                                att_data = part.get_payload(decode=True)
                                attachments.append((filename, att_data))
                else:
                    part_payload = msg.get_payload(decode=True)
                    if part_payload:
                        body = part_payload.decode()

                asyncio.run_coroutine_threadsafe(
                    self.process_command(sender, subject, body, attachments),
                    self.bot.loop,
                )

    async def _handle_help(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        response = (
            "Available Email Commands:\n"
            "!help - Show this message\n"
            "!ping - Check bot latency\n"
            "!suggestions - Get a list of suggested books\n"
            "!suggest [Title] by [Author] OR !suggest [ISBN] - Add a book suggestion\n"
            "!deletesuggestion [Title or ISBN] - Delete a book suggestion\n"
            "!addbook [Title] by [Author] | [ISBN] | [Status] OR !addbook [ISBN] | [Status] - Add a book to reading list (default status: planned)\n"
            "!deletebook [Title or ISBN] - Remove a book from reading list\n"
            "!reminders - List your active reminders\n"
            "!remind [Time] | [Text] | [Timezone (optional)] - Set a reminder (e.g. !remind in 5 mins | check oven | jp)\n"
            "!deletereminder [Text or 'all'] - Delete a reminder\n"
            "!bookinfo [Title or ISBN] - Look up book details\n"
            "!notes - List your recent notes\n"
            "!note [Text] - Save a note (Subject becomes Title. Email attachments are supported.)\n"
            "!deletenote [Title or ID] - Delete a note"
        )
        return response, []

    async def _handle_ping(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        return f"Pong! Bot latency is {round(self.bot.latency * 1000)}ms", []

    async def _handle_bookinfo(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        bookinfo_cog = self.bot.get_cog("BookInfo")
        if not bookinfo_cog:
            return "There was an error with the command.", []
        if args:
            response = await bookinfo_cog.get_book_info_text(args)
        else:
            response = "Syntax error. Use: !bookinfo [Title or ISBN]"
        return response, []

    async def _handle_suggestions(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        suggested_cog = self.bot.get_cog("SuggestedBooks")
        if not suggested_cog:
            return "There was an error with the command.", []
        response = await suggested_cog.get_suggestions_text()
        return response, []

    async def _handle_suggest(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        suggested_cog = self.bot.get_cog("SuggestedBooks")
        if not suggested_cog:
            return "There was an error with the command.", []

        title = args
        author = ""
        isbn = ""
        if is_isbn(args):
            isbn = args.replace("-", "").strip()
            title = ""
        elif " by " in args:
            title, author = args.split(" by ", 1)

        await suggested_cog.add_suggestion(title.strip(), author.strip(), isbn, sender, "Email")
        response = f"Added suggestion: {title} by {author}" if not isbn else f"Added suggestion with ISBN: {isbn}"
        return response, []

    async def _handle_deletesuggestion(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        suggested_cog = self.bot.get_cog("SuggestedBooks")
        if not suggested_cog:
            return "There was an error with the command.", []
        if not args:
            return "Syntax error. Use: !deletesuggestion [Title, ISBN, or ID]", []
        response = await suggested_cog.delete_suggestion(args.strip(), user_name=sender, is_owner=True)
        return response, []

    async def _handle_addbook(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        reading_cog = self.bot.get_cog("ReadingList")
        if not reading_cog:
            return "There was an error with the command.", []

        parts = [p.strip() for p in args.split("|")]
        title_author = parts[0] if len(parts) > 0 else "Unknown"
        title = title_author
        author = ""
        isbn = ""
        status = "planned"

        if is_isbn(title_author):
            isbn = title_author.replace("-", "").strip()
            title = ""
            if len(parts) > 1:
                status = parts[1].lower()
        else:
            if " by " in title_author:
                title, author = title_author.split(" by ", 1)
            isbn = parts[1] if len(parts) > 1 else ""
            status = parts[2].lower() if len(parts) > 2 else "planned"

        publish_date = ""
        today = datetime.now().strftime("%Y-%m-%d")
        start_date = today if status in ["read", "reading"] else ""
        end_date = today if status == "read" else ""

        await reading_cog.add_book_to_pocketbase(
            title.strip(),
            author.strip(),
            status,
            publish_date,
            isbn,
            start_date,
            end_date,
        )
        return f"Successfully added {title or isbn} to the reading list.", []

    async def _handle_deletebook(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        reading_cog = self.bot.get_cog("ReadingList")
        if not reading_cog:
            return "There was an error with the command.", []
        if not args:
            return "Syntax error. Use: !deletebook [Title or ISBN]", []
        owner_id = self._get_owner_id()
        response = await reading_cog.delete_book_from_pocketbase(owner_id, args.strip())
        return response, []

    async def _handle_reminders(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        reminders_cog = self.bot.get_cog("Reminders")
        if not reminders_cog:
            return "There was an error with the command.", []
        owner_id = self._get_owner_id()
        response = await reminders_cog.get_reminders_text(owner_id, for_discord=False)
        return response, []

    async def _handle_remind(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        reminders_cog = self.bot.get_cog("Reminders")
        if not reminders_cog:
            return "There was an error with the command.", []

        owner_id = self._get_owner_id()
        if "|" in args:
            parts = args.split("|")
            when = parts[0].strip()
            text = parts[1].strip()
            user_tz = parts[2].strip() if len(parts) > 2 else None
            response = await reminders_cog.add_reminder(owner_id, when, text, for_discord=False, user_tz=user_tz)
        else:
            response = "Syntax error. Use: !remind [Time] | [Text] | [Timezone (optional)]"
        return response, []

    async def _handle_deletereminder(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        reminders_cog = self.bot.get_cog("Reminders")
        if not reminders_cog:
            return "There was an error with the command.", []
        if not args:
            return "Syntax error. Use: !deletereminder [Text, Index, or 'all']", []
        owner_id = self._get_owner_id()
        response = await reminders_cog.delete_reminder(owner_id, args.strip())
        return response, []

    async def _handle_notes(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        notes_cog = self.bot.get_cog("Notes")
        if not notes_cog:
            return "There was an error with the command.", []

        owner_id = self._get_owner_id()
        query = args.strip() if args else None
        notes = await notes_cog.get_notes(owner_id, query=query)
        out_attachments = []

        if not notes:
            return f"No notes found{' for that name' if query else ''}.", []

        if query:
            note = notes[0]
            response = "**Note Details:**\n\n"
            title = note["title"] or " ".join(note["text"].split()[:5]) + ("..." if len(note["text"].split()) > 5 else "")
            if not title:
                title = "Untitled Note"
            response += f"Title: {title}\n"
            if note.get("text"):
                response += f"{note['text']}\n"
            if note.get("attachment_urls"):
                headers = {}
                if note.get("file_token"):
                    headers["Authorization"] = note["file_token"]
                try:
                    import httpx
                    async with httpx.AsyncClient() as client:
                        for idx, att_url in enumerate(note["attachment_urls"]):
                            resp = await client.get(att_url, headers=headers)
                            if resp.status_code == 200:
                                out_attachments.append((note["attachment_filenames"][idx], resp.content))
                except Exception as e:
                    print(f"Failed to download attachment for email: {e}")
            if note.get("created"):
                response += f"(Saved on {note['created']})\n"
            if note.get("updated") and note.get("updated") != note.get("created"):
                response += f"(Updated on {note['updated']})\n"
        else:
            response = "**Your Recent Notes:**\n\n"
            for idx, note in enumerate(reversed(notes), 1):
                title = note.get("title")
                if not title:
                    words = note.get("text", "").split()
                    title = " ".join(words[:5]) + ("..." if len(words) > 5 else "")
                    if not title:
                        title = "Untitled Note"
                date_str = f" (Saved on {note['created']})" if note.get("created") else ""
                if note.get("updated") and note.get("updated") != note.get("created"):
                    date_str += f" (Updated on {note['updated']})"
                response += f"{idx}. {title}{date_str}\n"

        return response, out_attachments

    async def _handle_note(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        notes_cog = self.bot.get_cog("Notes")
        if not notes_cog:
            return "There was an error with the command.", []

        owner_id = self._get_owner_id()
        text = args
        title = subject if subject else ""

        if not text and not attachments:
            response = "You must provide either text or an attachment for the note."
        else:
            response = await notes_cog.add_note(owner_id, text, title, attachments)
        return response, []

    async def _handle_deletenote(self, args: str, subject: str, sender: str, attachments: list) -> tuple[str, list]:
        notes_cog = self.bot.get_cog("Notes")
        if not notes_cog:
            return "There was an error with the command.", []
        if not args:
            return "Syntax error. Use: !deletenote [Title, Keyword, or ID]", []
        owner_id = self._get_owner_id()
        response = await notes_cog.delete_note(owner_id, args.strip())
        return response, []

    async def process_command(self, sender: str, subject: str, body: str, attachments: list = None):
        full_text = f"{subject}\n{body}"
        command_line = ""
        for line in full_text.splitlines():
            line = line.strip()
            if line.startswith("!"):
                command_line = line
                break

        if not command_line:
            return

        parts = command_line.split(" ", 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handler = self.command_handlers.get(cmd)
        response = ""
        out_attachments = []

        try:
            if handler:
                response, out_attachments = await handler(args, subject, sender, attachments or [])
            else:
                response = "There was an error with the command."
        except Exception as e:
            sentry_sdk.capture_exception(e)
            response = "There was an error with the command."

        # Strip Discord markdown for plain text email/SMS
        clean_response = response.replace("**", "").replace("__", "")
        await asyncio.to_thread(self._send_email, sender, f"Re: {subject or 'Command'}", clean_response, out_attachments)

    def _send_email(self, to_addr: str, subject: str, body: str, out_attachments: list = None):
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = self.email_user
        msg["To"] = to_addr

        if out_attachments:
            import mimetypes
            for filename, file_data in out_attachments:
                ctype, encoding = mimetypes.guess_type(filename)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=filename)

        try:
            server = smtplib.SMTP_SSL(self.email_smtp_host, self.email_smtp_port)
            server.login(self.email_user, self.email_pass)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            print(f"Failed to send email reply: {e}")
            sentry_sdk.capture_exception(e)


async def setup(bot):
    await bot.add_cog(EmailGateway(bot))
