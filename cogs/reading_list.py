import os
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands
from pocketbase import PocketBase

class ReadingList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    def get_pb_client(self):
        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing in environment variables.")
        url = self.pb_url if "://" in self.pb_url else f"https://{self.pb_url}"
        pb = PocketBase(url)
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

    @app_commands.command(name="addbook", description="Adds a book to the reading list on PocketBase.")
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        publish_date="Publish date",
        isbn="ISBN of the book",
        status="Status of the book",
        start_date="Start reading date (YYYY-MM-DD)",
        end_date="Finished reading date (YYYY-MM-DD)"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Planned", value="planned"),
        app_commands.Choice(name="Reading", value="reading"),
        app_commands.Choice(name="Read", value="read"),
        app_commands.Choice(name="Dropped", value="dropped"),
    ])
    async def add_book(
        self,
        interaction: discord.Interaction,
        title: str,
        author: str,
        publish_date: str,
        isbn: str,
        status: app_commands.Choice[str],
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        await interaction.response.defer()
        status_val = status.value
        today = datetime.now().strftime("%Y-%m-%d")

        final_start_date = (
            start_date
            if start_date
            else (today if status_val in ["read", "reading"] else "")
        )
        final_end_date = end_date if end_date else (today if status_val == "read" else "")

        try:
            await self.add_book_to_pocketbase(title, author, status_val, publish_date, isbn, final_start_date, final_end_date)
            await interaction.followup.send(
                f"Successfully added **{title}** by {author} to the reading list!"
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def add_book_to_pocketbase(
        self,
        title: str,
        author: str,
        status_val: str,
        publish_date: str,
        isbn: str,
        final_start_date: str,
        final_end_date: str
    ):
        isbn = isbn.replace("-", "")

        def _add():
            pb = self.get_pb_client()
            new_book = {
                "title": title,
                "author": author,
                "status": status_val,
                "publishDate": publish_date,
                "isbn": isbn,
                "startDate": final_start_date,
                "endDate": final_end_date,
            }
            pb.collection("books").create(new_book)

        await self.bot.loop.run_in_executor(None, _add)

    async def fetch_reading_list(self) -> list[dict]:
        def _fetch():
            try:
                if not self.pb_url or not self.pb_user or not self.pb_password:
                    return []
                pb = self.get_pb_client()
                records = pb.collection("books").get_full_list()
                result = []
                for r in records:
                    result.append({
                        "title": getattr(r, "title", ""),
                        "author": getattr(r, "author", ""),
                        "status": getattr(r, "status", ""),
                        "publishDate": getattr(r, "publishDate", ""),
                        "isbn": getattr(r, "isbn", ""),
                        "startDate": getattr(r, "startDate", ""),
                        "endDate": getattr(r, "endDate", ""),
                    })
                return result
            except Exception as e:
                sentry_sdk.capture_exception(e)
                return []

        return await self.bot.loop.run_in_executor(None, _fetch)

async def setup(bot):
    await bot.add_cog(ReadingList(bot))
