import asyncio
import os
import discord
from discord.ext import commands, tasks
import sentry_sdk
import datetime

class BooksSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sync_task.start()

    def cog_unload(self):
        self.sync_task.cancel()

    @tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
    async def sync_task(self):
        try:
            print("Starting daily one-way books sync...")
            process = await asyncio.create_subprocess_exec(
                "uv", "run", "utils/sync_books.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                print(f"Error running sync_books.py: {stderr.decode()}")
                sentry_sdk.capture_message(f"Books sync script failed: {stderr.decode()}", level="error")
            else:
                print(f"Successfully ran sync_books.py:\n{stdout.decode()}")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"Exception in background books sync task: {e}")

    @sync_task.before_loop
    async def before_sync_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(BooksSync(bot))
