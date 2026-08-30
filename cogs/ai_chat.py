import asyncio
import os
import re

import discord
import sentry_sdk
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import errors, types


class AIChat(commands.Cog):
    """General AI Chat command with threaded conversations."""

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
        if os.path.exists(prompt_file):
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                print(f"Failed to read {prompt_file}: {e}")
        return None

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
        self, raw_turns: list[dict[str, str]]
    ) -> list[types.Content]:
        if not raw_turns:
            return []

        # Filter out empty texts
        filtered = [t for t in raw_turns if t.get("text", "").strip()]
        if not filtered:
            return []

        # Merge consecutive turns with the same role
        merged: list[dict[str, str]] = []
        for turn in filtered:
            if merged and merged[-1]["role"] == turn["role"]:
                merged[-1]["text"] += "\n" + turn["text"]
            else:
                merged.append({"role": turn["role"], "text": turn["text"]})

        # Ensure conversation starts with a user turn
        while merged and merged[0]["role"] != "user":
            merged.pop(0)

        # Ensure conversation ends with a user turn
        while merged and merged[-1]["role"] != "user":
            merged.pop()

        if not merged:
            return []

        contents = [
            types.Content(
                role=t["role"], parts=[types.Part.from_text(text=t["text"])]
            )
            for t in merged
        ]
        return contents

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
                        r"\*\*(?:Question|Ask):\*\*\s*(.+)",
                        content,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if match:
                        raw_turns.append({"role": "user", "text": match.group(1).strip()})
                    else:
                        raw_turns.append({"role": "model", "text": content})
                else:
                    clean = re.sub(r"^!ask\s*", "", content, flags=re.IGNORECASE).strip()
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
                        raw_turns.append({"role": "model", "text": content})
                    else:
                        continue
                else:
                    clean = re.sub(r"^!ask\s*", "", content, flags=re.IGNORECASE).strip()
                    if clean:
                        raw_turns.append({"role": "user", "text": clean})
        except Exception as e:
            print(f"Error reading thread history: {e}")

        if additional_prompt:
            clean_add = additional_prompt.strip()
            if clean_add:
                raw_turns.append({"role": "user", "text": clean_add})

        return self._consolidate_turns(raw_turns)

    async def _is_ai_chat_thread(self, thread: discord.Thread) -> bool:
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

    async def _generate_ai_response(
        self, contents: list[types.Content] | str
    ) -> str:
        sys_prompt = self.get_system_instruction()
        config = types.GenerateContentConfig(
            system_instruction=sys_prompt if sys_prompt else None,
            tools=[{"google_search": {}}],
        )

        response = await self.client.aio.models.generate_content(
            model="gemini-3.7-flash", contents=contents, config=config
        )
        return response.text or ""

    @commands.command(name="ask", help="Ask Gemini a question.")
    async def ask_prefix(self, ctx: commands.Context, *, prompt: str):
        if not self.is_user_authorized(ctx.author.id):
            return

        if not self.client:
            await ctx.send("Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.")
            return

        prompt = prompt.strip()
        if not prompt:
            await ctx.send("Please provide a question or prompt.")
            return

        # Case 1: Inside an existing thread
        if isinstance(ctx.channel, discord.Thread):
            lock = self._get_thread_lock(ctx.channel.id)
            async with lock:
                self.active_threads.add(ctx.channel.id)
                async with ctx.typing():
                    try:
                        contents = await self._build_thread_contents(ctx.channel)
                        if not contents:
                            contents = prompt
                        text = await self._generate_ai_response(contents)
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
                    text = await self._generate_ai_response(prompt)
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
                text = await self._generate_ai_response(prompt)
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
                    thread_name = self._generate_thread_name(prompt)
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

    @app_commands.command(name="ask", description="Send a prompt to the Gemini API")
    @app_commands.describe(prompt="The prompt to send to Gemini")
    async def ask_slash(self, interaction: discord.Interaction, prompt: str):
        if not self.client:
            await interaction.response.send_message("Gemini API key is not configured.", ephemeral=True)
            return

        prompt = prompt.strip()
        if not prompt:
            await interaction.response.send_message("Prompt cannot be empty.", ephemeral=True)
            return

        # Defer response since Gemini generation takes time
        await interaction.response.defer()

        # Case 1: Inside an existing thread
        if isinstance(interaction.channel, discord.Thread):
            lock = self._get_thread_lock(interaction.channel.id)
            async with lock:
                self.active_threads.add(interaction.channel.id)
                try:
                    contents = await self._build_thread_contents(
                        interaction.channel, additional_prompt=prompt
                    )
                    if not contents:
                        contents = prompt
                    text = await self._generate_ai_response(contents)
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
                text = await self._generate_ai_response(prompt)
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
            text = await self._generate_ai_response(prompt)
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
            msg = await interaction.followup.send(f"💬 **Question:** {prompt}", wait=True)
            if msg and hasattr(msg, "create_thread"):
                thread_name = self._generate_thread_name(prompt)
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

        # Only handle messages in threads
        if not isinstance(message.channel, discord.Thread):
            return

        # Ignore prefix commands so process_commands handles them
        if message.content.startswith("!"):
            return

        # Verify thread is an active AI chat thread
        if not await self._is_ai_chat_thread(message.channel):
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
                    text = await self._generate_ai_response(contents)
                    if not text:
                        await message.channel.send("Received empty response from Gemini.")
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


async def setup(bot):
    await bot.add_cog(AIChat(bot))
