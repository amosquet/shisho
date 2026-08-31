import os
import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk
import httpx

import mimetypes
import json
import re
from google.genai import types

from utils.db import (
    get_pb_client,
    get_pb_url,
    get_discord_user_id,
    prepare_file_upload_payload,
    run_in_executor,
)
from utils.llm import get_gemini_client, get_gemini_model, generate_content_with_retry

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
                if attachment_filename.lower().endswith(('.jpg', '.jpeg')):
                    mime_type = 'image/jpeg'
                elif attachment_filename.lower().endswith('.png'):
                    mime_type = 'image/png'
                elif attachment_filename.lower().endswith('.webp'):
                    mime_type = 'image/webp'
                elif attachment_filename.lower().endswith('.gif'):
                    mime_type = 'image/gif'
                elif attachment_filename.lower().endswith(('.heic', '.heif')):
                    mime_type = 'image/heic'
                elif attachment_filename.endswith('.ogg'):
                    mime_type = 'audio/ogg'
                    
                if mime_type and (mime_type.startswith('audio/') or mime_type.startswith('image/')):
                    client = get_gemini_client()
                    if client:
                        try:
                            if mime_type.startswith('audio/'):
                                prompt = "Transcribe the audio accurately. Also generate a short, concise title for this note. Return ONLY a valid JSON object with 'title' and 'text' keys."
                            else:
                                prompt = "Analyze and extract all relevant text and key information from this image accurately for a personal note. Also generate a short, concise title for this note. Return ONLY a valid JSON object with 'title' and 'text' keys."
                            model_name = get_gemini_model()
                            response = await generate_content_with_retry(
                                client,
                                model=model_name,
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
                            return "Failed to process attachment with AI. An internal error occurred."
                    else:
                        return "Gemini API key not configured for AI processing."

            def _add_to_pb():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

                entry = {
                    "owner": str(pb_user_id),
                    "text": text,
                    "title": title
                }
                
                files = {"attachment": attachments} if attachments else None
                final_entry = prepare_file_upload_payload(entry, files)
                    
                pb.collection("notes").create(final_entry)
                return "Note saved successfully!"

            res = await run_in_executor(_add_to_pb)
            return res
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return "Failed to save note. An internal error occurred."

    async def get_notes(self, user_id: str, limit: int = 10, query: str = None):
        try:
            def _get_from_pb():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."
                
                filter_str = f"owner = '{pb_user_id}'"
                query_params = {"sort": "-id"}
                if query:
                    safe_query = query.replace("'", "\\'")
                    filter_str += f" && (title ~ '{safe_query}' || text ~ '{safe_query}')"
                
                if filter_str:
                    query_params["filter"] = filter_str
                records = pb.collection("notes").get_full_list(query_params=query_params)
                
                base_url = get_pb_url(self.pb_url)
                results = []
                for record in records:
                    record_owner = getattr(record, "owner", "") or (record.get("owner", "") if hasattr(record, "get") else "")
                    if record_owner == pb_user_id:
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
                                note["attachment_urls"] = [f"{base_url}/api/files/{record.collection_id}/{record.id}/{att}" for att in attachment]
                                note["attachment_filenames"] = attachment
                            else:
                                note["attachment_urls"] = [f"{base_url}/api/files/{record.collection_id}/{record.id}/{attachment}"]
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

            notes = await run_in_executor(_get_from_pb)
            return notes
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return str(e)

    async def delete_note(self, user_id: str, note_id_or_query: str) -> str:
        try:
            def _delete_from_pb():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

                clean_target = note_id_or_query.strip()
                if not clean_target:
                    return "Error: Please specify a note title, ID, or keyword to delete."

                # Try finding by exact ID first
                try:
                    record = pb.collection("notes").get_one(clean_target)
                    record_owner = getattr(record, "owner", "") or (record.get("owner", "") if hasattr(record, "get") else "")
                    if record_owner == pb_user_id:
                        title = getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else "") or "Untitled Note"
                        pb.collection("notes").delete(record.id)
                        return f"Successfully deleted note: **{title}**"
                except Exception:
                    pass

                # Search notes owned by user
                safe_query = clean_target.replace("'", "\\'")
                filter_str = f"owner = '{pb_user_id}' && (title ~ '{safe_query}' || text ~ '{safe_query}')"
                records = pb.collection("notes").get_full_list(query_params={"filter": filter_str})
                if not records:
                    return f"No notes found matching '{clean_target}'."

                # Prefer exact title match if multiple
                matched_record = None
                for r in records:
                    r_title = getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else "")
                    if r_title.lower() == clean_target.lower():
                        matched_record = r
                        break
                if not matched_record:
                    matched_record = records[0]

                title = getattr(matched_record, "title", "") or (matched_record.get("title", "") if hasattr(matched_record, "get") else "") or "Untitled Note"
                pb.collection("notes").delete(matched_record.id)
                return f"Successfully deleted note: **{title}**"

            return await run_in_executor(_delete_from_pb)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to delete note: {e}"

    async def note_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            notes = await self.get_notes(str(interaction.user.id), limit=25, query=current if current else None)
            if isinstance(notes, str) or not notes:
                return []
            choices = []
            for n in notes:
                title = n.get("title") or " ".join(n.get("text", "").split()[:5]) or "Untitled Note"
                name_preview = title[:100]
                choices.append(app_commands.Choice(name=name_preview, value=n.get("id", name_preview)))
            return choices[:25]
        except Exception:
            return []

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
                msg += f"{note['text']}\n"

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
                await interaction.followup.send(content=msg, files=file_attachments, suppress_embeds=True)
            else:
                await interaction.followup.send(content=msg, suppress_embeds=True)
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
            
            await interaction.followup.send(content=msg, suppress_embeds=True)

    @app_commands.command(name="deletenote", description="Delete a personal note.")
    @app_commands.describe(note="The note to delete (select from list or type title/ID)")
    async def slash_deletenote(self, interaction: discord.Interaction, note: str):
        await interaction.response.defer(ephemeral=True)
        response = await self.delete_note(str(interaction.user.id), note)
        await interaction.followup.send(response)

    @slash_deletenote.autocomplete("note")
    async def slash_deletenote_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.note_autocomplete(interaction, current)

async def setup(bot):
    await bot.add_cog(Notes(bot))
