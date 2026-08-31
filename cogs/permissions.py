import os

import discord
from discord import app_commands
from discord.ext import commands

from utils.discord_helpers import (
    async_add_user_to_whitelist,
    async_get_cog_whitelist,
    async_remove_user_from_whitelist,
)


def is_owner():
    def predicate(interaction: discord.Interaction) -> bool:
        owner_id = int(os.getenv("OWNER_ID", "0"))
        return interaction.user.id == owner_id
    return app_commands.check(predicate)


class Permissions(commands.Cog):
    """Commands for managing bot permissions and whitelists."""

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the bot owner can use commands in this cog."""
        owner_id = int(os.getenv("OWNER_ID", "0"))
        if interaction.user.id != owner_id:
            raise app_commands.CheckFailure("You do not have permission to use this command.")
        return True

    @app_commands.command(name="whitelist", description="Manages a plugin's whitelist. (Owner only)")
    @app_commands.describe(
        action="Whether to add or remove the user",
        cog_name="The name of the plugin/cog",
        user_id="The Discord user ID"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
    ])
    @is_owner()
    async def manage_whitelist(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        cog_name: str,
        user_id: str,
    ):
        clean_user_id = user_id.strip()
        if not clean_user_id.isdigit():
            await interaction.response.send_message(
                f"❌ Invalid Discord User ID: `{user_id}`. Please provide a numeric user ID.",
                ephemeral=True,
            )
            return

        action_val = action.value
        cog_clean = cog_name.strip()

        if action_val == "add":
            added = await async_add_user_to_whitelist(clean_user_id, cog_clean)
            if added:
                await interaction.response.send_message(
                    f"✅ Successfully added `{clean_user_id}` to the `{cog_clean}` whitelist (persisted)."
                )
            else:
                await interaction.response.send_message(
                    f"ℹ️ User `{clean_user_id}` is already in the `{cog_clean}` whitelist."
                )

        elif action_val == "remove":
            removed = await async_remove_user_from_whitelist(clean_user_id, cog_clean)
            if removed:
                await interaction.response.send_message(
                    f"✅ Successfully removed `{clean_user_id}` from the `{cog_clean}` whitelist (persisted)."
                )
            else:
                await interaction.response.send_message(
                    f"ℹ️ User `{clean_user_id}` is not in the `{cog_clean}` whitelist."
                )

    @app_commands.command(name="showwhitelist", description="Shows the current whitelist for a plugin.")
    @app_commands.describe(cog_name="The name of the plugin/cog")
    async def show_whitelist(self, interaction: discord.Interaction, cog_name: str):
        cog_clean = cog_name.strip()
        whitelist_ids = await async_get_cog_whitelist(cog_clean)
        if whitelist_ids:
            formatted = ", ".join(str(uid) for uid in whitelist_ids)
            await interaction.response.send_message(
                f"**Whitelist for {cog_clean}:**\n`{formatted}`"
            )
        else:
            await interaction.response.send_message(
                f"**Whitelist for {cog_clean}:**\n`Empty or not set.`"
            )


async def setup(bot):
    await bot.add_cog(Permissions(bot))
