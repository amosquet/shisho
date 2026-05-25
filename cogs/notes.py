import os
import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk
from pocketbase import PocketBase
from pocketbase.client import FileUpload
import httpx

class Notes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    async def add_note(self, user_id: str, text: str = "", title: str = "", attachment_bytes: bytes = None, attachment_filename: str = None) -> str:
        try:
            def _add_to_pb():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                
                entry = {
                    "user_id": str(user_id),
                    "text": text,
                    "title": title
                }
                if attachment_bytes and attachment_filename:
                    entry["attachment"] = FileUpload((attachment_filename, attachment_bytes))
                    
                pb.collection("notes").create(entry)

            await self.bot.loop.run_in_executor(None, _add_to_pb)
            return "Note saved successfully!"
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to save note: {e}"

    async def get_notes(self, user_id: str, limit: int = 10, query: str = None):
        try:
            def _get_from_pb():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                
                filter_str = f"user_id = '{user_id}'"
                if query:
                    safe_query = query.replace("'", "\\'")
                    filter_str += f" && (title ~ '{safe_query}' || text ~ '{safe_query}')"
                
                # Fetch recent notes
                records = pb.collection("notes").get_list(1, limit, query_params={
                    "filter": filter_str,
                    "sort": "-created" # newest first
                })
                
                results = []
                for record in records.items:
                    note = {
                        "id": record.id,
                        "title": getattr(record, "title", ""),
                        "text": getattr(record, "text", ""),
                        "created": getattr(record, "created", ""),
                        "attachment_filename": "",
                        "attachment_url": "",
                        "file_token": ""
                    }
                    
                    attachment = getattr(record, "attachment", "")
                    if attachment:
                        note["attachment_filename"] = attachment
                        # Construct URL manually to be safe against SDK differences
                        note["attachment_url"] = f"{self.pb_url}/api/files/{record.collection_id}/{record.id}/{attachment}"
                        note["file_token"] = pb.auth_store.token
                        
                    results.append(note)
                return results

            notes = await self.bot.loop.run_in_executor(None, _get_from_pb)
            return notes
        except Exception as e:
            print(f"Error fetching notes: {e}")
            sentry_sdk.capture_exception(e)
            return []

    @app_commands.command(name="note", description="Add a personal note.")
    @app_commands.describe(
        text="The content of your note",
        title="Optional title for the note",
        attachment="Optional file attachment"
    )
    async def slash_note(self, interaction: discord.Interaction, text: str = "", title: str = "", attachment: discord.Attachment = None):
        await interaction.response.defer(ephemeral=True)
        if not text and not attachment:
            await interaction.followup.send("You must provide either text or an attachment.")
            return

        att_bytes = None
        att_name = None
        if attachment:
            att_bytes = await attachment.read()
            att_name = attachment.filename

        response = await self.add_note(str(interaction.user.id), text, title, att_bytes, att_name)
        await interaction.followup.send(response)

    @app_commands.command(name="notes", description="List your recent notes, or view a specific note by name.")
    @app_commands.describe(name="Optional name of a specific note to view fully")
    async def slash_notes(self, interaction: discord.Interaction, name: str = None):
        await interaction.response.defer(ephemeral=True)
        notes = await self.get_notes(str(interaction.user.id), limit=10, query=name)
        
        if not notes:
            await interaction.followup.send(f"No notes found{' for that name' if name else ''}.")
            return

        if name:
            note = notes[0]
            title = note["title"] or " ".join(note["text"].split()[:5]) + ("..." if len(note["text"].split()) > 5 else "")
            if not title:
                title = "Untitled Note"
            
            msg = f"**{title}**\n"
            if note["text"]:
                msg += f"{note['text']}\n"
            msg += f"*Saved on {note['created']}*"

            file_attachment = discord.utils.MISSING
            if note["attachment_url"]:
                headers = {}
                if note["file_token"]:
                    headers["Authorization"] = note["file_token"]
                
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(note["attachment_url"], headers=headers)
                        if resp.status_code == 200:
                            file_attachment = discord.File(io.BytesIO(resp.content), filename=note["attachment_filename"])
                except Exception as e:
                    print(f"Failed to download attachment: {e}")
                    sentry_sdk.capture_exception(e)

            if file_attachment is not discord.utils.MISSING:
                await interaction.followup.send(content=msg, file=file_attachment)
            else:
                await interaction.followup.send(content=msg)
        else:
            msg = "**Your Recent Notes:**\n\n"
            for idx, note in enumerate(reversed(notes), 1):
                title = note["title"]
                if not title:
                    words = note["text"].split()
                    title = " ".join(words[:5]) + ("..." if len(words) > 5 else "")
                    if not title:
                        title = "Untitled Note"
                msg += f"{idx}. **{title}** (*{note['created']}*)\n"
            
            await interaction.followup.send(msg)

async def setup(bot):
    await bot.add_cog(Notes(bot))
