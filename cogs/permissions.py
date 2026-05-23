import os

from discord.ext import commands


class Permissions(commands.Cog):
    """Commands for managing bot permissions and whitelists."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Only the bot owner can use commands in this cog."""
        owner_id = int(os.getenv("OWNER_ID", "0"))
        return ctx.author.id == owner_id

    @commands.command(name="whitelist")
    async def manage_whitelist(self, ctx, action: str, cog_name: str, user_id: str):
        """Manages a plugin's whitelist.
        Usage: !whitelist add <cog_name> <user_id>
        Usage: !whitelist remove <cog_name> <user_id>
        Note: This updates the environment for the current session.
        Permanent changes should be made in the .env file.
        """
        action = action.lower()
        cog_name = cog_name.upper()
        env_key = f"WHITELIST_{cog_name}"

        current_whitelist = os.getenv(env_key, "")
        whitelist_ids = [
            uid.strip() for uid in current_whitelist.split(",") if uid.strip()
        ]

        if action == "add":
            if user_id in whitelist_ids:
                await ctx.send(
                    f"User `{user_id}` is already in the `{cog_name}` whitelist."
                )
            else:
                whitelist_ids.append(user_id)
                os.environ[env_key] = ",".join(whitelist_ids)
                await ctx.send(
                    f"Successfully added `{user_id}` to the `{cog_name}` whitelist."
                )

        elif action == "remove":
            if user_id not in whitelist_ids:
                await ctx.send(
                    f"User `{user_id}` is not in the `{cog_name}` whitelist."
                )
            else:
                whitelist_ids.remove(user_id)
                os.environ[env_key] = ",".join(whitelist_ids)
                await ctx.send(
                    f"Successfully removed `{user_id}` from the `{cog_name}` whitelist."
                )

        else:
            await ctx.send("Invalid action. Use `add` or `remove`.")

    @commands.command(name="showwhitelist")
    async def show_whitelist(self, ctx, cog_name: str):
        """Shows the current whitelist for a plugin."""
        cog_name = cog_name.upper()
        env_key = f"WHITELIST_{cog_name}"
        whitelist = os.getenv(env_key, "Empty or not set.")
        await ctx.send(f"**Whitelist for {cog_name}:**\n`{whitelist}`")


async def setup(bot):
    await bot.add_cog(Permissions(bot))
