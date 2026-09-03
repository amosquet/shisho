import discord
from discord.ext import commands

from utils.discord_helpers import (
    async_add_user_to_whitelist,
    async_get_cog_whitelist,
    async_remove_user_from_whitelist,
)


class Permissions(commands.Cog):
    """Commands for managing bot permissions and whitelists."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Only the bot owner can use commands in this cog."""
        return await self.bot.is_owner(ctx.author)

    @commands.command(name="whitelist")
    @commands.is_owner()
    async def whitelist(
        self,
        ctx: commands.Context,
        action: str,
        cog_name: str,
        user_id: str,
    ):
        """Manages a plugin's whitelist. (Owner only)

        Usage:
          !whitelist add <plugin> <user_id>
          !whitelist remove <plugin> <user_id>
        """
        action_val = action.strip().lower()
        if action_val not in ("add", "remove"):
            await ctx.send(
                "❌ Action must be either `add` or `remove`. Usage: `!whitelist <add|remove> <cog_name> <user_id>`"
            )
            return

        clean_user_id = user_id.strip()
        if not clean_user_id.isdigit():
            await ctx.send(
                f"❌ Invalid Discord User ID: `{user_id}`. Please provide a numeric user ID."
            )
            return

        cog_clean = cog_name.strip()

        if action_val == "add":
            added = await async_add_user_to_whitelist(clean_user_id, cog_clean)
            if added:
                await ctx.send(
                    f"✅ Successfully added `{clean_user_id}` to the `{cog_clean}` whitelist (persisted)."
                )
            else:
                await ctx.send(
                    f"ℹ️ User `{clean_user_id}` is already in the `{cog_clean}` whitelist."
                )

        elif action_val == "remove":
            removed = await async_remove_user_from_whitelist(clean_user_id, cog_clean)
            if removed:
                await ctx.send(
                    f"✅ Successfully removed `{clean_user_id}` from the `{cog_clean}` whitelist (persisted)."
                )
            else:
                await ctx.send(
                    f"ℹ️ User `{clean_user_id}` is not in the `{cog_clean}` whitelist."
                )

    @commands.command(name="showwhitelist")
    @commands.is_owner()
    async def show_whitelist(self, ctx: commands.Context, cog_name: str):
        """Shows the current whitelist for a plugin."""
        cog_clean = cog_name.strip()
        whitelist_ids = await async_get_cog_whitelist(cog_clean)
        if whitelist_ids:
            formatted = ", ".join(str(uid) for uid in whitelist_ids)
            await ctx.send(
                f"**Whitelist for {cog_clean}:**\n`{formatted}`"
            )
        else:
            await ctx.send(
                f"**Whitelist for {cog_clean}:**\n`Empty or not set.`"
            )


async def setup(bot):
    await bot.add_cog(Permissions(bot))
