import asyncio
import json
import os
import re
from datetime import datetime

import discord
import sentry_sdk
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import errors, types

CONCIERGE_PROMPT = """You are an expert Book Concierge. You provide highly specific and thoughtful book recommendations based on the user's queries.
You can answer questions about books, summarize plots, and suggest reading orders.

Keep your responses extremely concise. Do not use conversational filler, pleasantries, or introductory/concluding remarks. Get straight to the point and only provide the requested information or recommendations.

CRITICAL INSTRUCTION:
If your response includes book recommendations or mentions specific books that the user might want to read, you MUST append a JSON block at the very end of your response containing a list of those book titles.

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

        # Get necessary cogs
        bot = interaction.client
        bookinfo_cog = bot.get_cog("BookInfo")
        readinglist_cog = bot.get_cog("ReadingList")

        if not bookinfo_cog or not readinglist_cog:
            await interaction.followup.send(
                "Required systems (BookInfo or ReadingList) are currently offline.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Looking up details for **{selected_title}**...", ephemeral=True
        )

        try:
            # Fetch book details
            book_data = await bookinfo_cog.fetch_book_data(selected_title)

            if isinstance(book_data, str):
                await interaction.followup.send(
                    f"Could not find details for **{selected_title}**: {book_data}",
                    ephemeral=True,
                )
                return

            # Prepare data for reading list
            title = book_data.get("title", selected_title)
            author = ", ".join(book_data.get("authors", ["Unknown Author"]))
            publish_date = book_data.get("publishedDate", "Unknown")
            isbn = "0000000000"  # Placeholder if not found

            status_val = "planned"

            # Add to reading list
            await readinglist_cog.add_book_to_pocketbase(
                str(interaction.user.id),
                title=title,
                author=author,
                status_val=status_val,
                publish_date=publish_date,
                isbn=isbn,
                final_start_date="",
                final_end_date="",
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

        cog_name = "BOOKCONCIERGE"
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
        return title

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
            async for msg in thread.history(limit=50, oldest_first=True):
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

    async def _generate_ai_response(
        self, contents: list[types.Content] | str
    ) -> tuple[str, list[str]]:
        config = types.GenerateContentConfig(
            system_instruction=CONCIERGE_PROMPT, tools=[{"google_search": {}}]
        )

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        response = await self.client.aio.models.generate_content(
            model=model_name, contents=contents, config=config
        )

        text = response.text or ""
        if not text:
            return "", []

        return self.extract_book_titles(text)

    async def build_personalized_query(self, user_id: str) -> str:
        reading_list_cog = self.bot.get_cog("ReadingList")
        if reading_list_cog:
            books = await reading_list_cog.fetch_reading_list(user_id)
            past_books = [
                f"- {b['title']} by {b['author']} (Status: {b['status']})"
                for b in books
                if b.get("status") in ["read", "reading"]
            ]
            if past_books:
                books_str = "\n".join(past_books[:30])  # limit to 30 recent books
                return (
                    f"I am looking for book recommendations tailored to my tastes. Here are some books I have read or am currently reading:\n"
                    f"{books_str}\n\n"
                    f"Please recommend some new books for me that I might like based on this list!"
                )
        return "Please recommend some good books for me!"

    async def process_query(self, query: str, user_id: str) -> tuple[str, list[str]]:
        if not self.client:
            return (
                "Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.",
                [],
            )

        actual_query = query.strip() if query else ""
        if not actual_query:
            actual_query = await self.build_personalized_query(user_id)

        try:
            return await self._generate_ai_response(actual_query)
        except errors.APIError as e:
            error_msg = str(e)
            if "high demand" in error_msg.lower() or "503" in error_msg:
                return "Gemini is currently experiencing high demand. Please try again later.", []
            return f"API Error: {error_msg}", []
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"An unexpected error occurred: {str(e)}", []

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
                            contents = query_str or await self.build_personalized_query(str(ctx.author.id))
                        clean_text, titles = await self._generate_ai_response(contents)
                        if not clean_text:
                            await ctx.send("Received empty response from the Concierge.")
                            return
                        view = ConciergeView(titles) if titles else None
                        chunks = self._split_message(clean_text)
                        for i, chunk in enumerate(chunks):
                            if i == len(chunks) - 1 and view:
                                await ctx.send(chunk, view=view)
                            else:
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
                    contents = query_str or await self.build_personalized_query(str(ctx.author.id))
                    clean_text, titles = await self._generate_ai_response(contents)
                    if not clean_text:
                        await ctx.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = self._split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await ctx.send(chunk, view=view)
                        else:
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
                contents = query_str or await self.build_personalized_query(str(ctx.author.id))
                clean_text, titles = await self._generate_ai_response(contents)
                if not clean_text:
                    await ctx.send("Received empty response from the Concierge.")
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

            chunks = self._split_message(clean_text)
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
                        contents = query_str or await self.build_personalized_query(str(interaction.user.id))
                    clean_text, titles = await self._generate_ai_response(contents)
                    if not clean_text:
                        await interaction.followup.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = self._split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await interaction.followup.send(chunk, view=view)
                        else:
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
                contents = query_str or await self.build_personalized_query(str(interaction.user.id))
                clean_text, titles = await self._generate_ai_response(contents)
                if not clean_text:
                    await interaction.followup.send("Received empty response from the Concierge.")
                    return
                view = ConciergeView(titles) if titles else None
                chunks = self._split_message(clean_text)
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1 and view:
                        await interaction.followup.send(chunk, view=view)
                    else:
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
            contents = query_str or await self.build_personalized_query(str(interaction.user.id))
            clean_text, titles = await self._generate_ai_response(contents)
            if not clean_text:
                await interaction.followup.send("Received empty response from the Concierge.")
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

        chunks = self._split_message(clean_text)
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

        lock = self._get_thread_lock(message.channel.id)
        async with lock:
            async with message.channel.typing():
                try:
                    contents = await self._build_thread_contents(message.channel)
                    if not contents:
                        return
                    clean_text, titles = await self._generate_ai_response(contents)
                    if not clean_text:
                        await message.channel.send("Received empty response from the Concierge.")
                        return
                    view = ConciergeView(titles) if titles else None
                    chunks = self._split_message(clean_text)
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and view:
                            await message.channel.send(chunk, view=view)
                        else:
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


async def setup(bot):
    await bot.add_cog(BookConcierge(bot))
