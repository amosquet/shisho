import asyncio
import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import sentry_sdk
import datetime

class BooksSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sync_task.start()

    def cog_unload(self):
        self.sync_task.cancel()

    async def _run_sync(self) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "uv", "run", "utils/sync_books.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode(), stderr.decode()

    @tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
    async def sync_task(self):
        try:
            print("Starting daily one-way books sync...")
            returncode, stdout, stderr = await self._run_sync()
            if returncode != 0:
                print(f"Error running sync_books.py: {stderr}")
                sentry_sdk.capture_message(f"Books sync script failed: {stderr}", level="error")
            else:
                print(f"Successfully ran sync_books.py:\n{stdout}")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"Exception in background books sync task: {e}")

    @sync_task.before_loop
    async def before_sync_task(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="syncbooks", description="Force sync owner's reading list from shisho_books to books collection.")
    async def syncbooks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            returncode, stdout, stderr = await self._run_sync()
            if returncode != 0:
                sentry_sdk.capture_message(f"Books sync script failed: {stderr}", level="error")
                await interaction.followup.send(f"❌ Books sync failed:\n```{stderr[:1800]}```")
            else:
                output_summary = stdout.strip() if stdout.strip() else "Sync complete."
                await interaction.followup.send(f"✅ Books sync completed:\n```{output_summary[:1800]}```")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ An error occurred during sync: {e}")

async def setup(bot):
    await bot.add_cog(BooksSync(bot))
