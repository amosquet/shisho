import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Literal, Optional

import discord
from discord.ext import commands

from utils.db import run_in_executor
from utils.llm import get_gemini_model, set_gemini_model, validate_gemini_model


class Admin(commands.Cog):
    """Administrative commands for bot management."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Only the bot owner can use commands in this cog."""
        return await self.bot.is_owner(ctx.author)

    @commands.command(name="reload")
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, extension: str = "all"):
        """Reloads an extension or all extensions. (Owner only)"""
        if extension.lower() == "all":
            reloaded = []
            errors = []
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py") and filename != "__init__.py":
                    ext_name = filename[:-3]
                    ext_path = f"cogs.{ext_name}"
                    try:
                        await self.bot.reload_extension(ext_path)
                        reloaded.append(ext_name)
                    except Exception as e:
                        errors.append(f"`{ext_name}`: {e}")

            message = ""
            if reloaded:
                message += f"Reloaded: {', '.join(reloaded)}\n"
            if errors:
                message += "Errors:\n" + "\n".join(errors)

            await ctx.send(message or "No extensions found to reload.")
        else:
            try:
                ext_path = (
                    extension if extension.startswith("cogs.") else f"cogs.{extension}"
                )
                await self.bot.reload_extension(ext_path)
                await ctx.send(f"Successfully reloaded `{ext_path}`.")
            except Exception as e:
                await ctx.send(f"Failed to reload `{extension}`: {e}")

    @commands.command(name="load")
    @commands.is_owner()
    async def load(self, ctx: commands.Context, extension: str):
        """Loads an extension. (Owner only)"""
        try:
            ext_path = (
                extension if extension.startswith("cogs.") else f"cogs.{extension}"
            )
            await self.bot.load_extension(ext_path)
            await ctx.send(f"Successfully loaded `{ext_path}`.")
        except Exception as e:
            await ctx.send(f"Failed to load `{extension}`: {e}")

    @commands.command(name="unload")
    @commands.is_owner()
    async def unload(self, ctx: commands.Context, extension: str):
        """Unloads an extension. (Owner only)"""
        try:
            ext_path = (
                extension if extension.startswith("cogs.") else f"cogs.{extension}"
            )
            await self.bot.unload_extension(ext_path)
            await ctx.send(f"Successfully unloaded `{ext_path}`.")
        except Exception as e:
            await ctx.send(f"Failed to unload `{extension}`: {e}")

    @commands.command(name="announce")
    @commands.is_owner()
    async def announce(self, ctx: commands.Context, *, message: str):
        """Creates a new announcement. (Owner only)"""
        def _save_announcement():
            data_dir = "data"
            os.makedirs(data_dir, exist_ok=True)
            filename = os.path.join(data_dir, "announcements.json")
            if not os.path.exists(filename) and os.path.exists("announcements.json"):
                filename = "announcements.json"

            if os.path.exists(filename):
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = []

            next_id = 1
            if data:
                try:
                    next_id = max(int(item["id"]) for item in data) + 1
                except (ValueError, KeyError):
                    next_id = len(data) + 1

            new_announcement = {
                "id": str(next_id),
                "message": message,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            data.append(new_announcement)

            target_file = os.path.join(data_dir, "announcements.json")
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            return next_id

        try:
            next_id = await run_in_executor(_save_announcement)
            await ctx.send(f"✅ Announcement created successfully! ID: {next_id}")
        except Exception as e:
            await ctx.send(f"❌ Failed to create announcement: {e}")

    @commands.command(name="update")
    @commands.is_owner()
    async def update(self, ctx: commands.Context):
        """Pulls changes from GitHub and restarts the bot. (Owner only)"""
        await ctx.send("🔄 Pulling updates and restarting...")
        try:
            await asyncio.create_subprocess_exec("/bin/bash", "./update_shisho.sh")
        except Exception as e:
            await ctx.send(f"❌ Failed to trigger update: {e}")

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
        """Syncs the command tree.
        
        Usage:
          !sync -> global sync
          !sync ~ -> sync current guild
          !sync * -> copies all global app commands to current guild and syncs
          !sync ^ -> clears all commands from the current guild target and syncs (removes guild commands)
          !sync id_1 id_2 -> syncs guilds with id 1 and 2
        """
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(f"Synced {len(synced)} commands {'globally.' if spec is None else 'to the current guild.'}")
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)} guilds.")

    @commands.group(name="model", invoke_without_command=True)
    @commands.is_owner()
    async def model_cmd(self, ctx: commands.Context, *, model_name: Optional[str] = None):
        """Shows or changes the active Gemini AI model. (Owner only)

        Usage:
          !model -> shows current active model
          !model <model_name> -> changes model to <model_name>
          !model set <model_name> -> changes model to <model_name>
        """
        if model_name is None:
            current = get_gemini_model()
            await ctx.send(f"🤖 **Current AI Model:** `{current}`")
            return

        ai_cog = self.bot.get_cog("AIChat")
        client = getattr(ai_cog, "client", None)
        is_valid, canonical, msg = await validate_gemini_model(model_name, client=client)
        if not is_valid:
            await ctx.send(f"❌ {msg}")
            return

        set_gemini_model(canonical)
        await ctx.send(f"✅ **Active AI Model changed to:** `{canonical}` (persisted globally)")

    @model_cmd.command(name="set")
    @commands.is_owner()
    async def model_set(self, ctx: commands.Context, *, model_name: str):
        """Sets the active Gemini AI model. (Owner only)"""
        ai_cog = self.bot.get_cog("AIChat")
        client = getattr(ai_cog, "client", None)
        is_valid, canonical, msg = await validate_gemini_model(model_name, client=client)
        if not is_valid:
            await ctx.send(f"❌ {msg}")
            return

        set_gemini_model(canonical)
        await ctx.send(f"✅ **Active AI Model changed to:** `{canonical}` (persisted globally)")


async def setup(bot):
    await bot.add_cog(Admin(bot))
