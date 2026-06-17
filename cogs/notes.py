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
import re
from google import genai
from google.genai import types

class Notes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    async def add_note(self, user_id: str, text: str = "", title: str = "", attachments: list = None) -> str:
        try:
            if not text and attachments and len(attachments) > 0:
                attachment_filename, attachment_bytes = attachments[0]
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
                                model='gemini-3.5-flash',
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
                
                # Look up PocketBase user ID from Discord ID
                user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if not user_records:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."
                pb_user_id = user_records[0].id
                
                class MultiFileUpload(FileUpload):
                    def __init__(self, file_data_list):
                        self.file_data_list = file_data_list
                    def get(self, key: str):
                        return tuple((key, data) for data in self.file_data_list)

                class BodyDict(dict):
                    def __init__(self, regular_data, file_uploads):
                        super().__init__(regular_data)
                        self.regular_data = regular_data
                        self.file_uploads = file_uploads
                    def items(self):
                        for k, v in self.regular_data.items():
                            yield k, v
                        for k, v in self.file_uploads.items():
                            yield k, v

                entry = {
                    "user_id": str(pb_user_id),
                    "text": text,
                    "title": title
                }
                
                if attachments:
                    file_uploads = {"attachment": MultiFileUpload(attachments)}
                    final_entry = BodyDict(entry, file_uploads)
                else:
                    final_entry = entry
                    
                pb.collection("notes").create(final_entry)
                return "Note saved successfully!"

            res = await self.bot.loop.run_in_executor(None, _add_to_pb)
            return res
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to save note: {e}"

    async def get_notes(self, user_id: str, limit: int = 10, query: str = None):
        try:
            def _get_from_pb():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                
                # Look up PocketBase user ID from Discord ID
                user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if not user_records:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."
                pb_user_id = user_records[0].id
                
                filter_str = f"user_id = '{pb_user_id}'"
                if query:
                    safe_query = query.replace("'", "\\'")
                    filter_str += f" && (title ~ '{safe_query}' || text ~ '{safe_query}')"
                
                query_params = {"sort": "-id"}
                if filter_str:
                    query_params["filter"] = filter_str
                records = pb.collection("notes").get_full_list()
                
                results = []
                for record in records:
                    record_user_id = getattr(record, "user_id", "") or (record.get("user_id", "") if hasattr(record, "get") else "")
                    if record_user_id == pb_user_id:
                        if query:
                            title = getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else "")
                            text = getattr(record, "text", "") or (record.get("text", "") if hasattr(record, "get") else "")
                            if query.lower() not in title.lower() and query.lower() not in text.lower():
                                continue

                        created_val = getattr(record, "created", "")
                        if not created_val and hasattr(record, "get"):
                            created_val = record.get("created", "")
                            
                        updated_val = getattr(record, "updated", "")
                        if not updated_val and hasattr(record, "get"):
                            updated_val = record.get("updated", "")
                            
                        note = {
                            "id": record.id,
                            "title": getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else ""),
                            "text": getattr(record, "text", "") or (record.get("text", "") if hasattr(record, "get") else ""),
                            "created": created_val,
                            "updated": updated_val,
                            "attachment_filenames": [],
                            "attachment_urls": [],
                            "file_token": ""
                        }
                        
                        attachment = getattr(record, "attachment", "") or (record.get("attachment", "") if hasattr(record, "get") else "")
                        if attachment:
                            if isinstance(attachment, list):
                                note["attachment_urls"] = [f"{self.pb_url}/api/files/{record.collection_id}/{record.id}/{att}" for att in attachment]
                                note["attachment_filenames"] = attachment
                            else:
                                note["attachment_urls"] = [f"{self.pb_url}/api/files/{record.collection_id}/{record.id}/{attachment}"]
                                note["attachment_filenames"] = [attachment]
                            note["file_token"] = pb.auth_store.token
                            
                        results.append(note)
                
                def sort_key(n):
                    updated = n.get("updated", "")
                    created = n.get("created", "")
                    has_updates = 1 if updated and updated != created else 0
                    return (has_updates, updated)
                
                results.sort(key=sort_key, reverse=True)
                return results[:limit]

            notes = await self.bot.loop.run_in_executor(None, _get_from_pb)
            return notes
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return str(e)

    @app_commands.command(name="note", description="Add a personal note.")
    @app_commands.describe(
        text="The content of your note",
        title="Optional title for the note",
        attachment="Optional file attachment",
        attachment2="Optional second file attachment",
        attachment3="Optional third file attachment",
        attachment4="Optional fourth file attachment",
        attachment5="Optional fifth file attachment"
    )
    async def slash_note(self, interaction: discord.Interaction, text: str = "", title: str = "", attachment: discord.Attachment = None, attachment2: discord.Attachment = None, attachment3: discord.Attachment = None, attachment4: discord.Attachment = None, attachment5: discord.Attachment = None):
        await interaction.response.defer(ephemeral=True)
        
        atts = []
        for att in [attachment, attachment2, attachment3, attachment4, attachment5]:
            if att:
                atts.append((att.filename, await att.read()))

        if not text and not atts:
            await interaction.followup.send("You must provide either text or an attachment.")
            return

        response = await self.add_note(str(interaction.user.id), text, title, atts)
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
                text_content = re.sub(r'(?<!<)(https?://[^\s>]+)(?!>)', r'<\1>', note["text"])
                msg += f"{text_content}\n"

            file_attachments = []
            if note.get("attachment_urls"):
                headers = {}
                if note.get("file_token"):
                    headers["Authorization"] = note["file_token"]
                
                try:
                    async with httpx.AsyncClient() as client:
                        for idx, att_url in enumerate(note["attachment_urls"]):
                            resp = await client.get(att_url, headers=headers)
                            if resp.status_code == 200:
                                att_name = note["attachment_filenames"][idx]
                                file_attachments.append(discord.File(io.BytesIO(resp.content), filename=att_name))
                except Exception as e:
                    print(f"Failed to download attachments: {e}")
                    sentry_sdk.capture_exception(e)

            if file_attachments:
                await interaction.followup.send(content=msg, files=file_attachments)
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
                
                msg += f"{idx}. **{title}**\n"
            
            await interaction.followup.send(msg)

async def setup(bot):
    await bot.add_cog(Notes(bot))
