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

import mimetypes
import json
from google import genai
from google.genai import types

class Notes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    async def add_note(self, user_id: str, text: str = "", title: str = "", attachment_bytes: bytes = None, attachment_filename: str = None) -> str:
        try:
            if not text and attachment_bytes and attachment_filename:
                mime_type, _ = mimetypes.guess_type(attachment_filename)
                
                # Check for Discord's common voice message format as well, which might not be guessed correctly
                if attachment_filename.endswith('.ogg'):
                    mime_type = 'audio/ogg'
                    
                if mime_type and mime_type.startswith('audio/'):
                    api_key = os.getenv("GEMINI_API_KEY")
                    if api_key:
                        try:
                            client = genai.Client(api_key=api_key)
                            prompt = "Transcribe the audio accurately. Also generate a short, concise title for this note. Return ONLY a valid JSON object with 'title' and 'text' keys."
                            
                            response = await client.aio.models.generate_content(
                                model='gemini-3.1-flash',
                                contents=[
                                    types.Part.from_bytes(data=attachment_bytes, mime_type=mime_type),
                                    prompt
                                ]
                            )
                            
                            res_text = response.text.strip()
                            if res_text.startswith("```json"):
                                res_text = res_text[7:-3].strip()
                            elif res_text.startswith("```"):
                                res_text = res_text[3:-3].strip()
                                
                            data = json.loads(res_text)
                            
                            text = data.get("text", "")
                            if not title:
                                title = data.get("title", "")
                                
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            return f"Failed to transcribe audio: {e}"
                    else:
                        return "Gemini API key not configured for transcription."

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
                
                query_params = {"sort": "-id"}
                if filter_str:
                    query_params["filter"] = filter_str
                
                # Fetch recent notes
                records = pb.collection("notes").get_list(1, limit, query_params=query_params)
                
                results = []
                for record in records.items:
                    # Depending on PB SDK version, created might be accessed via dict or attribute
                    created_val = getattr(record, "created", "")
                    if not created_val and hasattr(record, "get"):
                        created_val = record.get("created", "")
                        
                    note = {
                        "id": record.id,
                        "title": getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else ""),
                        "text": getattr(record, "text", "") or (record.get("text", "") if hasattr(record, "get") else ""),
                        "created": created_val,
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
            sentry_sdk.capture_exception(e)
            return str(e)

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
        
        if isinstance(notes, str):
            await interaction.followup.send(f"An error occurred fetching notes: {notes}")
            return
        
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
            date_str = f"*Saved on {note['created']}*" if note['created'] else ""
            msg += f"{date_str}"

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
                
                date_str = f" (*{note['created']}*)" if note['created'] else ""
                msg += f"{idx}. **{title}**{date_str}\n"
            
            await interaction.followup.send(msg)

async def setup(bot):
    await bot.add_cog(Notes(bot))
