import os
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk

from utils import google_books

class BookInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    @app_commands.command(name="bookinfo", description="Look up a book's details by title or ISBN.")
    @app_commands.describe(query="The title or ISBN of the book")
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def bookinfo(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        result = await google_books.fetch_book_data(query, self.google_books_api_key)
        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
            return

        await self.send_book_embed(interaction, result)

    async def get_book_info_text(self, query: str) -> str:
        result = await google_books.fetch_book_data(query, self.google_books_api_key)
        if isinstance(result, str):
            return result
            
        title = result.get("title")
        authors = ", ".join(result.get("authors", []))
        description = result.get("description", "")
        if len(description) > 300:
            description = description[:300] + "..."
            
        return (
            f"Title: {title}\n"
            f"Author(s): {authors}\n"
            f"Published: {result.get('publishedDate')}\n"
            f"Pages: {result.get('pageCount')}\n"
            f"Rating: {result.get('averageRating')}\n\n"
            f"Synopsis: {description}"
        )

    async def send_book_embed(self, interaction: discord.Interaction, book_data: dict):
        title = book_data.get("title")
        authors = ", ".join(book_data.get("authors", []))
        description = book_data.get("description", "")
        
        # Truncate description so it doesn't blow up the embed
        if len(description) > 1000:
            description = description[:1000] + "..."

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        embed.add_field(name="Author(s)", value=authors, inline=True)
        embed.add_field(name="Published", value=book_data.get("publishedDate"), inline=True)
        embed.add_field(name="Pages", value=str(book_data.get("pageCount")), inline=True)
        embed.add_field(name="Rating", value=str(book_data.get("averageRating")), inline=True)

        thumbnail = book_data.get("thumbnail")
        if thumbnail:
            thumbnail = thumbnail.replace("http://", "https://")
            embed.set_thumbnail(url=thumbnail)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(BookInfo(bot))
