import os

from discord.ext import commands


class Admin(commands.Cog):
    """Administrative commands for bot management."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Only the bot owner can use commands in this cog."""
        owner_id = int(os.getenv("OWNER_ID", "0"))
        return ctx.author.id == owner_id

    @commands.command(name="reload")
    async def reload(self, ctx, extension: str = "all"):
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
    async def load(self, ctx, extension: str):
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
    async def unload(self, ctx, extension: str):
        """Unloads an extension. (Owner only)"""
        try:
            ext_path = (
                extension if extension.startswith("cogs.") else f"cogs.{extension}"
            )
            await self.bot.unload_extension(ext_path)
            await ctx.send(f"Successfully unloaded `{ext_path}`.")
        except Exception as e:
            await ctx.send(f"Failed to unload `{extension}`: {e}")


async def setup(bot):
    await bot.add_cog(Admin(bot))
