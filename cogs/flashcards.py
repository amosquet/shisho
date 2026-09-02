"""
cogs/flashcards.py - Dedicated Anki flashcard generation cog for Shisho.
Enables creating Anki flashcards (.apkg) from uploaded PDFs, Obsidian vault notes, or prompts.
"""

import io
import os
import re
from typing import List, Optional
import discord
from discord import app_commands
from discord.ext import commands
from google.genai import types
import sentry_sdk

from utils.discord_helpers import is_user_authorized, split_message
from utils.llm import (
    get_gemini_client,
    get_gemini_model,
    format_gemini_error,
    generate_content_with_retry,
)
from utils import anki as anki_utils
from utils import obsidian as vault_utils
from tools.anki import handle_create_anki_deck, handle_vault_export_anki_deck
from tools.registry import AI_CHAT_TOOLS, TOOL_HANDLERS, execute_tool


class Flashcards(commands.Cog):
    """Anki Flashcard generation and Obsidian Spaced Repetition integration."""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.client = get_gemini_client(self.api_key)

    def is_user_authorized(self, user_id: int) -> bool:
        # Defaults to AIChat permission or general access
        return is_user_authorized(user_id, "Flashcards") or is_user_authorized(user_id, "AIChat")

    async def _autocomplete_vault_notes(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete helper for Obsidian vault note paths."""
        try:
            vault_path = vault_utils.get_vault_path()
            if not vault_path or not vault_path.exists():
                return []
            
            # List notes from vault
            files = vault_utils.list_vault_files(recursive=True)
            choices = []
            curr_lower = current.lower().strip()
            for f in files:
                if not f["is_dir"] and f["path"].endswith((".md", ".markdown")):
                    p = f["path"]
                    if not curr_lower or curr_lower in p.lower():
                        choices.append(app_commands.Choice(name=p[:100], value=p))
                        if len(choices) >= 25:
                            break
            return choices
        except Exception:
            return []

    # =========================================================================
    # Slash Command: /flashcards
    # =========================================================================

    flashcards_group = app_commands.Group(
        name="flashcards",
        description="Create and export Anki flashcards (.apkg) from PDFs, Obsidian notes, or topics",
    )

    @flashcards_group.command(
        name="create",
        description="Generate an Anki flashcard deck from an uploaded PDF, Obsidian note, or prompt",
    )
    @app_commands.describe(
        prompt="Topic or instructions for flashcard generation (e.g. 'Key concepts from Chapter 3')",
        file="Optional document or PDF attachment to generate flashcards from",
        vault_note="Optional Obsidian note path in your vault (e.g. 'Biology/Cell.md')",
        deck_name="Optional name for the Anki deck (defaults to topic or filename)",
        card_count="Target number of flashcards to generate (default 10, max 30)",
        card_type="Card format: Basic (Front/Back), Reversed, or Cloze (Fill in the blank)",
        save_to_vault="Whether to also save a markdown flashcard note into your Obsidian vault",
    )
    @app_commands.choices(
        card_type=[
            app_commands.Choice(name="Basic (Front → Back)", value="basic"),
            app_commands.Choice(name="Basic & Reversed (Bidirectional)", value="reversed"),
            app_commands.Choice(name="Cloze Deletion (Fill in the blank)", value="cloze"),
        ]
    )
    @app_commands.autocomplete(vault_note=_autocomplete_vault_notes)
    async def create_slash(
        self,
        interaction: discord.Interaction,
        prompt: str = "",
        file: Optional[discord.Attachment] = None,
        vault_note: Optional[str] = None,
        deck_name: Optional[str] = None,
        card_count: Optional[int] = 10,
        card_type: Optional[str] = "basic",
        save_to_vault: Optional[bool] = False,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You do not have permission to use Flashcard generation.", ephemeral=True
            )
            return

        if not self.client:
            await interaction.response.send_message(
                "Gemini API key is not configured.", ephemeral=True
            )
            return

        if not prompt and not file and not vault_note:
            await interaction.response.send_message(
                "Please provide a prompt/topic, upload a file (PDF/document), or select an Obsidian vault note.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        user_id_str = str(interaction.user.id)
        parts: List[types.Part] = []
        source_desc = ""

        # 1. Process File Attachment (PDF, TXT, MD, etc.)
        if file:
            try:
                file_bytes = await file.read()
                mime = file.content_type or "application/pdf"
                if file.filename.lower().endswith(".pdf"):
                    mime = "application/pdf"
                elif file.filename.lower().endswith((".txt", ".md", ".csv")):
                    mime = "text/plain"
                
                parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
                source_desc = f"uploaded file `{file.filename}`"
                if not deck_name:
                    deck_name = os.path.splitext(file.filename)[0].replace("_", " ").title()
            except Exception as e:
                await interaction.followup.send(f"Failed to read attached file: {e}")
                return

        # 2. Process Obsidian Vault Note
        if vault_note:
            try:
                note_data = vault_utils.read_note(vault_note)
                note_content = note_data.get("content", "")
                parts.append(
                    types.Part.from_text(
                        text=f"[Obsidian Note: {note_data['path']}]\n{note_content}"
                    )
                )
                source_desc = f"Obsidian note `{note_data['path']}`"
                if not deck_name:
                    deck_name = note_data.get("filename", "Vault Note").replace(".md", "").title()
            except Exception as e:
                await interaction.followup.send(f"Error reading Obsidian note '{vault_note}': {e}")
                return

        count = max(1, min(card_count or 10, 30))
        target_deck = deck_name.strip() if deck_name else (prompt[:30] if prompt else "Flashcards")

        instruction = (
            f"Generate exactly {count} high-quality, high-yield Anki flashcards based on the provided material. "
            f"Card type format: '{card_type}'. Deck name: '{target_deck}'. "
            f"Make questions atomic, clear, and focused on key facts, formulas, principles, or definitions. "
            f"Call the tool `create_anki_deck` with `deck_name='{target_deck}'`, `save_to_vault={save_to_vault}`, and the list of card objects. "
        )
        if prompt:
            instruction += f"User Instructions / Topic: {prompt}\n"

        parts.append(types.Part.from_text(text=instruction))

        # Execute with Gemini and tool calling
        try:
            exec_context = {
                "interaction": interaction,
                "guild": interaction.guild,
                "channel": interaction.channel,
                "out_files": [],
            }

            model_name = get_gemini_model()
            config = types.GenerateContentConfig(
                tools=AI_CHAT_TOOLS,
            )

            contents_list = [types.Content(role="user", parts=parts)]
            response = await generate_content_with_retry(
                self.client,
                model=model_name,
                contents=contents_list,
                config=config,
            )

            # Check tool calls
            function_calls = response.function_calls or []
            tool_output_msg = ""
            for fc in function_calls:
                if fc.name in TOOL_HANDLERS:
                    tool_res = await execute_tool(
                        self.bot, fc.name, fc.args or {}, user_id_str, context=exec_context
                    )
                    tool_output_msg += f"\n{tool_res}"

            # Prepare Discord response
            out_files = exec_context.get("out_files", [])
            discord_files = []
            for f in out_files:
                discord_files.append(
                    discord.File(fp=io.BytesIO(f["bytes"]), filename=f["filename"])
                )

            final_text = tool_output_msg.strip() or (response.text or "Flashcards generated!")
            chunks = split_message(final_text)

            for idx, chunk in enumerate(chunks):
                if idx == 0 and discord_files:
                    await interaction.followup.send(chunk, files=discord_files)
                else:
                    await interaction.followup.send(chunk)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(format_gemini_error(e, include_details=True))

    @flashcards_group.command(
        name="from_note",
        description="Convert an existing Obsidian vault note into an Anki .apkg deck file",
    )
    @app_commands.describe(
        vault_note="The Obsidian note to export as an Anki deck",
        deck_name="Optional custom name for the Anki deck",
    )
    @app_commands.autocomplete(vault_note=_autocomplete_vault_notes)
    async def from_note_slash(
        self,
        interaction: discord.Interaction,
        vault_note: str,
        deck_name: Optional[str] = None,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You do not have permission to use Flashcards.", ephemeral=True
            )
            return

        await interaction.response.defer()

        exec_context = {"out_files": []}
        res = await handle_vault_export_anki_deck(
            self.bot,
            {"path": vault_note, "deck_name": deck_name or ""},
            str(interaction.user.id),
            context=exec_context,
        )

        discord_files = [
            discord.File(fp=io.BytesIO(f["bytes"]), filename=f["filename"])
            for f in exec_context.get("out_files", [])
        ]

        await interaction.followup.send(res, files=discord_files)

    # =========================================================================
    # Prefix Commands: !flashcards / !anki
    # =========================================================================

    @commands.command(name="flashcards", aliases=["anki", "deck"], help="Create Anki flashcards (.apkg) from a prompt or attachment.")
    async def flashcards_prefix(self, ctx: commands.Context, *, prompt: str = ""):
        if not self.is_user_authorized(ctx.author.id):
            return

        if not self.client:
            await ctx.send("Gemini API key is not configured.")
            return

        parts: List[types.Part] = []
        user_id_str = str(ctx.author.id)

        # Check for message attachments
        for att in ctx.message.attachments:
            try:
                file_bytes = await att.read()
                mime = att.content_type or "application/pdf"
                if att.filename.lower().endswith(".pdf"):
                    mime = "application/pdf"
                elif att.filename.lower().endswith((".txt", ".md", ".csv")):
                    mime = "text/plain"
                parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
            except Exception as e:
                print(f"Error reading attachment for flashcards: {e}")

        # Check for referenced message
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref_msg = ctx.message.reference.resolved
                if not isinstance(ref_msg, discord.Message):
                    ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                if ref_msg and hasattr(ref_msg, "clean_content") and ref_msg.clean_content:
                    parts.append(types.Part.from_text(text=f"[Referenced Content]:\n{ref_msg.clean_content}"))
                for att in getattr(ref_msg, "attachments", []):
                    try:
                        att_bytes = await att.read()
                        mime = att.content_type or "application/pdf"
                        parts.append(types.Part.from_bytes(data=att_bytes, mime_type=mime))
                    except Exception:
                        pass
            except Exception:
                pass

        if not prompt and not parts:
            await ctx.send("Please provide a topic/prompt or attach a document/PDF to generate flashcards.")
            return

        clean_prompt = prompt.strip() or "Generate study flashcards"
        parts.append(
            types.Part.from_text(
                text=f"Generate 10 high-quality Anki flashcards for: {clean_prompt}. Call `create_anki_deck` with `deck_name='{clean_prompt[:30]}'` and the card list."
            )
        )

        async with ctx.typing():
            try:
                exec_context = {
                    "message": ctx.message,
                    "guild": ctx.guild,
                    "channel": ctx.channel,
                    "out_files": [],
                }

                model_name = get_gemini_model()
                config = types.GenerateContentConfig(
                    tools=AI_CHAT_TOOLS,
                )

                contents_list = [types.Content(role="user", parts=parts)]
                response = await generate_content_with_retry(
                    self.client,
                    model=model_name,
                    contents=contents_list,
                    config=config,
                )

                function_calls = response.function_calls or []
                tool_output_msg = ""
                for fc in function_calls:
                    if fc.name in TOOL_HANDLERS:
                        tool_res = await execute_tool(
                            self.bot, fc.name, fc.args or {}, user_id_str, context=exec_context
                        )
                        tool_output_msg += f"\n{tool_res}"

                out_files = exec_context.get("out_files", [])
                discord_files = [
                    discord.File(fp=io.BytesIO(f["bytes"]), filename=f["filename"])
                    for f in out_files
                ]

                final_text = tool_output_msg.strip() or (response.text or "Flashcards generated!")
                chunks = split_message(final_text)

                for idx, chunk in enumerate(chunks):
                    if idx == 0 and discord_files:
                        await ctx.send(chunk, files=discord_files)
                    else:
                        await ctx.send(chunk)

            except Exception as e:
                sentry_sdk.capture_exception(e)
                await ctx.send(format_gemini_error(e, include_details=False))


async def setup(bot):
    await bot.add_cog(Flashcards(bot))
