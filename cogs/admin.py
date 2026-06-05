import os
import subprocess

from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands


def is_owner():
    def predicate(interaction: discord.Interaction) -> bool:
        owner_id = int(os.getenv("OWNER_ID", "0"))
        return interaction.user.id == owner_id
    return app_commands.check(predicate)


class Admin(commands.Cog):
    """Administrative commands for bot management."""

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Extra safety: Only the bot owner can use commands in this cog."""
        owner_id = int(os.getenv("OWNER_ID", "0"))
        if interaction.user.id != owner_id:
            raise app_commands.CheckFailure("You do not have permission to use this command.")
        return True

    @app_commands.command(name="reload", description="Reloads an extension or all extensions. (Owner only)")
    @app_commands.describe(extension="The extension to reload (or 'all')")
    @is_owner()
    async def reload(self, interaction: discord.Interaction, extension: str = "all"):
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

            await interaction.response.send_message(message or "No extensions found to reload.")
        else:
            try:
                ext_path = (
                    extension if extension.startswith("cogs.") else f"cogs.{extension}"
                )
                await self.bot.reload_extension(ext_path)
                await interaction.response.send_message(f"Successfully reloaded `{ext_path}`.")
            except Exception as e:
                await interaction.response.send_message(f"Failed to reload `{extension}`: {e}")

    @app_commands.command(name="load", description="Loads an extension. (Owner only)")
    @app_commands.describe(extension="The extension to load")
    @is_owner()
    async def load(self, interaction: discord.Interaction, extension: str):
        try:
            ext_path = (
                extension if extension.startswith("cogs.") else f"cogs.{extension}"
            )
            await self.bot.load_extension(ext_path)
            await interaction.response.send_message(f"Successfully loaded `{ext_path}`.")
        except Exception as e:
            await interaction.response.send_message(f"Failed to load `{extension}`: {e}")

    @app_commands.command(name="unload", description="Unloads an extension. (Owner only)")
    @app_commands.describe(extension="The extension to unload")
    @is_owner()
    async def unload(self, interaction: discord.Interaction, extension: str):
        try:
            ext_path = (
                extension if extension.startswith("cogs.") else f"cogs.{extension}"
            )
            await self.bot.unload_extension(ext_path)
            await interaction.response.send_message(f"Successfully unloaded `{ext_path}`.")
        except Exception as e:
            await interaction.response.send_message(f"Failed to unload `{extension}`: {e}")

    @app_commands.command(name="update", description="Pulls changes from GitHub and restarts the bot. (Owner only)")
    @is_owner()
    async def update(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Pulling updates and restarting...")
        try:
            subprocess.Popen(["/bin/bash", "./update_shisho.sh"])
        except Exception as e:
            # We use followup since we already sent a response
            await interaction.followup.send(f"❌ Failed to trigger update: {e}")

    @app_commands.command(name="sync", description="Syncs the command tree. (Owner only)")
    @app_commands.describe(spec="Sync specification (~ for current guild, * for copy global, ^ for clear)")
    @is_owner()
    async def sync_slash(self, interaction: discord.Interaction, spec: Optional[Literal["~", "*", "^"]] = None):
        if spec == "~":
            if interaction.guild:
                synced = await self.bot.tree.sync(guild=interaction.guild)
                msg = f"Synced {len(synced)} commands to the current guild."
            else:
                msg = "You must be in a guild to sync the current guild."
        elif spec == "*":
            if interaction.guild:
                self.bot.tree.copy_global_to(guild=interaction.guild)
                synced = await self.bot.tree.sync(guild=interaction.guild)
                msg = f"Copied global commands and synced {len(synced)} commands to the current guild."
            else:
                msg = "You must be in a guild to copy global commands."
        elif spec == "^":
            if interaction.guild:
                self.bot.tree.clear_commands(guild=interaction.guild)
                await self.bot.tree.sync(guild=interaction.guild)
                msg = "Cleared all commands from the current guild and synced."
            else:
                msg = "You must be in a guild to clear current guild commands."
        else:
            synced = await self.bot.tree.sync()
            msg = f"Synced {len(synced)} commands globally."

        await interaction.response.send_message(msg, ephemeral=True)

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

            await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}")
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


async def setup(bot):
    await bot.add_cog(Admin(bot))
