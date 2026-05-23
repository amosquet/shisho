import asyncio
import os
import random

import discord
import sentry_sdk
from discord.ext import commands
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


class CustomHelpCommand(commands.DefaultHelpCommand):
    def __init__(self, **options):
        options['verify_checks'] = False
        super().__init__(**options)
        self.show_all = False

    async def filter_commands(self, cmds, *, sort=False, key=None):
        cmds = await super().filter_commands(cmds, sort=sort, key=key)
        
        if self.show_all:
            return cmds
            
        filtered = []
        for cmd in cmds:
            if OWNER_ID and self.context.author.id == OWNER_ID:
                filtered.append(cmd)
                continue
                
            if cmd.cog:
                cog_name = cmd.cog.qualified_name.upper()
                if os.getenv(f"WHITELIST_ENABLE_{cog_name}", "").lower() == "false":
                    filtered.append(cmd)
                    continue
                    
                whitelist_env = os.getenv(f"WHITELIST_{cog_name}", "")
                if whitelist_env:
                    whitelist = [int(uid.strip()) for uid in whitelist_env.split(",") if uid.strip().isdigit()]
                    if self.context.author.id in whitelist:
                        filtered.append(cmd)
                        continue
            elif cmd.name == "help":
                filtered.append(cmd)
                continue
            elif not OWNER_ID:
                filtered.append(cmd)
                continue
                
        return filtered
        
    async def command_callback(self, ctx, *, command: str = None):
        if command == "all":
            self.show_all = True
            command = None
        else:
            self.show_all = False
            
        await super().command_callback(ctx, command=command)


class ShishoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # Required for command prefix processing
        super().__init__(command_prefix="!", intents=intents, help_command=CustomHelpCommand())

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

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return  # Ignore command not found errors

        if isinstance(error, commands.CheckFailure):
            # Log check failures to console for easier debugging
            print(
                f"Permission denied for user {ctx.author} (ID: {ctx.author.id}) on command: {ctx.command}"
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
            await ctx.send(random.choice(responses))
            return

        # For all other errors, capture them in Sentry
        import traceback
        traceback.print_exception(type(error), error, error.__traceback__)
        sentry_sdk.capture_exception(error)

        # Notify the user (optional, can be customised)
        if ctx.command:
            await ctx.send(
                f"An error occurred in `{ctx.command.name}`. The developers have been notified."
            )
        else:
            await ctx.send(
                "An unexpected error occurred. The developers have been notified."
            )

    async def on_ready(self):
        if self.user:
            print(f"Logged in as {self.user} (ID: {self.user.id})")
        else:
            print("Logged in, but user information is unavailable.")
        print("------")


def is_authorised_check(ctx):
    # Help command should always be publicly callable
    if ctx.command and ctx.command.name == "help":
        return True

    # Owner always has access
    if OWNER_ID and ctx.author.id == OWNER_ID:
        return True

    # Check for cog-specific whitelist
    if ctx.cog:
        cog_name = ctx.cog.qualified_name.upper()

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
            return ctx.author.id in whitelist

    return not OWNER_ID


async def main():
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables.")
        print("Please copy .env.example to .env and fill in your tokens.")
        return
    bot = ShishoBot()
    bot.add_check(is_authorised_check)
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
