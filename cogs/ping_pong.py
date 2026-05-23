import discord
from discord import app_commands
from discord.ext import commands


class PingPong(commands.Cog):
    """Ping pong command."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Checks the bot's latency.")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! ({latency}ms)")


async def setup(bot):
    await bot.add_cog(PingPong(bot))
