import os

import discord
from discord import app_commands
from discord.ext import commands


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
    async def manage_whitelist(self, interaction: discord.Interaction, action: app_commands.Choice[str], cog_name: str, user_id: str):
        action_val = action.value
        cog_name = cog_name.upper()
        env_key = f"WHITELIST_{cog_name}"

        current_whitelist = os.getenv(env_key, "")
        whitelist_ids = [
            uid.strip() for uid in current_whitelist.split(",") if uid.strip()
        ]

        if action_val == "add":
            if user_id in whitelist_ids:
                await interaction.response.send_message(
                    f"User `{user_id}` is already in the `{cog_name}` whitelist."
                )
            else:
                whitelist_ids.append(user_id)
                os.environ[env_key] = ",".join(whitelist_ids)
                await interaction.response.send_message(
                    f"Successfully added `{user_id}` to the `{cog_name}` whitelist."
                )

        elif action_val == "remove":
            if user_id not in whitelist_ids:
                await interaction.response.send_message(
                    f"User `{user_id}` is not in the `{cog_name}` whitelist."
                )
            else:
                whitelist_ids.remove(user_id)
                os.environ[env_key] = ",".join(whitelist_ids)
                await interaction.response.send_message(
                    f"Successfully removed `{user_id}` from the `{cog_name}` whitelist."
                )

    @app_commands.command(name="showwhitelist", description="Shows the current whitelist for a plugin.")
    @app_commands.describe(cog_name="The name of the plugin/cog")
    async def show_whitelist(self, interaction: discord.Interaction, cog_name: str):
        cog_name = cog_name.upper()
        env_key = f"WHITELIST_{cog_name}"
        whitelist = os.getenv(env_key, "Empty or not set.")
        await interaction.response.send_message(f"**Whitelist for {cog_name}:**\n`{whitelist}`")


async def setup(bot):
    await bot.add_cog(Permissions(bot))
