import os

import discord
from discord.ext import commands

from utils.discord_helpers import is_user_authorized


class Notifications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_id = int(os.getenv("OWNER_ID", "0"))

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from bots to avoid loops or spam
        if message.author.bot:
            return

        if message.author.id == self.owner_id:
            return

        if self.owner_id and any(
            mention.id == self.owner_id for mention in message.mentions
        ):
            if not is_user_authorized(message.author.id, "NOTIFICATIONS"):
                return

            owner = self.bot.get_user(self.owner_id)
            if not owner:
                try:
                    owner = await self.bot.fetch_user(self.owner_id)
                except Exception:
                    # Silently fail if owner cannot be found/fetched
                    return

            server_name = message.guild.name if message.guild else "Direct Messages"

            # Create a jump link if in a guild
            if message.guild:
                jump_url = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
                location = f"**{server_name}** (#{message.channel.name})"
            else:
                jump_url = "N/A (Direct Message)"
                location = "Direct Messages"

            dm_content = f"🔔 **{message.author.name}** mentioned you in {location}!"
            dm_content += f"\n**Message content:** {message.clean_content}"
            if message.guild:
                dm_content += f"\n**Jump to message:** [Click here]({jump_url})"

            try:
                await owner.send(dm_content)
            except discord.Forbidden:
                # This happens if the owner has DMs closed
                pass
            except Exception:
                # General catch-all for other errors (like rate limits)
                pass


async def setup(bot):
    await bot.add_cog(Notifications(bot))
