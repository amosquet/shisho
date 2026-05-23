from discord.ext import commands


class PingPong(commands.Cog):
    """Ping pong command."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx):
        """Checks the bot's latency."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! ({latency}ms)")


async def setup(bot):
    await bot.add_cog(PingPong(bot))
