import asyncio
import mimetypes
import os
import re
from datetime import datetime

import discord
import sentry_sdk
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import errors, types

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
    description="Retrieves the user's reading list from PocketBase, including book titles, authors, and statuses (planned, reading, read, dropped). Use this whenever the user asks about their reading list or books they have saved/read.",
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
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
        self.active_threads: set[int] = set()
        self._thread_locks: dict[int, asyncio.Lock] = {}

    def _get_thread_lock(self, thread_id: int) -> asyncio.Lock:
        if thread_id not in self._thread_locks:
            self._thread_locks[thread_id] = asyncio.Lock()
        return self._thread_locks[thread_id]

    def is_user_authorized(self, user_id: int) -> bool:
        owner_id = int(os.getenv("OWNER_ID", "0"))
        if owner_id and user_id == owner_id:
            return True

        cog_name = "AICHAT"
        if os.getenv(f"WHITELIST_ENABLE_{cog_name}", "").lower() == "false":
            return True

        whitelist_env = os.getenv(f"WHITELIST_{cog_name}", "")
        if whitelist_env:
            whitelist = [
                int(uid.strip())
                for uid in whitelist_env.split(",")
                if uid.strip().isdigit()
            ]
            return user_id in whitelist

        return not owner_id

    def get_system_instruction(self) -> str | None:
        prompt_file = "gemini_prompt.txt"
        base_prompt = ""
        if os.path.exists(prompt_file):
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    base_prompt = f.read().strip()
            except Exception as e:
                print(f"Failed to read {prompt_file}: {e}")

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
            "2. When the user asks for book recommendations, provide creative, engaging book recommendations directly (you may call `get_reading_list` to check their reading history first).\n"
            "3. For general conversation or greetings (like 'hello'), chat naturally in your sarcastic, intelligent Shisho persona without giving unsolicited status updates or asking what to update.\n"
            "4. When @mentioned in a server channel, if the mention is merely ambient chatter talking about you to someone else without asking for help, reply ONLY with '[NO_ACTION]'.\n"
            "5. If given an audio recording or voice memo without explicit instructions, transcribe/summarize it and save it with `add_note`."
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
        if is_prefix:
            content = re.sub(r"^!ask\s*", "", content, flags=re.IGNORECASE).strip()

        # Remove bot mention from content if present
        if self.bot.user:
            bot_name = getattr(self.bot.user, "name", "")
            if isinstance(bot_name, str) and bot_name:
                content = re.sub(rf"@{re.escape(bot_name)}\b", "", content, flags=re.IGNORECASE).strip()
            guild = getattr(message, "guild", None)
            me = getattr(guild, "me", None) if guild else None
            nick = getattr(me, "nick", None) if me else None
            if isinstance(nick, str) and nick:
                content = re.sub(rf"@{re.escape(nick)}\b", "", content, flags=re.IGNORECASE).strip()
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

    def _split_message(self, text: str, max_len: int = 1990) -> list[str]:
        if not text:
            return []
        if len(text) <= max_len:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break
            split_idx = remaining.rfind("\n", 0, max_len)
            if split_idx == -1 or split_idx < max_len // 2:
                split_idx = remaining.rfind(" ", 0, max_len)
            if split_idx == -1 or split_idx < max_len // 2:
                split_idx = max_len
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip()
        return [c for c in chunks if c]

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

        # Ensure conversation starts with a user turn
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

                # Enrich via Google Books if needed
                if isbn and (not title or not author):
                    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
                    if api_key:
                        from utils import google_books
                        book_data = await google_books.fetch_book_data(isbn, api_key)
                        if isinstance(book_data, dict):
                            title = title or book_data.get("title", "")
                            authors = book_data.get("authors", [])
                            author = author or ", ".join(authors)
                            publish_date = publish_date or book_data.get("publishedDate", "")

                today = datetime.now().strftime("%Y-%m-%d")
                final_start = today if status in ["read", "reading"] else ""
                final_end = today if status == "read" else ""

                await reading_list_cog.add_book_to_pocketbase(
                    discord_id=user_id,
                    title=title or "Unknown Title",
                    author=author or "Unknown Author",
                    status_val=status if status in ["planned", "reading", "read", "dropped"] else "planned",
                    publish_date=publish_date,
                    isbn=isbn,
                    final_start_date=final_start,
                    final_end_date=final_end,
                )
                return f"Successfully added '{title}' by {author or 'Unknown Author'} (status: {status}) to the reading list."

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

        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        max_tool_turns = 5
        for _ in range(max_tool_turns):
            response = await self.client.aio.models.generate_content(
                model=model_name, contents=contents_list, config=config
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
                        for chunk in self._split_message(text):
                            await ctx.send(chunk)
                    except errors.APIError as e:
                        sentry_sdk.capture_exception(e)
                        error_msg = str(e)
                        if "high demand" in error_msg.lower() or "503" in error_msg:
                            await ctx.send("Gemini is currently experiencing high demand. Please try again later.")
                        else:
                            await ctx.send("An error occurred while communicating with the API.")
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        await ctx.send("An unexpected error occurred.")
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
                    for chunk in self._split_message(text):
                        await ctx.send(chunk)
                except errors.APIError as e:
                    sentry_sdk.capture_exception(e)
                    error_msg = str(e)
                    if "high demand" in error_msg.lower() or "503" in error_msg:
                        await ctx.send("Gemini is currently experiencing high demand. Please try again later.")
                    else:
                        await ctx.send("An error occurred while communicating with the API.")
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    await ctx.send("An unexpected error occurred.")
            return

        # Case 3: Guild Text Channel (Create a thread)
        async with ctx.typing():
            try:
                contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(contents, user_id=user_id_str)
                if not text:
                    await ctx.send("Received empty response from Gemini.")
                    return
            except errors.APIError as e:
                sentry_sdk.capture_exception(e)
                error_msg = str(e)
                if "high demand" in error_msg.lower() or "503" in error_msg:
                    await ctx.send("Gemini is currently experiencing high demand. Please try again later.")
                else:
                    await ctx.send("An error occurred while communicating with the API.")
                return
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await ctx.send("An unexpected error occurred.")
                return

            chunks = self._split_message(text)
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
                    chunks = self._split_message(text)
                    for chunk in chunks:
                        await interaction.followup.send(chunk)
                except errors.APIError as e:
                    sentry_sdk.capture_exception(e)
                    error_msg = str(e)
                    if "high demand" in error_msg.lower() or "503" in error_msg:
                        await interaction.followup.send("Gemini is currently experiencing high demand. Please try again later.")
                    else:
                        await interaction.followup.send(f"API Error: {error_msg}")
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    await interaction.followup.send(f"An unexpected error occurred: {str(e)}")
            return

        # Case 2: DM Channel (no threads)
        if isinstance(interaction.channel, discord.DMChannel):
            try:
                contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(contents, user_id=user_id_str)
                if not text:
                    await interaction.followup.send("Received empty response from Gemini.")
                    return
                chunks = self._split_message(text)
                for chunk in chunks:
                    await interaction.followup.send(chunk)
            except errors.APIError as e:
                sentry_sdk.capture_exception(e)
                error_msg = str(e)
                if "high demand" in error_msg.lower() or "503" in error_msg:
                    await interaction.followup.send("Gemini is currently experiencing high demand. Please try again later.")
                else:
                    await interaction.followup.send(f"API Error: {error_msg}")
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await interaction.followup.send(f"An unexpected error occurred: {str(e)}")
            return

        # Case 3: Guild Text Channel (Create a thread)
        try:
            contents = [types.Content(role="user", parts=parts)]
            text = await self._generate_ai_response(contents, user_id=user_id_str)
            if not text:
                await interaction.followup.send("Received empty response from Gemini.")
                return
        except errors.APIError as e:
            sentry_sdk.capture_exception(e)
            error_msg = str(e)
            if "high demand" in error_msg.lower() or "503" in error_msg:
                await interaction.followup.send("Gemini is currently experiencing high demand. Please try again later.")
            else:
                await interaction.followup.send(f"API Error: {error_msg}")
            return
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An unexpected error occurred: {str(e)}")
            return

        chunks = self._split_message(text)
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

        is_bot_mentioned = self.bot.user and any(m.id == self.bot.user.id for m in message.mentions)
        is_in_thread = isinstance(message.channel, discord.Thread)
        is_dm = isinstance(message.channel, discord.DMChannel)

        # We respond if:
        # 1. The message is in DMs (talking directly to the bot), OR
        # 2. The bot was @mentioned anywhere, OR
        # 3. The message is inside an active AI chat thread
        if not is_dm and not is_bot_mentioned:
            if not is_in_thread:
                return
            if not await self._is_ai_chat_thread(message.channel):
                return
        else:
            # If in thread and bot was mentioned, check it's not a concierge thread
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
                clean_text = re.sub(rf"@{re.escape(bot_name)}\b", "", clean_text, flags=re.IGNORECASE).strip()
            guild = getattr(message, "guild", None)
            me = getattr(guild, "me", None) if guild else None
            nick = getattr(me, "nick", None) if me else None
            if isinstance(nick, str) and nick:
                clean_text = re.sub(rf"@{re.escape(nick)}\b", "", clean_text, flags=re.IGNORECASE).strip()
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
                        chunks = self._split_message(text)
                        for chunk in chunks:
                            await message.channel.send(chunk)
                    except errors.APIError as e:
                        sentry_sdk.capture_exception(e)
                        error_msg = str(e)
                        if "high demand" in error_msg.lower() or "503" in error_msg:
                            await message.channel.send("Gemini is currently experiencing high demand. Please try again later.")
                        else:
                            await message.channel.send("An error occurred while communicating with the API.")
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        await message.channel.send("An unexpected error occurred.")
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
                    chunks = self._split_message(text)
                    for chunk in chunks:
                        await message.channel.send(chunk)
                except errors.APIError as e:
                    sentry_sdk.capture_exception(e)
                    error_msg = str(e)
                    if "high demand" in error_msg.lower() or "503" in error_msg:
                        await message.channel.send("Gemini is currently experiencing high demand. Please try again later.")
                    else:
                        await message.channel.send("An error occurred while communicating with the API.")
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    await message.channel.send("An unexpected error occurred.")
            return

        # Case 3: Message is in a Guild Text Channel
        async with message.channel.typing():
            try:
                parts = await self._extract_message_parts(message, is_prefix=False)
                if not parts:
                    return
                contents = [types.Content(role="user", parts=parts)]
                text = await self._generate_ai_response(contents, user_id=user_id_str)
                if not text or text.strip() == "[NO_ACTION]":
                    return
            except errors.APIError as e:
                sentry_sdk.capture_exception(e)
                error_msg = str(e)
                if "high demand" in error_msg.lower() or "503" in error_msg:
                    await message.channel.send("Gemini is currently experiencing high demand. Please try again later.")
                else:
                    await message.channel.send("An error occurred while communicating with the API.")
                return
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await message.channel.send("An unexpected error occurred.")
                return

            chunks = self._split_message(text)
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
