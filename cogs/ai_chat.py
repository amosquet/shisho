import asyncio
import mimetypes
import os
import re
from datetime import datetime

import discord
import sentry_sdk
from discord import app_commands
from discord.ext import commands
from google.genai import errors, types

from tools import AI_CHAT_TOOLS, TOOL_HANDLERS, execute_tool
from utils.discord_helpers import split_message, is_user_authorized, format_for_discord
from utils.llm import (
    get_gemini_client,
    get_gemini_model,
    format_gemini_error,
    generate_content_with_retry,
)


class AIChat(commands.Cog):
    """General AI Chat command with threaded conversations and multimodal image/audio support."""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.client = get_gemini_client(self.api_key)
        self.active_threads: set[int] = set()
        self._thread_locks: dict[int, asyncio.Lock] = {}

    def _get_thread_lock(self, thread_id: int) -> asyncio.Lock:
        if thread_id not in self._thread_locks:
            self._thread_locks[thread_id] = asyncio.Lock()
        return self._thread_locks[thread_id]

    def is_user_authorized(self, user_id: int) -> bool:
        return is_user_authorized(user_id, "AIChat")

    def get_system_instruction(self) -> str | None:
        if not hasattr(self, "_cached_prompt") or self._cached_prompt is None:
            prompt_file = "gemini_prompt.txt"
            base_prompt = ""
            if os.path.exists(prompt_file):
                try:
                    with open(prompt_file, "r", encoding="utf-8") as f:
                        base_prompt = f.read().strip()
                except Exception as e:
                    print(f"Failed to read {prompt_file}: {e}")
            self._cached_prompt = base_prompt
        else:
            base_prompt = self._cached_prompt

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        behavior_instruction = (
            "You are Shisho (ししょ) responding inside Discord. You are an open-source Discord bot created by amosquet (Artus). Your official GitHub repository is https://github.com/amosquet/shisho.\n"
            f"Current date and time: {now_str}\n\n"
            "You are a versatile, intelligent AI assistant with broad knowledge across all domains—including geography, science, math, history, technology, coding, trivia, and daily questions. You can answer general knowledge and calculation questions directly, accurately, and succinctly in your witty/sarcastic Shisho persona.\n"
            "When asked about your source code, GitHub repository, developer/creator, or how to contribute, provide the official GitHub repository link: https://github.com/amosquet/shisho.\n\n"
            "You ALSO have direct access to database tools and Discord management tools:\n"
            "- Reading List: Call `get_reading_list` to see books. Call `add_book` to add books. Call `update_book` to update existing books (status, start/end dates, completed, rating, description, etc.). Call `delete_book` to remove books.\n"
            "- Recommendations / Suggested Books: Call `get_recommendations` to see books on the user's recommended list (books suggested by friends or public suggestions). Call `add_recommendation` to recommend a book. Call `delete_recommendation` to remove/dismiss a recommendation.\n"
            "- Reminders: Call `set_reminder` to set reminders. Call `list_reminders` to see reminders (can filter by status='active', 'sent', or 'all'). Call `update_reminder` to edit or reschedule existing reminders. Call `delete_reminder` to cancel/delete reminders.\n"
            "- Notes: Call `add_note` to save notes (supporting plain text, rich text editor content, and file attachments). Call `get_notes` to search or retrieve saved notes (returns active/unarchived notes by default; set `archived=true` only when the user explicitly asks for archived notes; pass clean search keywords). Call `archive_note` to archive a note. Call `unarchive_note` to restore an archived note. Call `delete_note` to delete a note. Call `delete_archived_notes` to delete all archived notes. Call `update_note` to update a note's text, title, rich text editor content, or archived status.\n"
            "- Printing: Call `print_document` to send a document, note, text summary, or attached file to the physical printer via PocketBase Realtime queue or email fallback. Call `list_print_jobs` to view queued, printing, and completed print jobs in the print queue. Call `cancel_print_job` to cancel an active print job. When the user asks to print an attached document (e.g. 'print this document', 'print this PDF', 'print this file', 'print this'), call `print_document` with the filename of the attached file.\n"
            "- Channel Messaging: Call `send_channel_message` to post or send a message directly to a specific Discord text channel or thread (e.g. when asked 'introduce yourself in #checkpoints', 'send a message to #general', 'post an announcement in #channel', 'say hello in #dev'). Call `list_channels` to view available text channels in the server.\n"
            "- AI Model Configuration: Call `get_ai_model` to see the currently active Gemini model. Call `set_ai_model` to change or switch the active Gemini model globally. Note that only the bot owner is authorized to change the model; if an unauthorized user attempts to change it, inform them in Shisho's witty persona that only the bot owner can change the AI model.\n"
            "- Obsidian / Markdown Vault: Call `vault_read_note` to read a markdown note (including parsed YAML frontmatter and body). Call `vault_write_note` to create or overwrite a note with markdown formatting, YAML frontmatter, and wikilinks. Call `vault_patch_note` to surgically replace a specific text snippet or block within an existing note. Call `vault_append_note` to append text, logs, or quotes to a note (optionally under a markdown heading). Call `vault_search` to search across the vault by note body, filenames, tags (`#tag`), or frontmatter. Call `vault_list_files` to explore the vault directory structure and notes. Call `vault_delete_note` to delete or safely move notes to the vault trash (`.trash/`). Call `vault_move_note` to rename or relocate notes. Call `vault_get_backlinks` to find notes linking to a target note via `[[wikilinks]]`.\n"
            "  * Note: Vault operations are strictly restricted to the bot owner.\n"
            "  * When performing actions on the Obsidian vault (e.g. formatting, fixing markdown tables, creating relations, summarizing notes, creating Maps of Content / MOCs, organizing folders): inspect/read or search existing notes before patching or modifying, use standard Obsidian conventions (YAML frontmatter with `---`, wikilinks `[[Note Name]]`, tags `#tag`, callouts `> [!note]`), and prefer `vault_patch_note` or `vault_append_note` for incremental edits.\n\n"
            "CRITICAL SCOPING & TOOL USAGE RULES:\n"
            "1. GENERAL QUESTIONS & TOPICS: When the user asks a general knowledge, factual, geographical, scientific, math, or technical question (e.g. 'distance between NJ and IN?', 'how far is New York from Chicago?', 'what is the speed of light?', 'write a python function', 'help me fix this code'), answer DIRECTLY and succinctly using your general knowledge. DO NOT call database tools (`get_reading_list`, `get_notes`, `list_reminders`, etc.) for general queries. DO NOT give unsolicited book recommendations unless the user explicitly asks for reading suggestions.\n"
            "2. NEVER claim you are only designed or limited to managing reading lists, notes, or reminders. You have full general AI capabilities and reasoning.\n"
            "3. ONLY call database tools when the user explicitly or clearly asks to interact with their personal records:\n"
            "   - When asked about the reading list (e.g. 'what\\'s on my reading list'), call ONLY `get_reading_list` and respond ONLY about the user\\'s books. When asked to update a book (e.g. 'mark as read', 'update reading status'), call `update_book`.\n"
            "   - When asked about recommendations / recommended list / suggestions (e.g. 'what books are on my recommended list', 'show my recommendations', 'what did friends suggest'), call ONLY `get_recommendations` (filter='for_me' or 'all') and respond with the books from the recommendations list. DO NOT say you don't have a recommended list.\n"
            "   - When asked about notes (e.g. 'search my notes', 'show archived notes', 'delete all the archived notes'), call the appropriate notes tool (`get_notes`, `delete_archived_notes`, `archive_note`, `update_note`, etc.) and respond ONLY about notes. DO NOT mention reading list or reminders.\n"
            "   - When asked about reminders (e.g. 'what reminders do I have'), call ONLY `list_reminders` and respond ONLY about reminders. When asked to reschedule or update a reminder, call `update_reminder`.\n"
            "   - When asked to print something (e.g. 'print this document', 'print this note', 'print my reading list', 'print this PDF'), call `print_document`. When asked to check print jobs or queue status, call `list_print_jobs`. When asked to cancel a print job, call `cancel_print_job`.\n"
            "   - When asked to post, send, announce, or introduce yourself in a specific channel (e.g. 'can you introduce yourself in #checkpoints', 'post this in #channel', 'send message to #general'), call `send_channel_message` with the target channel and the formatted message content. Once sent, provide a brief confirmation in the current conversation (e.g. 'Done, posted to #checkpoints!'). DO NOT print the entire announcement or intro into the current channel when the user requested it to be sent to another channel.\n"
            "   - When asked what model you are using or running (e.g. 'what AI model are you on?', 'what model is this?'), call `get_ai_model`.\n"
            "   - When asked to change, switch, or set the AI model (e.g. 'switch to Gemini 2.5 Pro', 'change model to gemini-2.5-flash-lite', 'use flash'), call `set_ai_model`.\n"
            "   - When asked about Obsidian vault, markdown files, or local vault notes (e.g. 'search my vault', 'format note in vault', 'create a note in Projects/Y', 'summarize my vault notes on Z', 'show backlinks to A', 'fix frontmatter'): call the appropriate vault tool (`vault_read_note`, `vault_write_note`, `vault_patch_note`, `vault_search`, `vault_list_files`, `vault_append_note`, `vault_get_backlinks`, etc.) and respond concisely with the outcome or requested content.\n"
            "   - When asked if pictures/images/files are uploaded or saved to the database: confirm that image and file attachments are indeed uploaded and attached directly to the note record in PocketBase.\n"
            "   - NEVER output a multi-category 'status update' or dashboard unless the user explicitly commands you to give an overall summary of everything.\n"
            "4. When the user explicitly asks for NEW book recommendations from you (the AI) (e.g. 'recommend me some books', 'what should I read next?'), first check the user's reading list (by calling `get_reading_list`) to see what books they have already read, are reading, or have planned/dropped. NEVER recommend books that are already on the user's reading list. Provide creative, engaging recommendations of new books tailored to their tastes.\n"
            "5. For general conversation or greetings (like 'hello'), chat naturally in your sarcastic, intelligent Shisho persona without giving unsolicited status updates or asking what to update.\n"
            "6. When @mentioned in a server channel, if the mention is merely ambient chatter talking about you to someone else without asking for help, reply ONLY with '[NO_ACTION]'.\n"
            "7. If given an audio recording or voice memo without explicit instructions, transcribe/summarize it and save it with `add_note`.\n"
            "8. When a user replies to someone's message (or references a previous message) and tags/pings you, or asks for follow-up actions like 'add this to my reading list', 'remind me about this', 'save this note', 'print this', or simply tags you:\n"
            "   - Carefully inspect the referenced message, any attachments/images/audio/documents, and the surrounding conversation history to determine the intent and correct course of action:\n"
            "     * Book Mentions & Suggestions: If the referenced message or conversation mentions a book title/author, book recommendation, or book cover image: if the user says 'add this' or tags you in response, call `add_book` to add it to their reading list. If recommending to a friend, call `add_recommendation`.\n"
            "     * Reminders & Deadlines: If the referenced message discusses a due date, assignment, quiz, meeting, event, or schedule, extract the date/time and description and call `set_reminder` for the user.\n"
            "     * Notes & Information: If the referenced message contains important information (e.g. door codes, passwords, notes, study material), save it with `add_note` if requested or appropriate.\n"
            "     * Printing & Documents: If the user asks to print the referenced message or attached file, call `print_document` with the attached filename or message text.\n"
            "     * Channel Messages: If the user asks to send or post the referenced message/content to another channel, call `send_channel_message`.\n"
            "     * Questions & Discussions: If the referenced message or conversation poses a question or topic, answer directly and concisely utilizing the conversation context.\n"
            "     * Summarization: If asked to summarize or explain the conversation/referenced message, provide a clear, concise summary.\n"
            "     * Tagged without specific prompt: Intelligently determine the most helpful action based on the message content and conversation history (e.g., set a reminder for a deadline, add a book mentioned, answer an unanswered question, or respond with witty banter in character).\n"
            "   - Execute any necessary database tools (`add_book`, `set_reminder`, `add_note`, `get_reading_list`, `add_recommendation`, `print_document`, `send_channel_message`, etc.) directly for the user invoking you without asking for details already in context.\n"
            "9. When the user attaches or shares an image (such as an assignment, syllabus, schedule, screenshot, or book cover) with a request like 'create a reminder for this', 'add this to my reading list', or 'save this note', analyze the image content to extract all relevant details (such as assignment/quiz name, due date/time, start date, book title, author) and execute the appropriate tool (`set_reminder`, `add_book`, or `add_note`) directly. Attached images/files will be automatically uploaded and saved to the PocketBase note record.\n"
            "10. When the user attaches or shares a document, PDF, image, or text file with a request to print (e.g. 'print this document', 'print this', 'print this PDF'), or asks to print a note or reading list, call `print_document` directly for the user without asking for clarification.\n"
            "11. Discord Threads: When the user asks to create or start a thread (e.g. 'make a thread with my note...', 'create a thread about...'), Shisho automatically creates the Discord thread in the channel and posts your response into it. NEVER claim that you cannot create threads or ask the user to copy/paste into a thread. When a user asks to make/start a thread with a note, document, book list, or topic, retrieve the required content (e.g. via `get_notes`) and output the full response directly.\n"
            "12. DISCORD MARKDOWN & NO LATEX: You are outputting directly into Discord chat. Discord does NOT support LaTeX or MathJax equations. NEVER use LaTeX tags or delimiters (do NOT use `$$...$$`, `$...$`, `\\text{}`, `\\mathbf{}`, `\\times`, `\\approx`, `\\frac{}{}`). Format all calculations, math, formulas, and balance breakdowns using standard plain text, clean Unicode (×, ÷, ≈, ±, ≤, ≥, °), and Discord markdown (**bold**, `inline code`, code blocks). Write currency amounts normally (e.g. $1,484.63) without LaTeX syntax.\n"
            "13. DISCORD SLASH COMMANDS & ACCOUNT MANAGEMENT: When a user asks about account management, account details/profile, registration, PIN resets, or actions for which Shisho has a dedicated slash command without a natural language AI tool, inform and guide them to the appropriate slash command directly (e.g. `/account` to view their linked account details/email/registration, `/register` to link/create a Shisho account, `/resetpin` to regenerate their companion app PIN, `/check_authors` to check for author releases, `/force_sync` to sync book metadata, or `/ping` for latency). Do not search notes or claim account info does not exist when the user is simply looking for their Shisho profile."
        )

        if base_prompt:
            return f"{base_prompt}\n\n{behavior_instruction}"
        return behavior_instruction

    def _should_create_thread(self, prompt: str, text: str, chunks: list[str]) -> bool:
        prompt_lower = (prompt or "").lower()
        # If user explicitly requested a thread in their prompt
        if re.search(r"\b(make|create|start|open|put|post|in)\s+(?:a\s+)?thread\b|\b(new\s+thread|to\s+a\s+thread)\b", prompt_lower) or "thread" in prompt_lower:
            return True
        # If response is long and split across multiple Discord chunks, create a thread
        if len(chunks) > 1 or len(text) > 400:
            return True
        return False

    def _get_audio_mime(self, filename: str, content_type: str | None = None) -> str | None:
        if content_type and content_type.startswith("audio/"):
            return content_type

        ext = os.path.splitext(filename.lower())[1]
        ext_map = {
            ".ogg": "audio/ogg",
            ".oga": "audio/ogg",
            ".opus": "audio/opus",
            ".mp3": "audio/mp3",
            ".mpeg": "audio/mpeg",
            ".wav": "audio/wav",
            ".wave": "audio/wav",
            ".m4a": "audio/m4a",
            ".aac": "audio/aac",
            ".flac": "audio/flac",
            ".webm": "audio/webm",
            ".weba": "audio/webm",
        }
        if ext in ext_map:
            return ext_map[ext]

        mime, _ = mimetypes.guess_type(filename)
        if mime and mime.startswith("audio/"):
            return mime
        return None

    def _get_image_mime(self, filename: str, content_type: str | None = None) -> str | None:
        if content_type and content_type.startswith("image/"):
            return content_type

        ext = os.path.splitext(filename.lower())[1]
        ext_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".heic": "image/heic",
            ".heif": "image/heif",
            ".gif": "image/gif",
        }
        if ext in ext_map:
            return ext_map[ext]

        mime, _ = mimetypes.guess_type(filename)
        if mime and mime.startswith("image/"):
            return mime
        return None

    def _get_document_mime(self, filename: str, content_type: str | None = None) -> str | None:
        if content_type:
            ct = content_type.lower().split(";")[0].strip()
            if ct in (
                "application/pdf",
                "text/plain",
                "text/markdown",
                "text/csv",
                "text/html",
                "text/xml",
                "application/json",
                "text/tab-separated-values",
            ):
                return ct
            if ct.startswith("text/"):
                return ct

        ext = os.path.splitext(filename.lower())[1]
        ext_map = {
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".csv": "text/csv",
            ".tsv": "text/tab-separated-values",
            ".json": "application/json",
            ".html": "text/html",
            ".htm": "text/html",
            ".xml": "text/xml",
            ".py": "text/plain",
            ".js": "text/plain",
            ".ts": "text/plain",
            ".c": "text/plain",
            ".cpp": "text/plain",
            ".h": "text/plain",
            ".java": "text/plain",
            ".go": "text/plain",
            ".rs": "text/plain",
            ".sh": "text/plain",
            ".yaml": "text/plain",
            ".yml": "text/plain",
            ".css": "text/plain",
        }
        if ext in ext_map:
            return ext_map[ext]

        mime, _ = mimetypes.guess_type(filename)
        if mime:
            if mime == "application/pdf" or mime.startswith("text/") or mime == "application/json":
                return mime
        return None

    async def _extract_message_parts(
        self,
        message: discord.Message,
        is_prefix: bool = False,
        attachments_out: list[dict] | None = None,
    ) -> list[types.Part]:
        parts: list[types.Part] = []

        content = message.clean_content.strip()
        if is_prefix or re.match(r"^!ask\b", content, re.IGNORECASE):
            content = re.sub(r"^!ask\s*", "", content, flags=re.IGNORECASE).strip()

        # Remove bot mention from content if present
        if self.bot.user:
            bot_name = getattr(self.bot.user, "name", "")
            if isinstance(bot_name, str) and bot_name:
                content = re.sub(rf"@{re.escape(bot_name)}(?:\b|\s+|$)", "", content, flags=re.IGNORECASE).strip()
            guild = getattr(message, "guild", None)
            me = getattr(guild, "me", None) if guild else None
            nick = getattr(me, "nick", None) if me else None
            if isinstance(nick, str) and nick:
                content = re.sub(rf"@{re.escape(nick)}(?:\b|\s+|$)", "", content, flags=re.IGNORECASE).strip()
            bot_id = getattr(self.bot.user, "id", None)
            if bot_id:
                content = re.sub(rf"<@!?{bot_id}>", "", content).strip()

        # Check for audio, image, and document attachments
        for att in getattr(message, "attachments", []):
            # Check audio
            audio_mime = self._get_audio_mime(att.filename, att.content_type)
            if audio_mime:
                try:
                    audio_bytes = await att.read()
                    if audio_bytes:
                        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime))
                        if attachments_out is not None:
                            attachments_out.append({
                                "filename": att.filename,
                                "bytes": audio_bytes,
                                "content_type": audio_mime,
                            })
                except Exception as e:
                    print(f"Failed to read audio attachment {att.filename}: {e}")
                    sentry_sdk.capture_exception(e)
                continue

            # Check image
            image_mime = self._get_image_mime(att.filename, att.content_type)
            if image_mime:
                try:
                    image_bytes = await att.read()
                    if image_bytes:
                        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime))
                        if attachments_out is not None:
                            attachments_out.append({
                                "filename": att.filename,
                                "bytes": image_bytes,
                                "content_type": image_mime,
                            })
                except Exception as e:
                    print(f"Failed to read image attachment {att.filename}: {e}")
                    sentry_sdk.capture_exception(e)
                continue

            # Check document (PDF, text, markdown, CSV, JSON, code, etc.)
            doc_mime = self._get_document_mime(att.filename, att.content_type)
            if doc_mime:
                try:
                    doc_bytes = await att.read()
                    if doc_bytes:
                        if len(doc_bytes) <= 20 * 1024 * 1024:
                            parts.append(types.Part.from_bytes(data=doc_bytes, mime_type=doc_mime))
                        if attachments_out is not None:
                            attachments_out.append({
                                "filename": att.filename,
                                "bytes": doc_bytes,
                                "content_type": doc_mime,
                            })
                except Exception as e:
                    print(f"Failed to read document attachment {att.filename}: {e}")
                    sentry_sdk.capture_exception(e)
                continue

            # Fallback for any other attachment: still register bytes in attachments_out for printing/tools
            try:
                raw_bytes = await att.read()
                if raw_bytes and attachments_out is not None:
                    attachments_out.append({
                        "filename": att.filename,
                        "bytes": raw_bytes,
                        "content_type": att.content_type or "application/octet-stream",
                    })
            except Exception as e:
                print(f"Failed to read attachment {att.filename}: {e}")

        # Extract text from embeds if present (e.g. link previews or bot embeds)
        embed_texts = []
        for embed in getattr(message, "embeds", []):
            if not embed:
                continue
            e_parts = []
            if getattr(embed, "title", None):
                e_parts.append(f"Title: {embed.title}")
            if getattr(embed, "description", None):
                e_parts.append(f"Description: {embed.description}")
            for field in getattr(embed, "fields", []):
                e_parts.append(f"{field.name}: {field.value}")
            if e_parts:
                embed_texts.append("\n".join(e_parts))

        combined_text = content
        if embed_texts:
            embeds_block = "[Embed Content:\n" + "\n---\n".join(embed_texts) + "]"
            if combined_text:
                combined_text = f"{combined_text}\n{embeds_block}"
            else:
                combined_text = embeds_block

        # Append mentioned users info if any other users were tagged
        if hasattr(message, "mentions") and message.mentions:
            bot_id = getattr(self.bot.user, "id", None) if self.bot.user else None
            other_mentions = [m for m in message.mentions if bot_id is None or m.id != bot_id]
            if other_mentions:
                mention_info = ", ".join(f"@{getattr(m, 'name', 'User')} (ID: {getattr(m, 'id', 'unknown')})" for m in other_mentions)
                mention_block = f"[Context - Mentioned Discord Users: {mention_info}]"
                if combined_text:
                    combined_text = f"{combined_text}\n{mention_block}"
                else:
                    combined_text = mention_block

        if combined_text:
            parts.append(types.Part.from_text(text=combined_text))

        return parts

    def _generate_thread_name(self, prompt: str) -> str:
        clean = " ".join(prompt.split())
        clean = re.sub(
            r"^(?:please\s+)?(?:make|create|start|open|put|post)\s+(?:a\s+)?thread\s+(?:with|about|for|on|using)?\s*",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip()
        prefix = "Ask: "
        max_len = 100 - len(prefix)
        if len(clean) > max_len:
            title = prefix + clean[: max_len - 3] + "..."
        elif clean:
            title = prefix + clean
        else:
            title = "Ask: Gemini Chat"
        return title

    def _consolidate_turns(
        self, raw_turns: list[dict]
    ) -> list[types.Content]:
        if not raw_turns:
            return []

        # Filter out turns without parts
        filtered = [t for t in raw_turns if t.get("parts")]
        if not filtered:
            return []

        # Merge consecutive turns with the same role
        merged: list[dict] = []
        for turn in filtered:
            if merged and merged[-1]["role"] == turn["role"]:
                merged[-1]["parts"].extend(turn["parts"])
            else:
                merged.append({"role": turn["role"], "parts": list(turn["parts"])})

        # Ensure conversation starts with a user turn without discarding model context
        if merged and merged[0]["role"] == "model":
            merged.insert(0, {
                "role": "user",
                "parts": [types.Part.from_text(text="[Continuing previous conversation]")]
            })

        while merged and merged[0]["role"] != "user":
            merged.pop(0)

        # Ensure conversation ends with a user turn
        while merged and merged[-1]["role"] != "user":
            merged.pop()

        if not merged:
            return []

        contents = [
            types.Content(role=t["role"], parts=t["parts"])
            for t in merged
        ]
        return contents

    async def _build_reply_chain_contents(
        self, message: discord.Message, attachments_out: list[dict] | None = None
    ) -> list[types.Content]:
        """Builds multi-turn Content list by gathering the reply chain and recent channel conversation history."""
        chain: list[discord.Message] = [message]
        visited_ids: set[int] = {message.id}
        curr_msg = message

        max_depth = 15
        while curr_msg.reference and curr_msg.reference.message_id and len(chain) <= max_depth:
            ref_id = curr_msg.reference.message_id
            if ref_id in visited_ids:
                break
            visited_ids.add(ref_id)

            ref_msg = None
            if isinstance(curr_msg.reference.resolved, discord.Message) or (
                hasattr(curr_msg.reference.resolved, "clean_content")
                and not isinstance(curr_msg.reference.resolved, discord.DeletedReferencedMessage)
            ):
                ref_msg = curr_msg.reference.resolved
            else:
                try:
                    ref_channel = curr_msg.channel
                    if (
                        curr_msg.reference.channel_id
                        and curr_msg.reference.channel_id != curr_msg.channel.id
                    ):
                        ref_channel = self.bot.get_channel(
                            curr_msg.reference.channel_id
                        ) or await self.bot.fetch_channel(curr_msg.reference.channel_id)
                    if ref_channel and hasattr(ref_channel, "fetch_message"):
                        ref_msg = await ref_channel.fetch_message(ref_id)
                except Exception as e:
                    print(f"Could not fetch referenced message {ref_id}: {e}")
                    break

            if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage) or not hasattr(ref_msg, "clean_content"):
                break

            chain.append(ref_msg)
            curr_msg = ref_msg

        # Determine the primary referenced message (the one directly replied to)
        primary_ref_msg = None
        if len(chain) > 1:
            primary_ref_msg = chain[1]

        # Fetch recent channel/thread conversation history for broader context
        recent_history: list[discord.Message] = []
        if hasattr(message.channel, "history"):
            try:
                history_msgs = [m async for m in message.channel.history(limit=15, before=message)]
                recent_history = history_msgs
            except Exception as e:
                print(f"Could not fetch channel history for reply: {e}")

        # Combine all messages into a unified list, deduplicating by ID
        all_msgs_dict: dict[int, discord.Message] = {}
        for m in recent_history:
            if hasattr(m, "id"):
                all_msgs_dict[m.id] = m
        for m in chain:
            if hasattr(m, "id"):
                all_msgs_dict[m.id] = m

        def get_msg_sort_key(m: discord.Message):
            created = getattr(m, "created_at", None)
            if isinstance(created, datetime):
                return (1, created.timestamp())
            elif isinstance(created, (int, float)):
                return (1, float(created))
            elif isinstance(created, str):
                return (1, created)
            msg_id = getattr(m, "id", None)
            if isinstance(msg_id, int):
                return (2, float(msg_id))
            elif isinstance(msg_id, str) and msg_id.isdigit():
                return (2, float(msg_id))
            return (3, 0.0)

        sorted_msgs = sorted(all_msgs_dict.values(), key=get_msg_sort_key)

        raw_turns: list[dict] = []
        for msg in sorted_msgs:
            if getattr(msg.author, "bot", False):
                if self.bot.user and msg.author.id == self.bot.user.id:
                    content = msg.clean_content.strip()
                    # Filter out system error messages
                    if (
                        content.startswith("Gemini API key is not configured")
                        or content.startswith("API Error:")
                        or content.startswith("An unexpected error occurred")
                        or content.startswith("Gemini is currently experiencing high demand")
                        or content.startswith("What would you like to update")
                        or content.startswith("What would you like an update on")
                        or content.startswith("Here is your status update")
                        or content.startswith("Here is your quick status update")
                        or content.startswith("Update on what, exactly")
                    ):
                        continue
                    if content:
                        raw_turns.append({
                            "role": "model",
                            "parts": [types.Part.from_text(text=content)]
                        })
                else:
                    # Message is from another bot - include as user context
                    content = msg.clean_content.strip()
                    if content:
                        raw_turns.append({
                            "role": "user",
                            "parts": [types.Part.from_text(text=f"[{getattr(msg.author, 'display_name', 'Bot')} (Bot)]: {content}")]
                        })
            else:
                parts = await self._extract_message_parts(msg, is_prefix=False, attachments_out=attachments_out)
                author_name = getattr(msg.author, "display_name", "User")
                author_handle = getattr(msg.author, "name", "")
                author_id = getattr(msg.author, "id", "")

                if msg.id == message.id:
                    ref_author_name = getattr(primary_ref_msg.author, "display_name", "the user") if primary_ref_msg and hasattr(primary_ref_msg, "author") else "the user"
                    has_text = any(hasattr(p, "text") and p.text is not None for p in parts)
                    if not has_text:
                        # User replied with only a ping or attachments
                        text_label = f"[{author_name} (replying to {ref_author_name})]: [Tagged @Shisho in reply to the referenced message to determine correct course of action]"
                        parts.insert(0, types.Part.from_text(text=text_label))
                    else:
                        new_parts = []
                        for p in parts:
                            if hasattr(p, "text") and p.text is not None:
                                new_parts.append(types.Part.from_text(text=f"[{author_name} (replying to {ref_author_name})]: {p.text}"))
                            else:
                                new_parts.append(p)
                        parts = new_parts
                elif primary_ref_msg and msg.id == primary_ref_msg.id:
                    id_info = f", ID: {author_id}" if author_id else ""
                    handle_info = f" (@{author_handle}{id_info})" if author_handle else ""
                    new_parts = []
                    has_text = False
                    for p in parts:
                        if hasattr(p, "text") and p.text is not None:
                            has_text = True
                            new_parts.append(types.Part.from_text(text=f"[Referenced Message from {author_name}{handle_info}]: {p.text}"))
                        else:
                            new_parts.append(p)
                    if not has_text and parts:
                        new_parts.insert(0, types.Part.from_text(text=f"[Referenced Message from {author_name}{handle_info}]"))
                    parts = new_parts
                else:
                    handle_info = f" (@{author_handle})" if author_handle else ""
                    new_parts = []
                    for p in parts:
                        if hasattr(p, "text") and p.text is not None:
                            new_parts.append(types.Part.from_text(text=f"[{author_name}{handle_info}]: {p.text}"))
                        else:
                            new_parts.append(p)
                    parts = new_parts

                if parts:
                    raw_turns.append({
                        "role": "user",
                        "parts": parts
                    })

        return self._consolidate_turns(raw_turns)

    async def _build_channel_contents(
        self,
        channel: discord.Thread | discord.DMChannel | discord.abc.Messageable,
        additional_parts: list[types.Part] | str | None = None,
        limit: int = 15,
        attachments_out: list[dict] | None = None,
    ) -> list[types.Content]:
        raw_turns: list[dict] = []

        if isinstance(channel, discord.Thread):
            starter_msg = channel.starter_message
            if not starter_msg and channel.parent and hasattr(channel.parent, "fetch_message"):
                try:
                    starter_msg = await channel.parent.fetch_message(channel.id)
                except Exception:
                    starter_msg = None

            if starter_msg:
                if starter_msg.author.id == self.bot.user.id:
                    content = starter_msg.clean_content.strip()
                    match = re.search(
                        r"\*\*(?:Question|Ask):\*\*\s*(.+)",
                        content,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if match:
                        raw_turns.append({
                            "role": "user",
                            "parts": [types.Part.from_text(text=match.group(1).strip())]
                        })
                    elif content:
                        raw_turns.append({
                            "role": "model",
                            "parts": [types.Part.from_text(text=content)]
                        })
                else:
                    user_parts = await self._extract_message_parts(starter_msg, is_prefix=True, attachments_out=attachments_out)
                    if user_parts:
                        author_name = getattr(starter_msg.author, "display_name", "User")
                        author_handle = getattr(starter_msg.author, "name", "")
                        handle_info = f" (@{author_handle})" if author_handle else ""
                        formatted_parts = []
                        for p in user_parts:
                            if hasattr(p, "text") and p.text is not None:
                                formatted_parts.append(types.Part.from_text(text=f"[{author_name}{handle_info}]: {p.text}"))
                            else:
                                formatted_parts.append(p)
                        raw_turns.append({"role": "user", "parts": formatted_parts})

        try:
            recent_msgs = [m async for m in channel.history(limit=limit)]
            recent_msgs.reverse()
            for msg in recent_msgs:
                if getattr(msg.author, "bot", False):
                    if self.bot.user and msg.author.id == self.bot.user.id:
                        content = msg.clean_content.strip()
                        if (
                            content.startswith("Gemini API key is not configured")
                            or content.startswith("API Error:")
                            or content.startswith("An unexpected error occurred")
                            or content.startswith("Gemini is currently experiencing high demand")
                            or content.startswith("What would you like to update")
                            or content.startswith("What would you like an update on")
                            or content.startswith("Here is your status update")
                            or content.startswith("Here is your quick status update")
                            or content.startswith("Update on what, exactly")
                        ):
                            continue
                        if content:
                            raw_turns.append({
                                "role": "model",
                                "parts": [types.Part.from_text(text=content)]
                            })
                    else:
                        content = msg.clean_content.strip()
                        if content:
                            raw_turns.append({
                                "role": "user",
                                "parts": [types.Part.from_text(text=f"[{getattr(msg.author, 'display_name', 'Bot')} (Bot)]: {content}")]
                            })
                else:
                    user_parts = await self._extract_message_parts(msg, is_prefix=True, attachments_out=attachments_out)
                    if user_parts:
                        author_name = getattr(msg.author, "display_name", "User")
                        author_handle = getattr(msg.author, "name", "")
                        handle_info = f" (@{author_handle})" if author_handle else ""
                        formatted_parts = []
                        for p in user_parts:
                            if hasattr(p, "text") and p.text is not None:
                                formatted_parts.append(types.Part.from_text(text=f"[{author_name}{handle_info}]: {p.text}"))
                            else:
                                formatted_parts.append(p)
                        raw_turns.append({"role": "user", "parts": formatted_parts})
        except Exception as e:
            print(f"Error reading channel history: {e}")

        if additional_parts:
            if isinstance(additional_parts, str):
                clean_add = additional_parts.strip()
                if clean_add:
                    raw_turns.append({
                        "role": "user",
                        "parts": [types.Part.from_text(text=clean_add)]
                    })
            elif isinstance(additional_parts, list) and additional_parts:
                raw_turns.append({
                    "role": "user",
                    "parts": list(additional_parts)
                })

        return self._consolidate_turns(raw_turns)

    # Alias for backward compatibility
    _build_thread_contents = _build_channel_contents

    async def _is_ai_chat_thread(self, thread: discord.Thread) -> bool:
        if thread.name.startswith("Recommend: ") or thread.name.startswith("Recommendations: "):
            return False

        if thread.id in self.active_threads:
            return True

        if thread.name.startswith("Ask: ") or thread.name.startswith("Gemini: "):
            self.active_threads.add(thread.id)
            return True

        starter_msg = thread.starter_message
        if not starter_msg and thread.parent and hasattr(thread.parent, "fetch_message"):
            try:
                starter_msg = await thread.parent.fetch_message(thread.id)
            except Exception:
                starter_msg = None

        if starter_msg:
            if (
                starter_msg.clean_content.startswith("!recommend")
                or "**Recommendations:**" in starter_msg.clean_content
                or "**Book Recommendations**" in starter_msg.clean_content
            ):
                return False

            if starter_msg.author.id == self.bot.user.id and (
                "**Question:**" in starter_msg.clean_content
                or "**Ask:**" in starter_msg.clean_content
            ):
                self.active_threads.add(thread.id)
                return True
            if starter_msg.clean_content.startswith("!ask"):
                self.active_threads.add(thread.id)
                return True

        try:
            async for msg in thread.history(limit=5, oldest_first=True):
                if msg.author.id == self.bot.user.id:
                    self.active_threads.add(thread.id)
                    return True
                break
        except Exception:
            pass

        return False

    async def _execute_tool(
        self, name: str, args: dict, user_id: str, context: dict | None = None
    ) -> str:
        return await execute_tool(self.bot, name, args, user_id, context=context)

    async def _generate_ai_response(
        self,
        contents: list[types.Content] | str,
        user_id: str = "",
        context: dict | None = None,
    ) -> str:
        if isinstance(contents, str):
            contents_list = [
                types.Content(
                    role="user", parts=[types.Part.from_text(text=contents)]
                )
            ]
        else:
            contents_list = list(contents)

        sys_prompt = self.get_system_instruction()
        config = types.GenerateContentConfig(
            system_instruction=sys_prompt if sys_prompt else None,
            tools=AI_CHAT_TOOLS,
        )

        model_name = get_gemini_model()
        max_tool_turns = 5
        for _ in range(max_tool_turns):
            response = await generate_content_with_retry(
                self.client,
                model=model_name,
                contents=contents_list,
                config=config,
            )

            # Check if the model requested any tool calls
            function_calls = response.function_calls
            if not function_calls:
                return format_for_discord(response.text or "")

            valid_function_calls = [
                fc for fc in function_calls if fc.name in TOOL_HANDLERS
            ]
            if not valid_function_calls:
                return format_for_discord(response.text or "")

            # Append the model turn that requested tool calls
            candidate_parts = []
            if response.candidates and response.candidates[0].content:
                candidate_parts = response.candidates[0].content.parts
            else:
                candidate_parts = [
                    types.Part(function_call=fc) for fc in valid_function_calls
                ]
            contents_list.append(types.Content(role="model", parts=candidate_parts))

            # Execute tool calls and collect responses
            tool_response_parts = []
            for fc in valid_function_calls:
                args = fc.args if isinstance(fc.args, dict) else {}
                result_str = await self._execute_tool(
                    fc.name, args, user_id, context=context
                )
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result_str},
                    )
                )

            # Append the tool response turn (Gemini API requires role='user' for function responses)
            contents_list.append(
                types.Content(role="user", parts=tool_response_parts)
            )

        return format_for_discord(response.text or "")

    @commands.command(name="ask", help="Ask Gemini a question or send an image, audio, or document.")
    async def ask_prefix(self, ctx: commands.Context, *, prompt: str = ""):
        if not self.is_user_authorized(ctx.author.id):
            return

        if not self.client:
            await ctx.send("Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.")
            return

        attachments_out: list[dict] = []
        parts = await self._extract_message_parts(ctx.message, is_prefix=True, attachments_out=attachments_out)
        if not parts:
            await ctx.send("Please provide a question, prompt, image, audio, or file attachment.")
            return

        user_id_str = str(ctx.author.id)
        exec_context = {"attachments": attachments_out, "message": ctx.message, "guild": ctx.guild, "channel": ctx.channel}

        # Case 1: Inside an existing thread
        if isinstance(ctx.channel, discord.Thread):
            lock = self._get_thread_lock(ctx.channel.id)
            async with lock:
                self.active_threads.add(ctx.channel.id)
                async with ctx.typing():
                    try:
                        contents = await self._build_thread_contents(ctx.channel, attachments_out=attachments_out)
                        if not contents:
                            contents = [types.Content(role="user", parts=parts)]
                        text = await self._generate_ai_response(
                            contents, user_id=user_id_str, context=exec_context
                        )
                        if not text:
                            await ctx.send("Received empty response from Gemini.")
                            return
                        for chunk in split_message(text):
                            await ctx.send(chunk)
                    except Exception as e:
                        await ctx.send(format_gemini_error(e, include_details=False))
            return

        # Case 2: Direct Message (Threads not supported in DMs)
        if isinstance(ctx.channel, discord.DMChannel):
            async with ctx.typing():
                try:
                    contents = [types.Content(role="user", parts=parts)]
                    text = await self._generate_ai_response(
                        contents, user_id=user_id_str, context=exec_context
                    )
                    if not text:
                        await ctx.send("Received empty response from Gemini.")
                        return
                    for chunk in split_message(text):
                        await ctx.send(chunk)
                except Exception as e:
                    await ctx.send(format_gemini_error(e, include_details=False))
            return

        # Case 3: Guild Text Channel (Create a thread)
        async with ctx.typing():
            try:
                contents = None
                if ctx.message.reference and ctx.message.reference.message_id:
                    contents = await self._build_reply_chain_contents(
                        ctx.message, attachments_out=attachments_out
                    )
                if not contents:
                    contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(
                    contents, user_id=user_id_str, context=exec_context
                )
                if not text:
                    await ctx.send("Received empty response from Gemini.")
                    return
            except Exception as e:
                await ctx.send(format_gemini_error(e, include_details=False))
                return

            chunks = split_message(text)
            thread = None
            if hasattr(ctx.message, "create_thread"):
                try:
                    has_img = any(self._get_image_mime(a.filename, a.content_type) for a in ctx.message.attachments)
                    has_doc = any(self._get_document_mime(a.filename, a.content_type) for a in ctx.message.attachments)
                    thread_prompt = prompt.strip() or ("Image Memo" if has_img else ("Document Memo" if has_doc else "Audio Memo"))
                    thread_name = self._generate_thread_name(thread_prompt)
                    thread = await ctx.message.create_thread(name=thread_name, auto_archive_duration=1440)
                    self.active_threads.add(thread.id)
                except Exception as e:
                    print(f"Failed to create thread for ask prefix command: {e}")
                    thread = None

            if thread:
                for chunk in chunks:
                    await thread.send(chunk)
            else:
                for chunk in chunks:
                    await ctx.send(chunk)

    @app_commands.command(name="ask", description="Send a prompt, image, audio, or document to the Gemini API")
    @app_commands.describe(
        prompt="The prompt to send to Gemini",
        image="Optional image attachment",
        audio="Optional audio or voice recording",
        file="Optional document or file attachment (PDF, TXT, MD, etc.)"
    )
    async def ask_slash(
        self,
        interaction: discord.Interaction,
        prompt: str = "",
        image: discord.Attachment = None,
        audio: discord.Attachment = None,
        file: discord.Attachment = None,
    ):
        if not self.client:
            await interaction.response.send_message("Gemini API key is not configured.", ephemeral=True)
            return

        parts: list[types.Part] = []
        attachments_out: list[dict] = []

        if image:
            mime = self._get_image_mime(image.filename, image.content_type)
            if mime:
                try:
                    img_bytes = await image.read()
                    if img_bytes:
                        parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
                        attachments_out.append({
                            "filename": image.filename,
                            "bytes": img_bytes,
                            "content_type": mime,
                        })
                except Exception as e:
                    print(f"Failed to read slash image attachment: {e}")
                    sentry_sdk.capture_exception(e)

        if audio:
            mime = self._get_audio_mime(audio.filename, audio.content_type)
            if mime:
                try:
                    audio_bytes = await audio.read()
                    if audio_bytes:
                        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime))
                        attachments_out.append({
                            "filename": audio.filename,
                            "bytes": audio_bytes,
                            "content_type": mime,
                        })
                except Exception as e:
                    print(f"Failed to read slash audio attachment: {e}")
                    sentry_sdk.capture_exception(e)

        if file:
            doc_mime = self._get_document_mime(file.filename, file.content_type) or self._get_image_mime(file.filename, file.content_type) or self._get_audio_mime(file.filename, file.content_type) or "application/octet-stream"
            try:
                file_bytes = await file.read()
                if file_bytes:
                    if len(file_bytes) <= 20 * 1024 * 1024 and doc_mime != "application/octet-stream":
                        parts.append(types.Part.from_bytes(data=file_bytes, mime_type=doc_mime))
                    attachments_out.append({
                        "filename": file.filename,
                        "bytes": file_bytes,
                        "content_type": doc_mime,
                    })
            except Exception as e:
                print(f"Failed to read slash file attachment: {e}")
                sentry_sdk.capture_exception(e)

        clean_prompt = prompt.strip()
        if clean_prompt:
            parts.append(types.Part.from_text(text=clean_prompt))

        if not parts:
            await interaction.response.send_message("Please provide a prompt, image, audio, or document file.", ephemeral=True)
            return

        user_id_str = str(interaction.user.id)
        exec_context = {
            "attachments": attachments_out,
            "interaction": interaction,
            "guild": interaction.guild,
            "channel": interaction.channel,
        }

        # Defer response since Gemini generation takes time
        await interaction.response.defer()

        # Case 1: Inside an existing thread
        if isinstance(interaction.channel, discord.Thread):
            lock = self._get_thread_lock(interaction.channel.id)
            async with lock:
                self.active_threads.add(interaction.channel.id)
                try:
                    contents = await self._build_thread_contents(
                        interaction.channel, additional_parts=parts, attachments_out=attachments_out
                    )
                    if not contents:
                        contents = [types.Content(role="user", parts=parts)]
                    text = await self._generate_ai_response(
                        contents, user_id=user_id_str, context=exec_context
                    )
                    if not text:
                        await interaction.followup.send("Received empty response from Gemini.")
                        return
                    chunks = split_message(text)
                    for chunk in chunks:
                        await interaction.followup.send(chunk)
                except Exception as e:
                    await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        # Case 2: DM Channel (no threads)
        if isinstance(interaction.channel, discord.DMChannel):
            try:
                contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(
                    contents, user_id=user_id_str, context=exec_context
                )
                if not text:
                    await interaction.followup.send("Received empty response from Gemini.")
                    return
                chunks = split_message(text)
                for chunk in chunks:
                    await interaction.followup.send(chunk)
            except Exception as e:
                await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        # Case 3: Guild Text Channel (Create a thread)
        try:
            contents = [types.Content(role="user", parts=parts)]
            text = await self._generate_ai_response(
                contents, user_id=user_id_str, context=exec_context
            )
            if not text:
                await interaction.followup.send("Received empty response from Gemini.")
                return
        except Exception as e:
            await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        chunks = split_message(text)
        thread = None
        try:
            starter_label = f"💬 **Question:** {clean_prompt}" if clean_prompt else ("🖼️ **Image Prompt**" if image else ("📄 **Document Prompt**" if file else "🎙️ **Audio Prompt**"))
            msg = await interaction.followup.send(starter_label, wait=True)
            if msg and hasattr(msg, "create_thread"):
                thread_title = clean_prompt or ("Image Memo" if image else ("Document Memo" if file else "Audio Memo"))
                thread_name = self._generate_thread_name(thread_title)
                thread = await msg.create_thread(name=thread_name, auto_archive_duration=1440)
                self.active_threads.add(thread.id)
        except Exception as e:
            print(f"Failed to create thread for slash ask command: {e}")
            thread = None

        if thread:
            for chunk in chunks:
                await thread.send(chunk)
        else:
            for chunk in chunks:
                await interaction.followup.send(chunk)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Ignore prefix commands so process_commands handles them
        if message.content.startswith("!"):
            return

        is_bot_mentioned = bool(self.bot.user and any(m.id == self.bot.user.id for m in message.mentions))
        is_in_thread = isinstance(message.channel, discord.Thread)
        is_dm = isinstance(message.channel, discord.DMChannel)

        is_reply = message.reference is not None and message.reference.message_id is not None
        is_reply_to_bot = False
        referenced_msg = None
        if is_reply:
            if isinstance(message.reference.resolved, discord.Message) or (
                hasattr(message.reference.resolved, "clean_content")
                and not isinstance(message.reference.resolved, discord.DeletedReferencedMessage)
            ):
                referenced_msg = message.reference.resolved
            elif message.reference.message_id:
                try:
                    ref_channel = message.channel
                    if (
                        message.reference.channel_id
                        and message.reference.channel_id != message.channel.id
                    ):
                        ref_channel = self.bot.get_channel(
                            message.reference.channel_id
                        ) or await self.bot.fetch_channel(message.reference.channel_id)
                    if ref_channel and hasattr(ref_channel, "fetch_message"):
                        referenced_msg = await ref_channel.fetch_message(message.reference.message_id)
                except Exception:
                    referenced_msg = None
            if (
                referenced_msg
                and not isinstance(referenced_msg, discord.DeletedReferencedMessage)
                and hasattr(referenced_msg, "author")
                and self.bot.user
                and referenced_msg.author.id == self.bot.user.id
            ):
                is_reply_to_bot = True

        # We respond if:
        # 1. The message is in DMs (talking directly to the bot), OR
        # 2. The bot was @mentioned anywhere, OR
        # 3. The message is a direct reply to one of the bot's messages, OR
        # 4. The message is inside an active AI chat thread
        if not is_dm and not is_bot_mentioned and not is_reply_to_bot:
            if not is_in_thread:
                return
            if not await self._is_ai_chat_thread(message.channel):
                return
        else:
            # If in thread and bot was mentioned/replied to, check it's not a concierge thread
            if is_in_thread and (
                message.channel.name.startswith("Recommend: ")
                or message.channel.name.startswith("Recommendations: ")
            ):
                return

        # Verify user authorization
        if not self.is_user_authorized(message.author.id):
            return

        if not self.client:
            return

        # Check if there is text, audio attachments, image attachments, or document attachments
        has_audio = any(
            self._get_audio_mime(att.filename, att.content_type)
            for att in message.attachments
        )
        has_image = any(
            self._get_image_mime(att.filename, att.content_type)
            for att in message.attachments
        )
        has_document = any(
            self._get_document_mime(att.filename, att.content_type)
            for att in message.attachments
        )
        has_any_att = bool(message.attachments)

        clean_text = message.clean_content
        if self.bot.user:
            bot_name = getattr(self.bot.user, "name", "")
            if isinstance(bot_name, str) and bot_name:
                clean_text = re.sub(rf"@{re.escape(bot_name)}(?:\b|\s+|$)", "", clean_text, flags=re.IGNORECASE).strip()
            guild = getattr(message, "guild", None)
            me = getattr(guild, "me", None) if guild else None
            nick = getattr(me, "nick", None) if me else None
            if isinstance(nick, str) and nick:
                clean_text = re.sub(rf"@{re.escape(nick)}(?:\b|\s+|$)", "", clean_text, flags=re.IGNORECASE).strip()
            bot_id = getattr(self.bot.user, "id", None)
            if bot_id:
                clean_text = re.sub(rf"<@!?{bot_id}>", "", clean_text).strip()

        if not clean_text and not has_audio and not has_image and not has_document and not has_any_att and not is_reply:
            return

        user_id_str = str(message.author.id)
        attachments_out: list[dict] = []

        # Case 1: Message is inside a thread
        if is_in_thread:
            lock = self._get_thread_lock(message.channel.id)
            async with lock:
                self.active_threads.add(message.channel.id)
                async with message.channel.typing():
                    try:
                        if is_reply:
                            contents = await self._build_reply_chain_contents(
                                message, attachments_out=attachments_out
                            )
                        else:
                            contents = await self._build_channel_contents(
                                message.channel, attachments_out=attachments_out
                            )
                        if not contents:
                            parts = await self._extract_message_parts(
                                message, is_prefix=False, attachments_out=attachments_out
                            )
                            if parts:
                                contents = [types.Content(role="user", parts=parts)]
                        if not contents:
                            return
                        exec_context = {
                            "attachments": attachments_out,
                            "message": message,
                            "guild": message.guild,
                            "channel": message.channel,
                        }
                        text = await self._generate_ai_response(
                            contents, user_id=user_id_str, context=exec_context
                        )
                        if not text or text.strip() == "[NO_ACTION]":
                            return
                        chunks = split_message(text)
                        for chunk in chunks:
                            await message.channel.send(chunk)
                    except Exception as e:
                        await message.channel.send(format_gemini_error(e, include_details=False))
            return

        # Case 2: Message is in DM (Maintains multi-turn context from DM history)
        if is_dm:
            async with message.channel.typing():
                try:
                    if is_reply:
                        contents = await self._build_reply_chain_contents(
                            message, attachments_out=attachments_out
                        )
                    else:
                        contents = await self._build_channel_contents(
                            message.channel, attachments_out=attachments_out
                        )
                    if not contents:
                        parts = await self._extract_message_parts(
                            message, is_prefix=False, attachments_out=attachments_out
                        )
                        if parts:
                            contents = [types.Content(role="user", parts=parts)]
                    if not contents:
                        return
                    exec_context = {
                        "attachments": attachments_out,
                        "message": message,
                        "guild": getattr(message, "guild", None),
                        "channel": message.channel,
                    }
                    text = await self._generate_ai_response(
                        contents, user_id=user_id_str, context=exec_context
                    )
                    if not text or text.strip() == "[NO_ACTION]":
                        return
                    chunks = split_message(text)
                    for chunk in chunks:
                        await message.channel.send(chunk)
                except Exception as e:
                    await message.channel.send(format_gemini_error(e, include_details=False))
            return

        # Case 3: Message is in a Guild Text Channel
        async with message.channel.typing():
            try:
                contents = None
                if is_reply:
                    contents = await self._build_reply_chain_contents(
                        message, attachments_out=attachments_out
                    )
                else:
                    parts = await self._extract_message_parts(
                        message, is_prefix=False, attachments_out=attachments_out
                    )
                    if parts:
                        contents = [types.Content(role="user", parts=parts)]
                if not contents:
                    parts = await self._extract_message_parts(
                        message, is_prefix=False, attachments_out=attachments_out
                    )
                    if not parts:
                        return
                    contents = [types.Content(role="user", parts=parts)]

                exec_context = {
                    "attachments": attachments_out,
                    "message": message,
                    "guild": message.guild,
                    "channel": message.channel,
                }
                text = await self._generate_ai_response(
                    contents, user_id=user_id_str, context=exec_context
                )
                if not text or text.strip() == "[NO_ACTION]":
                    return
            except Exception as e:
                await message.channel.send(format_gemini_error(e, include_details=False))
                return

            chunks = split_message(text)
            should_create_thread = self._should_create_thread(clean_text, text, chunks)

            thread = None
            if should_create_thread and hasattr(message, "create_thread"):
                try:
                    thread_title = clean_text or ("Image Chat" if has_image else ("Document Chat" if has_document else "AI Chat"))
                    thread_name = self._generate_thread_name(thread_title)
                    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
                    self.active_threads.add(thread.id)
                except Exception as e:
                    print(f"Failed to create thread for mention: {e}")
                    thread = None

            if thread:
                for chunk in chunks:
                    await thread.send(chunk)
            else:
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        try:
                            await message.reply(chunk)
                        except Exception:
                            await message.channel.send(chunk)
                    else:
                        await message.channel.send(chunk)


async def setup(bot):
    await bot.add_cog(AIChat(bot))
