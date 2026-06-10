import asyncio
import os
import random

import discord
import sentry_sdk
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Initialise Sentry
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        traces_sample_rate=1.0,
        # Set profiles_sample_rate to 1.0 to capture 100%
        # of transactions for profiling.
        profiles_sample_rate=1.0,
    )


async def is_authorised_interaction_check(interaction: discord.Interaction) -> bool:
    # Owner always has access
    if OWNER_ID and interaction.user.id == OWNER_ID:
        return True

    # Check for cog-specific whitelist
    if interaction.command:
        cog = getattr(interaction.command, 'binding', None)  # type: ignore
        if cog:
            cog_name = cog.__cog_name__.upper()

            # Check if whitelist is explicitly disabled for this cog (making it public)
            if os.getenv(f"WHITELIST_ENABLE_{cog_name}", "").lower() == "false":
                return True

            whitelist_env = os.getenv(f"WHITELIST_{cog_name}", "")
            if whitelist_env:
                whitelist = [
                    int(uid.strip())
                    for uid in whitelist_env.split(",")
                    if uid.strip().isdigit()
                ]
                return interaction.user.id in whitelist

    return not OWNER_ID


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        print(
            f"Permission denied for user {interaction.user} (ID: {interaction.user.id}) "
            f"on command: {interaction.command.name if interaction.command else 'Unknown'}"
        )
        responses = [
            "Nice try!",
            "Access Denied!",
            "Not today, my friend.",
            "You're not on the list. *adjusts sunglasses*",
            "Error 403: Too much ambition, not enough permissions.",
            "*Shisho shakes its head in disapproval.*",
            "Only the chosen ones can do that.",
        ]
        msg = random.choice(responses)
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    # For all other errors, capture them in Sentry
    import traceback
    traceback.print_exception(type(error), error, error.__traceback__)
    sentry_sdk.capture_exception(error)

    # Notify the user
    msg = (
        f"An error occurred in `{interaction.command.name if interaction.command else 'Unknown'}`. "
        "The developers have been notified."
    )
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


class ShishoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # Required for message parsing in listeners like notifications
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.tree.interaction_check = is_authorised_interaction_check
        self.tree.on_error = on_app_command_error
        self.disconnect_time = None
        self.is_first_ready = True

    def _format_duration(self, duration_seconds: int) -> str:
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if seconds > 0 or not parts:
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
            
        return ", ".join(parts)

    async def _notify_reconnect(self):
        if not OWNER_ID:
            return
            
        try:
            owner = await self.fetch_user(OWNER_ID)
            if not owner:
                return

            if self.disconnect_time:
                duration = discord.utils.utcnow() - self.disconnect_time
                duration_seconds = int(duration.total_seconds())
                self.disconnect_time = None
                
                if duration_seconds > 5:
                    duration_str = self._format_duration(duration_seconds)
                    await owner.send(f"ししょ is back from a service interruption that lasted {duration_str}.")
        except Exception as e:
            print(f"Could not send reconnect message to owner: {e}")

    async def setup_hook(self):
        # Load extensions (cogs)
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and filename != "__init__.py":
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    print(f"Loaded extension: {filename}")
                except Exception as e:
                    print(f"Failed to load extension {filename}: {e}")
                    sentry_sdk.capture_exception(e)
                    
        # Sync the app commands
        await self.tree.sync()
        print("Application commands synced.")

    async def on_disconnect(self):
        if self.disconnect_time is None:
            self.disconnect_time = discord.utils.utcnow()
        print("Bot disconnected. Recorded disconnect time.")

    async def on_resumed(self):
        print("Bot session resumed.")
        await self._notify_reconnect()

    async def on_ready(self):
        if self.user:
            print(f"Logged in as {self.user} (ID: {self.user.id})")
        else:
            print("Logged in, but user information is unavailable.")
        print("------")
        
        if self.is_first_ready:
            self.is_first_ready = False
            if OWNER_ID:
                try:
                    owner = await self.fetch_user(OWNER_ID)
                    if owner:
                        await owner.send("ししょ is online and ready")
                except Exception as e:
                    print(f"Could not send startup message to owner: {e}")
        else:
            await self._notify_reconnect()


async def main():
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables.")
        print("Please copy .env.example to .env and fill in your tokens.")
        return
    bot = ShishoBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
