import asyncio
import json
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

CONCIERGE_PROMPT = """You are an expert Book Concierge. You provide highly specific and thoughtful book recommendations based on the user's queries.
You can answer questions about books, summarize plots, and suggest reading orders.

Keep your responses extremely concise. Do not use conversational filler, pleasantries, or introductory/concluding remarks. Get straight to the point and only provide the requested information or recommendations.

CRITICAL RULES:
1. EXCLUSION RULE: You MUST NEVER recommend any book that is already on the user's reading list, regardless of status (read, reading, planned, dropped). Every recommended book MUST be a new title not found on the reading list.
2. If your response includes book recommendations or mentions specific books that the user might want to read, you MUST append a JSON block at the very end of your response containing a list of those book titles.

The JSON block must be formatted EXACTLY like this:
```json
[
  "Book Title 1",
  "Book Title 2"
]
```
Ensure it is a valid JSON array of strings. Do not include authors or any other information in this JSON array, just the plain titles.
If you are NOT recommending any books (e.g., just answering a general question), do NOT include the JSON block.
"""


class BookSelect(discord.ui.Select):
    def __init__(self, book_titles: list[str]):
        # Options must be limited to 25 items and labels max 100 chars
        options = []
        for title in book_titles[:25]:
            label = title[:100]
            options.append(
                discord.SelectOption(label=label, description="Add to Reading List")
            )

        super().__init__(
            placeholder="Select a book to instantly add it to your Planned list...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_title = self.values[0]

        # Get necessary cog
        readinglist_cog = interaction.client.get_cog("ReadingList")

        if not readinglist_cog:
            await interaction.followup.send(
                "Required system (ReadingList) is currently offline.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Looking up details for **{selected_title}**...", ephemeral=True
        )

        try:
            from utils import google_books

            api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
            book_data = None
            if api_key:
                book_data = await google_books.fetch_book_data(selected_title, api_key)

            if isinstance(book_data, dict):
                title = book_data.get("title", selected_title)
                authors = book_data.get("authors", ["Unknown Author"])
                author = ", ".join(authors) if isinstance(authors, list) else str(authors)
                publish_date = book_data.get("publishedDate", "")
                isbn = book_data.get("isbn", "")
                image_url = book_data.get("thumbnail", "")
                desc = book_data.get("description", "")
                description = desc if desc != "No description available." else ""
            else:
                title = selected_title
                author = "Unknown Author"
                publish_date = ""
                isbn = ""
                image_url = ""
                description = ""

            cover_filename = None
            cover_data = None
            if image_url:
                cover_filename, cover_data = await google_books.download_image(image_url)

            status_val = "planned"

            # Add to reading list
            await readinglist_cog.add_book_to_pocketbase(
                discord_id=str(interaction.user.id),
                title=title,
                author=author,
                status_val=status_val,
                publish_date=publish_date if publish_date != "Unknown" else "",
                isbn=isbn,
                final_start_date="",
                final_end_date="",
                image_url=image_url,
                description=description,
                cover_filename=cover_filename,
                cover_data=cover_data,
            )

            await interaction.followup.send(
                f"✅ Successfully added **{title}** by {author} to your Planned reading list!",
                ephemeral=True,
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                f"❌ An error occurred while adding the book: {e}", ephemeral=True
            )


class ConciergeView(discord.ui.View):
    def __init__(self, book_titles: list[str]):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.add_item(BookSelect(book_titles))


class BookConcierge(commands.Cog):
    """AI Book Concierge for recommendations with threaded conversations."""

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
        return is_user_authorized(user_id, "BookConcierge")

    def _generate_thread_name(self, prompt: str) -> str:
        clean = " ".join(prompt.split())
        prefix = "Recommend: "
        max_len = 100 - len(prefix)
        if clean:
            if len(clean) > max_len:
                title = prefix + clean[: max_len - 3] + "..."
            else:
                title = prefix + clean
        else:
            title = "Recommend: Book Recommendations"
    def extract_book_titles(self, text: str) -> tuple[str, list[str]]:
        """Extracts the JSON block of book titles from the response if present."""
        titles = []
        clean_text = text

        # Look for the last JSON code block
        matches = list(re.finditer(
            r"```json\s*(\[.*?\])\s*```", text, re.DOTALL | re.IGNORECASE
        ))
        if matches:
            match = matches[-1]
            json_str = match.group(1)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, list):
                    titles = [str(item) for item in parsed if isinstance(item, str)]
                # Remove the JSON block from the text shown to the user
                clean_text = text[: match.start()].strip()
            except json.JSONDecodeError:
                pass

        return clean_text, titles

    def _consolidate_turns(
        self, raw_turns: list[dict[str, str]]
    ) -> list[types.Content]:
        if not raw_turns:
            return []

        filtered = [t for t in raw_turns if t.get("text", "").strip()]
        if not filtered:
            return []

        merged: list[dict[str, str]] = []
        for turn in filtered:
            if merged and merged[-1]["role"] == turn["role"]:
                merged[-1]["text"] += "\n" + turn["text"]
            else:
                merged.append({"role": turn["role"], "text": turn["text"]})

        while merged and merged[0]["role"] != "user":
            merged.pop(0)

        while merged and merged[-1]["role"] != "user":
            merged.pop()

        if not merged:
            return []

        return [
            types.Content(
                role=t["role"], parts=[types.Part.from_text(text=t["text"])]
            )
            for t in merged
        ]

    async def _build_thread_contents(
        self, thread: discord.Thread, additional_prompt: str | None = None
    ) -> list[types.Content]:
        raw_turns: list[dict[str, str]] = []

        starter_msg = thread.starter_message
        if not starter_msg and thread.parent and hasattr(thread.parent, "fetch_message"):
            try:
                starter_msg = await thread.parent.fetch_message(thread.id)
            except Exception:
                starter_msg = None

        if starter_msg:
            content = starter_msg.clean_content.strip()
            if content:
                if starter_msg.author.id == self.bot.user.id:
                    match = re.search(
                        r"\*\*(?:Recommendations?):\*\*\s*(.+)",
                        content,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if match:
                        raw_turns.append({"role": "user", "text": match.group(1).strip()})
                    elif "Book Recommendations" in content:
                        raw_turns.append({"role": "user", "text": "Please recommend some good books based on my reading list."})
                    else:
                        clean_text, _ = self.extract_book_titles(content)
                        raw_turns.append({"role": "model", "text": clean_text})
                else:
                    clean = re.sub(r"^!recommend\s*", "", content, flags=re.IGNORECASE).strip()
                    if clean:
                        raw_turns.append({"role": "user", "text": clean})

        try:
            recent_msgs = [m async for m in thread.history(limit=30)]
            recent_msgs.reverse()
            for msg in recent_msgs:
                content = msg.clean_content.strip()
                if not content:
                    continue

                if msg.author.bot:
                    if msg.author.id == self.bot.user.id:
                        if (
                            content.startswith("Gemini API key is not configured")
                            or content.startswith("API Error:")
                            or content.startswith("An unexpected error occurred")
                            or content.startswith("Gemini is currently experiencing high demand")
                        ):
                            continue
                        clean_text, _ = self.extract_book_titles(content)
                        raw_turns.append({"role": "model", "text": clean_text})
                    else:
                        continue
                else:
                    clean = re.sub(r"^!recommend\s*", "", content, flags=re.IGNORECASE).strip()
                    if clean:
                        raw_turns.append({"role": "user", "text": clean})
        except Exception as e:
            print(f"Error reading thread history: {e}")

        if additional_prompt:
            clean_add = additional_prompt.strip()
            if clean_add:
                raw_turns.append({"role": "user", "text": clean_add})

        return self._consolidate_turns(raw_turns)

    async def _is_concierge_thread(self, thread: discord.Thread) -> bool:
        if thread.id in self.active_threads:
            return True

        if thread.name.startswith("Recommend: ") or thread.name.startswith("Recommendations: "):
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
                self.active_threads.add(thread.id)
                return True

        return False

    @staticmethod
    def _normalize_title(title: str) -> str:
        t = title.lower().strip()
        t = re.sub(r"^(the|a|an)\s+", "", t)
        t = re.sub(r"[^\w\s]", "", t)
        return " ".join(t.split())

    def _is_book_on_list(self, candidate_title: str, user_books: list[dict]) -> bool:
        norm_candidate = self._normalize_title(candidate_title)
        if not norm_candidate:
            return False

        # If candidate title has subtitle separated by colon or dash
        candidate_main = re.split(r"[:\-–—]", candidate_title)[0]
        norm_candidate_main = self._normalize_title(candidate_main)

        for b in user_books:
            existing_title = b.get("title", "")
            norm_existing = self._normalize_title(existing_title)
            if not norm_existing:
                continue

            if norm_candidate == norm_existing:
                return True

            existing_main = re.split(r"[:\-–—]", existing_title)[0]
            norm_existing_main = self._normalize_title(existing_main)

            if (
                norm_candidate_main
                and norm_existing_main
                and norm_candidate_main == norm_existing_main
            ):
                return True

            if norm_candidate_main and norm_candidate_main == norm_existing:
                return True

            if norm_existing_main and norm_existing_main == norm_candidate:
                return True

        return False

    def _filter_out_existing_books(
        self, titles: list[str], user_books: list[dict]
    ) -> list[str]:
        return [t for t in titles if not self._is_book_on_list(t, user_books)]

    def _format_reading_list_context(self, books: list[dict]) -> str:
        if not books:
            return ""
        lines = []
        for b in books:
            title = b.get("title", "Unknown Title")
            author = b.get("author", "Unknown Author")
            status = b.get("status", "unknown")
            lines.append(f"- {title} by {author} (Status: {status})")
        return "\n".join(lines)

    def _get_system_instruction(self, user_books: list[dict] | None = None) -> str:
        prompt = CONCIERGE_PROMPT
        if user_books:
            formatted_list = self._format_reading_list_context(user_books)
            if formatted_list:
                prompt += (
                    f"\nUSER'S CURRENT READING LIST (DO NOT RECOMMEND ANY OF THESE TITLES):\n"
                    f"{formatted_list}\n"
                )
        return prompt

    async def _generate_ai_response(
        self,
        contents: list[types.Content] | str,
        user_id: str = "",
        user_books: list[dict] | None = None,
    ) -> tuple[str, list[str]]:
        if user_books is None and user_id:
            reading_list_cog = self.bot.get_cog("ReadingList")
            if reading_list_cog:
                try:
                    user_books = await reading_list_cog.fetch_reading_list(user_id)
                except Exception as e:
                    print(f"Failed to fetch reading list for user {user_id}: {e}")
                    user_books = []
            else:
                user_books = []

        sys_prompt = self._get_system_instruction(user_books)
        config = types.GenerateContentConfig(
            system_instruction=sys_prompt, tools=[{"google_search": {}}]
        )

        model_name = get_gemini_model()
        response = await generate_content_with_retry(
            self.client,
            model=model_name,
            contents=contents,
            config=config,
        )

        text = response.text or ""
        if not text:
            return "", []

        clean_text, titles = self.extract_book_titles(text)
        if user_books and titles:
            titles = self._filter_out_existing_books(titles, user_books)
        return clean_text, titles

    async def build_personalized_query(
        self, user_id: str, user_books: list[dict] | None = None
    ) -> str:
        if user_books is None:
            reading_list_cog = self.bot.get_cog("ReadingList")
            if reading_list_cog:
                try:
                    user_books = await reading_list_cog.fetch_reading_list(user_id)
                except Exception as e:
                    print(f"Failed to fetch reading list for user {user_id}: {e}")
                    user_books = []
            else:
                user_books = []

        if user_books:
            past_books = [
                f"- {b['title']} by {b['author']} (Status: {b['status']})"
                for b in user_books
                if b.get("status") in ["read", "reading", "planned"]
            ]
            if past_books:
                books_str = "\n".join(past_books[::-1][:30])  # limit to 30 most recent books
                return (
                    f"I am looking for book recommendations tailored to my tastes. Here are some books from my reading list for reference:\n"
                    f"{books_str}\n\n"
                    f"Please recommend some new books for me that I might like based on this list, but do NOT recommend any book that is already on my reading list!"
                )
        return "Please recommend some good books for me! (Do not recommend any books that are already on my reading list.)"

    async def process_query(self, query: str, user_id: str) -> tuple[str, list[str]]:
        if not self.client:
            return (
                "Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.",
                [],
            )

        reading_list_cog = self.bot.get_cog("ReadingList")
        user_books = await reading_list_cog.fetch_reading_list(user_id) if reading_list_cog else []

        actual_query = query.strip() if query else ""
        if not actual_query:
            actual_query = await self.build_personalized_query(user_id, user_books=user_books)

        try:
            return await self._generate_ai_response(
                actual_query, user_id=user_id, user_books=user_books
            )
        except Exception as e:
            return format_gemini_error(e, include_details=True), []

    @commands.command(
        name="recommend",
        help="Ask the AI Book Concierge for book recommendations.",
    )
    async def recommend_prefix(self, ctx: commands.Context, *, query: str = ""):
        if not self.is_user_authorized(ctx.author.id):
            return

        if not self.client:
            await ctx.send("Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.")
            return

        user_id_str = str(ctx.author.id)
        reading_list_cog = self.bot.get_cog("ReadingList")
        user_books = await reading_list_cog.fetch_reading_list(user_id_str) if reading_list_cog else []

        query_str = query.strip() if query else ""

        # Case 1: Inside an existing thread
        if isinstance(ctx.channel, discord.Thread):
            lock = self._get_thread_lock(ctx.channel.id)
            async with lock:
                self.active_threads.add(ctx.channel.id)
                async with ctx.typing():
                    try:
                        contents = await self._build_thread_contents(ctx.channel, additional_prompt=query_str or None)
                        if not contents:
                            contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
                        clean_text, titles = await self._generate_ai_response(
                            contents, user_id=user_id_str, user_books=user_books
                        )
                        if not clean_text:
                            await ctx.send("Received empty response from the Concierge.")
                            return
                        view = ConciergeView(titles) if titles else None
                        chunks = split_message(clean_text)
                        for i, chunk in enumerate(chunks):
                            if i == len(chunks) - 1 and view:
                                await ctx.send(chunk, view=view)
                            else:
                                await ctx.send(chunk)
                    except Exception as e:
                        await ctx.send(format_gemini_error(e, include_details=False))
            return

        # Case 2: Direct Message (Threads not supported in DMs)
        if isinstance(ctx.channel, discord.DMChannel):
            async with ctx.typing():
                try:
                    contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
                    clean_text, titles = await self._generate_ai_response(
                        contents, user_id=user_id_str, user_books=user_books
                    )
                    if not clean_text:
                        await ctx.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await ctx.send(chunk, view=view)
                        else:
                            await ctx.send(chunk)
                except Exception as e:
                    await ctx.send(format_gemini_error(e, include_details=False))
            return

        # Case 3: Guild Text Channel (Create a thread)
        async with ctx.typing():
            try:
                contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
                clean_text, titles = await self._generate_ai_response(
                    contents, user_id=user_id_str, user_books=user_books
                )
                if not clean_text:
                    await ctx.send("Received empty response from the Concierge.")
                    return
            except Exception as e:
                await ctx.send(format_gemini_error(e, include_details=False))
                return

            chunks = split_message(clean_text)
            view = ConciergeView(titles) if titles else None
            thread = None
            if hasattr(ctx.message, "create_thread"):
                try:
                    thread_name = self._generate_thread_name(query_str)
                    thread = await ctx.message.create_thread(name=thread_name, auto_archive_duration=1440)
                    self.active_threads.add(thread.id)
                except Exception as e:
                    print(f"Failed to create thread for recommend prefix command: {e}")
                    thread = None

            target = thread or ctx
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1 and view:
                    await target.send(chunk, view=view)
                else:
                    await target.send(chunk)

    @app_commands.command(
        name="recommend", description="Ask the AI Book Concierge for recommendations"
    )
    @app_commands.describe(
        query="Your query (leave blank for personalized recommendations)"
    )
    async def recommend_slash(
        self, interaction: discord.Interaction, query: str | None = None
    ):
        await self._handle_slash_command(interaction, query)

    async def _handle_slash_command(
        self, interaction: discord.Interaction, query: str | None
    ):
        if not self.client:
            await interaction.response.send_message("Gemini API key is not configured.", ephemeral=True)
            return

        user_id_str = str(interaction.user.id)
        reading_list_cog = self.bot.get_cog("ReadingList")
        user_books = await reading_list_cog.fetch_reading_list(user_id_str) if reading_list_cog else []

        query_str = query.strip() if query else ""

        await interaction.response.defer()

        # Case 1: Inside an existing thread
        if isinstance(interaction.channel, discord.Thread):
            lock = self._get_thread_lock(interaction.channel.id)
            async with lock:
                self.active_threads.add(interaction.channel.id)
                try:
                    contents = await self._build_thread_contents(
                        interaction.channel, additional_prompt=query_str or None
                    )
                    if not contents:
                        contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
                    clean_text, titles = await self._generate_ai_response(
                        contents, user_id=user_id_str, user_books=user_books
                    )
                    if not clean_text:
                        await interaction.followup.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await interaction.followup.send(chunk, view=view)
                        else:
                            await interaction.followup.send(chunk)
                except Exception as e:
                    await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        # Case 2: DM Channel (no threads)
        if isinstance(interaction.channel, discord.DMChannel):
            try:
                contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
                clean_text, titles = await self._generate_ai_response(
                    contents, user_id=user_id_str, user_books=user_books
                )
                if not clean_text:
                    await interaction.followup.send("Received empty response from the Concierge.")
                    return
                view = ConciergeView(titles) if titles else None
                chunks = split_message(clean_text)
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1 and view:
                        await interaction.followup.send(chunk, view=view)
                    else:
                        await interaction.followup.send(chunk)
            except Exception as e:
                await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        # Case 3: Guild Text Channel (Create a thread)
        try:
            contents = query_str or await self.build_personalized_query(user_id_str, user_books=user_books)
            clean_text, titles = await self._generate_ai_response(
                contents, user_id=user_id_str, user_books=user_books
            )
            if not clean_text:
                await interaction.followup.send("Received empty response from the Concierge.")
                return
        except Exception as e:
            await interaction.followup.send(format_gemini_error(e, include_details=True))
            return

        chunks = split_message(clean_text)
        view = ConciergeView(titles) if titles else None
        thread = None
        try:
            starter_content = f"📚 **Recommendations:** {query_str}" if query_str else "📚 **Book Recommendations**"
            msg = await interaction.followup.send(starter_content, wait=True)
            if msg and hasattr(msg, "create_thread"):
                thread_name = self._generate_thread_name(query_str)
                thread = await msg.create_thread(name=thread_name, auto_archive_duration=1440)
                self.active_threads.add(thread.id)
        except Exception as e:
            print(f"Failed to create thread for slash recommend command: {e}")
            thread = None

        if thread:
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1 and view:
                    await thread.send(chunk, view=view)
                else:
                    await thread.send(chunk)
        else:
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1 and view:
                    await interaction.followup.send(chunk, view=view)
                else:
                    await interaction.followup.send(chunk)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Only handle messages in threads
        if not isinstance(message.channel, discord.Thread):
            return

        # Ignore prefix commands so process_commands handles them
        if message.content.startswith("!"):
            return

        # Verify thread is an active Concierge thread
        if not await self._is_concierge_thread(message.channel):
            return

        # Verify user authorization
        if not self.is_user_authorized(message.author.id):
            return

        if not self.client:
            return

        content = message.clean_content.strip()
        if not content:
            return

        user_id_str = str(message.author.id)
        reading_list_cog = self.bot.get_cog("ReadingList")
        user_books = await reading_list_cog.fetch_reading_list(user_id_str) if reading_list_cog else []

        lock = self._get_thread_lock(message.channel.id)
        async with lock:
            async with message.channel.typing():
                try:
                    contents = await self._build_thread_contents(message.channel)
                    if not contents:
                        return
                    clean_text, titles = await self._generate_ai_response(
                        contents, user_id=user_id_str, user_books=user_books
                    )
                    if not clean_text:
                        await message.channel.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await message.channel.send(chunk, view=view)
                        else:
                            await message.channel.send(chunk)
                except Exception as e:
                    await message.channel.send(format_gemini_error(e, include_details=False))


async def setup(bot):
    await bot.add_cog(BookConcierge(bot))
