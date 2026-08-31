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

from utils.discord_helpers import split_message, is_user_authorized
from utils.llm import (
    get_gemini_client,
    get_gemini_model,
    format_gemini_error,
    generate_content_with_retry,
)

ADD_BOOK_TOOL = types.FunctionDeclaration(
    name="add_book",
    description="Adds a book to the user's reading list on PocketBase.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="The title of the book",
            ),
            "author": types.Schema(
                type=types.Type.STRING,
                description="The author of the book (optional)",
            ),
            "status": types.Schema(
                type=types.Type.STRING,
                description="Reading status of the book",
                enum=["planned", "reading", "read", "dropped"],
            ),
            "publish_date": types.Schema(
                type=types.Type.STRING,
                description="The publication date or year (optional)",
            ),
            "isbn": types.Schema(
                type=types.Type.STRING,
                description="ISBN of the book (optional)",
            ),
        },
        required=["title"],
    ),
)

SET_REMINDER_TOOL = types.FunctionDeclaration(
    name="set_reminder",
    description="Sets a reminder for the user. Supports natural language times like 'in 5 minutes', 'tomorrow at 3pm', 'next Friday at 10am'.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "when": types.Schema(
                type=types.Type.STRING,
                description="When to remind the user (e.g. 'in 2 hours', 'tomorrow at 10am', 'Friday at 3pm')",
            ),
            "text": types.Schema(
                type=types.Type.STRING,
                description="The content / message of what to remind the user about",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Optional timezone or abbreviation (e.g. 'US/Eastern', 'jp', 'fr', 'ca', 'US/Central', 'US/Pacific')",
            ),
        },
        required=["when", "text"],
    ),
)

ADD_NOTE_TOOL = types.FunctionDeclaration(
    name="add_note",
    description="Saves a personal note to the user's notes in PocketBase.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "text": types.Schema(
                type=types.Type.STRING,
                description="The body / content of the note",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="Optional title or subject for the note",
            ),
        },
        required=["text"],
    ),
)

GET_NOTES_TOOL = types.FunctionDeclaration(
    name="get_notes",
    description="Retrieves recent personal notes from the user's PocketBase notes collection, optionally filtering by search query.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Optional keyword to filter or search notes by title or text",
            ),
        },
    ),
)

GET_READING_LIST_TOOL = types.FunctionDeclaration(
    name="get_reading_list",
    description="Retrieves the user's reading list from PocketBase, including book titles, authors, and statuses (planned, reading, read, dropped). Use this whenever the user asks about their reading list or asks for book recommendations, to avoid recommending books already on their list.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="Optional filter by status: 'planned', 'reading', 'read', 'dropped', or 'all'",
            ),
        },
    ),
)

LIST_REMINDERS_TOOL = types.FunctionDeclaration(
    name="list_reminders",
    description="Lists the user's active, upcoming reminders. Use this whenever the user asks what reminders they have scheduled.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of reminders to return (optional)",
            ),
        },
    ),
)

AI_CHAT_TOOLS = [
    types.Tool(
        function_declarations=[
            ADD_BOOK_TOOL,
            SET_REMINDER_TOOL,
            ADD_NOTE_TOOL,
            GET_NOTES_TOOL,
            GET_READING_LIST_TOOL,
            LIST_REMINDERS_TOOL,
        ],
    )
]


class AIChat(commands.Cog):
    """General AI Chat command with threaded conversations and multimodal audio support."""

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

        behavior_instruction = (
            "You are Shisho (ししょ) responding inside Discord.\n"
            "You have direct access to database tools and Google Search:\n"
            "- Reading List: Call `get_reading_list` to see books. Call `add_book` to add books.\n"
            "- Reminders: Call `set_reminder` to set reminders. Call `list_reminders` to see upcoming reminders.\n"
            "- Notes: Call `add_note` to save notes. Call `get_notes` to search or retrieve saved notes.\n"
            "- Google Search: Search the web whenever up-to-date or factual knowledge is needed.\n\n"
            "CRITICAL SCOPING RULES:\n"
            "1. ONLY answer what the user explicitly asks for. NEVER bundle unprompted status updates or combine categories:\n"
            "   - When asked about the reading list (e.g. 'what\\'s on my reading list'), call ONLY `get_reading_list` and respond ONLY about the user\\'s books. DO NOT mention reminders, notes, or unrelated features.\n"
            "   - When asked about notes (e.g. 'search my notes'), call ONLY `get_notes` and respond ONLY about notes. DO NOT mention reading list or reminders.\n"
            "   - When asked about reminders (e.g. 'what reminders do I have'), call ONLY `list_reminders` and respond ONLY about reminders. DO NOT mention reading list or notes.\n"
            "   - NEVER output a multi-category 'status update' or dashboard unless the user explicitly commands you to give an overall summary of everything.\n"
            "2. When the user asks for book recommendations, first check the user's reading list (by calling `get_reading_list`) to see what books they have already read, are reading, or have planned/dropped. NEVER recommend books that are already on the user's reading list. Provide creative, engaging recommendations of new books tailored to their tastes.\n"
            "3. For general conversation or greetings (like 'hello'), chat naturally in your sarcastic, intelligent Shisho persona without giving unsolicited status updates or asking what to update.\n"
            "4. When @mentioned in a server channel, if the mention is merely ambient chatter talking about you to someone else without asking for help, reply ONLY with '[NO_ACTION]'.\n"
            "5. If given an audio recording or voice memo without explicit instructions, transcribe/summarize it and save it with `add_note`.\n"
            "6. When a user replies to a message (such as a previous book recommendation, note, reminder, or chat message) with follow-up instructions like 'add it to my reading list', 'remind me about this', or 'explain more', use the conversation context and referenced message to fulfill their request directly without asking for information already provided in the context."
        )

        if base_prompt:
            return f"{base_prompt}\n\n{behavior_instruction}"
        return behavior_instruction

    def _should_create_thread(self, prompt: str, text: str, chunks: list[str]) -> bool:
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

    async def _extract_message_parts(self, message: discord.Message, is_prefix: bool = False) -> list[types.Part]:
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

        # Check for audio attachments
        for att in message.attachments:
            mime_type = self._get_audio_mime(att.filename, att.content_type)
            if mime_type:
                try:
                    audio_bytes = await att.read()
                    if audio_bytes:
                        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime_type))
                except Exception as e:
                    print(f"Failed to read audio attachment {att.filename}: {e}")
                    sentry_sdk.capture_exception(e)

        if content:
            parts.append(types.Part.from_text(text=content))

        return parts

    def _generate_thread_name(self, prompt: str) -> str:
        clean = " ".join(prompt.split())
        prefix = "Ask: "
        max_len = 100 - len(prefix)
        if len(clean) > max_len:
            title = prefix + clean[: max_len - 3] + "..."
        else:
            title = prefix + clean
        return title or "Ask: Gemini Chat"

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
        self, message: discord.Message
    ) -> list[types.Content]:
        """Builds multi-turn Content list by traversing the Discord message reply chain backwards."""
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

        # Reverse so oldest message is first
        chain.reverse()

        raw_turns: list[dict] = []
        for msg in chain:
            if msg.author.bot:
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
                            "parts": [types.Part.from_text(text=f"[{msg.author.display_name} (Bot)]: {content}")]
                        })
            else:
                parts = await self._extract_message_parts(msg, is_prefix=False)
                if parts:
                    raw_turns.append({
                        "role": "user",
                        "parts": parts
                    })

        return self._consolidate_turns(raw_turns)

    async def _build_channel_contents(
        self, channel: discord.Thread | discord.DMChannel | discord.abc.Messageable, additional_parts: list[types.Part] | str | None = None
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
                    user_parts = await self._extract_message_parts(starter_msg, is_prefix=True)
                    if user_parts:
                        raw_turns.append({"role": "user", "parts": user_parts})

        try:
            recent_msgs = [m async for m in channel.history(limit=15)]
            recent_msgs.reverse()
            for msg in recent_msgs:
                if msg.author.bot:
                    if msg.author.id == self.bot.user.id:
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
                        continue
                else:
                    user_parts = await self._extract_message_parts(msg, is_prefix=True)
                    if user_parts:
                        raw_turns.append({"role": "user", "parts": user_parts})
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

    async def _execute_tool(self, name: str, args: dict, user_id: str) -> str:
        try:
            if name == "add_book":
                reading_list_cog = self.bot.get_cog("ReadingList")
                if not reading_list_cog:
                    return "Error: ReadingList cog is unavailable."

                title = str(args.get("title", "")).strip()
                author = str(args.get("author", "")).strip()
                status = str(args.get("status", "planned")).strip().lower()
                publish_date = str(args.get("publish_date", "")).strip()
                isbn = str(args.get("isbn", "")).strip()

                if not title and not isbn:
                    return "Error: Title or ISBN is required."

                api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
                image_url = ""
                description = ""
                cover_filename = None
                cover_data = None

                # Automatically fetch book data from Google Books API to populate missing details and cover
                if api_key:
                    from utils import google_books
                    search_query = isbn if isbn else (f"{title} {author}".strip() if (title and author) else (title or ""))
                    if search_query:
                        book_data = await google_books.fetch_book_data(search_query, api_key)
                        if isinstance(book_data, dict):
                            title = title or book_data.get("title", "")
                            authors = book_data.get("authors", [])
                            if not author and authors and authors != ["Unknown Author"]:
                                author = ", ".join(authors)
                            if not publish_date and book_data.get("publishedDate") and book_data.get("publishedDate") != "Unknown":
                                publish_date = book_data.get("publishedDate")
                            if not isbn and book_data.get("isbn"):
                                isbn = book_data.get("isbn")
                            image_url = book_data.get("thumbnail", "")
                            desc = book_data.get("description", "")
                            if desc and desc != "No description available.":
                                description = desc

                            if image_url:
                                cover_filename, cover_data = await google_books.download_image(image_url)

                today = datetime.now().strftime("%Y-%m-%d")
                final_start = today if status in ["read", "reading"] else ""
                final_end = today if status == "read" else ""

                await reading_list_cog.add_book_to_pocketbase(
                    discord_id=user_id,
                    title=title or "Unknown Title",
                    author=author or "Unknown Author",
                    status_val=status if status in ["planned", "reading", "read", "dropped"] else "planned",
                    publish_date=publish_date if publish_date != "Unknown" else "",
                    isbn=isbn,
                    final_start_date=final_start,
                    final_end_date=final_end,
                    image_url=image_url,
                    description=description,
                    cover_filename=cover_filename,
                    cover_data=cover_data,
                )
                return f"Successfully added '{title or 'Unknown Title'}' by {author or 'Unknown Author'} (status: {status}) to the reading list."

            elif name == "set_reminder":
                reminders_cog = self.bot.get_cog("Reminders")
                if not reminders_cog:
                    return "Error: Reminders cog is unavailable."
                when = str(args.get("when", "")).strip()
                text = str(args.get("text", "")).strip()
                tz = str(args.get("timezone", "")).strip()
                if not when or not text:
                    return "Error: 'when' and 'text' are required for a reminder."
                res = await reminders_cog.add_reminder(user_id, when, text, for_discord=True, user_tz=tz or None)
                return res

            elif name == "add_note":
                notes_cog = self.bot.get_cog("Notes")
                if not notes_cog:
                    return "Error: Notes cog is unavailable."
                text = str(args.get("text", "")).strip()
                title = str(args.get("title", "")).strip()
                if not text and not title:
                    return "Error: Note text or title is required."
                res = await notes_cog.add_note(user_id, text=text, title=title)
                return res

            elif name == "get_notes":
                notes_cog = self.bot.get_cog("Notes")
                if not notes_cog:
                    return "Error: Notes cog is unavailable."
                query = str(args.get("query", "")).strip()
                notes = await notes_cog.get_notes(user_id, limit=10, query=query or None)
                if isinstance(notes, str):
                    return notes
                if not notes:
                    return f"No notes found{' matching ' + query if query else ''}."
                formatted = []
                for n in notes:
                    t = n.get("title") or "Untitled Note"
                    txt = n.get("text") or ""
                    created = n.get("created") or ""
                    formatted.append(f"Title: {t}\nContent: {txt}\nDate: {created}")
                return "\n---\n".join(formatted)

            elif name == "get_reading_list":
                reading_list_cog = self.bot.get_cog("ReadingList")
                if not reading_list_cog:
                    return "Error: ReadingList cog is unavailable."
                status_filter = str(args.get("status", "")).strip().lower()
                books = await reading_list_cog.fetch_reading_list(user_id)
                if not books:
                    return "No books found in reading list."
                if status_filter and status_filter != "all":
                    filtered_books = [b for b in books if b.get("status", "").lower() == status_filter]
                    if filtered_books:
                        books = filtered_books
                formatted = [
                    f"- {b.get('title', 'Unknown')} by {b.get('author', 'Unknown')} (Status: {b.get('status', 'unknown')})"
                    for b in books
                ]
                return "\n".join(formatted)

            elif name == "list_reminders":
                reminders_cog = self.bot.get_cog("Reminders")
                if not reminders_cog:
                    return "Error: Reminders cog is unavailable."
                res = await reminders_cog.get_reminders_text(user_id, for_discord=False)
                return res or "No active reminders."

            return f"Error: Unknown tool '{name}'."
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"An error occurred while executing {name}: {str(e)}"

    async def _generate_ai_response(
        self, contents: list[types.Content] | str, user_id: str = ""
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
                return response.text or ""

            # Append the model turn that requested tool calls
            candidate_parts = []
            if response.candidates and response.candidates[0].content:
                candidate_parts = response.candidates[0].content.parts
            else:
                candidate_parts = [
                    types.Part(function_call=fc) for fc in function_calls
                ]
            contents_list.append(types.Content(role="model", parts=candidate_parts))

            # Execute tool calls and collect responses
            tool_response_parts = []
            for fc in function_calls:
                args = fc.args if isinstance(fc.args, dict) else {}
                result_str = await self._execute_tool(fc.name, args, user_id)
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

        return response.text or ""

    @commands.command(name="ask", help="Ask Gemini a question or send a voice memo.")
    async def ask_prefix(self, ctx: commands.Context, *, prompt: str = ""):
        if not self.is_user_authorized(ctx.author.id):
            return

        if not self.client:
            await ctx.send("Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.")
            return

        parts = await self._extract_message_parts(ctx.message, is_prefix=True)
        if not parts:
            await ctx.send("Please provide a question, prompt, or audio attachment.")
            return

        user_id_str = str(ctx.author.id)

        # Case 1: Inside an existing thread
        if isinstance(ctx.channel, discord.Thread):
            lock = self._get_thread_lock(ctx.channel.id)
            async with lock:
                self.active_threads.add(ctx.channel.id)
                async with ctx.typing():
                    try:
                        contents = await self._build_thread_contents(ctx.channel)
                        if not contents:
                            contents = [types.Content(role="user", parts=parts)]
                        text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    contents = await self._build_reply_chain_contents(ctx.message)
                if not contents:
                    contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    thread_prompt = prompt.strip() or "Audio Memo"
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

    @app_commands.command(name="ask", description="Send a prompt or audio file to the Gemini API")
    @app_commands.describe(
        prompt="The prompt to send to Gemini",
        audio="Optional audio or voice recording"
    )
    async def ask_slash(
        self,
        interaction: discord.Interaction,
        prompt: str = "",
        audio: discord.Attachment = None,
    ):
        if not self.client:
            await interaction.response.send_message("Gemini API key is not configured.", ephemeral=True)
            return

        parts: list[types.Part] = []
        if audio:
            mime = self._get_audio_mime(audio.filename, audio.content_type)
            if mime:
                try:
                    audio_bytes = await audio.read()
                    if audio_bytes:
                        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime))
                except Exception as e:
                    print(f"Failed to read slash audio attachment: {e}")
                    sentry_sdk.capture_exception(e)

        clean_prompt = prompt.strip()
        if clean_prompt:
            parts.append(types.Part.from_text(text=clean_prompt))

        if not parts:
            await interaction.response.send_message("Please provide a prompt or an audio file.", ephemeral=True)
            return

        user_id_str = str(interaction.user.id)

        # Defer response since Gemini generation takes time
        await interaction.response.defer()

        # Case 1: Inside an existing thread
        if isinstance(interaction.channel, discord.Thread):
            lock = self._get_thread_lock(interaction.channel.id)
            async with lock:
                self.active_threads.add(interaction.channel.id)
                try:
                    contents = await self._build_thread_contents(
                        interaction.channel, additional_parts=parts
                    )
                    if not contents:
                        contents = [types.Content(role="user", parts=parts)]
                    text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                text = await self._generate_ai_response(contents, user_id=user_id_str)
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
            text = await self._generate_ai_response(contents, user_id=user_id_str)
            if not text:
                await interaction.followup.send("Received empty response from Gemini.")
                return
        except Exception as e:
            await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        chunks = split_message(text)
        thread = None
        try:
            starter_label = f"💬 **Question:** {clean_prompt}" if clean_prompt else "🎙️ **Audio Prompt**"
            msg = await interaction.followup.send(starter_label, wait=True)
            if msg and hasattr(msg, "create_thread"):
                thread_title = clean_prompt or "Audio Memo"
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

        # Check if there is text or audio attachments
        has_audio = any(
            self._get_audio_mime(att.filename, att.content_type)
            for att in message.attachments
        )
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

        if not clean_text and not has_audio:
            return

        user_id_str = str(message.author.id)

        # Case 1: Message is inside a thread
        if is_in_thread:
            lock = self._get_thread_lock(message.channel.id)
            async with lock:
                self.active_threads.add(message.channel.id)
                async with message.channel.typing():
                    try:
                        contents = await self._build_channel_contents(message.channel)
                        if not contents:
                            parts = await self._extract_message_parts(message, is_prefix=False)
                            if parts:
                                contents = [types.Content(role="user", parts=parts)]
                        if not contents:
                            return
                        text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    contents = await self._build_channel_contents(message.channel)
                    if not contents:
                        parts = await self._extract_message_parts(message, is_prefix=False)
                        if parts:
                            contents = [types.Content(role="user", parts=parts)]
                    if not contents:
                        return
                    text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    contents = await self._build_reply_chain_contents(message)
                if not contents:
                    parts = await self._extract_message_parts(message, is_prefix=False)
                    if not parts:
                        return
                    contents = [types.Content(role="user", parts=parts)]

                text = await self._generate_ai_response(contents, user_id=user_id_str)
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
                    thread_title = clean_text or "AI Chat"
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
