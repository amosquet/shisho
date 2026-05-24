import os
import asyncio
import imaplib
import smtplib
import email
from email.message import EmailMessage
import re
import datetime

import discord
from discord.ext import commands, tasks
import sentry_sdk

def is_isbn(s: str) -> bool:
    clean = s.replace("-", "").replace(" ", "").strip()
    return clean.isdigit() and len(clean) in (10, 13)

class EmailGateway(commands.Cog):
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

        if self.email_host and self.email_user and self.email_pass:
            self.check_emails.start()

    def cog_unload(self):
        self.check_emails.cancel()

    @tasks.loop(seconds=10.0)
    async def check_emails(self):
        try:
            await asyncio.to_thread(self._check_and_process)
        except Exception as e:
            print(f"Error in email gateway: {e}")
            sentry_sdk.capture_exception(e)

    @check_emails.before_loop
    async def before_check_emails(self):
        await self.bot.wait_until_ready()

    def _check_and_process(self):
        mail = imaplib.IMAP4_SSL(self.email_host, self.email_port)
        mail.login(self.email_user, self.email_pass)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            mail.logout()
            return

        for num in messages[0].split():
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue
                
            for response_part in data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    sender = email.utils.parseaddr(msg.get("From"))[1].lower()
                    
                    if sender not in self.allowed_emails:
                        print(f"Unauthorized email from {sender}")
                        continue

                    subject = msg.get("Subject", "")
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                body = part.get_payload(decode=True).decode()
                                break
                    else:
                        body = part_payload = msg.get_payload(decode=True)
                        if part_payload:
                            body = part_payload.decode()

                    # Process command
                    # We run this in the event loop so it can call other async cog methods
                    asyncio.run_coroutine_threadsafe(
                        self.process_command(sender, subject, body), 
                        self.bot.loop
                    )

        mail.logout()

    async def process_command(self, sender: str, subject: str, body: str):
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

        response = ""
        try:
            if cmd == "!ping":
                response = f"Pong! Bot latency is {round(self.bot.latency * 1000)}ms"
            elif cmd == "!suggestions":
                suggested_cog = self.bot.get_cog("SuggestedBooks")
                if suggested_cog:
                    response = await suggested_cog.get_suggestions_text()
                else:
                    response = "Error: SuggestedBooks cog not loaded."
            elif cmd == "!suggest":
                suggested_cog = self.bot.get_cog("SuggestedBooks")
                if suggested_cog:
                    # Syntax: !suggest Title by Author
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
                else:
                    response = "Error: SuggestedBooks cog not loaded."
            elif cmd == "!addbook":
                reading_cog = self.bot.get_cog("ReadingList")
                if reading_cog:
                    # Syntax: !addbook Title by Author | ISBN | Status
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
                    today = datetime.datetime.now().strftime("%Y-%m-%d")
                    start_date = today if status in ["read", "reading"] else ""
                    end_date = today if status == "read" else ""
                    
                    await reading_cog.add_book_to_github(
                        title.strip(), 
                        author.strip(), 
                        status, 
                        publish_date, 
                        isbn, 
                        start_date, 
                        end_date
                    )
                    response = f"Successfully added {title or isbn} to the reading list."
                else:
                    response = "Error: ReadingList cog not loaded."
            else:
                response = f"Unknown command: {cmd}"
        except Exception as e:
            sentry_sdk.capture_exception(e)
            response = f"Error executing {cmd}: {e}"
            
        # Strip Discord markdown for plain text email/SMS
        clean_response = response.replace("**", "").replace("__", "")
        await asyncio.to_thread(self._send_email, sender, f"Re: {subject or 'Command'}", clean_response)
        
    def _send_email(self, to_addr, subject, body):
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = self.email_user
        msg["To"] = to_addr

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
