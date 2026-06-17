import os
import aiohttp
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands
from pocketbase import PocketBase


class SuggestedBooks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    @app_commands.command(name="suggest", description="Suggests a book.")
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        isbn="ISBN of the book (optional, fetches details if provided alone)"
    )
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def suggest_book(
        self, 
        interaction: discord.Interaction, 
        title: str | None = None, 
        author: str | None = None, 
        isbn: str | None = None
    ):
        if not title and not isbn:
            await interaction.response.send_message("Please provide a title or an ISBN.", ephemeral=True)
            return
            
        await interaction.response.defer()

        title = title or ""
        author = author or ""
        isbn = isbn or ""

        try:
            display_name = await self.add_suggestion(title, author, isbn, str(interaction.user), "Discord")
            await interaction.followup.send(
                f"Thanks {interaction.user.mention}! {display_name} has been added to the suggested books list."
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def add_suggestion(self, title: str, author: str, isbn: str, suggested_by: str, suggested_from: str) -> str:
        title = title or ""
        author = author or ""
        isbn = (isbn or "").replace("-", "")

        if isbn and not (title and author):
            if not self.google_books_api_key:
                raise Exception("Google Books API configuration missing. Cannot fetch book details.")
            try:
                url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&key={self.google_books_api_key}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("items"):
                                vol_info = data["items"][0].get("volumeInfo", {})
                                fetched_title = vol_info.get("title")
                                fetched_authors = vol_info.get("authors")
                                if fetched_title:
                                    title = fetched_title
                                if fetched_authors:
                                    author = ", ".join(fetched_authors)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                # Fail gracefully and proceed with whatever we had parsed originally

        display_name = ""
        if title and author:
            display_name = f"**{title}** by {author}"
        elif title:
            display_name = f"**{title}**"
        elif isbn:
            display_name = f"ISBN: {isbn}"
        else:
            display_name = "Unknown Book"

        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing.")

        def add_to_pocketbase():
            pb = PocketBase(self.pb_url or "")
            pb.collection("shisho_users").auth_with_password(self.pb_user or "", self.pb_password or "")

            entry = {
                "title": title,
                "author": author,
                "isbn": isbn,
                "suggestedBy": suggested_by,
                "suggestedFrom": suggested_from,
                "dateSuggested": datetime.now().strftime("%Y-%m-%d"),
            }

            pb.collection("suggested_books").create(entry)

        await self.bot.loop.run_in_executor(None, add_to_pocketbase)
        return display_name

    @app_commands.command(name="suggestions", description="Lists the latest suggested books.")
    async def list_suggestions(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if not self.pb_url or not self.pb_user or not self.pb_password:
            await interaction.followup.send("Error: PocketBase configuration missing.")
            return

        try:
            response = await self.get_suggestions_text()
            await interaction.followup.send(response)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def get_suggestions_text(self) -> str:
        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing.")

        def get_from_pocketbase():
            pb = PocketBase(self.pb_url or "")
            pb.collection("shisho_users").auth_with_password(self.pb_user or "", self.pb_password or "")
            return pb.collection("suggested_books").get_list(1, 10, query_params={"sort": "-dateSuggested"})

        result = await self.bot.loop.run_in_executor(None, get_from_pocketbase)

        if not result.items:
            return "No books have been suggested yet!"

        response = "**Latest Suggested Books:**\n"
        for idx, record in enumerate(result.items, 1):
            title = getattr(record, "title", "")
            isbn = getattr(record, "isbn", "")
            author = getattr(record, "author", "")
            
            display_title = title if title else (f"ISBN: {isbn}" if isbn else "Unknown Book")
            
            if author:
                response += f"{idx}. **{display_title}** by {author}\n"
            else:
                response += f"{idx}. **{display_title}**\n"

        return response


async def setup(bot):
    await bot.add_cog(SuggestedBooks(bot))
